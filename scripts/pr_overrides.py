#!/usr/bin/env python3
"""
pr_overrides.py — deterministic half of the optional Mem PR-override store.

`/oracle-release` discovers each ORC ticket's GitHub PRs with one list sweep
plus a search secondary (gather_notes_data_oracle.py). When the sweep is wrong
or empty, a human records the right PR number once, and that decision lives in
ONE Mem collection ("Oracle PR Overrides", one note per ticket). Mem is only
reachable through the Mem MCP (Claude-side), so this script never talks to
Mem itself. It does the two deterministic, testable halves around the
orchestrator's Mem calls, exactly like dev_directory.py does for the developer
directory:

  load_overrides   Library entry point, imported by the gather script. Parses
                   the saved `list_notes` dump into {ticket -> override},
                   newest `Resolved at` wins when a ticket has several notes.

  pending          Given ONE `ORC-NNN -> PR` pick from the preview, emit the
                   Mem write that records it: a `create` when the ticket has
                   no note yet, or an `update` (full body + the note's
                   current `version`) when it does. The orchestrator performs
                   the listed call.

  show             Print the parsed overrides as a table (used in the preview).

Both subcommands print a machine-readable block on every exit path:

    ---PR_OVERRIDES_RESULT---
    {"ok", "action", ...counts..., "error"}

Exit codes: 0 success, 1 bad input (ticket not ORC-NNN, PR not a positive
integer, unreadable dump). A missing or empty notes file is NOT an error — it
just means no overrides.

Mem is overrides-only, never a cache: a note exists only because a person
decided. The sweep stays the source of truth for everything else.

Usage:
    python3 pr_overrides.py pending --ticket ORC-232 --pr 415 \
        --notes /tmp/oracle_pr_overrides.json \
        --out /tmp/oracle_pr_overrides_pending.json \
        --resolved-by shubham@medtricslab.com \
        [--reason "two open PRs; 416 is a spike branch"] \
        [--data /tmp/oracle_notes_data.json]
    python3 pr_overrides.py show --notes /tmp/oracle_pr_overrides.json

Note schema (first line = Mem note title):

    PR Override: ORC-232

    **Ticket:** ORC-232
    **PR:** 415
    **PR URL:** https://github.com/medtrics/medtrics-oracle/pull/415
    **Resolved by:** shubham@medtricslab.com
    **Resolved at:** 2026-09-02T12:00:00Z
    **Reason:** two open PRs; 416 is a spike branch

A human may edit the `PR` line in Mem directly; the next run picks it up.
"""

import argparse
import datetime as _dt
import json
import os
import re
import sys
from pathlib import Path

COLLECTION_TITLE = "Oracle PR Overrides"
COLLECTION_DESCRIPTION = (
    "medtrics-release-notes: one note per Oracle (ORC) ticket recording the "
    "GitHub PR a human chose when /oracle-release's PR sweep was wrong or "
    "empty. Read by the gather script before the primary rule; written only "
    "from the /oracle-release preview. Overrides only, never a cache. Safe to "
    "edit the PR line or trash a note by hand."
)
TITLE_PREFIX = "PR Override: "

# Tolerates hand-edited shapes: an optional leading "- " / "* " bullet and the
# colon inside OR outside the bold (`**PR:** 415` and `**PR**: 415`).
FIELD_RE = re.compile(
    r"^(?:[-*]\s+)?\*\*(?P<key>[^*]+?)(?::\*\*|\*\*:)\s*(?P<value>.*)$")
TICKET_RE = re.compile(r"^ORC-\d+$")
REPO_RE = re.compile(r"^[\w.\-]+/[\w.\-]+$")

# Note field labels (exact rendering) and their internal keys.
FIELDS = [
    ("Ticket", "ticket"),
    ("PR", "pr"),
    ("PR URL", "pr_url"),
    ("Resolved by", "resolved_by"),
    ("Resolved at", "resolved_at"),
    ("Reason", "reason"),
]


def parse_pr_number(value):
    """A positive integer PR number from a string/int, else None.
    Tolerates a leading '#' and a full GitHub PR URL."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    raw = str(value or "").strip()
    m = re.search(r"/pull/(\d+)", raw)  # URL, with or without a fragment/query
    if m:
        raw = m.group(1)
    # "PR 415", "PR#415", "#415" -> "415"
    raw = re.sub(r"^(?:PR\s*)?#?\s*", "", raw, flags=re.IGNORECASE)
    if not raw.isdigit():
        return None
    n = int(raw)
    return n if n > 0 else None


def parse_ts(value):
    """ISO-8601 (`Z` or offset or naive) -> aware UTC datetime, else None."""
    if not value or not isinstance(value, str):
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = _dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed


# --------------------------------------------------------------------------- #
# Note parsing / rendering
# --------------------------------------------------------------------------- #

def is_override_note(note):
    """True when the note's title line carries the override prefix (used to
    tell a malformed override note from an unrelated stray note)."""
    content = (note.get("content") or "") if isinstance(note, dict) else ""
    lines = content.splitlines()
    return bool(lines) and lines[0].strip().startswith(TITLE_PREFIX)


def parse_note(note):
    """Parse one Mem note (dict with content/id/version) into an override.

    Returns None for notes that are not override notes (no `PR Override:`
    title), whose ticket is not `ORC-NNN`, or whose PR is not a positive
    integer — so stray or hand-broken notes cannot corrupt a run.
    """
    content = note.get("content") or ""
    lines = content.splitlines()
    if not lines:
        return None
    title = lines[0].strip()
    if not title.startswith(TITLE_PREFIX):
        return None
    rec = {
        "note_id": note.get("id") or note.get("note_id") or "",
        "version": note.get("version"),
    }
    for _label, key in FIELDS:
        rec[key] = ""
    for line in lines[1:]:
        m = FIELD_RE.match(line.strip())
        if not m:
            continue
        label = m.group("key").strip().lower()
        value = m.group("value").strip()
        for flabel, key in FIELDS:
            if flabel.lower() == label:
                rec[key] = value
    title_ticket = title[len(TITLE_PREFIX):].strip().upper()
    field_ticket = rec["ticket"].strip().upper()
    ticket = field_ticket or title_ticket
    if not TICKET_RE.match(ticket):
        return None
    pr = parse_pr_number(rec["pr"])
    if pr is None:
        return None
    rec["parse_warnings"] = []
    if (field_ticket and TICKET_RE.match(title_ticket)
            and field_ticket != title_ticket):
        rec["parse_warnings"].append(
            f"override note {rec['note_id'] or '?'}: title says "
            f"{title_ticket} but the Ticket field says {field_ticket}; "
            f"Ticket wins")
    rec["ticket"] = ticket
    rec["pr"] = pr
    for key in ("pr_url", "resolved_by", "resolved_at", "reason"):
        if rec[key] == "—":
            rec[key] = ""
    return rec


def render_note(rec):
    """Render an override back to the note body (first line = title)."""
    out = [f"{TITLE_PREFIX}{rec['ticket']}", ""]
    for label, key in FIELDS:
        val = rec.get(key)
        out.append(f"**{label}:** {val if val not in (None, '') else '—'}")
    return "\n".join(out) + "\n"


def load_notes(path):
    """Load the orchestrator's saved `list_notes` response as raw note dicts.

    Accepts a JSON object with a `notes` / `items` / `results` list, a bare
    list, or a missing/empty file (-> no notes). Non-dict entries are dropped.
    """
    p = Path(path) if path else None
    if not p or not p.exists() or p.stat().st_size == 0:
        return []
    raw = json.loads(p.read_text())
    if isinstance(raw, dict):
        notes = raw.get("notes") or raw.get("items") or raw.get("results") or []
    elif isinstance(raw, list):
        notes = raw
    else:
        notes = []
    return [n for n in notes if isinstance(n, dict)]


def load_overrides(path):
    """{ticket -> override} from a saved dump, plus a list of warnings.

    Malformed override notes (prefix present, bad ticket or PR) are ignored
    with a warning naming the note id. Entries sharing a note id (the command
    may have replaced a note in the dump after an update) are deduped first —
    highest `version` wins, silently. Several notes for one ticket: the one
    with the newest parseable `Resolved at` wins (an unparseable date sorts
    oldest), with a warning. Never raises: a missing/empty file is no
    overrides; an unreadable/malformed dump is no overrides plus a warning.
    """
    warnings = []
    overrides = {}
    per_ticket = {}
    try:
        notes = load_notes(path)
    except (OSError, ValueError) as e:
        return {}, [f"override dump unreadable ({path}): {e} — no overrides "
                    f"applied; re-run the Mem list_notes dump"]

    def _version(n):
        try:
            return int(n.get("version") or 0)
        except (TypeError, ValueError):
            return 0

    by_id = {}
    anonymous = []
    for note in notes:
        nid = note.get("id") or note.get("note_id")
        if not nid:
            anonymous.append(note)
            continue
        prev = by_id.get(nid)
        if prev is None or _version(note) >= _version(prev):
            by_id[nid] = note

    for note in list(by_id.values()) + anonymous:
        rec = parse_note(note)
        if rec is None:
            if is_override_note(note):
                nid = note.get("id") or note.get("note_id") or "?"
                warnings.append(f"override note {nid} ignored: malformed "
                                f"(ticket must be ORC-NNN, PR a positive "
                                f"integer)")
            continue
        warnings.extend(rec.pop("parse_warnings", []))
        per_ticket.setdefault(rec["ticket"], []).append(rec)
    floor = _dt.datetime.min.replace(tzinfo=_dt.timezone.utc)
    for ticket, recs in per_ticket.items():
        if len(recs) > 1:
            recs.sort(key=lambda r: parse_ts(r.get("resolved_at")) or floor,
                      reverse=True)
            warnings.append(f"{len(recs)} override notes for {ticket}; "
                            f"newest used (PR {recs[0]['pr']}, note "
                            f"{recs[0]['note_id'] or '?'}) — trash the stale "
                            f"one(s) in Mem")
        overrides[ticket] = recs[0]
    return overrides, warnings


# --------------------------------------------------------------------------- #
# Subcommands
# --------------------------------------------------------------------------- #

def emit_result(payload):
    print()
    print("---PR_OVERRIDES_RESULT---")
    print(json.dumps(payload))


def _repo_from_env(env_path):
    """Best-effort GITHUB_REPO ('owner/repo') from a .env file; '' if absent.
    Mirrors the gather script's normalization (URL, .git, slashes)."""
    if not env_path or not os.path.exists(env_path):
        return ""
    repo = ""
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() == "GITHUB_REPO":
                repo = value.strip().strip("'\"")
    repo = re.sub(r"^https?://github\.com/", "", repo, flags=re.IGNORECASE)
    repo = repo.rstrip("/")
    if repo.lower().endswith(".git"):
        repo = repo[:-4]
    repo = repo.rstrip("/")
    return repo if REPO_RE.match(repo) else ""


def _url_from_data(data_path, ticket, pr):
    """The PR's web_url from the gathered data when the ticket's mrs already
    carry that PR; '' otherwise (missing file, no match)."""
    if not data_path:
        return ""
    p = Path(data_path)
    if not p.exists():
        return ""
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return ""
    for issue in (data.get("issues") or []) if isinstance(data, dict) else []:
        if (issue.get("identifier") or "").upper() != ticket:
            continue
        for mr in issue.get("mrs") or []:
            if mr.get("iid") == pr and mr.get("web_url"):
                return mr["web_url"]
    return ""


def cmd_pending(args):
    ticket = (args.ticket or "").strip().upper()
    if not TICKET_RE.match(ticket):
        raise ValueError(f"ticket must match ORC-NNN (got {args.ticket!r})")
    pr = parse_pr_number(args.pr)
    if pr is None:
        raise ValueError(f"PR must be a positive integer (got {args.pr!r})")
    overrides, warnings = load_overrides(args.notes)
    for w in warnings:
        print(f"WARNING: {w}", file=sys.stderr)

    url = _url_from_data(args.data, ticket, pr)
    if not url:
        repo = _repo_from_env(args.env_file)
        if repo:
            url = f"https://github.com/{repo}/pull/{pr}"
    now = args.now or _dt.datetime.now(_dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    rec = {"ticket": ticket, "pr": pr, "pr_url": url,
           "resolved_by": (args.resolved_by or "").strip(),
           "resolved_at": now, "reason": (args.reason or "").strip()}

    existing = overrides.get(ticket)
    if existing is not None and existing.get("note_id"):
        write = {"action": "update", "ticket": ticket, "pr": pr,
                 "note_id": existing["note_id"],
                 "version": existing.get("version"),
                 "previous_pr": existing["pr"],
                 "content": render_note(rec)}
    else:
        write = {"action": "create", "collection_title": COLLECTION_TITLE,
                 "ticket": ticket, "pr": pr, "content": render_note(rec)}

    out = {"collection_title": COLLECTION_TITLE,
           "collection_description": COLLECTION_DESCRIPTION,
           "writes": [write]}
    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=2))
    else:
        print(json.dumps(out, indent=2))
    print(f"PR override pending: {ticket} -> PR {pr} ({write['action']}"
          + (f", was PR {existing['pr']}" if write["action"] == "update" else "")
          + f"); url {url or 'unknown'}.", file=sys.stderr)
    emit_result({"ok": True, "action": "pending", "ticket": ticket, "pr": pr,
                 "write_action": write["action"], "pr_url": url,
                 "out": args.out or None, "warnings": warnings, "error": None})
    return 0


def cmd_show(args):
    overrides, warnings = load_overrides(args.notes)
    if overrides:
        print(f"{'Ticket':<10} {'PR':<7} {'Resolved by':<28} "
              f"{'Resolved at':<22} Reason")
        print(f"{'-'*10} {'-'*7} {'-'*28} {'-'*22} {'-'*30}")
        for ticket in sorted(overrides, key=lambda t: int(t.split('-')[1])):
            o = overrides[ticket]
            print(f"{ticket:<10} {o['pr']:<7} {(o['resolved_by'] or '—'):<28} "
                  f"{(o['resolved_at'] or '—'):<22} {o['reason'] or '—'}")
    else:
        print("No PR overrides.")
    for w in warnings:
        print(f"WARNING: {w}", file=sys.stderr)
    emit_result({"ok": True, "action": "show", "count": len(overrides),
                 "overrides": {t: o["pr"] for t, o in overrides.items()},
                 "warnings": warnings, "error": None})
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="Deterministic half of the Mem PR-override store")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("pending", help="Emit the Mem write for one ORC -> PR pick")
    p1.add_argument("--ticket", required=True, help="ORC-NNN")
    p1.add_argument("--pr", required=True, help="PR number (positive integer)")
    p1.add_argument("--notes", default="", help="Saved Mem list_notes response JSON "
                    "(missing/empty file = no overrides)")
    p1.add_argument("--out", default="", help="Write the pending-writes JSON here "
                    "(default: stdout)")
    p1.add_argument("--resolved-by", default="", help="Who decided (email)")
    p1.add_argument("--reason", default="", help="Why (free text, one line)")
    p1.add_argument("--data", default="", help="Gathered data JSON — used only to "
                    "fill the PR URL when the ticket's mrs already carry the PR")
    p1.add_argument("--env-file", default=".env", help="Read GITHUB_REPO here to "
                    "build the PR URL when --data has no match (best-effort)")
    p1.add_argument("--now", default="", help="Override the Resolved at timestamp")

    p2 = sub.add_parser("show", help="Print the parsed overrides table")
    p2.add_argument("--notes", default="", help="Saved Mem list_notes response JSON")

    args = ap.parse_args()
    try:
        if args.cmd == "pending":
            return cmd_pending(args)
        return cmd_show(args)
    except (ValueError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        emit_result({"ok": False, "action": args.cmd, "error": str(e)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
