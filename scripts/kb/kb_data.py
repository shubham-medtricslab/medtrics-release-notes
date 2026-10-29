#!/usr/bin/env python3
"""
kb_data.py — demo form fills a KB screenshot needs: planned by rule from the form
the capture really saw, learned in a blocked dry run, approved once per run, then
driven by the kb-data-maker agent under guardrails. No model plans anything.

Some screens only exist after a form is filled and Save is pressed (a schedule,
an assignment): pressing Save opens a prompt ("Notify Participants?") and the
request goes out on the prompt's choice, not on Save. Saving is two-step:

  DRY RUN (first time for a form): every write is blocked by the capture page. The
          maker fills the form, presses Save, reads the prompt (title, buttons), clicks
          the prompt's safe answer (a negated "No, don't send ..." or Cancel; the page
          script picks it), and reads the blocked request ("POST /path"). `learn` turns
          that into a Mem "form flow" note. Nothing is stored.
  SAVE    (the next run, once a usable flow note exists): the owner approves a plan that
          says "WILL BE SAVED" with the exact values and the exact request; the page
          script arms a ONE-SHOT allowance for exactly that request (see kb_capture.py,
          the write guard), and the record is logged in Mem ("demo record"). Claude never
          deletes demo data: `demo-data` lists it with links, the owner deletes it, and
          `demo-data-done` marks it deleted.

  python3 kb_data.py plan    --groups groups.json --calls $RUN/shots/calls.json
          --shots $RUN/shots/shots.json (or the raw/ folder) --data filtered.json
          --out $RUN/data_plan.json [--flows flows.json] [--records records.json]
          [--today YYYY-MM-DD] [--period-end YYYY-MM-DD]
          Per ticket whose capture entry is write-gated, builds the fill from the
          probe's `fields` of the form screen: date -> a far-future weekday of the period;
          time -> 09:00 (start) / 10:00 (end); a required select -> its first option
          starting "ZZ DEMO"; a required text / name / title -> "ZZ KB DEMO – <ticket>";
          optional fields stay empty; a required multiselect / dual list / checkbox is a
          stop (unsupported_required_picker / unsupported_required_field); no ZZ DEMO
          option is a stop (needs_owner). Mode `save` when --flows holds a usable form
          flow for the ticket's (start, path, save), else `dry_run`. A live demo record
          with the same fingerprint (--records) means the record is reused, not saved
          twice. Prints one preview line per record and writes {"approved": false, ...}.
  python3 kb_data.py approve --plan $RUN/data_plan.json
          Sets approved true and stores the sha256 of the canonical plan. Run only after
          the owner says yes (asked EVERY run). Refused (exit 2) when the plan has no
          checked_sha256 (`plan` writes it) or it no longer matches the plan.
          kb_capture.py plan-calls --data-plan also needs approved true and a matching
          approved_sha256.
  python3 kb_data.py brief   --plan $RUN/data_plan.json --calls $RUN/shots/calls.json --out $RUN/shots/data.md
          The kb-data-maker's instructions (approved, unchanged plan only): per record the
          ready-to-send kbclick / probe calls, field fill scripts, the write visit id and GIF.
          The save-mode safety checks (start, click path, choose rule, request method / path /
          query rules, flow usable) run again on the approved plan; a plan that fails them (a
          hand edit with a recomputed hash) is refused, exit 2, error unsafe_plan, nothing written.
  python3 kb_data.py learn   --raw $RUN/shots/raw --plan $RUN/data_plan.json
          --out $RUN/flow_notes.json [--flows flows.json] [--run-id ID] [--today D]
          The dry runs' raw results -> Mem writes for the "KB Demo Data" collection
          (kind "form flow": dialog title, buttons, choose text, request, request query). A
          dry run is learned only when exactly ONE non-GET was blocked after the answer click
          (compared with the probe before it); more than one is `ambiguous_request` (unusable).
  python3 kb_data.py records --raw $RUN/shots/raw --plan $RUN/data_plan.json
          --out $RUN/record_notes.json [--records records.json] [--calls calls.json]
          [--run-id ID] [--base-url URL] [--today D]
          The save runs' raw results -> Mem writes (kind "demo record": ticket, what, link,
          fingerprint, server id, run, status, delete-before). No local record file: if the
          Mem write fails, print the record lines in chat and RUN-SUMMARY. Status: created (a 2xx
          came back), unverified (an allowed request was logged but no status came back),
          not_saved (a non-2xx, or no allowed request logged and no response: not live, never
          reused, not listed). The fingerprint (reuse key) is ticket + start + path + save text +
          the form's shape, never a date or value.
  python3 kb_data.py demo-data --records records.json
          The live demo records (created / unverified) with links.
  python3 kb_data.py demo-data-done --records records.json --ids <note id|server id|fingerprint> ...
          --out updates.json [--today D]
          Mem updates marking those records deleted (the OWNER deleted them).

`flows.json` / `records.json` are the orchestrator's saved list_notes dump of the
"KB Demo Data" collection ({"notes": [{id, version, content}]}, a bare list, or a missing
file). A writes file is a JSON list of {"action": "create"|"update", "collection":
"demo", "note_id", "version", "title", "content"} for the orchestrator to perform.

Prints one human line (or a table), then:

    ---KB_DATA_RESULT---
    {"ok", "action", ...}
"""

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import kb_capture as C                  # noqa: E402  (stub / install format, EXPORT_OPTS, regexes)

PREFIX = "ZZ KB DEMO"
DASH = "–"
PERSONAS = ("admin", "faculty")                 # one shared sign-in; the learner never fills forms
SAVE_TEXTS = ("save", "create", "add", "submit")
MAX_RECORDS = 3                                 # per ticket
FORBIDDEN_RE = re.compile(r"\b(?:invite|publish|approve|email|delete|remove|archive)", re.I)
SEND_RE = re.compile(r"\b(?:send|notify)\b", re.I)
NEGATED_RE = re.compile(C.CLICK_RX["neg"], re.I)           # don't / do not / not just before (a bare "no" does not negate)
PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{8,}\d)(?!\d)")
ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# The prompt-answer rule is kbclick's choose mode (one source: kb_capture.CLICK_RX).
CHOOSE_START_RE = re.compile(f"(?:{C.CLICK_RX['cc']})|(?:{C.CLICK_RX['cn']})", re.I)
CHOOSE_NO_RE = re.compile(C.CLICK_RX["no"], re.I)           # a bare "No" counts only as the whole text
CHOOSE_BAD_RE = re.compile(C.CLICK_RX["cb"], re.I)
REQUEST_RE = re.compile(r"^(POST|PUT|PATCH) (/[^?#\s]*)$")
CHANNELS = ("fetch", "xhr")                     # the guard's allowance covers these only
MIN_DAYS_AHEAD = 14                             # a typed date is never "tomorrow"
FALLBACK_DAYS = 180                             # period unknown: today + 180 days
DELETE_BEFORE_DAYS = 7                          # a demo record is deleted a week before its date
START_TIME, END_TIME = "09:00", "10:00"
DEMO_OPTION = "ZZ DEMO"                         # the demo option a required select takes
PICK_RULE = "first option starting 'ZZ DEMO'"
TICKET_RE = re.compile(r"^[A-Z][A-Z0-9]*-\d+$")
GUARD_TEXT = DEMO_OPTION                        # visible on an existing demo record's page
DATE_LABEL_RE = re.compile(r"\bdate\b", re.I)
TIME_LABEL_RE = re.compile(r"\btime\b|^(?:start|end)s?$", re.I)
END_LABEL_RE = re.compile(r"\b(?:end|ends|finish|to|until)\b", re.I)
NATIVE_TYPES = ("date", "time", "datetime-local")
LIVE_STATUSES = ("created", "unverified")
DRY, SAVE = "dry_run", "save"
REASON_AMBIGUOUS = "ambiguous_request"          # more than one write was blocked after the prompt's choice

DEMO_COLLECTION = {"name": "KB Demo Data", "env_key": "MEM_KB_DEMO_COLLECTION",
                   "description": "medtrics-release-notes /kb-articles: the dry-run flows learned for "
                                  "demo forms (kind 'form flow') and every demo record the pipeline "
                                  "saved on the demo tenant (kind 'demo record') so the owner can "
                                  "delete them. Claude never deletes demo data."}
FLOW_PREFIX = "KB flow: "
DEMO_PREFIX = "KB demo: "
SEP = " · "
NONE = "—"
FIELD_RE = re.compile(r"^\*\*(?P<key>[^*]+?):\*\*\s*(?P<value>.*)$")
FLOW_FIELDS = [("Kind", "kind"), ("Ticket", "ticket"), ("Key", "key"), ("Start", "start"), ("Path", "path"),
               ("Save", "save"), ("Dialog title", "dialog"), ("Buttons", "buttons"), ("Choose", "choose"),
               ("Request", "request"), ("Request query", "query"), ("Request channel", "channel"), ("Request on", "request_on"),
               ("Status", "status"), ("Reason", "reason"), ("Learned", "learned"), ("Run", "run")]
DEMO_FIELDS = [("Kind", "kind"), ("Ticket", "ticket"), ("What", "what"), ("Fingerprint", "fingerprint"),
               ("Link", "link"), ("Date", "date"), ("Start time", "start_time"), ("End time", "end_time"),
               ("Venue", "venue"), ("Server id", "server_id"), ("Run", "run"), ("Status", "status"),
               ("Created", "created"), ("Delete before", "delete_before"), ("Deleted", "deleted")]


def emit(payload):
    print("---KB_DATA_RESULT---")
    print(json.dumps(payload))


def _load(path):
    return json.loads(Path(path).read_text())


def _today(a):
    return _dt.date.fromisoformat(a.today) if getattr(a, "today", None) else _dt.date.today()


def _oneline(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def _json1(x):
    return json.dumps(x, ensure_ascii=False, separators=(",", ":"))


# ── dates and values ──────────────────────────────────────────────────────────
def _weekday_on_or_before(d):
    while d.weekday() >= 5:
        d -= _dt.timedelta(days=1)
    return d


def far_date(today, period_end=None):
    """The date a form fill types: the last weekday of the current period's final
    month when the period is known and that is at least MIN_DAYS_AHEAD away, else
    today + 180 days (rolled forward to a weekday). Never tomorrow."""
    floor = today + _dt.timedelta(days=MIN_DAYS_AHEAD)
    if period_end:
        nxt = (period_end.replace(day=28) + _dt.timedelta(days=4)).replace(day=1)
        last = _weekday_on_or_before(nxt - _dt.timedelta(days=1))
        if last >= floor:
            return last
    d = today + _dt.timedelta(days=FALLBACK_DAYS)
    while d.weekday() >= 5:
        d += _dt.timedelta(days=1)
    return d


def forbidden_in(text):
    """The forbidden word in a save / choose / path text, or "". send / notify pass
    when negated ("No, don't send notifications")."""
    t = text or ""
    m = FORBIDDEN_RE.search(t)
    if m:
        return m.group(0).lower()
    for m in SEND_RE.finditer(t):
        if not NEGATED_RE.search(t[:m.start()]):
            return m.group(0).lower()
    return ""


def choose_problem(choose):
    """Why a prompt answer is refused, or "". Only cancel-type answers are allowed:
    Cancel…, Close…, Skip…, Don't…, Do not…, "No, don't…" and exactly "No". A bare "No"
    in front of anything else does not count ("No, send notifications" is refused).
    yes / save / submit / confirm / continue / ok / proceed are refused anywhere in the
    text, and so are send / notify (unless right after don't / do not / not) and
    invite / publish / approve / email / delete / remove / archive."""
    t = " ".join(str(choose or "").split())
    if not (CHOOSE_NO_RE.match(t) or CHOOSE_START_RE.match(t)):
        return "choose must be a cancel-type answer (Cancel…, Close…, Skip…, Don't…, Do not…, \"No, don't…\" or exactly \"No\")"
    m = CHOOSE_BAD_RE.search(t)
    word = m.group(0).lower() if m else forbidden_in(t)
    return f"choose \"{choose}\" contains \"{word}\"" if word else ""


REQUEST_BAD_RE = re.compile(r"delete|remove|destroy|archive|invite|publish|approve|e-?mail|logout|signout|"
                            r"portal_change|impersonate|\b(?:send|notify)\b", re.I)


QUERY_RE = re.compile(r"^(?:\?[^#\s]*)?$")


def request_problem(request, query=""):
    """Why a learned "METHOD /path" (+ its query string, "" or "?a=b") may not be allowed, or "".
    Only POST / PUT / PATCH to a plain same-origin path, and a path or query that says delete /
    remove / destroy / archive / invite / publish / approve / email / send / notify / logout is
    refused (the owner decides those)."""
    m = REQUEST_RE.match(str(request or "").strip())
    if not m:
        return "the learned request is not POST / PUT / PATCH with a plain path"
    query = str(query or "")
    if not QUERY_RE.match(query):
        return "the learned request query is not a plain query string"
    bad = REQUEST_BAD_RE.search(m.group(2)) or REQUEST_BAD_RE.search(query)
    return f"the learned request {'path' if REQUEST_BAD_RE.search(m.group(2)) else 'query'} contains \"{bad.group(0).lower()}\"" if bad else ""


def start_problem(start):
    if not start.startswith("/") or "://" in start or start.startswith("//"):
        return "start must be a path on the demo site"
    word = forbidden_in(start) or (C._CLICK_RE["attr"].search(start) or [""])[0].strip("/?&=_- ").lower()
    return f"start \"{start}\" contains \"{word}\"" if word else ""


def fingerprint(ticket, start, path, fields, save):
    """The reuse key of a record: ticket, start page, click path, save text and the record kind
    (the form's shape: each field's label and kind). Never a value - the date, times and names
    are derived from today / the period and change from day to day - so the same form planned
    on another day is the same record and a live one is reused, not saved twice."""
    body = _json1([ticket, start, path, save, [[f["label"], f.get("kind", "")] for f in fields]])
    return hashlib.sha1(body.encode("utf-8")).hexdigest()[:12]


def flow_key(ticket, start, path, save):
    """The key a "form flow" note is found by: the ticket's (start, click path, save text)."""
    return hashlib.sha1(_json1([ticket, start, path, str(save).lower()]).encode("utf-8")).hexdigest()[:12]


# ── Mem notes: form flow + demo record ────────────────────────────────────────
def load_dump(path):
    """The saved list_notes response -> list of note dicts. Missing / empty -> []."""
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
                v = re.sub(r"[\\\s]+$", "", m.group("value")).strip()     # Mem: a hard break is a trailing backslash
                rec[key] = "" if v == NONE else v
    return rec


def _render(title, schema, rec):
    out = [title, ""]
    for label, key in schema:
        out.append(f"**{label}:** {_oneline(rec.get(key)) or NONE}")
    return "\n".join(out) + "\n"


def flow_title(rec):
    return f"{FLOW_PREFIX}{rec['ticket']}{SEP}{rec['key']}"


def render_flow_note(rec):
    return _render(flow_title(rec), FLOW_FIELDS, {**rec, "kind": "form flow"})


def _parse_note(note, prefix, schema, kind):
    content = note if isinstance(note, str) else (note.get("content") or "")
    lines = content.splitlines()
    if not lines or not lines[0].strip().startswith(prefix):
        return None
    rec = _fields(lines[1:], schema)
    if rec["kind"] != kind or not rec["ticket"]:
        return None
    if isinstance(note, dict):
        rec["note_id"] = note.get("id") or note.get("note_id") or ""
        rec["version"] = note.get("version")
    return rec


def parse_flow_note(note):
    rec = _parse_note(note, FLOW_PREFIX, FLOW_FIELDS, "form flow")
    if rec:
        for k in ("path", "buttons"):
            try:
                rec[k] = json.loads(rec[k]) if rec[k] else []
            except ValueError:
                rec[k] = []
    return rec


def demo_title(rec):
    return f"{DEMO_PREFIX}{rec['ticket']}{SEP}{rec.get('date') or NONE}{SEP}{rec['fingerprint']}"


def render_demo_note(rec):
    return _render(demo_title(rec), DEMO_FIELDS, {**rec, "kind": "demo record"})


def parse_demo_note(note):
    return _parse_note(note, DEMO_PREFIX, DEMO_FIELDS, "demo record")


def flow_index(notes):
    """{(ticket, key): flow record}; the newest `Learned` wins."""
    idx = {}
    for n in notes:
        rec = parse_flow_note(n)
        if not rec:
            continue
        k = (rec["ticket"], rec["key"])
        if k not in idx or rec.get("learned", "") >= idx[k].get("learned", ""):
            idx[k] = rec
    return idx


def demo_records(notes):
    return [r for r in (parse_demo_note(n) for n in notes) if r]


def flow_problem(flow):
    """Why a stored flow is not usable for a real save, or "" when it is."""
    if flow.get("status") != "learned":
        return f"flow status is {flow.get('status') or 'unknown'}" + (f" ({flow['reason']})" if flow.get("reason") else "")
    if not flow.get("dialog"):
        return "no prompt title learned"
    if flow.get("channel") not in CHANNELS:
        return f"the request went out as {flow.get('channel') or '?'}, not fetch/xhr"
    if flow.get("request_on") == "ambiguous":
        return REASON_AMBIGUOUS
    if flow.get("request_on") != "choose":
        return "the request is not sent by the prompt's choice"
    return choose_problem(flow.get("choose")) or request_problem(flow.get("request"), flow.get("query"))


# ── reading the capture: the form screen's fields ────────────────────────────
def load_screens(path):
    """{visit_id: {"fields", "tw", "btn", "found"}} from shots.json (its `screens`, written by
    kb_capture.py ingest) or from a raw/ folder (probe of every item)."""
    p = Path(path)
    out = {}
    if p.is_dir():
        for f in sorted(p.glob("*.json")):
            try:
                r = json.loads(f.read_text())
            except ValueError:
                continue
            for it in (r.get("items") or []) if isinstance(r, dict) else []:
                pr = C.parse_probe(it.get("probe"))
                if pr and it.get("visit_id"):
                    out[it["visit_id"]] = C.screen_form(pr)
        return out
    m = _load(p)
    for s in m.get("screens") or []:
        if s.get("visit_id"):
            out[s["visit_id"]] = {k: s[k] for k in ("fields", "tw", "btn", "found") if s.get(k)}
    return out


def form_screen(calls, eid, screens):
    """The entry's last visit (deepest click) whose screen listed form controls, else {}."""
    mine = sorted((v for v in calls.get("visits") or [] if v.get("entry") == eid),
                  key=lambda v: v.get("step", 0), reverse=True)
    for v in mine:
        sc = screens.get(v["visit_id"]) or {}
        if sc.get("fields"):
            return sc
    return {}


def save_text(screen):
    """The form's save button as the page shows it: the first of Save / Create / Add / Submit
    among the dialog's buttons or the labels the probe found, else "Save"."""
    seen = [str(x) for x in (screen.get("btn") or []) + (screen.get("found") or [])]
    for want in SAVE_TEXTS:
        for t in seen:
            if t.strip().lower() == want:
                return t.strip()
    return "Save"


# ── plan ──────────────────────────────────────────────────────────────────────
def plan_fills(fields, ticket, today, period_end):
    """(fills, stop | None). fills: [{"label", "kind", "type", "name", "value"}] in form
    order. date / time controls are always filled (a form cannot save without them and
    "required" is not always visible in the DOM); every other control only when required;
    optional ones stay empty. stop: {"reason", "detail"}."""
    d_iso = far_date(today, period_end).isoformat()
    name_value = f"{PREFIX} {DASH} {ticket}"
    fills, seen = [], set()
    for f in fields:
        kind, label, req = str(f.get("kind") or "other"), str(f.get("label") or "").strip(), bool(f.get("required"))
        ty, name = str(f.get("type") or ""), str(f.get("name") or "")
        if not label:
            if req:
                return fills, {"reason": "unsupported_required_field", "detail": f"a required {kind} field has no label"}
            continue
        if (label.lower(), kind) in seen:
            continue
        value = None
        as_kind = kind
        if kind == "text" and DATE_LABEL_RE.search(label):
            as_kind = "date"
        elif kind == "text" and TIME_LABEL_RE.search(label):
            as_kind = "time"
        if as_kind == "date":
            value = d_iso + (f"T{START_TIME}" if ty == "datetime-local" else "")
        elif as_kind == "time":
            value = END_TIME if END_LABEL_RE.search(label) else START_TIME
        elif not req:
            continue
        elif kind in ("multiselect", "duallist"):
            return fills, {"reason": "unsupported_required_picker", "detail": f"\"{label}\" is a required {kind}"}
        elif kind == "select":
            zz = f.get("zz") or next((o for o in f.get("opts") or [] if str(o).upper().startswith(DEMO_OPTION)), "")
            if not zz:
                return fills, {"reason": "needs_owner", "detail": f"no '{DEMO_OPTION}' option for required \"{label}\""}
            value = zz
        elif kind in ("text", "textarea"):
            if ty in ("email", "tel", "url"):
                return fills, {"reason": "unsupported_required_field", "detail": f"\"{label}\" wants an {ty} (personal data)"}
            value = "1" if ty == "number" else name_value
        else:
            return fills, {"reason": "unsupported_required_field", "detail": f"\"{label}\" is a required {kind}"}
        seen.add((label.lower(), kind))
        fills.append({"label": label, "kind": as_kind, "type": ty, "name": name, "value": value})
    return fills, None


def _click_specs(clicks):
    out = []
    for k in clicks or []:
        if isinstance(k, dict) and k.get("row"):
            out.append({"row": True})
        else:
            t = k.get("text") if isinstance(k, dict) else k
            if str(t or "").strip():
                out.append({"text": str(t).strip()})
    return out


def build_plan(calls, groups, screens, issues, flows, records, today, period_end):
    """-> (tickets {tid: [record]}, stopped [..], reused [..])."""
    skipped = {(c.get("persona"), c.get("url")) for c in (groups or {}).get("capture_plan") or []
               if c.get("skip_reason")}
    tickets, stopped, reused = {}, [], []
    for eid, e in (calls.get("entries") or {}).items():
        gated = e.get("write_gated")
        tids = [t for t in e.get("tickets") or [] if TICKET_RE.match(str(t))]
        if not gated or not tids or (e.get("persona"), e.get("url")) in skipped:
            continue
        tid = tids[0]                                  # one record per entry: a second save would duplicate it
        n = len(tickets.get(tid, [])) + sum(1 for s in stopped if s["ticket"] == tid)
        rid = f"r{n + 1}"

        def stop(reason, detail=""):
            stopped.append({"ticket": tid, "id": rid, "entry": eid, "reason": reason, "detail": detail})
        if n >= MAX_RECORDS:
            stop("too_many_records", f"more than {MAX_RECORDS} records for one ticket")
            continue
        if e.get("persona") not in PERSONAS:
            stop("persona", "form fills run as admin or faculty")
            continue
        start = C.start_key(e.get("url"))
        why = start_problem(start)
        if why:
            stop("needs_owner", why)
            continue
        path = _click_specs(e.get("clicks"))
        bad = next((forbidden_in(k["text"]) for k in path if k.get("text") and forbidden_in(k["text"])), "")
        if bad:
            stop("needs_owner", f"path click contains \"{bad}\"")
            continue
        screen = form_screen(calls, eid, screens)
        if not screen:
            stop("no_form_screen", "the capture saw no form controls on the page")
            continue
        fills, why = plan_fills(screen["fields"], tid, today, period_end)
        if why:
            stop(why["reason"], why["detail"])
            continue
        if not fills:
            stop("no_form_screen", "no field to fill was found on the form")
            continue
        save = save_text(screen)
        last = next((k["text"] for k in reversed(path) if k.get("text")), "")
        page = C.page_label(start)
        what = f"{page} — {last}" if last and page else (last or page or "form")
        fp = fingerprint(tid, start, path, fills, save)
        live = next((r for r in records if r["ticket"] == tid and r["fingerprint"] == fp
                     and r["status"] in LIVE_STATUSES), None)
        if live:
            reused.append({"ticket": tid, "id": rid, "what": what, "fingerprint": fp, "server_id": live["server_id"],
                           "link": live["link"], "created": live["created"]})
            continue
        rec = {"id": rid, "entry": eid, "what": what, "persona": e["persona"], "start": start, "path": path,
               "fields": fills, "save": save, "fingerprint": fp, "mode": DRY,
               "after": {"expect": "", "choose": "", "labels": []}}
        flow = flows.get((tid, flow_key(tid, start, path, save)))
        if flow:
            why = flow_problem(flow)
            if why:
                rec["flow_note"] = f"stored flow not usable: {why}"
            else:
                rec["mode"] = SAVE
                rec["flow"] = {"dialog": flow["dialog"], "choose": flow["choose"], "request": flow["request"],
                               "query": flow.get("query", ""), "buttons": flow["buttons"],
                               "channel": flow["channel"], "request_on": flow["request_on"], "status": flow["status"]}
                rec["after"] = {"expect": flow["dialog"], "choose": flow["choose"],
                                "labels": [flow["dialog"], flow["choose"]]}
        tickets.setdefault(tid, []).append(rec)
    return tickets, stopped, reused


def _fvalue(f):
    return f"{f['label']}={f['value']}"


def preview_line(tid, r):
    """One owner-facing line per record."""
    where = r["start"] + "".join(" → " + ("first row" if k.get("row") else f"\"{k['text']}\"") for k in r["path"])
    fields = "; ".join(_fvalue(f) for f in r["fields"]) or "no fields"
    if r["mode"] == SAVE:
        fl = r["flow"]
        return (f"- {tid} · {r['id']} · {r['persona']} · WILL BE SAVED on the demo site (a demo record; you delete it "
                f"afterwards, it is logged in Mem): {where} · fields: {fields} · presses \"{r['save']}\", prompt "
                f"\"{fl['dialog']}\", then clicks \"{fl['choose']}\" · exactly one request is allowed: "
                f"{fl['request']}{fl.get('query', '')}; every other write stays blocked.")
    note = f" ({r['flow_note']})" if r.get("flow_note") else ""
    return (f"- {tid} · {r['id']} · {r['persona']} · DRY RUN, nothing is saved{note}: {where} · fields: {fields} · "
            f"presses \"{r['save']}\" (blocked), reads the prompt's buttons and the request it would send, clicks the "
            f"safe answer (blocked) · the real save happens next run once the prompt is learned.")


def preview_text(tickets, stopped=(), reused=()):
    lines = [preview_line(tid, r) for tid, recs in tickets.items() for r in recs]
    dates = sorted({f["value"][:10] for recs in tickets.values() for r in recs for f in r["fields"]
                    if f["kind"] == "date"})
    if dates:
        lines.append("Dates: " + ", ".join(dates) + " (far-future weekday in the current period, never tomorrow).")
    for s in stopped:
        lines.append(f"- {s['ticket']} · {s['id']} · STOPPED {s['reason']}: {s['detail']}")
    for r in reused:
        lines.append(f"- {r['ticket']} · {r['id']} · REUSED the live demo record {r['server_id'] or r['fingerprint']}"
                     f" ({r['what']}): no second save.")
    return "\n".join(lines)


def canonical(plan):
    """The approved content: the tickets only (approval flags, the stopped / reused lists and
    the rejected list are not part of what the owner agreed to)."""
    return json.dumps({"tickets": plan.get("tickets") or {}}, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))


def plan_hash(plan):
    return hashlib.sha256(canonical(plan).encode("utf-8")).hexdigest()


def cmd_plan(a):
    try:
        calls, groups = _load(a.calls), _load(a.groups)
        screens = load_screens(a.shots)
        issues = (_load(a.data).get("issues") or []) if a.data else []
        flows = flow_index(load_dump(a.flows))
        records = demo_records(load_dump(a.records))
    except (OSError, ValueError) as e:
        print(f"data: cannot read an input: {e}")
        emit({"ok": False, "action": "plan", "error": str(e)})
        return 1
    today = _today(a)
    period_end = _dt.date.fromisoformat(a.period_end) if a.period_end else None
    tickets, stopped, reused = build_plan(calls, groups, screens, issues, flows, records, today, period_end)
    plan = {"approved": False, "tickets": {t: {"records": r} for t, r in tickets.items()},
            "stopped": stopped, "reused": reused}
    plan["checked_sha256"] = plan_hash(plan)          # only what `plan` produced can be approved
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(plan, indent=1, ensure_ascii=False))
    recs = [r for rs in tickets.values() for r in rs]
    n_save = sum(1 for r in recs if r["mode"] == SAVE)
    if recs or stopped or reused:
        print(preview_text(tickets, stopped, reused))
    print(f"data plan: {len(recs)} record(s) for {len(tickets)} ticket(s) ({n_save} to SAVE, {len(recs) - n_save} dry run), "
          f"{len(stopped)} stopped, {len(reused)} reused, not approved -> {a.out}. Ask the owner every run; if they "
          f"say no, those screens stay needs_write.")
    emit({"ok": True, "action": "plan", "records": len(recs), "tickets": len(tickets), "save": n_save,
          "dry_run": len(recs) - n_save, "stopped": stopped, "reused": reused, "approved": False,
          "always_ask": True, "collection": demo_collection_name(), "out": a.out})
    return 0


def demo_collection_name():
    return (os.environ.get(DEMO_COLLECTION["env_key"]) or "").strip() or DEMO_COLLECTION["name"]


def cmd_approve(a):
    try:
        plan = _load(a.plan)
    except (OSError, json.JSONDecodeError) as e:
        emit({"ok": False, "action": "approve", "error": str(e)})
        return 1
    if not plan.get("checked_sha256") or plan["checked_sha256"] != plan_hash(plan):
        print("data approve: the plan was not produced by `kb_data.py plan`, or was edited after it; "
              "re-run plan and show the owner the preview")
        emit({"ok": False, "action": "approve",
              "error": "plan_not_checked" if not plan.get("checked_sha256") else "plan_changed"})
        return 2
    plan["approved"] = True
    plan["approved_sha256"] = plan_hash(plan)
    Path(a.plan).write_text(json.dumps(plan, indent=1, ensure_ascii=False))
    recs = [r for t in (plan.get("tickets") or {}).values() for r in t.get("records") or []]
    n_save = sum(1 for r in recs if r.get("mode") == SAVE)
    print(f"data plan approved: {len(recs)} record(s), {n_save} to be SAVED, {len(recs) - n_save} dry run")
    emit({"ok": True, "action": "approve", "records": len(recs), "save": n_save, "approved": True,
          "sha256": plan["approved_sha256"]})
    return 0


# ── brief ─────────────────────────────────────────────────────────────────────
def _args_for(calls, persona):
    """Probe args of the first visit of this persona (else any visit)."""
    visits = calls.get("visits") or []
    for v in visits:
        if v.get("persona") == persona:
            return v["args"]
    return visits[0]["args"] if visits else None


def click_json(side, text=None, row=False, write=None, pa=None, choose=False, allow=None):
    """kbclick args — same shape kb_capture builds for a click visit's click_args.
    choose: dry-run mode, the page picks the safe answer in the open dialog itself.
    allow: save mode, {"method", "path", "dlg", "query"} for the one-shot allowance."""
    args = {"side": side}
    if row:
        args["row"] = True
        args["pa"] = {**(pa or {}), "labels": []}
    elif choose:
        args["choose"] = True
    else:
        args["text"] = text
    if write:
        args["write"] = list(write)
    if allow:
        args["allow"] = dict(allow)
    return args


_FIELD_JS = r"""(function(a){
__GUARD__
var D=document;
__FIELDS__
var dl=[].slice.call(D.querySelectorAll(__DLG__)).filter(kfV);
var root=dl.length?dl[dl.length-1]:D;
var want=kfN(a.label).toLowerCase();
var cs=kfList(root,a.side).filter(function(x){return x.d.label.toLowerCase()===want;});
if(a.name&&cs.length>1){var bn=cs.filter(function(x){return x.d.name===a.name;});if(bn.length)cs=bn;}
if(!cs.length)throw new Error('KB_FIELD_NOTFOUND:'+a.label);
var el=cs[0].e,k=cs[0].d.kind;
if(/^(password|file|hidden)$/i.test(el.type||'')||/^(multiselect|duallist|checkbox|radio|other)$/.test(k))throw new Error('KB_FIELD_REFUSED:'+a.label);
if(a.mode==='read')return JSON.stringify({ok:true,value:el.value,placeholder:el.placeholder||''});
if(el.tagName==='SELECT'){var o=[].slice.call(el.options).filter(function(x){return x.text.trim()===a.value;})[0];
 if(!o)throw new Error('KB_FIELD_NOOPTION:'+a.label);el.value=o.value;}
else{var proto=el.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
 el.focus();Object.getOwnPropertyDescriptor(proto,'value').set.call(el,a.value);}
el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));
el.dispatchEvent(new Event('blur',{bubbles:true}));el.dispatchEvent(new FocusEvent('focusout',{bubbles:true}));
var now=el.tagName==='SELECT'?el.options[el.selectedIndex].text.trim():el.value;
if(now!==a.value)throw new Error('KB_FIELD_NOSTICK:'+a.label+':'+now);
return JSON.stringify({ok:true,value:now});
})"""
# the write guard first (like every page script), then the shared control reader, so a label the
# probe reported is the label this script finds
def field_script():
    """Built on demand, not at import: the guard inside it carries the run's arm token,
    which brief loads (C.use_run_token) after this module is imported."""
    return (_FIELD_JS.replace("__GUARD__", C.guard_source()).replace("__FIELDS__", C.fields_source())
            .replace("__DLG__", json.dumps(C.DIALOG_SEL)))


FIELD_JS = field_script()                    # import-time copy, for tests and callers outside a run


FIELD_KEY = "kbf"


def field_install_action():
    """javascript_tool input that stores the field script (kbf) in sessionStorage next to the probe
    (kbp), the click guard (kbc) and the option picker (kbo): each field call is then a short stub."""
    text = (f"{C.JS_MARKER}var F={json.dumps(field_script())};sessionStorage.setItem('{FIELD_KEY}',F);"
            f"'{FIELD_KEY}:'+F.length")
    return {"action": "javascript_exec", "tabId": "$TAB", "text": text}


def field_js(label, value=None, mode="set", side="", name=""):
    """Vue-commit fill (QA engineering-gaps #7): set the value, dispatch input + change +
    blur, re-read and compare; throws KB_FIELD_NOSTICK when it did not stick. The stub runs the
    stored `kbf` script (field_install_action); throws KB_NOPROBE when it was not installed."""
    args = {"label": label, "mode": mode, "side": side}
    if name:
        args["name"] = name
    if mode == "set":
        args["value"] = value
    return C.stub(args, FIELD_KEY)


def field_block(f, side):
    """How the maker fills one field: `native` (date/time inputs and text: fill_js sets the
    value) or `typed` (a text-type date / time picker: type it, Tab, read_js)."""
    native = f["kind"] in ("text", "textarea", "select") or f.get("type") in NATIVE_TYPES
    base = {"label": f["label"], "kind": f["kind"], "value": f["value"],
            "read_js": field_js(f["label"], mode="read", side=side, name=f.get("name", ""))}
    if native:
        return {**base, "fill": "native", "fill_js": field_js(f["label"], f["value"], side=side, name=f.get("name", ""))}
    return {**base, "fill": "typed"}


def record_block(ticket, rec, visit, args, portal_entry=""):
    side = args.get("side", "")
    pa = args
    mode = rec.get("mode", DRY)
    flow = rec.get("flow") or {}
    write = [rec["save"]] + ([flow["choose"]] if mode == SAVE else [])
    probe_labels = [flow["dialog"], flow["choose"]] if mode == SAVE else []
    block = {
        "ticket": ticket, "record_id": rec["id"], "what": rec["what"], "mode": mode,
        "fingerprint": rec["fingerprint"],
        "visit_id": visit["visit_id"], "gif": visit["gif"],
        "navigate": rec["start"],
        "guard_js": C.stub({**args, "labels": []}),
        "path_picks": [{"click": k, "js": C.stub(click_json(side, k.get("text"), bool(k.get("row")), pa=pa), C.KBC_KEY)}
                       for k in rec["path"]],
        "needs_demo_context": any(k.get("row") for k in rec["path"]),
        "fields": [field_block(f, side) for f in rec["fields"]],
        "pre_save_probe_js": C.stub({**args, "labels": [rec["save"]]}),
        "save_pick": {"text": rec["save"],
                      "js": C.stub(click_json(side, rec["save"], write=[rec["save"]]), C.KBC_KEY)},
        "probe_js": C.stub({**args, "labels": probe_labels}),
        "export": C._export(visit["gif"]),
        "post_probe_js": C.stub({**args, "labels": []}),
    }
    if mode == SAVE:
        m, p = flow["request"].split(" ", 1)
        allow = {"method": m, "path": p, "dlg": flow["dialog"], "query": flow.get("query", "")}
        block["expect"] = flow["dialog"]
        block["request"] = flow["request"]
        block["choose_pick"] = {
            "text": flow["choose"], "expect": flow["dialog"],
            "js": C.stub(click_json(side, flow["choose"], write=write, allow=allow),
                         C.KBC_KEY)}
        block["pending"] = {"visit_id": visit["visit_id"], "record_id": rec["id"], "ticket": ticket,
                            "mode": SAVE, "pending": rec["fingerprint"]}
    else:
        block["choose_pick"] = {"rule": "the page picks the safe answer itself",
                                "js": C.stub(click_json(side, write=[rec["save"]], choose=True), C.KBC_KEY)}
    if portal_entry:                      # a non-admin record first switches to its own portal
        block["portal_entry"] = portal_entry
    return block


HEAD = """# Demo form fills to make

You fill the forms listed below on the shared demo tenant, one form at a time,
exactly as written, press Save, read the prompt that opens, answer it once, and
record what came back. You do not judge pages. A plugin hook blocks every key press
except `Tab` and `Escape`, every drag, any typed text holding a newline or tab, and
any `javascript_tool` text not starting with `/*KB*/` for you.

Every block has a `mode`:

- **`dry_run`**: NOTHING IS STORED. The capture page blocks every non-GET request.
  You press Save, the prompt opens, you click its safe answer (the page script picks
  it: `choose_pick`), and the page tells the probe which request it blocked. The
  plugin learns the prompt's buttons and that request from your raw item; the real
  save happens on a later run.
- **`save`**: the owner approved a REAL save of a demo record. The same steps, but
  `choose_pick.js` also arms a one-shot allowance for exactly one request, the one
  named in the block's `request`, valid for 10 seconds from your click on that exact
  button, only after the probe has seen the prompt. Save itself never sends anything. Everything else stays blocked.

Folders: the run folder is given to you; raw files go in `<run folder>/shots/raw/`.
Read and write only inside the run folder; never use /tmp. Budget: about 25 tool
uses per form fill; prefer `find` over `read_page`.

## Hard rules (stop the form fill on any surprise)

- Fill ONLY the forms below. Never edit, delete or touch an existing record,
  except opening an existing "ZZ DEMO" session / group / venue through the listed path.
- Type only the listed field values. Never a personal name, email, phone number or
  password. Never press Enter (Save is pressed through kbclick). The only keys you may
  press are `Tab` (after typing a date or time) and `Escape` (only to close a dialog when
  the `choose_pick` click throws).
- Every `javascript_tool` text starts with `/*KB*/` (the scripts below all do); a
  hook denies any other. Only `navigate` to the demo site (a path, or the base URL +
  a path); never to a delete / remove / logout URL.
- Click only through the installed `kbclick` (the `js` below, then `left_click` at the
  `x`,`y` it returns). Never click anywhere else. Never click a prompt answer yourself:
  the only prompt click is `choose_pick`, and in `save` mode it is exactly one click.
  Never click Yes, OK, Continue, Confirm, or any send / notify / invite / publish /
  approve / email option.
- If a `kbclick` call throws (`KB_CLICK_REFUSED`, `KB_CLICK_NOTFOUND`, `KB_TENANT_ROW`),
  a probe contains `KB_LOGIN` or `KB_PERSONA`, a field script throws
  (`KB_FIELD_NOTFOUND`, `KB_FIELD_NOSTICK`, `KB_FIELD_NOOPTION`, `KB_FIELD_REFUSED`), or
  the page looks different from the plan (an error banner, a different record):
  `gif_creator` `clear`, write the item with `write_error` (the error text), skip the rest
  of that form fill, carry on with the next one. `KB_LOGIN` ends the run: write the item,
  then stop.

## Install (already done)

The orchestrator stored the probe (`kbp:<number>`), the click guard, the option picker
and the field script (`kbf:<number>`) in this tab before it started you: those scripts are
too long for you to send, so you never send them. Every call below is a short stub that
runs them. If one throws `KB_NOPROBE` or `KB_NOFIELD` (a new tab, or the scripts are gone):
`gif_creator` `clear`, write nothing for that form fill, and reply
`KB_DATA_DONE ok=<form fills done> failed=<form fills with write_error> stop=needs_install`.
The orchestrator re-sends the install (`data_install.json`) and starts you again.

## Each form fill

0. If the block has `portal_entry`, `navigate` to the base URL + `portal_entry`,
   `wait` 4 seconds, run `guard_js`; this form fill runs on that portal.
1. `navigate` to the base URL + `navigate` below; `computer` `wait` 4 seconds, then
   run `guard_js` (the probe: it installs the write guard in the fresh page; nothing
   is filled or clicked before it ran). If a click path follows a group row, send
   the same row `kbclick` once more when the result says `"group": true`.
2. For each entry of `path_picks`, in order: run its `js` with `javascript_tool`
   (it returns `{{"ok":true,"x":…,"y":…}}` or throws), `computer` `left_click` at
   exactly that `x`,`y`, `wait` 2 seconds, then run `guard_js` again (a click can
   load a new page or open a frame) before the next pick and before any field.
3. If `needs_demo_context` is true: `find` "ZZ DEMO" text once. If nothing on the
   page contains "ZZ DEMO", this is not a demo record: write `write_error`
   "not_a_demo_record" and skip the form fill.
4. Fill each entry of `fields`, in order, by its `fill`:
   - `native`: run `fill_js` with `javascript_tool`. It sets the value, dispatches
     input + change + blur, re-reads the value and throws `KB_FIELD_NOSTICK` if it
     did not stick (Vue forms keep a separate model; setting only the DOM value
     would be lost). Do not use `form_input` for these.
   - `typed` (a date or time picker that is a text box): `find` the input by its label,
     `left_click` it, read its placeholder (`read_js`), `computer` `type` the value in the
     placeholder's format (a date `2026-12-31` as `12/31/2026` for MM/DD/YYYY, a time
     `09:00` as `9:00 AM` for a 12-hour box), press `Tab`, then run `read_js`: it must show
     the same day / time; if not, `KB_FIELD_NOSTICK`: stop the form fill.
   Never touch any other field.
5. Run `pre_save_probe_js`. It must contain the Save text and `wb` equal to 0 or
   missing; if `wb` is above 0 something already tried to write: stop the form fill.
6. `gif_creator` `start_recording`.
7. Run `save_pick.js`, `left_click` at its `x`,`y`, `wait` 3 seconds. Save opens a
   prompt; no request leaves the page.
8. Run `probe_js` with `javascript_tool`. Keep its result verbatim (`dlg` is the open
   prompt: `t` title, `x` text, `bt` button texts; `h` the title too; `wb` / `wl` /
   `wk` the blocked writes). If no prompt is shown (no `dlg`) and `wb` is 0, Save did
   nothing: `write_error` "save_did_nothing". In `save` mode the prompt must show the block's
   `expect` text; if it does not, `write_error` "prompt_not_seen" and click nothing.
9. `gif_creator` `stop_recording`, then the `export` action (replace `"$TAB"`). The
   `gif` is the filename in the `Downloaded "…"` text of the export result.
10. `dry_run`: run `choose_pick.js`, keep its result verbatim (`label` is the answer
    it found), `left_click` at its `x`,`y`, `wait` 3 seconds, then run `post_probe_js`
    and keep its result verbatim. If `choose_pick.js` throws, press `Escape` once, write
    `write_error` with the error and stop the form fill (click nothing else).
    `save`: FIRST write the raw item with `pending` (below); only then run
    `choose_pick.js` (it arms the allowance; if it throws, nothing was sent: `Escape`
    once, `write_error`, stop), `left_click` at its `x`,`y` ONCE, `wait` 3 seconds, then
    run `post_probe_js`: its `saved` is `{{"m","p","s": HTTP status,"id"}}` and `al` lists the
    one allowed request. Never click twice, never retry the click.
11. Write the item (below) and the file after EVERY form fill.

## Raw file

Write `<run folder>/shots/raw/data-<ticket>.json` for each ticket (all its form fills):

```json
{{"batch_id": "data-<ticket>", "status": "ok", "error": null,
 "items": [{{"visit_id": "<visit_id>", "ticket": "<ticket>", "record_id": "<record_id>", "mode": "dry_run",
            "probe": <step 8 probe verbatim>, "gif": "<filename>", "blocked": <post_probe wb>,
            "choose": <choose_pick.js result verbatim>, "post_probe": <post_probe_js result verbatim>}},
           {{"visit_id": "<visit_id>", "ticket": "<ticket>", "record_id": "<record_id>", "mode": "save",
            "probe": <step 8 probe verbatim>, "gif": "<filename>", "blocked": <post_probe wb>,
            "pending": "<fingerprint>"}},
           {{"visit_id": "<visit_id>", "write_error": "<error text>"}}]}}
```

In `save` mode the item is written twice: first with `pending` (before the choose click);
then, after `post_probe_js`, replace `pending` by
`"created": {{"fingerprint": "<fingerprint>", "id": "<saved.id or empty>", "status": "<saved.s>", "page_url": "<post_probe.u>"}}`
and add `"post_probe": <post_probe_js result verbatim>`. If `saved` is missing, still write
`created` with `status` "unknown". Never invent a status: the plugin reads the post probe (`al`,
`saved`); no allowed request and no response means NOT saved. Never delete the `pending` item before writing `created`.

`status` is `ok`, or `expired` after `KB_LOGIN`, or `bridge_down` after a tool or
transport error or a call denied by a hook naming `KB_GUARD` (do not retry it).
Reply with one line: `KB_DATA_DONE ok=<form fills done> failed=<form fills with write_error>`.
"""


def plan_problems(plan):
    """The safety rules `plan` applied, run again on an approved plan (a hand-edited plan with a
    recomputed hash still passes the hash check): every record's start and click-path words, the
    mode, and for a SAVE record the whole learned flow (status, prompt, channel, choose rule,
    request method / path / query rules). -> ["<ticket>:<record>: <why>", ...]."""
    out = []
    for tid, body in (plan.get("tickets") or {}).items():
        for rec in (body or {}).get("records") or []:
            who = f"{tid}:{rec.get('id')}"
            if rec.get("mode") not in (DRY, SAVE):
                out.append(f"{who}: unknown mode {rec.get('mode')!r}")
                continue
            why = start_problem(str(rec.get("start") or ""))
            if why:
                out.append(f"{who}: {why}")
            for k in rec.get("path") or []:
                bad = forbidden_in(k.get("text") or "") if isinstance(k, dict) else ""
                if bad:
                    out.append(f"{who}: path click contains \"{bad}\"")
            if rec.get("mode") == SAVE:
                flow = rec.get("flow") if isinstance(rec.get("flow"), dict) else {}
                why = flow_problem(flow) if flow else "no learned flow"
                if why:
                    out.append(f"{who}: save flow not usable: {why}")
    return out


def cmd_brief(a):
    C.use_run_token(Path(a.calls).parent)        # same token as the capture's guard
    try:
        plan, calls = _load(a.plan), _load(a.calls)
    except (OSError, json.JSONDecodeError) as e:
        emit({"ok": False, "action": "brief", "error": str(e)})
        return 1
    if not plan.get("approved") or not plan.get("approved_sha256"):
        print("data brief: the plan is not approved (run `kb_data.py approve` after the owner says yes)")
        emit({"ok": False, "action": "brief", "error": "plan_not_approved"})
        return 2
    if plan_hash(plan) != plan["approved_sha256"]:
        print("data brief: the plan changed after it was approved; plan and ask the owner again")
        emit({"ok": False, "action": "brief", "error": "plan_changed"})
        return 2
    problems = plan_problems(plan)
    if problems:
        print("data brief: the approved plan fails the save-mode safety checks, nothing written:\n  "
              + "\n  ".join(problems))
        emit({"ok": False, "action": "brief", "error": "unsafe_plan", "problems": problems})
        return 2
    try:
        routes = _load(a.routes)
    except (OSError, json.JSONDecodeError) as e:
        emit({"ok": False, "action": "brief", "error": f"routes: {e}"})
        return 1
    entries_in = (routes.get("demo") or {}).get("portal_entry") or {}
    admin_entry = entries_in.get("admin") or ""
    writes = {(v.get("ticket"), v.get("record_id")): v for v in calls.get("write_visits") or []}
    blocks, missing, mismatched = [], [], []
    for tid, body in (plan.get("tickets") or {}).items():
        for rec in body.get("records") or []:
            v = writes.get((tid, rec["id"]))
            args = _args_for(calls, rec["persona"])
            if not v or not args:
                missing.append(f"{tid}:{rec['id']}")
                continue
            entry_url = ((calls.get("entries") or {}).get(v.get("entry")) or {}).get("url")
            if not entry_url or C.start_key(entry_url) != C.start_key(rec["start"]):
                missing.append(f"{tid}:{rec['id']}")                     # start differs from its capture entry
                mismatched.append({"record": f"{tid}:{rec['id']}", "reason": "start_mismatch",
                                   "start": rec["start"], "entry_url": entry_url or ""})
                continue
            pe = entries_in.get(rec["persona"]) or ""
            blocks.append(record_block(tid, rec, v, args, pe if rec["persona"] != "admin" else ""))
    if not blocks:
        print("data brief: no record has a planned write visit")
        emit({"ok": False, "action": "brief", "error": "no_records", "missing": missing,
              "start_mismatch": mismatched})
        return 1
    md = HEAD.replace("{{", "{").replace("}}", "}")
    admin_args = _args_for(calls, "admin")
    setup = {"navigate": admin_entry,
             "probe_js": C.stub({**admin_args, "labels": []}) if admin_args else ""}
    md += (f"\nBase URL: `{calls.get('base_url', '')}`\n\n## Start (before the first form fill)\n\n"
           "Form fills run on the Admin portal: `navigate` to the base URL + `navigate` below, "
           "`computer` `wait` 4 seconds, then run `probe_js` (it throws `KB_PERSONA` or `KB_LOGIN` "
           "if this is not the signed-in Admin portal: stop and write `bridge_down`).\n\n"
           f"```json\n{json.dumps(setup, indent=1)}\n```\n\n## Form fills\n")
    for b in blocks:
        md += f"\n### {b['ticket']} / {b['record_id']}: {b['what']} ({b['mode']})\n\n```json\n{json.dumps(b, indent=1, ensure_ascii=False)}\n```\n"
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md)
    # the long scripts go to the orchestrator, which sends them before the maker starts
    base_install = calls.get("install") or C.install_actions()
    if isinstance(base_install, dict):
        base_install = [{"name": "javascript_tool", "input": base_install}]
    base_install = list(base_install)
    if not base_install or base_install[0].get("name") != "navigate":    # older calls.json
        base_install = C.install_prefix(routes.get("demo")) + base_install
    install_path = out.parent / "data_install.json"
    install_path.write_text(json.dumps(
        {"install": list(base_install) + [{"name": "javascript_tool", "input": field_install_action()}]},
        indent=1))
    print(f"data brief: {len(blocks)} form fill(s), {len(missing)} without a write visit "
          f"({len(mismatched)} start_mismatch) -> {out}")
    emit({"ok": True, "action": "brief", "records": len(blocks), "missing": missing,
          "start_mismatch": mismatched, "out": str(out), "install": str(install_path)})
    return 0


# ── raw results: learn / records ─────────────────────────────────────────────
def raw_items(raw):
    """[(ticket, item)] from a raw/ folder (data-*.json) or one raw file; the ticket is the item's
    own `ticket`, else the file name data-<ticket>.json."""
    p = Path(raw)
    files = sorted(p.glob("data-*.json")) if p.is_dir() else [p]
    out = []
    for f in files:
        try:
            r = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        tid_f = re.sub(r"^data-", "", f.stem)
        for it in (r.get("items") or []) if isinstance(r, dict) else []:
            if isinstance(it, dict) and it.get("visit_id"):
                out.append((str(it.get("ticket") or tid_f), it))
    return out


def _probe(x):
    p = C.parse_probe(x)
    return p if p is not None else (x if isinstance(x, dict) else {})


def _new_writes(post, wb0):
    """The blocked non-GETs the guard logged after the pre-choose probe (sequence number > its
    `wb`), from the post-choose probe's bounded log `bl`."""
    out = []
    for e in post.get("bl") or []:
        try:
            if isinstance(e, dict) and int(e.get("n")) > wb0:
                out.append(e)
        except (TypeError, ValueError):
            continue
    return out


def learned_from(item):
    """What a dry run taught: {"dialog_title", "buttons", "choose_text", "request", "query",
    "channel", "request_on", "blocked"}. request_on: "choose" (exactly ONE non-GET was blocked
    after the prompt's answer was clicked, compared with the probe before the click: the only
    kind a real save can allow), "ambiguous" (more than one: a background request in the gap
    would be learned as the save), "save" (Save itself fired it) or ""."""
    pr, post = _probe(item.get("probe")), _probe(item.get("post_probe"))
    dlg = pr.get("dlg") if isinstance(pr.get("dlg"), dict) else {}
    h = pr.get("h") if isinstance(pr.get("h"), dict) else {}
    ch = item.get("choose") if isinstance(item.get("choose"), dict) else {}
    wb0, wb1 = int(pr.get("wb") or 0), int(post.get("wb") or 0)
    request, query, channel, on = "", "", "", ""
    if post and wb1 > wb0:
        ev = _new_writes(post, wb0)
        if ev:
            e = ev[-1]
            request, query, channel = f"{e.get('m')} {e.get('p')}", str(e.get("q") or ""), str(e.get("s") or "")
        else:
            request, query, channel = str(post.get("wl") or ""), str(post.get("wq") or ""), str(post.get("wk") or "")
        on = "choose" if max(wb1 - wb0, len(ev)) == 1 else "ambiguous"
    elif wb0 > 0:
        request, query, channel, on = str(pr.get("wl") or ""), str(pr.get("wq") or ""), str(pr.get("wk") or ""), "save"
    return {"dialog_title": str(dlg.get("t") or h.get("t") or "").strip(),
            "buttons": [str(b) for b in dlg.get("bt") or []],
            "choose_text": str(ch.get("label") or "").strip(),
            "request": request, "query": query, "channel": channel, "request_on": on, "blocked": max(wb0, wb1)}


def learned_status(rec):
    """("learned" | "unusable", reason) for a learned dict (same rules as flow_problem)."""
    flow = {"status": "learned", "dialog": rec["dialog_title"], "channel": rec["channel"],
            "request_on": rec["request_on"], "choose": rec["choose_text"], "request": rec["request"],
            "query": rec.get("query", "")}
    why = ("no prompt was open after Save" if not rec["dialog_title"] and not rec["buttons"]
           else "no safe answer was clicked" if not rec["choose_text"]
           else REASON_AMBIGUOUS if rec["request_on"] == "ambiguous"
           else "no request was seen" if not rec["request"] else flow_problem(flow))
    return ("unusable", why) if why else ("learned", "")


def _plan_record(plan, ticket, rid):
    for r in ((plan.get("tickets") or {}).get(ticket) or {}).get("records") or []:
        if r.get("id") == rid:
            return r
    return None


def _write(action, existing, rec, render, title):
    w = {"action": action, "collection": "demo", "note_id": (existing or {}).get("note_id", ""),
         "version": (existing or {}).get("version"), "title": title(rec), "content": render(rec)}
    return w


def cmd_learn(a):
    try:
        plan = _load(a.plan)
        flows = flow_index(load_dump(a.flows))
    except (OSError, ValueError) as e:
        emit({"ok": False, "action": "learn", "error": str(e)})
        return 1
    today = _today(a).isoformat()
    writes, lines, skipped = [], [], []
    for ticket, it in raw_items(a.raw):
        if it.get("mode") != DRY or it.get("write_error"):
            continue
        r = _plan_record(plan, ticket, str(it.get("record_id") or ""))
        if not r:
            skipped.append({"ticket": ticket, "visit_id": it["visit_id"], "why": "no such record in the plan"})
            continue
        L = learned_from(it)
        status, why = learned_status(L)
        key = flow_key(ticket, r["start"], r["path"], r["save"])
        rec = {"ticket": ticket, "key": key, "start": r["start"], "path": _json1(r["path"]), "save": r["save"],
               "dialog": L["dialog_title"], "buttons": _json1(L["buttons"]), "choose": L["choose_text"],
               "request": L["request"], "query": L["query"], "channel": L["channel"], "request_on": L["request_on"],
               "status": status, "reason": why, "learned": today, "run": a.run_id or ""}
        old = flows.get((ticket, key))
        writes.append(_write("update" if old else "create", old, rec, render_flow_note, flow_title))
        lines.append(f"- {ticket} · {r['id']} · prompt \"{L['dialog_title'] or '?'}\" · buttons {L['buttons']} · "
                     f"answer \"{L['choose_text'] or '?'}\" · request {L['request'] or '(none seen)'}{L['query']}"
                     f"{' via ' + L['channel'] if L['channel'] else ''} -> "
                     + ("learned: the next run can save for real" if status == "learned" else f"UNUSABLE ({why})"))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(writes, indent=1, ensure_ascii=False))
    for ln in lines:
        print(ln)
    n_ok = sum(1 for w in writes if "**Status:** learned" in w["content"])
    print(f"learn: {len(writes)} form flow note(s) ({n_ok} usable) -> {a.out}")
    emit({"ok": True, "action": "learn", "notes": len(writes), "usable": n_ok, "skipped": skipped,
          "collection": demo_collection_name(), "out": a.out})
    return 0


def delete_before(date_iso):
    try:
        return (_dt.date.fromisoformat(date_iso) - _dt.timedelta(days=DELETE_BEFORE_DAYS)).isoformat()
    except (TypeError, ValueError):
        return ""


def _status_of(created, post=None):
    """Note status from what the maker saw. `post` is the parsed post-choose probe ({} / None =
    none): its `saved` (the allowed request's response) and `al` (the allowed requests the guard
    logged) are the evidence; the maker's own `created.status` is used only when there is no probe.
      created     - a 2xx came back.
      not_saved   - a non-2xx came back, or the guard logged no allowed request and no response came
                    (the allowance was blocked / never armed / expired): nothing was stored, so the
                    record is not live, never reused, not listed for deletion.
      unverified  - an allowed request WAS logged (so it may have been stored) but no status came
                    back (none, or 0 = the request errored); or there is no probe at all."""
    saved = (post or {}).get("saved") if isinstance((post or {}).get("saved"), dict) else {}
    if post:
        s = str(saved.get("s") if saved.get("s") is not None else "").strip().lower()
    else:
        s = str((created or {}).get("status") or "").strip().lower()
    if re.fullmatch(r"2\d\d", s):
        return "created"
    if s not in ("", "0", "unknown", "none", "null"):        # "0": the request errored, no HTTP status
        return "not_saved"
    if post:
        return "unverified" if post.get("al") else "not_saved"
    return "unverified"


def cmd_records(a):
    try:
        plan = _load(a.plan)
        existing = demo_records(load_dump(a.records))
        calls = _load(a.calls) if a.calls else {}
        routes = _load(a.routes)
    except (OSError, ValueError) as e:
        emit({"ok": False, "action": "records", "error": str(e)})
        return 1
    base = (a.base_url or calls.get("base_url") or (routes.get("demo") or {}).get("base_url") or "").rstrip("/")
    run_id = a.run_id or calls.get("run_id") or ""
    today = _today(a).isoformat()
    writes, lines = [], []
    for ticket, it in raw_items(a.raw):
        if it.get("mode") != SAVE or not (it.get("created") or it.get("pending")):
            continue
        r = _plan_record(plan, ticket, str(it.get("record_id") or ""))
        if not r:
            continue
        cr = it.get("created") if isinstance(it.get("created"), dict) else {}
        post = _probe(it.get("post_probe")) if isinstance(it.get("post_probe"), (dict, str)) else {}
        saved = post.get("saved") if isinstance(post.get("saved"), dict) else {}
        status = _status_of(cr, post) if cr else "unverified"
        fp = str(cr.get("fingerprint") or it.get("pending") or r["fingerprint"])
        times = [f["value"] for f in r["fields"] if f["kind"] == "time"]
        date = next((f["value"][:10] for f in r["fields"] if f["kind"] == "date"), "")
        page = str(cr.get("page_url") or r["start"])
        rec = {"ticket": ticket, "what": r["what"], "fingerprint": fp, "link": base + page if page.startswith("/") else page,
               "date": date, "start_time": times[0] if times else "", "end_time": times[1] if len(times) > 1 else "",
               "venue": next((f"{f['label']}={f['value']}" for f in r["fields"] if f["kind"] == "select"), ""),
               "server_id": str(cr.get("id") or saved.get("id") or ""), "run": run_id, "status": status,
               "created": today, "delete_before": delete_before(date), "deleted": ""}
        old = next((x for x in existing if x["ticket"] == ticket and x["fingerprint"] == fp), None)
        writes.append(_write("update" if old else "create", old, rec, render_demo_note, demo_title))
        lines.append(f"- {ticket} · {r['id']} · {status.upper()} · {rec['what']} · {rec['link']} · "
                     f"{rec['date'] or '?'} · server id {rec['server_id'] or '?'} · delete before {rec['delete_before'] or '?'}")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(writes, indent=1, ensure_ascii=False))
    for ln in lines:
        print(ln)
    live = [w for w in writes if re.search(r"\*\*Status:\*\* (?:created|unverified)", w["content"])]
    print(f"records: {len(writes)} demo record note(s) ({len(live)} live) -> {a.out}. If the Mem write fails, "
          f"print these lines in chat and RUN-SUMMARY so the record is not lost.")
    emit({"ok": True, "action": "records", "notes": len(writes), "live": len(live), "lines": lines,
          "collection": demo_collection_name(), "out": a.out})
    return 0


# ── demo-data / demo-data-done ───────────────────────────────────────────────
def live_records(records):
    return [r for r in records if r["status"] in LIVE_STATUSES]


def cmd_demo_data(a):
    try:
        recs = live_records(demo_records(load_dump(a.records)))
    except (OSError, ValueError) as e:
        emit({"ok": False, "action": "demo-data", "error": str(e)})
        return 1
    rows = [{"ticket": r["ticket"], "what": r["what"], "link": r["link"], "date": r["date"],
             "server_id": r["server_id"], "delete_before": r["delete_before"], "status": r["status"],
             "fingerprint": r["fingerprint"], "note_id": r.get("note_id", ""), "run": r["run"]} for r in recs]
    rows.sort(key=lambda x: (x["delete_before"] or "9999", x["ticket"]))
    if not rows:
        print("demo data: no live demo records")
    else:
        print("| Ticket | What | Link | Date | Server id | Delete before | Status |")
        print("|---|---|---|---|---|---|---|")
        for x in rows:
            print(f"| {x['ticket']} | {x['what']} | {x['link']} | {x['date'] or '—'} | {x['server_id'] or '—'} | "
                  f"{x['delete_before'] or '—'} | {x['status']} |")
        print(f"{len(rows)} live demo record(s). Delete them on the demo site yourself, then "
              f"`demo-data-done --ids <note id|server id|fingerprint> ...`. Claude never deletes demo data.")
    emit({"ok": True, "action": "demo-data", "count": len(rows), "records": rows, "collection": demo_collection_name()})
    return 0


def cmd_demo_data_done(a):
    try:
        recs = demo_records(load_dump(a.records))
    except (OSError, ValueError) as e:
        emit({"ok": False, "action": "demo-data-done", "error": str(e)})
        return 1
    ids = {str(x).strip() for chunk in a.ids for x in re.split(r"[,\s]+", chunk) if x.strip()}
    today = _today(a).isoformat()
    writes, matched, unknown = [], set(), []
    for r in live_records(recs):
        keys = {r.get("note_id", ""), r["server_id"], r["fingerprint"], f"{r['ticket']}:{r['server_id']}"} - {""}
        hit = keys & ids
        if not hit:
            continue
        matched |= hit
        writes.append(_write("update", r, {**r, "status": "deleted", "deleted": today}, render_demo_note, demo_title))
    unknown = sorted(ids - matched)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(writes, indent=1, ensure_ascii=False))
    print(f"demo-data-done: {len(writes)} record(s) marked deleted -> {a.out}"
          + (f"; no live record matches: {', '.join(unknown)}" if unknown else ""))
    emit({"ok": not unknown, "action": "demo-data-done", "updated": len(writes), "unknown": unknown, "out": a.out})
    return 0 if not unknown else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("plan"); s.add_argument("--groups", required=True); s.add_argument("--calls", required=True)
    s.add_argument("--shots", required=True); s.add_argument("--data"); s.add_argument("--out", required=True)
    s.add_argument("--flows"); s.add_argument("--records"); s.add_argument("--today")
    s.add_argument("--period-end", dest="period_end")
    s = sub.add_parser("approve"); s.add_argument("--plan", required=True)
    s = sub.add_parser("brief"); s.add_argument("--plan", required=True)
    s.add_argument("--calls", required=True); s.add_argument("--out", required=True)
    s.add_argument("--routes", default=str(C.DEFAULT_ROUTES))
    s = sub.add_parser("learn"); s.add_argument("--raw", required=True); s.add_argument("--plan", required=True)
    s.add_argument("--out", required=True); s.add_argument("--flows"); s.add_argument("--run-id", dest="run_id")
    s.add_argument("--today")
    s = sub.add_parser("records"); s.add_argument("--raw", required=True); s.add_argument("--plan", required=True)
    s.add_argument("--out", required=True); s.add_argument("--records"); s.add_argument("--calls")
    s.add_argument("--run-id", dest="run_id"); s.add_argument("--base-url", dest="base_url")
    s.add_argument("--routes", default=str(C.DEFAULT_ROUTES)); s.add_argument("--today")
    s = sub.add_parser("demo-data"); s.add_argument("--records", required=True)
    s = sub.add_parser("demo-data-done"); s.add_argument("--records", required=True)
    s.add_argument("--ids", nargs="+", required=True); s.add_argument("--out", required=True)
    s.add_argument("--today")
    a = ap.parse_args(argv)
    return {"plan": cmd_plan, "approve": cmd_approve, "brief": cmd_brief, "learn": cmd_learn,
            "records": cmd_records, "demo-data": cmd_demo_data, "demo-data-done": cmd_demo_data_done}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
