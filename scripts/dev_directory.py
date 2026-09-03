#!/usr/bin/env python3
"""
dev_directory.py — deterministic half of the optional Mem developer directory.

The release commands (/release-slack, /oracle-release) can cache each
developer's Slack user ID in ONE Mem collection ("Release Notes Developer
Directory", one note per developer). Mem is only reachable through the Mem MCP
(Claude-side), so this script never talks to Mem itself. Instead it does the
two deterministic, testable halves around the orchestrator's Mem calls:

  prefill   Given the gathered data JSON and a JSON dump of the directory's
            notes (the orchestrator saves the Mem `list_notes` response to a
            file), write `assignee_slack_id` onto every issue whose developer
            is already in the directory — matched by exact email, then by
            GitHub/GitLab login, then by a unique normalized full name — and
            stamp `assignee_slack_resolved_via: "mem"`. The Slack resolver
            (slack_resolve_users.py) then skips those issues.

  pending   After the resolver ran, emit the Mem writes needed to store every
            developer it newly resolved (resolved_via != "mem"): a `create`
            for a developer with no note yet, or an `update` (full body +
            the note's current `version`) that merges new identifiers into an
            existing note. The orchestrator performs the listed calls.

Both subcommands print a machine-readable block on every exit path:

    ---DEV_DIRECTORY_RESULT---
    {"ok", "action", ...counts..., "error"}

Exit codes: 0 success, 1 configuration error (unreadable input). A missing or
empty notes file is NOT an error — it just means an empty directory (prefill
matches nothing; pending creates a note for every resolved developer).

Usage:
    python3 dev_directory.py prefill --data /tmp/oracle_notes_data.json \
        --notes /tmp/oracle_release_dev_directory.json
    python3 dev_directory.py pending --data /tmp/oracle_notes_data.json \
        --notes /tmp/oracle_release_dev_directory.json \
        --out /tmp/oracle_release_dev_directory_pending.json \
        --source /oracle-release

Note schema (first line = Mem note title):

    Developer: Chris Tokodi

    **Slack ID:** U02AB0913
    **Emails:** chris@medtricslab.com
    **GitHub login:** ctokodi
    **GitLab username:** ctokodi
    **Optimus assignee id:** 42
    **Resolved via:** email
    **First seen:** 2026-09-02
    **Last used:** 2026-09-02
    **Source:** medtrics-release-notes /oracle-release
"""

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path

COLLECTION_TITLE = "Release Notes Developer Directory"
COLLECTION_DESCRIPTION = (
    "medtrics-release-notes: one note per developer mapping name, emails, "
    "GitHub/GitLab logins and Optimus assignee id to a Slack user id. Read by "
    "/release-slack and /oracle-release before the Slack resolver runs; written "
    "with every newly resolved developer. Safe to edit or delete a note by hand."
)
TITLE_PREFIX = "Developer: "

FIELD_RE = re.compile(r"^\*\*(?P<key>[^*]+?):\*\*\s*(?P<value>.*)$")
# Sanity shape only (real ids are 9–11 chars); the lint gate enforces Slack's
# exact mention grammar downstream.
SLACK_ID_RE = re.compile(r"^[UW][A-Z0-9]{4,}$")

# Note field labels (exact rendering) and their internal keys.
FIELDS = [
    ("Slack ID", "slack_id"),
    ("Emails", "emails"),
    ("GitHub login", "github_login"),
    ("GitLab username", "gitlab_username"),
    ("Optimus assignee id", "optimus_assignee_id"),
    ("Resolved via", "resolved_via"),
    ("First seen", "first_seen"),
    ("Last used", "last_used"),
    ("Source", "source"),
]
LIST_FIELDS = {"emails"}


def normalize_name(value):
    """Lowercase, strip punctuation, collapse whitespace — for name matching."""
    if not value:
        return ""
    v = re.sub(r"[^\w\s]", " ", str(value).lower())
    return " ".join(v.split())


def _split_list(value):
    return [p.strip() for p in re.split(r"[,\s]+", value or "") if p.strip()]


# --------------------------------------------------------------------------- #
# Issue identity — mirrors slack_resolve_users.issue_identity, plus logins.
# --------------------------------------------------------------------------- #

def issue_identity(issue):
    """Return the developer identity for an issue as a dict.

    Classic (Optimus assignee) shape: `assignee_email` / `assignee_name` /
    `assignee_id`. Oracle (GitHub PR author) shape has no assignee, so the
    first `mrs[]` entry's `author_email` / `author_name` / `author_username`
    is the developer. The login is only taken from `mrs[]` when the issue has
    NO assignee fields — on the classic track the MR author may be a different
    person from the assignee, and mixing them would corrupt the directory.
    """
    ident = {"email": "", "name": "", "login": "", "assignee_id": "",
             "track": ""}
    email = (issue.get("assignee_email") or "").strip().lower()
    name = (issue.get("assignee_name") or "").strip()
    if email or name:
        ident.update(email=email, name=name,
                     assignee_id=str(issue.get("assignee_id") or "").strip(),
                     track="classic")
        return ident
    for mr in issue.get("mrs", []) or []:
        e = (mr.get("author_email") or "").strip().lower()
        n = (mr.get("author_name") or "").strip()
        u = (mr.get("author_username") or "").strip()
        if e or n or u:
            ident.update(email=e, name=n or u, login=u, track="oracle")
            return ident
    return ident


# --------------------------------------------------------------------------- #
# Note parsing / rendering
# --------------------------------------------------------------------------- #

def parse_note(note):
    """Parse one Mem note (dict with content/id/version) into a record.

    Returns None for notes that are not directory notes (no `Developer:` title
    or no Slack ID) so stray notes in the collection are ignored.
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
        "name": title[len(TITLE_PREFIX):].strip(),
    }
    for label, key in FIELDS:
        rec[key] = [] if key in LIST_FIELDS else ""
    for line in lines[1:]:
        m = FIELD_RE.match(line.strip())
        if not m:
            continue
        label = m.group("key").strip().lower()
        value = m.group("value").strip()
        for flabel, key in FIELDS:
            if flabel.lower() == label:
                if key in LIST_FIELDS:
                    rec[key] = [v.lower() for v in _split_list(value)]
                else:
                    rec[key] = value
    if not SLACK_ID_RE.match(rec.get("slack_id", "")):
        return None
    return rec


def render_note(rec):
    """Render a record back to the note body (first line = title)."""
    out = [f"{TITLE_PREFIX}{rec.get('name', '').strip() or rec['slack_id']}", ""]
    for label, key in FIELDS:
        val = rec.get(key)
        if key in LIST_FIELDS:
            val = ", ".join(val or [])
        out.append(f"**{label}:** {val or '—'}")
    return "\n".join(out) + "\n"


def load_notes(path):
    """Load the orchestrator's saved `list_notes` response.

    Accepts a JSON object with a `notes` list, a bare list, or a missing/empty
    file (→ empty directory). Returns the parsed directory records.
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
    records = []
    for n in notes:
        if not isinstance(n, dict):
            continue
        rec = parse_note(n)
        if rec:
            records.append(rec)
    return records


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #

class Directory:
    def __init__(self, records):
        self.records = records
        self.by_email = {}
        self.by_login = {}
        self.by_name = {}
        self.by_slack = {}
        for r in records:
            self.by_slack.setdefault(r["slack_id"], r)
            for e in r.get("emails", []):
                self.by_email.setdefault(e.lower(), r)
            for login in (r.get("github_login"), r.get("gitlab_username")):
                if login and login != "—":
                    self.by_login.setdefault(login.lower(), r)
            n = normalize_name(r.get("name"))
            if n and " " in n:
                self.by_name.setdefault(n, set()).add(r["slack_id"])

    def match(self, ident):
        """Return (record, how) or (None, None). Email → login → unique name."""
        if ident["email"] and ident["email"] in self.by_email:
            return self.by_email[ident["email"]], "email"
        if ident["login"] and ident["login"].lower() in self.by_login:
            return self.by_login[ident["login"].lower()], "login"
        n = normalize_name(ident["name"])
        if n and " " in n:
            ids = self.by_name.get(n) or set()
            if len(ids) == 1:
                return self.by_slack[next(iter(ids))], "name"
        return None, None


# --------------------------------------------------------------------------- #
# Subcommands
# --------------------------------------------------------------------------- #

def emit_result(payload):
    print()
    print("---DEV_DIRECTORY_RESULT---")
    print(json.dumps(payload))


def _load_data(path):
    p = Path(path)
    if not p.exists():
        raise ValueError(f"data file not found: {p}")
    data = json.loads(p.read_text())
    if not isinstance(data, dict) or not isinstance(data.get("issues"), list):
        raise ValueError("data file is not a JSON object with an 'issues' list")
    return data


def cmd_prefill(args):
    data = _load_data(args.data)
    directory = Directory(load_notes(args.notes))
    prefilled = 0
    by_how = {"email": 0, "login": 0, "name": 0}
    unmatched = []
    for issue in data["issues"]:
        ident_label = issue.get("identifier", "?")
        if issue.get("assignee_slack_id") and not args.force:
            continue  # already resolved by someone else — leave it alone
        rec, how = directory.match(issue_identity(issue))
        if rec:
            issue["assignee_slack_id"] = rec["slack_id"]
            issue["assignee_slack_resolved_via"] = "mem"
            if not issue.get("assignee_name") and rec.get("name"):
                # Oracle shape: give the storyteller a proper display name.
                issue.setdefault("assignee_name_from_directory", rec["name"])
            prefilled += 1
            by_how[how] += 1
        else:
            unmatched.append(ident_label)
    Path(args.data).write_text(json.dumps(data, indent=2))
    total = len(data["issues"])
    print(f"Directory prefill: {prefilled}/{total} developer(s) from Mem "
          f"({by_how['email']} by email, {by_how['login']} by login, "
          f"{by_how['name']} by name); {len(directory.records)} note(s) loaded."
          + (f" Left for the resolver: {', '.join(unmatched)}" if unmatched else ""),
          file=sys.stderr)
    emit_result({"ok": True, "action": "prefill", "prefilled": prefilled,
                 "total": total, "by_email": by_how["email"],
                 "by_login": by_how["login"], "by_name": by_how["name"],
                 "notes_loaded": len(directory.records),
                 "unmatched": unmatched, "error": None})
    return 0


def _merge_identity(rec, ident, via, today, source):
    """Merge an issue identity into a record; return True if anything changed."""
    changed = False
    if ident["email"] and ident["email"] not in rec["emails"]:
        rec["emails"].append(ident["email"])
        changed = True
    if ident["login"]:
        key = "github_login" if ident["track"] == "oracle" else "gitlab_username"
        if not rec.get(key) or rec[key] == "—":
            rec[key] = ident["login"]
            changed = True
    if ident["assignee_id"] and (not rec.get("optimus_assignee_id")
                                 or rec["optimus_assignee_id"] == "—"):
        rec["optimus_assignee_id"] = ident["assignee_id"]
        changed = True
    if not rec.get("name") and ident["name"]:
        rec["name"] = ident["name"]
        changed = True
    if not rec.get("resolved_via") or rec["resolved_via"] == "—":
        rec["resolved_via"] = via
        changed = True
    if not rec.get("first_seen") or rec["first_seen"] == "—":
        rec["first_seen"] = today
        changed = True
    if changed:
        rec["last_used"] = today
        rec["source"] = source
    return changed


def cmd_pending(args):
    data = _load_data(args.data)
    records = load_notes(args.notes)
    directory = Directory(records)
    today = args.today or _dt.date.today().isoformat()
    source = f"medtrics-release-notes {args.source}".strip()

    creates = {}      # slack_id -> new record
    updates = {}      # note_id -> (record, changed)
    skipped_mem = 0
    for issue in data["issues"]:
        uid = (issue.get("assignee_slack_id") or "").strip()
        via = (issue.get("assignee_slack_resolved_via") or "").strip()
        if not uid:
            continue
        if via == "mem":
            skipped_mem += 1
            continue
        ident = issue_identity(issue)
        via = via or "unknown"
        existing = directory.by_slack.get(uid)
        if existing is None:
            existing, _how = directory.match(ident)
            if existing is not None and existing["slack_id"] != uid:
                # The directory maps this identity to a DIFFERENT Slack id than
                # the resolver just found. Never silently overwrite — surface it.
                existing = None
        if existing is not None:
            rec = existing
            changed = _merge_identity(rec, ident, via, today, source)
            if changed:
                updates[rec["note_id"]] = rec
            continue
        rec = creates.get(uid)
        if rec is None:
            rec = {"note_id": "", "version": None, "name": ident["name"],
                   "slack_id": uid, "emails": [], "github_login": "",
                   "gitlab_username": "", "optimus_assignee_id": "",
                   "resolved_via": via, "first_seen": today,
                   "last_used": today, "source": source}
            creates[uid] = rec
        _merge_identity(rec, ident, via, today, source)

    writes = []
    for rec in creates.values():
        writes.append({"action": "create", "collection_title": COLLECTION_TITLE,
                       "slack_id": rec["slack_id"], "name": rec["name"],
                       "content": render_note(rec)})
    for note_id, rec in updates.items():
        writes.append({"action": "update", "note_id": note_id,
                       "version": rec.get("version"), "slack_id": rec["slack_id"],
                       "name": rec["name"], "content": render_note(rec)})

    out = {"collection_title": COLLECTION_TITLE,
           "collection_description": COLLECTION_DESCRIPTION,
           "writes": writes}
    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=2))
    else:
        print(json.dumps(out, indent=2))
    print(f"Directory pending: {len(creates)} create(s), {len(updates)} update(s); "
          f"{skipped_mem} issue(s) already served from Mem.", file=sys.stderr)
    emit_result({"ok": True, "action": "pending", "creates": len(creates),
                 "updates": len(updates), "served_from_mem": skipped_mem,
                 "out": args.out or None, "error": None})
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="Deterministic half of the Mem developer directory")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("prefill", help="Fill assignee_slack_id from directory notes")
    p1.add_argument("--data", required=True, help="Gathered data JSON (mutated in place)")
    p1.add_argument("--notes", default="", help="Saved Mem list_notes response JSON "
                    "(missing/empty file = empty directory)")
    p1.add_argument("--force", action="store_true",
                    help="Overwrite an assignee_slack_id that is already set")

    p2 = sub.add_parser("pending", help="Emit Mem writes for newly resolved developers")
    p2.add_argument("--data", required=True, help="Gathered data JSON (read-only)")
    p2.add_argument("--notes", default="", help="Saved Mem list_notes response JSON")
    p2.add_argument("--out", default="", help="Write the pending-writes JSON here "
                    "(default: stdout)")
    p2.add_argument("--source", default="", help="Calling command, e.g. /oracle-release")
    p2.add_argument("--today", default="", help="Override today's date (YYYY-MM-DD)")

    args = ap.parse_args()
    try:
        if args.cmd == "prefill":
            return cmd_prefill(args)
        return cmd_pending(args)
    except (ValueError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        emit_result({"ok": False, "action": args.cmd, "error": str(e)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
