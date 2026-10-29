#!/usr/bin/env python3
"""
kb_data.py — demo FORM FILLS a KB screenshot needs, planned, checked and approved
once per run, then driven by the kb-data-maker agent under guardrails.

Some screens only exist after a form is filled and Save is pressed (a schedule,
an assignment): pressing Save opens a prompt ("Notify Participants?"). In 0.27.0
NOTHING IS EVER STORED on the shared demo tenant: the capture page blocks every
non-GET request, so the Save request is blocked, the prompt is captured, and the
data maker then clicks the cancel-type choice. Approval is asked every run (named
ids and --yes never skip it); "no" leaves those screens as needs_write.

  python3 kb_data.py bundle  --groups groups.json --data data.json --out $RUN/data_planner.md
          One brief for ONE kb-data-planner call: per ticket whose capture entry
          is write-gated or whose QA checklist has "Required Test Data", the
          checklist's Required Test Data / Direct Actions / UI path and the route.
          No such ticket: no file, no planner call.
  python3 kb_data.py check   --draft $RUN/data_plan_draft.json --out $RUN/data_plan.json [--today YYYY-MM-DD]
          [--period-end YYYY-MM-DD]
          Validates the planner's draft (choose must be cancel / close / no /
          don't / do not / skip; forbidden words in save / choose / path; save
          text; no personal data; far-future dates), drops records that fail,
          prints one preview line per form fill, writes {"approved": false, ...}.
  python3 kb_data.py approve --plan $RUN/data_plan.json
          Sets approved true and stores the sha256 of the canonical plan. Run only
          after the owner says yes. Refused (exit 2) when the plan has no
          checked_sha256 (`check` writes it) or it no longer matches the plan.
          kb_capture.py plan-calls --data-plan also needs approved true and a
          matching approved_sha256.
  python3 kb_data.py brief   --plan $RUN/data_plan.json --calls $RUN/shots/calls.json --out $RUN/shots/data.md
          The kb-data-maker's instructions (approved, unchanged plan only; a plan
          edited after approval is refused): per form fill the ready-to-send
          kbclick / kbopt / probe calls, field fill scripts, the write visit id
          and GIF name.

Prints one human line, then:

    ---KB_DATA_RESULT---
    {"ok", "action", ...}
"""

import argparse
import datetime as _dt
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import kb_capture as C                  # noqa: E402  (stub / install format, EXPORT_OPTS)

PREFIX = "ZZ KB DEMO"
DASH = "–"
PERSONAS = ("admin", "faculty")                 # one shared sign-in; the learner never fills forms
SAVE_TEXTS = ("save", "create", "add", "submit")
MAX_RECORDS = 3                                 # per ticket
FORBIDDEN_RE = re.compile(r"\b(?:invite|publish|approve|email|delete|remove|archive)", re.I)
SEND_RE = re.compile(r"\b(?:send|notify)\b", re.I)
NEGATED_RE = re.compile(C.CLICK_RX["neg"], re.I)           # don't / do not / not just before (a bare "no" does not negate)
NAME_LABEL_RE = re.compile(r"\b(?:name|title|subject)\b", re.I)
PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{8,}\d)(?!\d)")
DATE_TOKEN_RE = re.compile(r"<\s*YYYY-MM-DD\s+(?:far future|next weekday)\s*>", re.I)
ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
CHOOSE_START_RE = re.compile(r"^(?:(?:cancel|close|skip)\b|don['’]?t\b|do not\b|no[,:]?\s+(?:don['’]?t|do not)\b)", re.I)
CHOOSE_NO_RE = re.compile(r"^no[.!]?$", re.I)                # a bare "No" counts only as the whole text
CHOOSE_BAD_RE = re.compile(r"\b(?:yes|save|submit|confirm|continue|ok|proceed)", re.I)
MIN_DAYS_AHEAD = 14                             # a typed date is never "tomorrow"
FALLBACK_DAYS = 180                             # period unknown: today + 180 days
PICK_RULE = "first option starting 'ZZ DEMO'"
NOT_STORED = "nothing is stored on the demo site"
FILL_WORD = "form fill (nothing is saved — the save request is blocked)"
TICKET_RE = re.compile(r"^[A-Z][A-Z0-9]*-\d+$")
BRIEF_CAP = 3200                                # chars per ticket in the planner brief
GUARD_TEXT = "ZZ DEMO"                          # visible on an existing demo record's page
OPTION_SLOT = "__OPTION__"                      # the one edit the maker makes: the exact option text


def emit(payload):
    print("---KB_DATA_RESULT---")
    print(json.dumps(payload))


def _load(path):
    return json.loads(Path(path).read_text())


def _clip(s, n):
    s = re.sub(r"[ \t]+", " ", s or "").strip()
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


# ── bundle ────────────────────────────────────────────────────────────────────
def section(text, name):
    """Lines under a checklist heading ("Required Test Data"), up to the blank line."""
    lines = (text or "").splitlines()
    for i, ln in enumerate(lines):
        if ln.strip(" :*#-").lower() == name.lower():
            out = []
            for nxt in lines[i + 1:]:
                if not nxt.strip():
                    if out:
                        break
                    continue
                out.append(nxt.rstrip())
            return out
    return []


def checklist_extract(text):
    """The parts of a QA checklist the planner needs, compact."""
    lines = (text or "").splitlines()
    ui = [ln.strip() for ln in lines if re.search(r"\bUI path\b", ln, re.I)][:2]
    actions = [ln.strip() for ln in lines
               if re.match(r"^\s*Action\s*:", ln, re.I) and re.search(r"\b(?:save|add|create)\b", ln, re.I)][:3]
    prompts = [ln.strip() for ln in lines
               if re.search(r"\bprompt", ln, re.I) and not re.match(r"^\s*(?:Action|Expected)\s*:", ln, re.I)][:3]
    return {"required": section(text, "Required Test Data"), "direct": section(text, "Direct Actions"),
            "ui_path": ui, "actions": actions, "prompts": prompts}


def needing(plan, issues):
    """{ticket: {"title", "entries": [...], "extract": {...}}} for tickets that need
    a record: a capture entry with write_gated text, or a checklist listing
    Required Test Data. Only tickets with a capturable entry (a URL, not skipped)."""
    by_id = {i.get("identifier"): i for i in issues}
    out = {}
    for c in plan.get("capture_plan") or []:
        if not c.get("url") or c.get("skip_reason"):
            continue
        for t in c.get("tickets") or []:
            issue = by_id.get(t) or {}
            ex = checklist_extract((issue.get("qa_checklist") or {}).get("text") or "")
            if not (c.get("write_gated") or ex["required"]):
                continue
            rec = out.setdefault(t, {"title": issue.get("title", ""), "entries": [], "extract": ex})
            rec["entries"].append({"persona": c.get("persona", ""), "url": c["url"],
                                   "clicks": c.get("clicks") or [], "write_gated": c.get("write_gated") or ""})
    return out


def _ticket_block(tid, rec):
    ex = rec["extract"]
    lines = [f"=== TICKET {tid} ===", f"title: {rec['title']}"]
    for e in rec["entries"]:
        clicks = " > ".join("[row]" if k.get("row") else k.get("text", "") if isinstance(k, dict) else str(k)
                            for k in e["clicks"]) or "(none)"
        lines.append(f"route: persona={e['persona']} start={e['url']} clicks={clicks}")
        if e["write_gated"]:
            lines.append(f"write-gated step (needs a saved record): {e['write_gated']}")
    for head, key in (("Required Test Data", "required"), ("Direct Actions", "direct")):
        if ex[key]:
            lines += [f"{head}:"] + ex[key]
    for head, key in (("UI path", "ui_path"), ("Save steps", "actions"), ("Prompts", "prompts")):
        if ex[key]:
            lines += [f"{head}:"] + ex[key]
    block = "\n".join(lines)
    return _clip(block.replace("\n", "↵"), BRIEF_CAP).replace("↵", "\n")


def planner_md(tickets, draft_path):
    head = [
        "# Demo data the KB screenshots need",
        "",
        "Plan the demo records to create so each ticket's write step can be captured.",
        f"Read your agent instructions for the rules and the JSON shape. Write `{draft_path}` and nothing else.",
        "Tickets below: " + ", ".join(tickets) + ".",
        "",
    ]
    return "\n".join(head)


def cmd_bundle(a):
    plan, data = _load(a.groups), _load(a.data)
    need = needing(plan, data.get("issues") or [])
    if not need:
        print("data: no ticket needs demo records (no planner call)")
        emit({"ok": True, "action": "bundle", "tickets": 0, "out": ""})
        return 0
    out = Path(a.out).resolve()
    draft = out.parent / "data_plan_draft.json"
    body = planner_md(list(need), draft) + "\n" + "\n\n".join(_ticket_block(t, r) for t, r in need.items()) + "\n"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body)
    print(f"data: {len(need)} ticket(s) may need demo records -> {out} (planner writes {draft})")
    emit({"ok": True, "action": "bundle", "tickets": len(need), "ticket_ids": list(need),
          "out": str(out), "draft": str(draft)})
    return 0


# ── check ─────────────────────────────────────────────────────────────────────
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
    t = " ".join(choose.split())
    if not (CHOOSE_NO_RE.match(t) or CHOOSE_START_RE.match(t)):
        return "choose must be a cancel-type answer (Cancel…, Close…, Skip…, Don't…, Do not…, \"No, don't…\" or exactly \"No\")"
    m = CHOOSE_BAD_RE.search(t)
    word = m.group(0).lower() if m else forbidden_in(t)
    return f"choose \"{choose}\" contains \"{word}\"" if word else ""


def personal_data(value):
    v = value or ""
    if "@" in v:
        return "an email address"
    if PHONE_RE.search(v) and not ISO_DATE_RE.fullmatch(v.strip()) and not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?", v.strip()):
        return "a phone-like number"
    return ""


def _norm_name(name, ticket):
    prefix = f"{PREFIX} {DASH} {ticket}"
    n = (name or "").strip()
    n = re.sub(r"^ZZ KB DEMO\s*[-–—]\s*", f"{PREFIX} {DASH} ", n)
    return n if n.startswith(prefix) else None


def check_record(ticket, rec, today, period_end=None):
    """(clean record | None, problem text). A record is a FORM FILL: nothing is
    saved; the Save request is blocked by the capture page."""
    if not isinstance(rec, dict):
        return None, "record is not an object"
    rid = str(rec.get("id") or "").strip()
    what = str(rec.get("what") or "").strip()
    if not rid or not what:
        return None, "needs an id and a 'what'"
    if rec.get("persona") not in PERSONAS:
        return None, "persona must be admin or faculty"
    start = str(rec.get("start") or "")
    if not start.startswith("/") or "://" in start or start.startswith("//"):
        return None, "start must be a path on the demo site"
    word = forbidden_in(start) or (C._CLICK_RE["attr"].search(start) or [""])[0].strip("/?&=_- ").lower()
    if word:
        return None, f"start \"{start}\" contains \"{word}\""
    name = ""
    if str(rec.get("name") or "").strip():
        name = _norm_name(rec.get("name"), ticket)
        if not name:
            return None, f"name must start \"{PREFIX} {DASH} {ticket}\" (or be left out)"
    path = []
    for k in rec.get("path") or []:
        if isinstance(k, dict) and k.get("row") is True:
            path.append({"row": True})
        elif isinstance(k, dict) and isinstance(k.get("text"), str) and k["text"].strip():
            word = forbidden_in(k["text"])
            if word:
                return None, f"path click \"{k['text']}\" contains \"{word}\""
            path.append({"text": k["text"].strip()})
        else:
            return None, "path items are {\"row\": true} or {\"text\": ...}"
    fields = []
    for f in rec.get("fields") or []:
        if not (isinstance(f, dict) and str(f.get("label") or "").strip()):
            return None, "each field needs a label and a value"
        label = str(f["label"]).strip()
        if f.get("pick"):
            fields.append({"label": label, "pick": PICK_RULE})      # never an invented name
            continue
        value = DATE_TOKEN_RE.sub(far_date(today, period_end).isoformat(), str(f.get("value", "")))
        if "<" in value and ">" in value:
            return None, f"field \"{label}\" has an unresolved placeholder"
        why = personal_data(value)
        if why:
            return None, f"field \"{label}\" looks like {why}"
        if ISO_DATE_RE.fullmatch(value.strip()):
            try:
                if _dt.date.fromisoformat(value.strip()) < today + _dt.timedelta(days=MIN_DAYS_AHEAD):
                    return None, f"field \"{label}\" date is too soon (use the far-future date)"
            except ValueError:
                return None, f"field \"{label}\" is not a valid date"
        if NAME_LABEL_RE.search(label) and not _norm_name(value, ticket):
            return None, f"field \"{label}\" must start \"{PREFIX} {DASH} {ticket}\""
        fields.append({"label": label, "value": _norm_name(value, ticket) or value})
    save = str(rec.get("save") or "").strip()
    if save.lower() not in SAVE_TEXTS:
        return None, "save must be one of Save, Create, Add, Submit"
    out = {"id": rid, "what": what, "persona": rec["persona"], "start": start, "path": path,
           "fields": fields, "save": save}
    if name:
        out["name"] = name
    after = rec.get("after")
    if after:
        if not isinstance(after, dict):
            return None, "after must be an object"
        expect, choose = str(after.get("expect") or "").strip(), str(after.get("choose") or "").strip()
        if not expect or not choose:
            return None, "after needs expect and choose"
        why = choose_problem(choose)
        if why:
            return None, why
        labels = [str(x) for x in after.get("labels") or [expect, choose] if str(x).strip()]
        out["after"] = {"expect": expect, "choose": choose, "labels": labels}
    return out, ""


def check_draft(draft, today, period_end=None):
    """({ticket: [clean records]}, [{ticket, id, why}])."""
    tickets, rejected = {}, []
    for tid, body in (draft.get("tickets") or {}).items():
        if not TICKET_RE.match(str(tid)):
            rejected.append({"ticket": str(tid), "id": "", "why": "not a ticket id"})
            continue
        recs = (body or {}).get("records") if isinstance(body, dict) else None
        for n, rec in enumerate(recs or []):
            if len(tickets.get(tid, [])) >= MAX_RECORDS:
                rejected.append({"ticket": tid, "id": str((rec or {}).get("id", "")),
                                 "why": f"more than {MAX_RECORDS} records for one ticket"})
                continue
            clean, why = check_record(tid, rec, today, period_end)
            if clean:
                tickets.setdefault(tid, []).append(clean)
            else:
                rejected.append({"ticket": tid, "id": str((rec or {}).get("id", n)) if isinstance(rec, dict) else str(n),
                                 "why": why})
    return tickets, rejected


def _fvalue(f):
    return f"{f['label']}=" + (f["pick"] if f.get("pick") else f["value"])


def preview_line(tid, r):
    """One owner-facing line per form fill."""
    where = r["start"] + "".join(" → " + ("first row" if k.get("row") else f"\"{k['text']}\"") for k in r["path"])
    fields = "; ".join(_fvalue(f) for f in r["fields"]) or "no fields"
    tail = f"presses \"{r['save']}\" — the save request is blocked"
    if r.get("after"):
        tail += f"; then clicks \"{r['after']['choose']}\" (prompt \"{r['after']['expect']}\")"
    return f"- {tid} · {r['id']} · {r['persona']} · {FILL_WORD}: {where} · fields: {fields} · {tail} · {NOT_STORED}."


def preview_text(tickets):
    lines = [preview_line(tid, r) for tid, recs in tickets.items() for r in recs]
    dates = sorted({f["value"] for recs in tickets.values() for r in recs for f in r["fields"]
                    if ISO_DATE_RE.fullmatch(f.get("value", ""))})
    if dates:
        lines.append("Dates: " + ", ".join(dates) + " (far-future weekday in the current period, never tomorrow).")
    return "\n".join(lines)


def canonical(plan):
    """The approved content: the tickets only (approval flags and the rejected
    list are not part of what the owner agreed to)."""
    return json.dumps({"tickets": plan.get("tickets") or {}}, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))


def plan_hash(plan):
    return hashlib.sha256(canonical(plan).encode("utf-8")).hexdigest()


def cmd_check(a):
    try:
        draft = _load(a.draft)
    except (OSError, json.JSONDecodeError) as e:
        print(f"data: cannot read the draft: {e}")
        emit({"ok": False, "action": "check", "error": str(e)})
        return 1
    today = _dt.date.fromisoformat(a.today) if a.today else _dt.date.today()
    period_end = _dt.date.fromisoformat(a.period_end) if a.period_end else None
    tickets, rejected = check_draft(draft, today, period_end)
    plan = {"approved": False, "tickets": {t: {"records": r} for t, r in tickets.items()}, "rejected": rejected}
    plan["checked_sha256"] = plan_hash(plan)          # only what `check` produced can be approved
    Path(a.out).write_text(json.dumps(plan, indent=1, ensure_ascii=False))
    n = sum(len(r) for r in tickets.values())
    if n:
        print(preview_text(tickets))
    for r in rejected:
        print(f"rejected {r['ticket']} {r['id']}: {r['why']}")
    print(f"data plan: {n} form fill(s) for {len(tickets)} ticket(s), {len(rejected)} rejected, "
          f"not approved -> {a.out}. Ask the owner every run; if they say no, those screens stay needs_write.")
    emit({"ok": True, "action": "check", "records": n, "tickets": len(tickets), "rejected": rejected,
          "approved": False, "always_ask": True, "out": a.out})
    return 0


def cmd_approve(a):
    try:
        plan = _load(a.plan)
    except (OSError, json.JSONDecodeError) as e:
        emit({"ok": False, "action": "approve", "error": str(e)})
        return 1
    if not plan.get("checked_sha256") or plan["checked_sha256"] != plan_hash(plan):
        print("data approve: the plan was not produced by `kb_data.py check`, or was edited after it; "
              "re-run check and show the owner the preview")
        emit({"ok": False, "action": "approve",
              "error": "plan_not_checked" if not plan.get("checked_sha256") else "plan_changed"})
        return 2
    plan["approved"] = True
    plan["approved_sha256"] = plan_hash(plan)
    Path(a.plan).write_text(json.dumps(plan, indent=1, ensure_ascii=False))
    n = sum(len(t.get("records") or []) for t in (plan.get("tickets") or {}).values())
    print(f"data plan approved: {n} form fill(s) (nothing is stored)")
    emit({"ok": True, "action": "approve", "records": n, "approved": True, "sha256": plan["approved_sha256"]})
    return 0


# ── brief ─────────────────────────────────────────────────────────────────────
def _args_for(calls, persona):
    """Probe args of the first visit of this persona (else any visit)."""
    visits = calls.get("visits") or []
    for v in visits:
        if v.get("persona") == persona:
            return v["args"]
    return visits[0]["args"] if visits else None


def click_json(side, text=None, row=False, write=None, pa=None):
    """kbclick args — same shape kb_capture builds for a click visit's click_args."""
    args = {"side": side}
    if row:
        args["row"] = True
        args["pa"] = {**(pa or {}), "labels": []}
    else:
        args["text"] = text
    if write:
        args["write"] = list(write)
    return args


_FIELD_JS = r"""(function(a){
__GUARD__
var vis=function(e){var r=e.getBoundingClientRect();return r.width>0&&r.height>0&&getComputedStyle(e).visibility!=='hidden';};
var dl=[].slice.call(document.querySelectorAll('.modal.show,.modal[style*="display: block"],[role=dialog],.swal2-popup')).filter(vis);
var root=dl.length?dl[dl.length-1]:document;
var norm=function(s){return (s||'').replace(/\s+/g,' ').replace(/[\s*:]+$/,'').trim().toLowerCase();};
var want=norm(a.label),el=null;
var labs=[].slice.call(root.querySelectorAll('label,.form-label,legend,th,dt')).filter(function(l){return vis(l)&&norm(l.textContent)===want;});
for(var i=0;i<labs.length&&!el;i++){var l=labs[i],f=l.control||(l.htmlFor&&document.getElementById(l.htmlFor));
 if(!f){var p=l.parentElement;for(var k=0;k<3&&p&&!f;k++,p=p.parentElement){var c=p.querySelectorAll('input,textarea,select');if(c.length===1)f=c[0];}}
 if(f&&vis(f))el=f;}
if(!el)throw new Error('KB_FIELD_NOTFOUND:'+a.label);
if(/^(password|file|hidden)$/i.test(el.type||''))throw new Error('KB_FIELD_REFUSED:'+a.label);
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
FIELD_JS = _FIELD_JS.replace("__GUARD__", C.guard_source())     # the write guard first, like every page script


def field_js(label, value=None, mode="set"):
    """Vue-commit fill (QA engineering-gaps #7): set the value, dispatch input +
    change + blur, re-read and compare; throws KB_FIELD_NOSTICK when it did not stick."""
    args = {"label": label, "mode": mode}
    if mode == "set":
        args["value"] = value
    return f"{C.JS_MARKER}{FIELD_JS}({json.dumps(args, ensure_ascii=False)})"


def field_block(f, side):
    """How the maker fills one field: text (fill_js), date (type, Tab, read_js) or
    pick (open the control, optional search, kbopt)."""
    if f.get("pick"):
        return {"label": f["label"], "kind": "pick", "rule": f["pick"], "search": GUARD_TEXT,
                "open_js": C.stub(click_json(side, f["label"]), C.KBC_KEY),
                "option_js": C.stub({"side": side, "option": OPTION_SLOT}, C.KBC_KEY),
                "option_slot": OPTION_SLOT}
    if ISO_DATE_RE.fullmatch(f["value"]):
        return {"label": f["label"], "kind": "date", "value": f["value"],
                "read_js": field_js(f["label"], mode="read")}
    return {"label": f["label"], "kind": "text", "value": f["value"],
            "fill_js": field_js(f["label"], f["value"])}


def record_block(ticket, rec, visit, args, portal_entry=""):
    side = args.get("side", "")
    pa = args
    after = rec.get("after")
    write = [rec["save"]] + ([after["choose"]] if after else [])
    vl = visit.get("labels")
    probe_labels = (after or {}).get("labels") or (vl if isinstance(vl, list) else None) or [rec["save"]]
    block = {
        "ticket": ticket, "record_id": rec["id"], "what": rec["what"],
        "visit_id": visit["visit_id"], "gif": visit["gif"],
        "navigate": rec["start"],
        "guard_js": C.stub({**args, "labels": []}),
        "path_picks": [{"click": k, "js": C.stub(click_json(side, k.get("text"), bool(k.get("row")), pa=pa), C.KBC_KEY)}
                       for k in rec["path"]],
        "needs_demo_context": any(k.get("row") for k in rec["path"]),
        "fields": [field_block(f, side) for f in rec["fields"]],
        "pre_save_probe_js": C.stub({**args, "labels": [rec["save"]]}),
        "save_pick": {"text": rec["save"],
                      "js": C.stub(click_json(side, rec["save"], write=write), C.KBC_KEY)},
        "probe_js": C.stub({**args, "labels": probe_labels}),
        "export": C._export(visit["gif"]),
        "choose_pick": ({"text": after["choose"], "expect": after["expect"],
                         "js": C.stub(click_json(side, after["choose"], write=write), C.KBC_KEY)}
                        if after else None),
    }
    if rec.get("name"):
        block["name"] = rec["name"]
    if portal_entry:                      # a non-admin record first switches to its own portal
        block["portal_entry"] = portal_entry
    return block


HEAD = """# Demo form fills to make

You fill the forms listed below on the shared demo tenant, one form at a time,
exactly as written, press Save, record what comes back, and cancel. NOTHING IS
EVER STORED: the capture page blocks every non-GET request, so the Save request
never leaves the browser. You do not judge pages. A plugin hook blocks every key
press except `Tab` and `Escape`, every drag, any typed text holding a newline or
tab, and any `javascript_tool` text not starting with `/*KB*/` for you.

Folders: the run folder is given to you; raw files go in `<run folder>/shots/raw/`.
Read and write only inside the run folder; never use /tmp. Budget: about 25 tool
uses per form fill; prefer `find` over `read_page`.

## Hard rules (stop the form fill on any surprise)

- Fill ONLY the forms below. Never edit, delete or touch an existing record,
  except opening an existing "ZZ DEMO" session / group / venue through the listed path.
- Type only the listed field values (and the search text `ZZ DEMO` in a pick
  field). Never a personal name, email, phone number or password. Never press
  Enter (Save is pressed through kbclick). The only keys you may press are `Tab`
  (after typing a date) and `Escape` (only to close a dialog when the `choose`
  click throws).
- Every `javascript_tool` text starts with `/*KB*/` (the scripts below all do); a
  hook denies any other. Only `navigate` to the demo site (a path, or the base URL +
  a path); never to a delete / remove / logout URL.
- Click only through the installed `kbclick` / `kbopt` (the `js` below, then
  `left_click` at the `x`,`y` it returns). Never click anywhere else. The `choose`
  text below is the only prompt answer you may click, and it is a cancel-type
  answer (Cancel, Close, No…, Don't…, Do not…, Skip…). Never click Yes, OK,
  Continue, Confirm, or any send / notify / invite / publish / approve / email option.
- If a `kbclick` / `kbopt` call throws (`KB_CLICK_REFUSED`, `KB_CLICK_NOTFOUND`,
  `KB_TENANT_ROW`), a probe contains `KB_LOGIN`, `KB_PERSONA` or `KB_NOPROBE`, a
  field script throws (`KB_FIELD_NOTFOUND`, `KB_FIELD_NOSTICK`, `KB_FIELD_NOOPTION`,
  `KB_FIELD_REFUSED`), or the page looks different from the plan (an error banner,
  a different record): `gif_creator` `clear`, write the item with `write_error`
  (the error text), skip the rest of that form fill, carry on with the next one.
  `KB_LOGIN` ends the run: write the item, then stop.

## Install (once per tab)

Send this `javascript_tool` action once before the first form fill (replace
`"$TAB"` with the numeric tab id):

```json
{install}
```

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
4. Fill each entry of `fields`, in order, by its `kind`:
   - `text`: run `fill_js` with `javascript_tool`. It sets the value, dispatches
     input + change + blur, re-reads the value and throws `KB_FIELD_NOSTICK` if it
     did not stick (Vue forms keep a separate model; setting only the DOM value
     would be lost). Do not use `form_input` for these.
   - `date`: `find` the input by its label, `left_click` it, read its placeholder
     (`read_js`), `computer` `type` the date in the placeholder's format (for
     example `2026-12-31` as `12/31/2026` when the placeholder says MM/DD/YYYY),
     press `Tab`, then run `read_js`: the date it shows must equal what you typed
     (same day); if not, `KB_FIELD_NOSTICK`: stop the form fill.
   - `pick` (audience, venue, group…): run `open_js` (the control's label), click at
     its `x`,`y`; if a search box opened `type` the `search` text; then `find`
     the visible options and take the FIRST whose exact text starts with
     "ZZ DEMO". If none, stop the form fill (`KB_FIELD_NOOPTION`). Put that exact
     option text in place of `option_slot` inside `option_js` (this is the only
     edit you may make to any JavaScript), run it, click at its `x`,`y`, `wait` 1.
   Never touch any other field.
5. Run `pre_save_probe_js`. It must contain the Save text and `wb` equal to 0 or
   missing; if `wb` is above 0 something already tried to write: stop the form fill.
6. `gif_creator` `start_recording`.
7. Run `save_pick.js` (write mode: it allows exactly the Save text and the
   cancel-type answer, inside the open dialog), `left_click` at its `x`,`y`,
   `wait` 3 seconds. The save request is blocked by the page; a prompt may open.
8. Run `probe_js` with `javascript_tool`. Keep its result verbatim. Its `wb` is the
   number of blocked writes (1 or more means the Save was attempted and blocked);
   its `h` is the dialog title. If the expected prompt is not shown (no label
   found and no `h`) and `wb` is 0, Save did nothing: `write_error` "save_did_nothing".
9. `gif_creator` `stop_recording`, then the `export` action (replace `"$TAB"`). The
   `gif` is the filename in the `Downloaded "…"` text of the export result.
10. If `choose_pick` is not null: run its `js`, `left_click` at its `x`,`y`, `wait`
    2 seconds. If it throws, press `Escape` once, write `write_error` with the
    error and stop the form fill (click nothing else).
11. Append the item and Write the file (below) after EVERY form fill.

## Raw file

Write `<run folder>/shots/raw/data-<ticket>.json` for each ticket (all its form fills):

```json
{{"batch_id": "data-<ticket>", "status": "ok", "error": null,
 "items": [{{"visit_id": "<visit_id>", "probe": <probe result verbatim>, "gif": "<filename>", "blocked": <wb>}},
           {{"visit_id": "<visit_id>", "write_error": "<error text>"}}]}}
```

`status` is `ok`, or `expired` after `KB_LOGIN`, or `bridge_down` after a tool or
transport error or a call denied by a hook naming `KB_GUARD` (do not retry it).
Reply with one line: `KB_DATA_DONE ok=<form fills done> failed=<form fills with write_error>`.
"""


def cmd_brief(a):
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
        print("data brief: the plan changed after it was approved; check and ask the owner again")
        emit({"ok": False, "action": "brief", "error": "plan_changed"})
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
    install = json.dumps(calls.get("install") or C.install_action(), indent=1)
    md = HEAD.replace("{{", "{").replace("}}", "}").replace("{install}", install)
    admin_args = _args_for(calls, "admin")
    setup = {"navigate": admin_entry,
             "probe_js": C.stub({**admin_args, "labels": []}) if admin_args else ""}
    md += (f"\nBase URL: `{calls.get('base_url', '')}`\n\n## Start (before the first form fill)\n\n"
           "Form fills run on the Admin portal: `navigate` to the base URL + `navigate` below, "
           "`computer` `wait` 4 seconds, then run `probe_js` (it throws `KB_PERSONA` or `KB_LOGIN` "
           "if this is not the signed-in Admin portal: stop and write `bridge_down`).\n\n"
           f"```json\n{json.dumps(setup, indent=1)}\n```\n\n## Form fills\n")
    for b in blocks:
        md += f"\n### {b['ticket']} / {b['record_id']}: {b['what']}\n\n```json\n{json.dumps(b, indent=1, ensure_ascii=False)}\n```\n"
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md)
    print(f"data brief: {len(blocks)} form fill(s), {len(missing)} without a write visit "
          f"({len(mismatched)} start_mismatch) -> {out}")
    emit({"ok": True, "action": "brief", "records": len(blocks), "missing": missing,
          "start_mismatch": mismatched, "out": str(out)})
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("bundle"); s.add_argument("--groups", required=True)
    s.add_argument("--data", required=True); s.add_argument("--out", required=True)
    s = sub.add_parser("check"); s.add_argument("--draft", required=True)
    s.add_argument("--out", required=True); s.add_argument("--today")
    s.add_argument("--period-end", dest="period_end")
    s = sub.add_parser("approve"); s.add_argument("--plan", required=True)
    s = sub.add_parser("brief"); s.add_argument("--plan", required=True)
    s.add_argument("--calls", required=True); s.add_argument("--out", required=True)
    s.add_argument("--routes", default=str(C.DEFAULT_ROUTES))
    a = ap.parse_args(argv)
    return {"bundle": cmd_bundle, "check": cmd_check, "approve": cmd_approve, "brief": cmd_brief}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
