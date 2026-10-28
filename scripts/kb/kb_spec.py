#!/usr/bin/env python3
"""
kb_spec.py — between capture and render: the writer's input, its output, and
the spec the gates judge.

  bundle        groups.json + inputs + shots + priors -> one writer_<group>.md
                per writer group: ticket data, prior article titles, and the
                VERIFIED shots as text (labels, never images).
  ingest        A writer reply that returned specs between the markers instead
                of writing files -> prose/<ticket>.json.
  assemble      prose + shot evidence -> specs/<ticket>.json, with annotated
                screenshots. Evidence failures (G3 anchor/score, G4, G5) are
                fixed here by dropping the image to a missing_shot marker —
                never by a model. Prose failures (G2, G6, a ringed label not
                printed in bold) are listed in failures.json for a retry.
  retry-bundle  failures.json -> one retry_<ticket>.md per failing article.

Writer contract (one JSON object per ticket, prose/<ticket>.json):

    {"ticket", "title", "steps_heading", "overview",
     "steps": [{"text", "shot": "s3" | null | "missing", "caption"}],
     "considerations": ["**Lead-in.** …"], "conclusion", "notes": []}

`shot: "missing"` means a screenshot would help but none was available; the
draft prints a marker there.

    ---KB_SPEC_RESULT---
    {"ok", "action", ...counts..., "error"}
"""

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import kb_format as F                   # noqa: E402
import kb_validate as V                 # noqa: E402

DEFAULT_ROUTES = HERE / "kb_routes.json"
START, END = "---KB_SPECS_START---", "---KB_SPECS_END---"
PROSE_GATES = ("G2", "G6")
BOLD_FAIL = "not printed in bold"


def emit(payload):
    print("---KB_SPEC_RESULT---")
    print(json.dumps(payload))


def _load(path, default=None):
    p = Path(path) if path else None
    if not p or not p.exists():
        return default
    return json.loads(p.read_text())


def shots_by_ticket(shots):
    out = {}
    for s in (shots or {}).get("shots", []):
        out.setdefault(s["ticket"], []).append(s)
    return out


# ── bundle ───────────────────────────────────────────────────────────────────
def writer_bundle(group, inputs, shots, priors, uncapturable):
    L = [f"GROUP {group['group_id']} · Persona: {group['persona'].title()} · "
         f"Portal: {group['portal']} · Audience: {group['audience']}", ""]
    for tid in group["tickets"]:
        i = inputs.get(tid) or {"title": "", "ticket": tid}
        L += [f"=== TICKET {tid} ===", f"Title: {i.get('title', '')}"]
        if i.get("client_title"):
            L.append(f"Client title (from release): {i['client_title']}")
        if i.get("client_summary"):
            L.append(f"Client summary (from release): {i['client_summary']}")
        L.append(f"Description (trimmed): {i.get('description') or '—'}")
        if i.get("acceptance_criteria"):
            L.append(f"Acceptance criteria: {i['acceptance_criteria']}")
        if i.get("ui_strings"):
            L.append("UI strings: " + ", ".join(f'"{s}"' for s in i["ui_strings"]))
        if i.get("routes"):
            L.append("Routes: " + ", ".join(i["routes"]))
        pri = priors.get(tid) or []
        if pri:
            L.append("Prior articles (list only, do not merge): " +
                     "; ".join(f"{p['id']} {p['title']}" for p in pri))
        mine = [s for s in shots.get(tid, []) if s.get("verified")]
        if mine:
            L.append("Shots you may use:")
            for s in mine:
                L.append(f"  {s['shot_id']} · label \"{s['label']}\" · {s['surface']} · {s['tenant']}")
        else:
            why = uncapturable.get(tid) or "nothing verified on screen"
            L.append(f"Shots you may use: none ({why}) — use \"shot\": \"missing\" where one would help")
        L.append("")
    return "\n".join(L)


# ── ingest ───────────────────────────────────────────────────────────────────
def parse_writer_reply(text):
    """Every JSON object between the markers (one per ticket, or a list)."""
    out = []
    for block in re.findall(re.escape(START) + r"(.*?)" + re.escape(END), text or "", re.S):
        block = block.strip()
        try:
            obj = json.loads(block)
            out += obj if isinstance(obj, list) else [obj]
            continue
        except json.JSONDecodeError:
            pass
        dec, pos = json.JSONDecoder(), 0
        while pos < len(block):
            m = re.compile(r"\S").search(block, pos)
            if not m:
                break
            try:
                obj, pos = dec.raw_decode(block, m.start())
                out.append(obj)
            except json.JSONDecodeError:
                break
    return [o for o in out if isinstance(o, dict) and o.get("ticket")]


# ── assemble ─────────────────────────────────────────────────────────────────
def label_as_written(step_text, label):
    """The bold span that differs from the ringed label only in the variable
    slot ('N selected' for a ringed '2 selected'), or None."""
    bold = re.findall(r"\*\*(.+?)\*\*", step_text or "")
    if any(label.lower() in b.lower() for b in bold):
        return None
    for b in bold:
        if b.lower() != label.lower() and V._generalise(b) == V._generalise(label):
            return b
    return None


def _annotate(shot, number, out_path):
    import qa_annotate as A
    from PIL import Image
    with Image.open(shot["frame"]) as im:
        # Frames are captured at 2x for OCR; ring and badge sizes are set for
        # a 1x page, so draw at 1x and let finalize() downscale from there.
        k = 1440 / im.width if im.width > 1440 else 1.0
        if k != 1.0:
            im = im.resize((1440, round(im.height * k)), Image.LANCZOS)
        box = [round(v * k) for v in shot["dom_box"]]
        img = A.annotate(im, A.anchor_from_box(box), number=number)
    watermarked = shot.get("tenant") != "CLEAR"
    if watermarked:
        img = A.watermark(img)
    A.finalize(img, out_path)
    return watermarked


def build_spec(prose, shots, meta, missing_reason, out_root, annotate=True):
    """prose + evidence -> spec (images annotated under out_root/assets/<ticket>/)."""
    tid = prose["ticket"]
    by_id = {s["shot_id"]: s for s in shots if s.get("verified")}
    steps, no_screen = [], False
    for n, st in enumerate(prose.get("steps") or [], 1):
        step = {"text": st.get("text", "")}
        if st.get("caption"):
            step["caption"] = st["caption"]
        sid = st.get("shot")
        if sid == "missing":
            if missing_reason == F.NO_SCREEN:
                no_screen = True                   # no marker; one note below
            else:
                step["missing_shot"] = missing_reason or "no screenshot captured"
        elif sid:
            shot = by_id.get(sid)
            if not shot:
                step["missing_shot"] = f"screenshot {sid} was not verified"
            else:
                rel = f"assets/{tid}/{n:02d}.jpg"
                watermarked = False
                if annotate:
                    (Path(out_root) / rel).parent.mkdir(parents=True, exist_ok=True)
                    watermarked = _annotate(shot, n, Path(out_root) / rel)
                ev = {k: shot.get(k) for k in ("anchor", "label", "label_score", "tenant",
                                               "dom_box", "tenant_dom", "tenant_ocr", "source")}
                ev["frame"] = Path(shot["frame"]).name
                ev["watermarked"] = watermarked
                law = label_as_written(step["text"], shot["label"])
                if law:
                    ev["label_as_written"] = law
                step.update(image=rel, evidence=ev)
        steps.append(step)
    return {
        "title": prose.get("title", ""), "steps_heading": prose.get("steps_heading") or "Steps",
        "overview": prose.get("overview", ""), "steps": steps,
        "considerations": prose.get("considerations") or [],
        "conclusion": prose.get("conclusion", ""), "meta": meta,
        "provenance": {"note": f"Captured on the demo tenant; annotated per {F.summary()}."},
        "notes": list(prose.get("notes") or []) + ([F.NO_SCREEN_NOTE] if no_screen else []),
    }


def split_failures(fails):
    """(evidence, prose). Evidence is fixed by script; prose goes back to the writer."""
    evidence, prose = [], []
    for f in fails:
        if f.gate in PROSE_GATES or (f.gate == "G3" and BOLD_FAIL in f.message):
            prose.append(f)
        elif f.gate in ("G3", "G4", "G5") and f.where.startswith("step "):
            evidence.append(f)
        else:
            prose.append(f)
    return evidence, prose


def demote(spec, fails):
    """Drop the image of every step an evidence gate refused."""
    for f in fails:
        i = int(f.where.split()[1]) - 1
        st = spec["steps"][i]
        if st.get("image"):
            st.pop("image", None)
            st.pop("evidence", None)
            st["missing_shot"] = "screenshot failed verification"
    return spec


def assemble_one(prose, shots, meta, missing_reason, out_root, annotate=True):
    spec = build_spec(prose, shots, meta, missing_reason, out_root, annotate)
    ok, fails = V.check(spec, "draft", out_root)
    if not ok:
        evidence, _ = split_failures(fails)
        if evidence:
            demote(spec, evidence)
            ok, fails = V.check(spec, "draft", out_root)
    _, prose_fails = split_failures(fails)
    return spec, ok, prose_fails


def retry_prompt(ticket, prose, fails):
    L = [f"=== RETRY {ticket} ===",
         "Your article for this ticket failed these gates. Fix ONLY what they name, keep",
         "everything else, and write the corrected JSON object to the same file.", ""]
    L += [f"- {f.gate} {f.where}: {f.message}" for f in fails]
    L += ["", "Current article:", json.dumps(prose, indent=1, ensure_ascii=False)]
    return "\n".join(L)


# ── CLI ──────────────────────────────────────────────────────────────────────
def cmd_bundle(a):
    groups = _load(a.groups)
    inputs, priors = _load(a.inputs, {}), _load(a.priors, {})
    shots = shots_by_ticket(_load(a.shots, {}))
    uncap = {}
    for tid, m in [(u["ticket"], u["reason"]) for u in groups.get("uncapturable") or []] + \
            list(((_load(a.shots, {}) or {}).get("missing") or {}).items()):
        t = _reason_text(m)                      # the writer never sees a raw code
        uncap[tid] = "this change has no screen" if t == F.NO_SCREEN else t
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    sizes = []
    for g in groups["writer_groups"]:
        text = writer_bundle(g, inputs, shots, priors, uncap)
        (out / f"writer_{g['group_id']}.md").write_text(text)
        sizes.append(len(text))
    print(f"writer bundles: {len(sizes)} · {sum(sizes):,} chars")
    emit({"ok": True, "action": "bundle", "groups": len(sizes), "chars": sum(sizes)})
    return 0


def cmd_ingest(a):
    objs = parse_writer_reply(Path(a.reply).read_text())
    out = Path(a.prose_dir)
    out.mkdir(parents=True, exist_ok=True)
    for o in objs:
        (out / f"{o['ticket']}.json").write_text(json.dumps(o, indent=1, ensure_ascii=False))
    print(f"ingested {len(objs)} specs")
    emit({"ok": True, "action": "ingest", "specs": len(objs)})
    return 0


def _reason_text(r):
    """A plan-time reason (str) or a shots.json `missing` entry ({reason, detail}) -> marker words."""
    if isinstance(r, dict):
        return F.missing_reason_text(r.get("reason"), r.get("detail", "")) or None
    return F.missing_reason_text(r) if r else r


def cmd_assemble(a):
    groups, routes = _load(a.groups), _load(a.routes)
    shots = shots_by_ticket(_load(a.shots, {}))
    priors = _load(a.priors, {})
    uncap = {u["ticket"]: u["reason"] for u in groups.get("uncapturable") or []}
    for tid, m in ((_load(a.shots, {}) or {}).get("missing") or {}).items():
        uncap[tid] = m                              # per-screen capture reason wins
    placed = {}
    for g in groups["writer_groups"]:
        for tid in g["tickets"]:
            placed[tid] = g
    surface_of = {}
    for c in groups.get("capture_plan") or []:
        for tid in c["tickets"]:
            surface_of.setdefault(tid, c["surface"])
    out_root = Path(a.out_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    prose_dir = Path(a.prose_dir)
    results, failures = [], []
    tickets = a.tickets.split(",") if a.tickets else list(placed)
    for tid in tickets:
        g = placed.get(tid)
        pf = prose_dir / f"{tid}.json"
        if not g or not pf.exists():
            results.append({"ticket": tid, "status": "failed", "reason": "writer returned nothing"})
            continue
        prose = json.loads(pf.read_text())
        meta = {"ticket": tid, "audience": g["audience"], "portal": g["portal"],
                "persona": g["persona"].title(), "surface": surface_of.get(tid, ""),
                "prior": priors.get(tid) or []}
        spec, ok, prose_fails = assemble_one(prose, shots.get(tid, []), meta,
                                             _reason_text(uncap.get(tid)), out_root, annotate=not a.no_annotate)
        (out_root / f"{tid}.json").write_text(json.dumps(spec, indent=1, ensure_ascii=False))
        if not ok:
            failures.append({"ticket": tid, "fails": [str(f) for f in prose_fails],
                             "gates": [[f.gate, f.where, f.message] for f in prose_fails]})
        missing = sum(1 for s in spec["steps"] if s.get("missing_shot"))
        results.append({"ticket": tid, "status": ("failed" if not ok else
                                                  "partial" if missing else "drafted")})
    Path(a.out_dir, "assemble.json").write_text(json.dumps(results, indent=1))
    Path(a.out_dir, "failures.json").write_text(json.dumps(failures, indent=1))
    n = {s: sum(1 for r in results if r["status"] == s) for s in ("drafted", "partial", "failed")}
    print(f"assembled: {n['drafted']} drafted · {n['partial']} partial · {n['failed']} need a retry/failed")
    emit({"ok": True, "action": "assemble", **n})
    return 0


def cmd_retry_bundle(a):
    failures = _load(a.failures, [])
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for f in failures:
        prose = _load(Path(a.prose_dir) / f"{f['ticket']}.json", {})
        fails = [V.Failure(*g) for g in f["gates"]]
        (out / f"retry_{f['ticket']}.md").write_text(retry_prompt(f["ticket"], prose, fails))
    print(f"retry bundles: {len(failures)}")
    emit({"ok": True, "action": "retry-bundle", "retries": len(failures)})
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("bundle"); s.add_argument("--groups", required=True)
    s.add_argument("--inputs", required=True); s.add_argument("--shots")
    s.add_argument("--priors"); s.add_argument("--out-dir", required=True)
    s = sub.add_parser("ingest"); s.add_argument("--reply", required=True)
    s.add_argument("--prose-dir", required=True)
    s = sub.add_parser("assemble"); s.add_argument("--groups", required=True)
    s.add_argument("--prose-dir", required=True); s.add_argument("--shots")
    s.add_argument("--priors"); s.add_argument("--routes", default=str(DEFAULT_ROUTES))
    s.add_argument("--out-dir", required=True); s.add_argument("--tickets")
    s.add_argument("--no-annotate", action="store_true")
    s = sub.add_parser("retry-bundle"); s.add_argument("--failures", required=True)
    s.add_argument("--prose-dir", required=True); s.add_argument("--out-dir", required=True)
    a = ap.parse_args(argv)
    try:
        return {"bundle": cmd_bundle, "ingest": cmd_ingest, "assemble": cmd_assemble,
                "retry-bundle": cmd_retry_bundle}[a.cmd](a)
    except (OSError, json.JSONDecodeError, KeyError) as e:
        emit({"ok": False, "action": a.cmd, "error": f"{type(e).__name__}: {e}"})
        return 1


if __name__ == "__main__":
    sys.exit(main())
