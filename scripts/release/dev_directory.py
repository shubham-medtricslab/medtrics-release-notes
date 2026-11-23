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

  cache     A local copy of the directory dump, so most runs make no Mem read.
            `cache get --out <notes.json>` writes the copy there when it is
            under 24h old (`fresh: true`), else reports `fresh: false` and the
            command reads Mem. `cache put --notes <notes.json>` stores a fresh
            dump. `cache apply --writes <pending.json> --results <results.json>`
            folds the write-backs Mem accepted into the copy (results: one
            {id, version} or null per write, in order); without results the
            copy is dropped. `cache clear` drops it. The copy lives in
            `.medtrics-release-notes/cache/` in the run folder.

Every subcommand prints a machine-readable block on every exit path:

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
    **GitHub login:** ctokodi, chris-tokodi
    **GitLab username:** ctokodi
    **Optimus assignee id:** 42
    **Resolved via:** email
    **First seen:** 2026-09-02
    **Last used:** 2026-09-02
    **Source:** medtrics-release-notes /oracle-release

`Emails`, `GitHub login` and `GitLab username` each hold a comma-separated
list, so one developer who commits under several logins matches on any of
them. A note with a single value is simply a one-item list.
"""

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "kb"))
import kb_paths  # noqa: E402

COLLECTION_TITLE = "Release Notes Developer Directory"
COLLECTION_DESCRIPTION = (
    "medtrics-release-notes: one note per developer mapping name, emails, "
    "GitHub/GitLab logins and Optimus assignee id to a Slack user id. Read by "
    "/release-slack and /oracle-release before the Slack resolver runs; written "
    "with every newly resolved developer. Safe to edit or delete a note by hand."
)
TITLE_PREFIX = "Developer: "
CACHE_NAME = "dev_directory.json"
CACHE_MAX_AGE_HOURS = 24

FIELD_RE = re.compile(r"^\*\*(?P<key>[^*]+?):\*\*\s*(?P<value>.*)$")
# Sanity shape only (real ids are 9–11 chars); the lint gate enforces Slack's
# exact mention grammar downstream.
SLACK_ID_RE = re.compile(r"^[UW][A-Z0-9]{4,}$")
# Mem hands notes back as Markdown: a trailing `\` as its hard line break,
# escapes such as `\_` and `\.`, and emails as autolinks (`<a@b.com>`).
MD_ESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!~|<>])")
AUTOLINK_RE = re.compile(r"^<(?:mailto:)?(?P<inner>[^<>\s]+)>$", re.I)
# What render_note writes for an empty field; never a real list item.
PLACEHOLDERS = {"—", "-", "–"}

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
LIST_FIELDS = {"emails", "github_login", "gitlab_username"}
# Emails compare lowercased; logins keep the case they were written in and
# compare case-insensitively (GitHub and GitLab logins are case-insensitive).
LOWERCASE_FIELDS = {"emails"}
# Extra labels a human may type by hand; parsed into the same field.
LABEL_ALIASES = {
    "github logins": "github_login",
    "gitlab usernames": "gitlab_username",
}


def normalize_name(value):
    """Lowercase, strip punctuation, collapse whitespace — for name matching."""
    if not value:
        return ""
    v = re.sub(r"[^\w\s]", " ", str(value).lower())
    return " ".join(v.split())


def clean_line(line):
    """One note line as written: Mem's trailing hard break and Markdown
    escapes removed (same rule as pod_map.clean_line)."""
    line = line.rstrip()
    if line.endswith("\\") and not line.endswith("\\\\"):
        line = line[:-1].rstrip()
    return MD_ESCAPE_RE.sub(r"\1", line)


def _clean_item(value):
    """Unwrap a Markdown autolink (`<a@b.com>`, `<mailto:a@b.com>`)."""
    value = (value or "").strip()
    m = AUTOLINK_RE.match(value)
    return m.group("inner") if m else value


def _split_list(value):
    items = (_clean_item(p) for p in re.split(r"[,\s]+", value or ""))
    return [p for p in items if p and p not in PLACEHOLDERS]


def _dedupe(items):
    """Drop case-insensitive repeats, keeping the first spelling and order."""
    seen, out = set(), []
    for item in items:
        if item.lower() not in seen:
            seen.add(item.lower())
            out.append(item)
    return out


def _has(items, value):
    return value.lower() in {i.lower() for i in items or []}


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
    title = re.sub(r"^\s*#{1,6}\s+", "", clean_line(lines[0])).strip()
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
        m = FIELD_RE.match(clean_line(line).strip())
        if not m:
            continue
        label = m.group("key").strip().lower()
        value = _clean_item(m.group("value"))
        key = LABEL_ALIASES.get(label) or next(
            (k for flabel, k in FIELDS if flabel.lower() == label), None)
        if key is None:
            continue
        if key in LIST_FIELDS:
            items = _split_list(value)
            if key in LOWERCASE_FIELDS:
                items = [v.lower() for v in items]
            rec[key] = _dedupe(items)
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


def _is_directory_note(note):
    """True when the note is titled as a directory note, parseable or not."""
    title = note.get("title") or (note.get("content") or "").split("\n", 1)[0]
    title = re.sub(r"^\s*#{1,6}\s+", "", clean_line(title)).strip()
    return title.startswith(TITLE_PREFIX)


def load_notes(path, stats=None):
    """Load the orchestrator's saved `list_notes` response.

    Accepts a JSON object with a `notes` list, a bare list, or a missing/empty
    file (→ empty directory). Returns the parsed directory records. When
    `stats` is a dict, `stats["dropped"]` lists the titles of directory notes
    that could not be parsed — so a Mem format change is loud, not silent.
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
        elif stats is not None and _is_directory_note(n):
            stats.setdefault("dropped", []).append(
                (n.get("title") or (n.get("content") or "").split("\n", 1)[0]).strip())
    return records


def _warn_dropped(stats):
    dropped = stats.get("dropped") or []
    if dropped:
        print(f"WARNING: {len(dropped)} directory note(s) could not be read "
              f"(no valid Slack ID after parsing): {', '.join(dropped)}. "
              "Check the note format in Mem.", file=sys.stderr)
    return len(dropped)


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
            for login in (r.get("github_login", []) + r.get("gitlab_username", [])):
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
    stats = {}
    directory = Directory(load_notes(args.notes, stats))
    dropped = _warn_dropped(stats)
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
                 "notes_dropped": dropped,
                 "unmatched": unmatched, "error": None})
    return 0


def _merge_identity(rec, ident, via, today, source):
    """Merge an issue identity into a record; return True if anything changed."""
    changed = False
    if ident["email"] and ident["email"] not in rec["emails"]:
        rec["emails"].append(ident["email"])
        changed = True
    if ident["login"]:
        # A developer may commit under several logins: add each new one.
        key = "github_login" if ident["track"] == "oracle" else "gitlab_username"
        if not _has(rec.get(key), ident["login"]):
            rec[key] = (rec.get(key) or []) + [ident["login"]]
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
    stats = {}
    records = load_notes(args.notes, stats)
    dropped = _warn_dropped(stats)
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
                   "slack_id": uid, "emails": [], "github_login": [],
                   "gitlab_username": [], "optimus_assignee_id": "",
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
                 "notes_dropped": dropped,
                 "out": args.out or None, "error": None})
    return 0


def cache_path(home=None):
    h = kb_paths.folder(home)
    return kb_paths.config_path(h).parent / "cache" / CACHE_NAME


def _now():
    return _dt.datetime.now(_dt.timezone.utc)


def cache_read(home=None, max_age_hours=CACHE_MAX_AGE_HOURS, now=None):
    """(notes, age_hours) when the copy is fresh, else (None, age or None)."""
    p = cache_path(home)
    try:
        rec = json.loads(p.read_text())
        fetched = _dt.datetime.fromisoformat(rec["fetched_at"])
        notes = rec["notes"]
        if not isinstance(notes, list):
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError):
        return None, None
    age = ((now or _now()) - fetched).total_seconds() / 3600
    if age < 0 or age >= max_age_hours:
        return None, age
    return notes, age


def cache_write(notes, home=None, fetched_at=None):
    p = cache_path(home)
    kb_paths.self_ignoring(p.parent.parent)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"fetched_at": (fetched_at or _now()).isoformat(),
                             "notes": notes}, indent=1, ensure_ascii=False))
    return p


def cache_clear(home=None):
    p = cache_path(home)
    if p.exists():
        p.unlink()
        return True
    return False


def cache_apply(writes, results, home=None):
    """Fold accepted write-backs into the copy, keeping its fetched_at.
    Returns the number applied; drops the copy when it cannot be kept true."""
    p = cache_path(home)
    try:
        rec = json.loads(p.read_text())
        notes = rec["notes"]
        fetched = _dt.datetime.fromisoformat(rec["fetched_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0
    if not isinstance(results, list) or len(results) != len(writes):
        cache_clear(home)
        return 0
    applied = 0
    for w, r in zip(writes, results):
        if not r:
            continue                   # Mem did not take it: the copy is still right
        if not isinstance(r, dict) or not r.get("id"):
            cache_clear(home)
            return 0
        if w.get("action") == "update":
            hit = next((n for n in notes if n.get("id") == w.get("note_id")), None)
            if hit is None:
                cache_clear(home)
                return 0
            hit["content"] = w["content"]
            hit["version"] = r.get("version")
        else:
            notes.append({"id": r["id"], "version": r.get("version"),
                          "content": w["content"]})
        applied += 1
    cache_write(notes, home, fetched)
    return applied


def cmd_cache(args):
    if args.action == "get":
        if not args.out:
            raise ValueError("cache get needs --out")
        notes, age = cache_read(max_age_hours=args.max_age_hours)
        if notes is not None:
            Path(args.out).write_text(json.dumps({"notes": notes}, indent=1,
                                                 ensure_ascii=False))
            print(f"Developer directory: local copy, {age:.1f}h old — no Mem read.",
                  file=sys.stderr)
        emit_result({"ok": True, "action": "cache-get", "fresh": notes is not None,
                     "age_hours": None if age is None else round(age, 2),
                     "notes": len(notes or []), "error": None})
    elif args.action == "put":
        notes = load_notes_raw(args.notes)
        path = cache_write(notes)
        emit_result({"ok": True, "action": "cache-put", "notes": len(notes),
                     "path": str(path), "error": None})
    elif args.action == "apply":
        writes = (json.loads(Path(args.writes).read_text()).get("writes") or []) \
            if args.writes else []
        results = json.loads(Path(args.results).read_text()) if args.results else None
        applied = cache_apply(writes, results)
        emit_result({"ok": True, "action": "cache-apply", "applied": applied,
                     "kept": cache_path().exists(), "error": None})
    else:
        emit_result({"ok": True, "action": "cache-clear", "cleared": cache_clear(),
                     "error": None})
    return 0


def load_notes_raw(path):
    """The saved list_notes response as a list of note dicts ([] when empty)."""
    p = Path(path) if path else None
    if not p or not p.exists() or p.stat().st_size == 0:
        return []
    raw = json.loads(p.read_text())
    if isinstance(raw, dict):
        raw = raw.get("notes") or raw.get("items") or raw.get("results") or []
    return [n for n in raw if isinstance(n, dict)] if isinstance(raw, list) else []


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

    p3 = sub.add_parser("cache", help="Local 24h copy of the directory dump")
    p3.add_argument("action", choices=("get", "put", "apply", "clear"))
    p3.add_argument("--out", default="", help="get: write the copy here")
    p3.add_argument("--notes", default="", help="put: the fresh list_notes dump")
    p3.add_argument("--writes", default="", help="apply: the pending-writes JSON")
    p3.add_argument("--results", default="",
                    help="apply: JSON list, one {id, version} or null per write")
    p3.add_argument("--max-age-hours", type=float, default=CACHE_MAX_AGE_HOURS)

    args = ap.parse_args()
    try:
        if args.cmd == "prefill":
            return cmd_prefill(args)
        if args.cmd == "cache":
            return cmd_cache(args)
        return cmd_pending(args)
    except (ValueError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        emit_result({"ok": False, "action": args.cmd, "error": str(e)})
        return 1


if __name__ == "__main__":
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import utf8_mode
    utf8_mode.ensure()          # Windows: rerun in UTF-8 mode
    sys.exit(main())
