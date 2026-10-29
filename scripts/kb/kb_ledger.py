#!/usr/bin/env python3
"""
kb_ledger.py — deterministic half of the KB-article Mem ledger.

/kb-articles keeps its history in Mem: one ledger note per ticket (which
article was drafted for it, from which run, what screenshots it carries, which
earlier article it follows) plus one run note per run, a catalogue of the
articles already published to Intercom, and a queue of released features
still waiting for an article (enqueued by `/release-slack --kb`, drained once
a run drafts them; queue notes are never deleted). Mem is only reachable through the Mem
MCP (Claude-side), so this script never talks to Mem itself. Like
release_archive.py and dev_directory.py, it renders and parses the notes and
computes the writes; the orchestrator performs the Mem calls.

  resolve         Print the four collection names (process env > .env > default).
  index           Ledger dump (+ optional queue dump) -> {ticket: status}.
  since           Release-archive dump -> legacy ticket ids released on/after a date.
  pending         Release-archive dump (+ optional pending queue) minus the ledger
                  -> released or queued, not yet drafted.
  todo            A ticket list minus what the ledger already covers — run BEFORE
                  the gather so a done ticket never costs a GitLab call.
  match           Gathered data + catalogue/ledger dumps -> top prior articles per ticket.
  writes          A finished run (run.json from kb_package.py) -> the Mem writes.
  seed-legacy     The hand-run pipeline's tickets.json + article drafts -> ledger writes.
  seed-catalogue  A FAQ Content Planner export (kb2.csv) -> catalogue writes.
  enqueue         A posted release's data + summaries -> queue create-writes for
                  its features; prints the ticket ids to draft now.
  drain           Tickets a run drafted + queue dump -> queue update-writes
                  marking their pending notes drained.
  queue-summaries Queue dump -> a release-format summaries file of the pending
                  entries (client title + summary) for kb_inputs.py --summaries.

A "dump" is the orchestrator's saved `list_notes` response for a collection
(`{"notes": [{id, version, content}, ...]}`, a bare list, or a missing file
for an empty collection). A "writes" file is a JSON list of
`{"action": "create"|"update", "collection": "ledger"|"catalogue"|"queue",
"note_id", "version", "title", "content"}` for the orchestrator to perform.

Every subcommand prints a machine-readable block last:

    ---KB_LEDGER_RESULT---
    {"ok", "action", ...counts..., "error"}

Exit codes: 0 success, 1 configuration error (unreadable input, bad date).

Ledger note schema (first line = Mem note title):

    KB: M1-1520 · How to Land Migrated Session Schedules as Unpublished

    **Ticket:** M1-1520
    **Optimus type:** feature
    **Status:** drafted
    **Article title:** How to Land Migrated Session Schedules as Unpublished
    **Generated:** 2026-10-27
    **Run:** kb-20261027-0930
    **Render:** draft
    **Persona:** Admin
    **Surface:** Curriculum > Setup > Migrate
    **Screenshots:** 2 of 3 (missing: 1 — no demo data)
    **Tenant:** CLEAR 2 · REVIEW 0 · BLOCK 0
    **Prior article:** CX-1782 How to View Option Values … (catalogue, 0.82)
    **Files:** How to ….docx, .pdf, .md
    **Tokens:** ~6,900 (writer, apportioned) + 0 (retries)
    **Est. cost:** $0.04 (estimate; main-chat tokens not included)
    **Published as:** —

Queue note schema (one per enqueue; Status flips pending -> drained in place):

    KB queue: M1-1520 · Land migrated session schedules as unpublished

    **Ticket:** M1-1520
    **Status:** pending
    **Source:** release-slack
    **Released:** 2026-10-27
    **Root ts:** 1761571234.000100
    **Title:** Migrate session schedules as unpublished
    **Client title:** Land migrated session schedules as unpublished
    **Client summary:** Administrators can now …
    **Queued:** 2026-10-27
    **Drained by:** —
    **Drained:** —
"""

import argparse
import csv
import datetime as _dt
import json
import math
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))           # scripts/ — reuse the release archive parser
import release_archive as RA                    # noqa: E402
sys.path.insert(0, str(HERE))
import kb_usage as U                            # noqa: E402

COLLECTIONS = {
    "ledger": ("MEM_KB_LEDGER_COLLECTION", "KB Article Ledger",
               "medtrics-release-notes /kb-articles: one note per legacy feature ticket "
               "recording the KB article drafted for it, plus one note per run. Read "
               "before every run so no ticket is drafted twice. Fill 'Published as' by "
               "hand once an article is live in Intercom."),
    "queue": ("MEM_KB_QUEUE_COLLECTION", "KB Queue",
              "medtrics-release-notes: features waiting for a KB article, enqueued by "
              "--kb on the release commands and drained by /kb-articles pending."),
    "catalogue": ("MEM_KB_CATALOGUE_COLLECTION", "KB Catalogue",
                  "medtrics-release-notes: one note per published Intercom KB article, "
                  "seeded from the FAQ Content Planner export. /kb-articles reads it to "
                  "list the earlier article a new feature follows."),
    "demo": ("MEM_KB_DEMO_COLLECTION", "KB Demo Data",
             "medtrics-release-notes /kb-articles: the form flows learned in dry runs and "
             "every demo record a run saved on the demo site, with its link and a "
             "delete-before date. The owner deletes the records; /kb-articles demo-data "
             "lists the live ones."),
}

LEDGER_PREFIX = "KB: "
RUN_PREFIX = "KB run: "
CATALOGUE_PREFIX = "KB article: "
QUEUE_PREFIX = "KB queue: "
SEP = " · "
NONE = "—"

FEATURE_TYPES = ("feature", "critical_feature")
STATUSES = ("drafted", "partial", "failed", "published", "legacy-draft", "matched")
# Statuses a run skips by default. partial (a draft with missing screenshots)
# is skipped too: re-drafting it weekly re-spends tokens for shots the demo
# still cannot show. --retry-partial brings it back once the demo can.
DONE = ("drafted", "partial", "published", "legacy-draft", "matched")
QUEUE_STATUSES = ("pending", "drained")

FIELD_RE = re.compile(r"^\*\*(?P<key>[^*]+?):\*\*\s*(?P<value>.*)$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LEGACY_ID_RE = re.compile(r"^(?:M1|M2|GEN)-\d+$|^[0-9a-f]{8}$", re.I)

LEDGER_FIELDS = [
    ("Ticket", "ticket"), ("Optimus type", "type"), ("Status", "status"),
    ("Article title", "title"), ("Generated", "generated"), ("Run", "run"),
    ("Render", "render"), ("Persona", "persona"), ("Surface", "surface"),
    ("Screenshots", "screenshots"), ("Tenant", "tenant"),
    ("Prior article", "prior"), ("Files", "files"), ("Tokens", "tokens"),
    ("Est. cost", "cost"),
    ("Published as", "published_as"),
]
QUEUE_FIELDS = [
    ("Ticket", "ticket"), ("Status", "status"), ("Source", "source"),
    ("Released", "released"), ("Root ts", "root_ts"), ("Title", "title"),
    ("Client title", "client_title"), ("Client summary", "client_summary"),
    ("Queued", "queued"), ("Drained by", "drained_by"), ("Drained", "drained"),
]
CATALOGUE_FIELDS = [
    ("Article id", "id"), ("Title", "title"), ("Module", "module"),
    ("URL", "url"), ("Tickets", "tickets"),
]


# ── collections ──────────────────────────────────────────────────────────────
def resolve_names(env_path=".env", environ=None):
    environ = os.environ if environ is None else environ
    out = {}
    for which, (key, default, desc) in COLLECTIONS.items():
        name = (environ.get(key) or "").strip() or \
            RA._read_env_file_key(env_path, key).strip() or default
        out[which] = {"name": name, "description": desc, "env_key": key}
    return out


# ── notes ────────────────────────────────────────────────────────────────────
def load_dump(path):
    """The saved list_notes response -> list of note dicts. Missing/empty -> []."""
    p = Path(path) if path else None
    if not p or not p.exists() or p.stat().st_size == 0:
        return []
    raw = json.loads(p.read_text())
    if isinstance(raw, dict):
        raw = raw.get("notes") or raw.get("items") or raw.get("results") or []
    return [n for n in raw if isinstance(n, dict)] if isinstance(raw, list) else []


def _fields(lines, schema):
    rec = {key: "" for _, key in schema}
    for line in lines:
        m = FIELD_RE.match(line.strip())
        if not m:
            continue
        label = m.group("key").strip().lower()
        for flabel, key in schema:
            if flabel.lower() == label:
                # Mem returns a hard line break as a trailing backslash
                v = re.sub(r"[\\\s]+$", "", m.group("value")).strip()
                rec[key] = "" if v == NONE else v
    return rec


def _render(title, schema, rec):
    out = [title, ""]
    for label, key in schema:
        out.append(f"**{label}:** {rec.get(key) or NONE}")
    return "\n".join(out) + "\n"


def ledger_title(rec):
    t = rec.get("title") or ""
    return f"{LEDGER_PREFIX}{rec['ticket']}" + (f"{SEP}{t}" if t else "")


def render_ledger_note(rec):
    return _render(ledger_title(rec), LEDGER_FIELDS, rec)


def parse_ledger_note(note):
    content = note if isinstance(note, str) else (note.get("content") or "")
    lines = content.splitlines()
    if not lines or not lines[0].strip().startswith(LEDGER_PREFIX):
        return None
    rec = _fields(lines[1:], LEDGER_FIELDS)
    if not rec["ticket"]:
        return None
    if isinstance(note, dict):
        rec["note_id"] = note.get("id") or note.get("note_id") or ""
        rec["version"] = note.get("version")
    return rec


def render_catalogue_note(rec):
    title = f"{CATALOGUE_PREFIX}{rec['id']}{SEP}{rec.get('title', '')}".rstrip(SEP)
    return _render(title, CATALOGUE_FIELDS, rec)


def parse_catalogue_note(note):
    content = note if isinstance(note, str) else (note.get("content") or "")
    lines = content.splitlines()
    if not lines or not lines[0].strip().startswith(CATALOGUE_PREFIX):
        return None
    rec = _fields(lines[1:], CATALOGUE_FIELDS)
    return rec if rec["id"] and rec["title"] else None


def _oneline(s):
    """A note field is one line: collapse wrapped summaries."""
    return re.sub(r"\s+", " ", str(s or "")).strip()


def queue_title(rec):
    t = _oneline(rec.get("client_title") or rec.get("title"))
    return f"{QUEUE_PREFIX}{rec['ticket']}" + (f"{SEP}{t}" if t else "")


def render_queue_note(rec):
    return _render(queue_title(rec), QUEUE_FIELDS,
                   {k: _oneline(rec.get(k)) for _, k in QUEUE_FIELDS})


def parse_queue_note(note):
    content = note if isinstance(note, str) else (note.get("content") or "")
    lines = content.splitlines()
    if not lines or not lines[0].strip().startswith(QUEUE_PREFIX):
        return None
    rec = _fields(lines[1:], QUEUE_FIELDS)
    if not rec["ticket"]:
        return None
    if isinstance(note, dict):
        rec["note_id"] = note.get("id") or note.get("note_id") or ""
        rec["version"] = note.get("version")
    return rec


def queue_index(notes):
    """{TICKET: record}; with several notes for a ticket a pending one wins,
    then the newest `Queued`."""
    idx = {}
    for n in notes:
        rec = parse_queue_note(n)
        if not rec:
            continue
        key = rec["ticket"].upper()
        have = idx.get(key)
        rank = (rec.get("status") == "pending", rec.get("queued", ""))
        if have is None or rank >= (have.get("status") == "pending", have.get("queued", "")):
            idx[key] = rec
    return idx


def _cost_text(usd):
    """'$0.31 (estimate; main-chat tokens not included)', or 'n/a (...)' when a
    model had no price."""
    return f"{'n/a' if usd is None else f'${usd:,.2f}'} ({U.COST_NOTE})"


def render_run_note(run):
    t = run.get("totals") or {}
    out = [f"{RUN_PREFIX}{run['date']}{SEP}{run['run_id']}", "",
           f"**Run:** {run['run_id']}", f"**Date:** {run['date']}",
           f"**Mode:** {run.get('mode') or NONE}",
           f"**Drafted:** {t.get('drafted', 0)}", f"**Partial:** {t.get('partial', 0)}",
           f"**Failed:** {t.get('failed', 0)}", f"**Skipped:** {t.get('skipped', 0)}",
           f"**Left by session expiry:** {', '.join(run.get('left_by_expiry') or []) or NONE}",
           f"**Output:** {run.get('output') or NONE}",
           f"**Tokens:** {t.get('tokens', 0):,} (estimate)",
           f"**Est. cost:** {_cost_text(t.get('est_cost_usd'))}", ""]
    for u in run.get("usage") or []:
        out.append(f"- {u.get('agent')}{SEP}{u.get('scope')}{SEP}{u.get('model')}"
                   f"{SEP}{u.get('total_tokens', 0):,} tokens"
                   + (f"{SEP}${u['est_cost_usd']:,.2f}"
                      if u.get("est_cost_usd") is not None else ""))
    return "\n".join(out) + "\n"


def ledger_index(notes):
    """{ticket: record}; when a ticket has several notes the newest
    `Generated` wins (a re-draft under --force updates in place, but a hand
    edit could duplicate)."""
    idx = {}
    for n in notes:
        rec = parse_ledger_note(n)
        if not rec:
            continue
        key = rec["ticket"].upper()
        if key not in idx or rec.get("generated", "") >= idx[key].get("generated", ""):
            idx[key] = rec
    return idx


# ── release archive -> released legacy tickets ───────────────────────────────
def released_tickets(archive_notes, since=None):
    """Legacy ticket ids from `Product: medtrics` archive notes, oldest first,
    each with the date it shipped. MR-NNNN (single-MR mode) is not a ticket."""
    seen = {}
    for n in archive_notes:
        rec = RA.parse_note(n)
        if not rec or rec.get("product") != "medtrics":
            continue
        date = rec.get("date") or ""
        if since and date < since:
            continue
        for t in rec.get("tickets") or []:
            if t.upper().startswith("MR-"):
                continue
            if t not in seen or date < seen[t]:
                seen[t] = date
    return sorted(seen.items(), key=lambda kv: (kv[1], kv[0]))


# ── prior-article matching ───────────────────────────────────────────────────
STOP = set("""a an and the of to for in on at by with from as is are be can now your you
how what where using understanding new option options when it its this that into via
add adding allow allows show shows view page field fields setting settings medtrics""".split())


def _tokens(text):
    return {w for w in re.findall(r"[a-z][a-z0-9]+", (text or "").lower())
            if w not in STOP and len(w) > 2}


def score(ticket_text, article_text):
    """Set cosine over content words — the same idea as match_candidates.json."""
    a, b = _tokens(ticket_text), _tokens(article_text)
    if not a or not b:
        return 0.0, []
    common = sorted(a & b)
    return round(len(common) / math.sqrt(len(a) * len(b)), 3), common


def _nid(x):
    """A ticket id compared loosely: upper-case, no backslashes or spaces."""
    return re.sub(r"[\\\s]", "", str(x or "")).upper()


def match(issues, catalogue, ledger, top=3, floor=0.3):
    out = {}
    cands = [{"id": c["id"], "title": c["title"], "module": c.get("module", ""),
              "source": "catalogue"} for c in catalogue]
    cands += [{"id": r["ticket"], "title": r["title"], "module": "", "source": "ledger"}
              for r in ledger.values() if r.get("title")]
    for issue in issues:
        tid = issue["identifier"]
        text = f"{issue.get('title', '')} {issue.get('module', '')}"
        ranked = []
        for c in cands:
            if _nid(c["id"]) == _nid(tid):
                continue
            s, common = score(text, f"{c['title']} {c['module']}")
            if s >= floor:
                ranked.append({**c, "score": s, "common": common})
        ranked.sort(key=lambda r: (-r["score"], r["id"]))
        out[tid] = ranked[:top]
    return out


def prior_line(priors):
    if not priors:
        return NONE
    p = priors[0]
    more = f" (+{len(priors) - 1} more)" if len(priors) > 1 else ""
    return f"{p['id']} {p['title']} ({p['source']}, {p['score']:.2f}){more}"


# ── writes ───────────────────────────────────────────────────────────────────
def ledger_writes(records, ledger, force=False):
    """create for a ticket with no note; update (with its version) when --force
    re-drafted a ticket that already has one."""
    writes = []
    for rec in records:
        content = render_ledger_note(rec)
        have = ledger.get(rec["ticket"].upper())
        if have and have.get("note_id"):
            if not force and have.get("status") in DONE:
                continue
            if have.get("published_as") and not rec.get("published_as"):
                rec = {**rec, "published_as": have["published_as"]}
                content = render_ledger_note(rec)
            writes.append({"action": "update", "collection": "ledger",
                           "note_id": have["note_id"], "version": have.get("version"),
                           "title": ledger_title(rec), "content": content})
        else:
            writes.append({"action": "create", "collection": "ledger",
                           "title": ledger_title(rec), "content": content})
    return writes


def _summary_map(summaries):
    """{IDENTIFIER (upper): block} from a dict or a list of parsed blocks."""
    blocks = summaries.values() if isinstance(summaries, dict) else (summaries or [])
    return {b["IDENTIFIER"].strip().upper(): b for b in blocks
            if isinstance(b, dict) and (b.get("IDENTIFIER") or "").strip()}


def enqueue_records(issues, summaries, ledger_index, queue_index, source, date, ts, today):
    """(records to create, ticket ids to draft now) for a posted release.
    Features only, no MR-NNNN; a ticket the ledger has done is dropped; one
    already pending is drafted but not queued twice; one drained earlier but
    not done (e.g. failed) is queued again."""
    sums = _summary_map(summaries)
    recs, ids, seen = [], [], set()
    for issue in issues or []:
        tid = (issue.get("identifier") or "").strip()
        key = tid.upper()
        if not tid or key in seen or key.startswith("MR-") \
                or issue.get("task_type") not in FEATURE_TYPES:
            continue
        seen.add(key)
        if (ledger_index.get(key) or {}).get("status") in DONE:
            continue
        ids.append(tid)
        if (queue_index.get(key) or {}).get("status") == "pending":
            continue
        block = sums.get(key) or {}
        title = block.get("TITLE") or issue.get("title") or ""
        recs.append({"ticket": tid, "status": "pending", "source": source,
                     "released": date, "root_ts": ts or "", "title": title,
                     "client_title": block.get("CLIENT_TITLE") or title,
                     "client_summary": block.get("EXEC_SUMMARY") or "",
                     "queued": today, "drained_by": "", "drained": ""})
    return recs, ids


def enqueue_report(requested, data, ledger, queue, recs):
    """One row per requested id for `/kb-articles --kb-queue`: queued, already
    queued, or why not."""
    issues = {i["identifier"].upper(): i for i in data.get("issues") or []}
    dropped = {d["identifier"].upper(): d.get("task_type") for d in data.get("dropped") or []}
    new = {r["ticket"].upper() for r in recs}
    rows = []
    for t in requested:
        k = t.upper()
        if k in new:
            res, why = "queued", "added to the KB Queue"
        elif k in dropped or (k in issues and issues[k].get("task_type") not in FEATURE_TYPES):
            res, why = "not queued", f"not a feature (type: {dropped.get(k) or issues[k].get('task_type') or 'unknown'})"
        elif k not in issues:
            res, why = "not queued", "not found in Optimus"
        elif (ledger.get(k) or {}).get("status") in DONE:
            res, why = "not queued", f"already has an article (ledger: {ledger[k]['status']})"
        elif (queue.get(k) or {}).get("status") == "pending":
            res, why = "already queued", "pending on the KB Queue"
        else:
            res, why = "not queued", "unknown"
        rows.append({"ticket": t, "result": res, "detail": why})
    return rows


def drain_writes(tickets, queue_notes, run_id, date):
    """update-writes flipping every pending queue note of `tickets` to drained."""
    want = {t.strip().upper() for t in tickets if t.strip()}
    writes = []
    for n in queue_notes:
        rec = parse_queue_note(n)
        if not rec or rec["ticket"].upper() not in want or rec.get("status") != "pending" \
                or not rec.get("note_id"):
            continue
        rec = {**rec, "status": "drained", "drained_by": run_id, "drained": date}
        writes.append({"action": "update", "collection": "queue",
                       "note_id": rec["note_id"], "version": rec.get("version"),
                       "title": queue_title(rec), "content": render_queue_note(rec)})
    return writes


def queue_summaries_text(queue, tickets=None):
    """Pending queue entries as release summary blocks (kb_inputs --summaries)."""
    want = {t.strip().upper() for t in tickets if t.strip()} if tickets else None
    blocks = []
    for key, rec in queue.items():
        if rec.get("status") != "pending" or (want is not None and key not in want):
            continue
        blocks.append("\n".join([f"IDENTIFIER: {rec['ticket']}",
                                 f"TITLE: {_oneline(rec.get('title'))}",
                                 f"CLIENT_TITLE: {_oneline(rec.get('client_title'))}",
                                 f"EXEC_SUMMARY: {_oneline(rec.get('client_summary'))}"]))
    return ("\n---\n".join(blocks) + "\n") if blocks else "", len(blocks)


def _ids_in(article):
    return set(re.findall(r"\b(?:M1|M2|GEN)-\d+\b|\b[0-9a-f]{8}\b", json.dumps(article)))


def seed_legacy_records(tickets, articles, today):
    """The hand-run pipeline's state as ledger records: tickets an article
    draft covers -> legacy-draft; tickets matched to a published article ->
    matched. Everything else is left for /kb-articles."""
    covered = {}
    for a in articles:
        for tid in _ids_in(a):
            covered.setdefault(tid.upper(), a.get("title", ""))
    recs = []
    for t in tickets:
        tid = t["id"]
        if tid.upper() in covered:
            status, title = "legacy-draft", covered[tid.upper()]
        elif t.get("article"):
            status, title = "matched", t["article"]
        else:
            continue
        recs.append({"ticket": tid, "type": t.get("type", ""), "status": status,
                     "title": title, "generated": today, "run": "hand-run 2026-10-19",
                     "render": "draft", "persona": t.get("persona", ""),
                     "surface": t.get("surface", "")})
    return recs


def catalogue_from_csv(path):
    """kb2.csv -> catalogue records. The export's column names are not pinned
    yet, so the common spellings are accepted and rows without an id and a
    title are dropped."""
    def pick(row, *names):
        for n in names:
            for k, v in row.items():
                if k and k.strip().lower() == n and (v or "").strip():
                    return v.strip()
        return ""
    out = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            rec = {"id": pick(row, "id", "article id", "kb id", "intercom id", "key"),
                   "title": pick(row, "title", "article title", "name", "question"),
                   "module": pick(row, "module", "category", "collection", "section"),
                   "url": pick(row, "url", "link", "article url"),
                   "tickets": pick(row, "tickets", "ticket", "source ticket")}
            if rec["id"] and rec["title"]:
                out.append(rec)
    return out


# ── CLI ──────────────────────────────────────────────────────────────────────
def emit(payload):
    print("---KB_LEDGER_RESULT---")
    print(json.dumps(payload))


def _write_json(path, obj):
    Path(path).write_text(json.dumps(obj, indent=1))


def _valid_date(s):
    if not DATE_RE.match(s or ""):
        raise ValueError(f"bad date {s!r}, want YYYY-MM-DD")
    _dt.date.fromisoformat(s)
    return s


def cmd_resolve(a):
    names = resolve_names(a.env_file)
    print(json.dumps(names, indent=1))
    emit({"ok": True, "action": "resolve", **{k: v["name"] for k, v in names.items()}})
    return 0


def cmd_index(a):
    idx = ledger_index(load_dump(a.ledger_notes))
    _write_json(a.out, {k: {"status": v["status"], "title": v["title"],
                            "note_id": v.get("note_id"), "version": v.get("version"),
                            "published_as": v.get("published_as")}
                        for k, v in idx.items()})
    counts = {s: sum(1 for v in idx.values() if v["status"] == s) for s in STATUSES}
    print(f"ledger: {len(idx)} tickets · " + " · ".join(f"{k} {v}" for k, v in counts.items() if v))
    emit({"ok": True, "action": "index", "tickets": len(idx), **counts})
    return 0


def cmd_since(a):
    try:
        since = _valid_date(a.date)
    except ValueError as e:
        emit({"ok": False, "action": "since", "error": str(e)})
        return 1
    rel = released_tickets(load_dump(a.archive_notes), since)
    print(",".join(t for t, _ in rel))
    emit({"ok": True, "action": "since", "tickets": len(rel)})
    return 0


def cmd_pending(a):
    ledger = ledger_index(load_dump(a.ledger_notes))
    skip = DONE if not a.retry_partial else tuple(s for s in DONE if s != "partial")
    rel = [t for t, _ in released_tickets(load_dump(a.archive_notes))
           if ledger.get(t.upper(), {}).get("status") not in skip]
    seen = {t.upper() for t in rel}
    queued = []
    for key, rec in queue_index(load_dump(a.queue_notes)).items():
        if rec.get("status") == "pending" and key not in seen \
                and ledger.get(key, {}).get("status") not in skip:
            seen.add(key)
            queued.append(rec["ticket"])
    print(",".join(rel + queued))
    emit({"ok": True, "action": "pending", "tickets": len(rel) + len(queued),
          "released": len(rel), "queued": len(queued)})
    return 0


def cmd_todo(a):
    ledger = ledger_index(load_dump(a.ledger_notes))
    skip = () if a.force else (DONE if not a.retry_partial
                                else tuple(s for s in DONE if s != "partial"))
    ids = [t.strip() for t in a.ids.split(",") if t.strip()]
    keep = [t for t in ids if ledger.get(t.upper(), {}).get("status") not in skip]
    print(",".join(keep))
    emit({"ok": True, "action": "todo", "tickets": len(keep), "skipped": len(ids) - len(keep)})
    return 0


def cmd_match(a):
    data = json.loads(Path(a.data).read_text())
    catalogue = [r for r in (parse_catalogue_note(n) for n in load_dump(a.catalogue_notes)) if r]
    ledger = ledger_index(load_dump(a.ledger_notes))
    res = match(data.get("issues") or [], catalogue, ledger)
    _write_json(a.out, res)
    hits = sum(1 for v in res.values() if v)
    print(f"prior articles: {hits} of {len(res)} tickets matched "
          f"({len(catalogue)} catalogue, {len(ledger)} ledger candidates)")
    emit({"ok": True, "action": "match", "tickets": len(res), "matched": hits,
          "catalogue": len(catalogue)})
    return 0


def cmd_writes(a):
    run = json.loads(Path(a.run).read_text())
    ledger = ledger_index(load_dump(a.ledger_notes))
    writes = ledger_writes(run.get("records") or [], ledger, force=a.force)
    writes.append({"action": "create", "collection": "ledger",
                   "title": f"{RUN_PREFIX}{run['date']}{SEP}{run['run_id']}",
                   "content": render_run_note(run)})
    _write_json(a.out, writes)
    n_c = sum(1 for w in writes if w["action"] == "create")
    print(f"ledger writes: {n_c} create · {len(writes) - n_c} update")
    emit({"ok": True, "action": "writes", "create": n_c, "update": len(writes) - n_c})
    return 0


def cmd_seed_legacy(a):
    tickets = json.loads(Path(a.tickets).read_text())
    articles = []
    for p in a.articles:
        articles += json.loads(Path(p).read_text())
    recs = seed_legacy_records(tickets, articles, a.date or _dt.date.today().isoformat())
    ledger = ledger_index(load_dump(a.ledger_notes))
    writes = ledger_writes(recs, ledger)
    _write_json(a.out, writes)
    remaining = len([t for t in tickets if t.get("type") in FEATURE_TYPES]) - len(recs)
    print(f"seed-legacy: {len(writes)} ledger notes to create · {remaining} tickets left for /kb-articles")
    emit({"ok": True, "action": "seed-legacy", "writes": len(writes), "remaining": remaining})
    return 0


def cmd_seed_catalogue(a):
    try:
        rows = catalogue_from_csv(a.csv)
    except OSError as e:
        emit({"ok": False, "action": "seed-catalogue", "error": str(e)})
        return 1
    have = {r["id"].upper() for r in (parse_catalogue_note(n) for n in load_dump(a.catalogue_notes)) if r}
    writes = [{"action": "create", "collection": "catalogue",
               "title": render_catalogue_note(r).splitlines()[0],
               "content": render_catalogue_note(r)}
              for r in rows if r["id"].upper() not in have]
    _write_json(a.out, writes)
    print(f"seed-catalogue: {len(rows)} rows · {len(writes)} new notes")
    emit({"ok": True, "action": "seed-catalogue", "rows": len(rows), "writes": len(writes)})
    return 0


def cmd_enqueue(a):
    try:
        date = _valid_date(a.date)
    except ValueError as e:
        emit({"ok": False, "action": "enqueue", "error": str(e)})
        return 1
    data = json.loads(Path(a.data).read_text())
    issues = data.get("issues") or []
    sums = RA.parse_summaries(Path(a.summaries).read_text()) \
        if a.summaries and Path(a.summaries).exists() else []
    queue = queue_index(load_dump(a.queue_notes))
    ledger = ledger_index(load_dump(a.ledger_notes))
    recs, ids = enqueue_records(issues, sums, ledger,
                                queue, a.source, date, a.ts, _dt.date.today().isoformat())
    _write_json(a.out, [{"action": "create", "collection": "queue",
                         "title": queue_title(r), "content": render_queue_note(r)} for r in recs])
    print(",".join(ids))
    if a.ids:
        rows = enqueue_report([t.strip() for t in a.ids.split(",") if t.strip()],
                              data, ledger, queue, recs)
        print("| Ticket | Result | Details |")
        print("|---|---|---|")
        for r in rows:
            print(f"| {r['ticket']} | {r['result']} | {r['detail']} |")
    emit({"ok": True, "action": "enqueue", "queued": len(recs),
          "already_queued": len(ids) - len(recs), "tickets": ids,
          "skipped": len(issues) - len(ids)})
    return 0


def cmd_drain(a):
    try:
        date = _valid_date(a.date)
    except ValueError as e:
        emit({"ok": False, "action": "drain", "error": str(e)})
        return 1
    ids = [t.strip() for t in a.tickets.split(",") if t.strip()]
    writes = drain_writes(ids, load_dump(a.queue_notes), a.run_id, date)
    _write_json(a.out, writes)
    print(f"drain: {len(writes)} queue notes drained · {len(ids)} tickets asked")
    emit({"ok": True, "action": "drain", "drained": len(writes), "tickets": len(ids)})
    return 0


def cmd_queue_summaries(a):
    ids = [t for t in (a.tickets or "").split(",") if t.strip()] or None
    text, n = queue_summaries_text(queue_index(load_dump(a.queue_notes)), ids)
    Path(a.out).write_text(text)
    print(f"queue-summaries: {n} pending tickets with a client summary block")
    emit({"ok": True, "action": "queue-summaries", "tickets": n})
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("resolve"); s.add_argument("--env-file", default=".env")
    s = sub.add_parser("index"); s.add_argument("--ledger-notes", required=True)
    s.add_argument("--out", required=True)
    s = sub.add_parser("since"); s.add_argument("--archive-notes", required=True)
    s.add_argument("--date", required=True)
    s = sub.add_parser("pending"); s.add_argument("--archive-notes", required=True)
    s.add_argument("--ledger-notes", required=True)
    s.add_argument("--queue-notes")
    s.add_argument("--retry-partial", action="store_true")
    s = sub.add_parser("todo"); s.add_argument("--ids", required=True)
    s.add_argument("--ledger-notes", required=True)
    s.add_argument("--force", action="store_true"); s.add_argument("--retry-partial", action="store_true")
    s = sub.add_parser("match"); s.add_argument("--data", required=True)
    s.add_argument("--catalogue-notes"); s.add_argument("--ledger-notes")
    s.add_argument("--out", required=True)
    s = sub.add_parser("writes"); s.add_argument("--run", required=True)
    s.add_argument("--ledger-notes"); s.add_argument("--force", action="store_true")
    s.add_argument("--out", required=True)
    s = sub.add_parser("seed-legacy"); s.add_argument("--tickets", required=True)
    s.add_argument("--articles", nargs="+", required=True)
    s.add_argument("--ledger-notes"); s.add_argument("--date")
    s.add_argument("--out", required=True)
    s = sub.add_parser("seed-catalogue"); s.add_argument("--csv", required=True)
    s.add_argument("--catalogue-notes"); s.add_argument("--out", required=True)
    s = sub.add_parser("enqueue"); s.add_argument("--data", required=True)
    s.add_argument("--summaries"); s.add_argument("--source", required=True)
    s.add_argument("--date", required=True); s.add_argument("--ts")
    s.add_argument("--ledger-notes"); s.add_argument("--queue-notes")
    s.add_argument("--ids", help="comma-separated ids asked for: print a per-ticket table")
    s.add_argument("--out", required=True)
    s = sub.add_parser("drain"); s.add_argument("--tickets", required=True)
    s.add_argument("--queue-notes", required=True); s.add_argument("--run-id", required=True)
    s.add_argument("--date", required=True); s.add_argument("--out", required=True)
    s = sub.add_parser("queue-summaries"); s.add_argument("--queue-notes", required=True)
    s.add_argument("--tickets"); s.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    try:
        return {"resolve": cmd_resolve, "index": cmd_index, "since": cmd_since,
                "pending": cmd_pending, "todo": cmd_todo, "match": cmd_match, "writes": cmd_writes,
                "seed-legacy": cmd_seed_legacy,
                "seed-catalogue": cmd_seed_catalogue, "enqueue": cmd_enqueue,
                "drain": cmd_drain, "queue-summaries": cmd_queue_summaries}[a.cmd](a)
    except (OSError, json.JSONDecodeError) as e:
        emit({"ok": False, "action": a.cmd, "error": str(e)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
