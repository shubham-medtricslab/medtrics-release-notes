#!/usr/bin/env python3
"""
kb_package.py — contact sheet, owner confirmation, then render and package one
/kb-articles run.

The articles are what goes to Intercom, so they are rendered client-clean (no
"Generated from" line, no banner, no watermark, no missing-shot marker) and only
after the owner has looked at every screenshot:

  1. contact-sheet  one numbered thumbnail per candidate screenshot (ticket, step,
                    tenant verdict and matched words under each) -> contact_sheet.png
                    (+ contact_sheet.json); the orchestrator shows it to the owner.
  2. confirm        --ok 1,3,4 | all | none  --by "<owner>" sets evidence.tenant_confirmed_by
                    (and the frame's sha256, evidence.tenant_confirmed_sha256) on those
                    frames. A BLOCK frame can never be confirmed, and a frame that is not
                    confirmed is left out of the article (the step keeps its text).
                    The sheet records each image's sha256: confirm refuses ("stale sheet",
                    exit 1) when any image changed since, and package ships a frame only
                    when its current sha256 equals the one the owner confirmed.
  3. package        (default) validates each final spec in publish mode, renders the
                    .docx/.md and (one LibreOffice batch) the PDF, copies only the
                    shipped screenshots, writes provenance to a sidecar, RUN-SUMMARY.md,
                    run.json (what kb_ledger.py turns into Mem notes) and a zip. The run's
                    output folder is emptied first (ticket folders, _provenance, old zips),
                    so a renamed title leaves no stale file behind.

Output root: $KB_OUTPUT_DIR, else /mnt/user-data/outputs (Cowork), else
.claude/outputs/kb/.

    <root>/kb-articles/<run_id>/
      RUN-SUMMARY.md
      <ticket>/<title>.{docx,pdf,md}
      <ticket>/assets/<ticket>/NN.jpg          (shipped screenshots only)
      _provenance/<ticket>.md
      kb-articles-<run_id>.zip

  python3 kb_package.py contact-sheet --run-dir R --specs-dir R/specs --out R/contact_sheet.png
  python3 kb_package.py confirm --run-dir R --ok 1,2,4 --by "owner@example.com"
  python3 kb_package.py [package] --run-dir R --groups groups.json \
      --specs-dir R/specs [--failed-after-retry M1-1,M1-2]

    ---KB_PACKAGE_RESULT---
    {"ok", "output", "drafted", "partial", "failed", "pdf", "error"}
"""

import argparse
import copy
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_kb_doc as B                # noqa: E402
import kb_format as F                   # noqa: E402
import kb_ledger as L                   # noqa: E402
import kb_pdf as P                      # noqa: E402
import kb_usage as U                    # noqa: E402
import kb_validate as V                 # noqa: E402


def emit(payload):
    print("---KB_PACKAGE_RESULT---")
    print(json.dumps(payload))


def output_root(environ=None):
    environ = os.environ if environ is None else environ
    if environ.get("KB_OUTPUT_DIR"):
        return Path(environ["KB_OUTPUT_DIR"])
    if Path("/mnt/user-data/outputs").is_dir():
        return Path("/mnt/user-data/outputs")
    return Path(".claude/outputs/kb")


def file_stem(title):
    """The article's file name: just its title, nothing internal in it."""
    clean = re.sub(r'[\\/:*?"<>|]+', "", title).strip()
    return clean[:120] or "article"


def shot_counts(spec):
    shown = sum(1 for s in spec["steps"] if s.get("image"))
    missing = [s["missing_shot"] for s in spec["steps"] if s.get("missing_shot")]
    tenant = {"CLEAR": 0, "REVIEW": 0, "BLOCK": 0}
    for s in spec["steps"]:
        t = (s.get("evidence") or {}).get("tenant")
        if s.get("image") and t in tenant:
            tenant[t] += 1
    return shown, missing, tenant


LEFT_OUT_BLOCK = "tenant screen BLOCK"
LEFT_OUT_UNCONFIRMED = "not confirmed on the contact sheet"
LEFT_OUT_CHANGED = "image changed after the owner confirmed it"


def image_sha256(path):
    """sha256 of an image file's bytes, "" when it cannot be read."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return ""


def confirmed_image_ok(step, specs_dir):
    """True when the frame's current sha256 equals the one recorded when the owner confirmed it."""
    ev = step.get("evidence") or {}
    want = ev.get("tenant_confirmed_sha256")
    return bool(want) and want == image_sha256(Path(specs_dir) / step["image"])


def finalize_spec(spec, specs_dir=None):
    """The spec as it will be published: every screenshot that is not shippable
    (BLOCK, or not confirmed by the owner) is taken out and parked under the step's
    `excluded` key (provenance only); the step keeps its text. With `specs_dir`, a confirmed
    frame also has to be the very image the owner saw (its sha256 equals the confirmed one).
    Returns (final_spec, left_out) with left_out = [{"step", "why", "tenant"}]."""
    spec = copy.deepcopy(spec)
    left = []
    for i, st in enumerate(spec["steps"], 1):
        changed = bool(st.get("image") and V.shippable(st) and specs_dir is not None
                       and not confirmed_image_ok(st, specs_dir))
        if st.get("image") and (not V.shippable(st) or changed):
            ev = st.get("evidence") or {}
            why = (LEFT_OUT_BLOCK if ev.get("tenant") == "BLOCK" else LEFT_OUT_CHANGED if changed
                   else LEFT_OUT_UNCONFIRMED)
            st["excluded"] = {"image": st.pop("image"), "evidence": st.pop("evidence", None) or {},
                              "caption": st.pop("caption", ""), "why": why}
            st["missing_shot"] = f"screenshot left out ({why})"
            left.append({"step": i, "why": why, "tenant": ev.get("tenant") or "—"})
    return spec, left


def _ticket_record(tid, spec, status, files, run_id, date, tokens, group, cost=None):
    rec = {"ticket": tid, "type": "feature", "status": status, "generated": date,
           "run": run_id, "render": "final", "persona": (group or {}).get("persona", "").title()}
    if spec:
        shown, missing, tenant = shot_counts(spec)
        total = shown + len(missing)
        rec.update(
            title=spec["title"], surface=spec["meta"].get("surface", ""),
            screenshots=(f"{shown} of {total}" + (f" (missing: {len(missing)} — {missing[0]})"
                                                  if missing else "")) if total else "none",
            tenant=" · ".join(f"{k} {v}" for k, v in tenant.items()),
            prior=L.prior_line(spec["meta"].get("prior") or []),
            files=", ".join(files))
    if tokens:
        rec["tokens"] = f"~{tokens:,} (apportioned, estimate)"
    if cost is not None:
        rec["cost"] = f"{U.money(cost)} ({U.COST_NOTE})"
    return rec


def clean_office_leftovers(out):
    """Delete LibreOffice's lock (.~lock.<file>#) and scratch (lu*.tmp) files that
    a PDF conversion can leave in an output folder. Returns the names removed."""
    gone = []
    for pat in (".~lock*#", "lu*.tmp"):
        for f in sorted(Path(out).rglob(pat)):
            if f.is_file():
                try:
                    f.unlink()
                    gone.append(f.name)
                except OSError:
                    pass
    return gone


def package(run_dir, groups, specs_dir, out_root, failed_after_retry=(), date=None,
            requested=()):
    run_id = groups["run_id"]
    date = date or groups.get("date") or _dt.date.today().isoformat()
    out = Path(out_root) / "kb-articles" / run_id
    out.mkdir(parents=True, exist_ok=True)
    for old in out.iterdir():                     # a re-run starts clean: no stale title, folder or zip
        if old.is_dir():
            shutil.rmtree(old, ignore_errors=True)
        elif old.suffix == ".zip":
            old.unlink()
    (out / "_provenance").mkdir(exist_ok=True)
    assembled = json.loads((Path(specs_dir) / "assemble.json").read_text())
    usage = {}
    up = Path(run_dir) / "usage_report.json"
    if up.exists():
        usage = json.loads(up.read_text())
    group_of = {t: g for g in groups["writer_groups"] for t in g["tickets"]}

    rows, docx, pairs = [], [], []
    for r in assembled:
        tid = r["ticket"]
        status = "failed" if tid in failed_after_retry else r["status"]
        spec_path = Path(specs_dir) / f"{tid}.json"
        spec = json.loads(spec_path.read_text()) if spec_path.exists() else None
        files, left = [], []
        if spec and status != "failed":
            spec, left = finalize_spec(spec, specs_dir)
            ok, fails = V.check(spec, "publish", str(specs_dir))
            if not ok:
                status = "failed"
                r["reason"] = "; ".join(str(f) for f in fails[:3])
            elif left and status == "drafted":
                status = "partial"
        if spec and status != "failed":
            tdir = out / tid
            tdir.mkdir(exist_ok=True)
            shutil.rmtree(tdir / "assets", ignore_errors=True)
            for st in spec["steps"]:                 # only shipped shots are copied
                if st.get("image"):
                    dest = tdir / st["image"]
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(Path(specs_dir) / st["image"], dest)
            stem = file_stem(spec["title"])
            B.render(spec, tdir / f"{stem}.docx", "publish", root=str(tdir))
            B.render_markdown(spec, tdir / f"{stem}.md", "publish", asset_base=f"assets/{tid}/")
            B.render_provenance_md(spec, out / "_provenance" / f"{tid}.md")
            docx.append(tdir / f"{stem}.docx")
            pairs.append({"pdf": str(tdir / f"{stem}.pdf"), "spec": spec})
            files = [f"{stem}.docx", ".pdf", ".md"]
        rows.append({"ticket": tid, "status": status, "spec": spec, "files": files,
                     "reason": r.get("reason", ""), "group": group_of.get(tid), "left_out": left})

    pdf_note, pdf_report = "", []
    if docx:
        by_dir = {}
        for d in docx:
            by_dir.setdefault(d.parent, []).append(d)
        try:
            for folder, ds in by_dir.items():
                P.convert(ds, folder)
            for p in pairs:
                # verify against the published spec (left-out shots are not expected in the PDF)
                errs, warns = P.verify(p["pdf"], p["spec"]) if Path(p["pdf"]).exists() else (["no PDF"], [])
                pdf_report.append({"pdf": Path(p["pdf"]).name, "errors": errs, "warnings": warns})
        except Exception as e:
            pdf_note = f"PDF step skipped: {type(e).__name__}: {e}"

    clean_office_leftovers(out)
    cost_of = usage.get("per_ticket_cost") or {}
    records = [_ticket_record(r["ticket"], r["spec"], r["status"], r["files"], run_id, date,
                              (usage.get("per_ticket") or {}).get(r["ticket"]), r["group"],
                              cost_of.get(r["ticket"]))
               for r in rows]
    reasons = shot_reasons(groups, run_dir)
    totals = {s: sum(1 for r in rows if r["status"] == s) for s in ("drafted", "partial", "failed")}
    totals["skipped"] = len(groups.get("skipped") or [])
    totals["tokens"] = usage.get("total_tokens", 0)
    totals["est_cost_usd"] = usage.get("est_cost_usd")
    resume = Path(run_dir) / "shots" / "resume.json"
    left = []
    if resume.exists():
        left = sorted({c["surface"] for c in json.loads(resume.read_text())["capture_plan"]})
    run = {"run_id": run_id, "date": date, "mode": groups.get("mode"), "totals": totals,
           "records": records, "left_by_expiry": left, "output": str(out),
           "tickets": ticket_table(requested, rows, groups, out, reasons, costs=cost_of),
           "left_out": {r["ticket"]: r["left_out"] for r in rows if r["left_out"]},
           "cost": {"est_usd": usage.get("est_cost_usd"), "note": U.COST_NOTE,
                    "input_share": U.INPUT_SHARE, "per_ticket": cost_of},
           "usage": [{"agent": u["agent"], "scope": u["scope"], "model": u["model"],
                      "total_tokens": u["total_tokens"], "est_cost_usd": u.get("est_cost_usd")}
                     for u in usage.get("rows", [])]}
    (Path(run_dir) / "run.json").write_text(json.dumps(run, indent=1, ensure_ascii=False))
    (out / "RUN-SUMMARY.md").write_text(summary_md(run, rows, groups, pdf_report, pdf_note, usage, reasons))

    zpath = out / f"kb-articles-{run_id}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(out.rglob("*")):
            if f.is_file() and f != zpath:
                z.write(f, f.relative_to(out).as_posix())
    return run, out, pdf_note


DONE_STATUSES = ("drafted", "partial")
NO_SCREEN_WORDS = "no screen (text only)"


def reason_words(r):
    """A reason code, {reason, detail} entry or plain words -> words for a reader
    (never a raw code like no_screen)."""
    if isinstance(r, dict):
        t = F.missing_reason_text(r.get("reason"), r.get("detail", ""))
    else:
        t = F.missing_reason_text(r)
    return NO_SCREEN_WORDS if t == F.NO_SCREEN else (t or "not captured")


def shot_reasons(groups, run_dir=None):
    """{TICKET: words} for tickets with no or missing screenshots: the plan's
    uncapturable list, then <run>/shots/shots.json `missing` (what capture saw)."""
    out = {u["ticket"].upper(): reason_words(u["reason"]) for u in groups.get("uncapturable") or []}
    if run_dir:
        try:
            miss = json.loads((Path(run_dir) / "shots" / "shots.json").read_text()).get("missing") or {}
        except (OSError, ValueError, AttributeError):
            miss = {}
        for tid, m in miss.items():
            out[str(tid).upper()] = reason_words(m)
    return out


def ticket_table(requested, rows, groups, out_dir, reasons=None, costs=None):
    """One row per ticket the run was given, in the order given: done or not,
    the files, and why. Tickets the run was not given but drafted anyway
    (e.g. from `pending`) are appended. With `costs` ({ticket: USD}, possibly
    empty) every row also carries "cost": the estimate as "$0.04", "n/a" when
    the run has none for that ticket, "—" for a ticket the run did not draft."""
    by_row = {r["ticket"].upper(): r for r in rows}
    skipped = {s["ticket"].upper(): s["reason"] for s in groups.get("skipped") or []}
    uncap = shot_reasons(groups) if reasons is None else reasons
    order = list(requested) + [r["ticket"] for r in rows
                               if r["ticket"].upper() not in {t.upper() for t in requested}]
    table = []
    for t in order:
        r = by_row.get(t.upper())
        if r and r["status"] in DONE_STATUSES:
            files = "—"
            if r["files"]:
                stem = Path(r["files"][0]).stem
                exts = [e for e in (".docx", ".pdf", ".md") if (Path(out_dir) / t / f"{stem}{e}").exists()]
                files = f"{t}/{stem} ({', '.join(exts)})"
            why = uncap.get(t.upper())
            detail = (NO_SCREEN_WORDS if why == NO_SCREEN_WORDS
                      else "all screenshots" if r["status"] == "drafted"
                      else f"screenshots missing: {why or 'not captured'}")
            table.append({"ticket": t, "done": "done", "files": files, "detail": detail,
                          "cost": U.money((costs or {}).get(t))})
        elif r:
            table.append({"ticket": t, "done": "not done", "files": "—",
                          "detail": f"error: {r['reason'] or 'gates refused after retries'}",
                          "cost": U.money((costs or {}).get(t)) if (costs or {}).get(t) is not None else "—"})
        elif t.upper() in skipped:
            reason = skipped[t.upper()]
            done = "already done" if reason.startswith("ledger:") else "not done"
            table.append({"ticket": t, "done": done, "files": "—", "detail": reason, "cost": "—"})
        else:
            table.append({"ticket": t, "done": "not done", "files": "—",
                          "detail": "error: not reached (no writer output)", "cost": "—"})
    if costs is None:
        for row in table:
            row.pop("cost", None)
    return table


_NO_TOTAL = object()


def table_md(table, total_cost=_NO_TOTAL):
    """The every-ticket table. Rows with a "cost" get an "Est. cost" column, and
    `total_cost` (USD or None = n/a; omit for no total row) adds a run-total row, e.g.

        | M1-465 | done | M1-465/How to … (.docx, .pdf, .md) | all screenshots | $0.04 |
        | **Run total** |  |  | all agent calls | $0.31 |
    """
    cost = any("cost" in r for r in table)
    L_ = ["| Ticket | Result | Files | Details |" + (" Est. cost |" if cost else ""),
          "|---|---|---|---|" + ("---|" if cost else "")]
    L_ += [f"| {r['ticket']} | {r['done']} | {r['files']} | {r['detail']} |"
           + (f" {r.get('cost', '—')} |" if cost else "") for r in table]
    if cost and total_cost is not _NO_TOTAL:
        L_.append(f"| **Run total** |  |  | all agent calls | {U.money(total_cost)} |")
    return L_


def _total_arg(run):
    c = run.get("cost")
    return c.get("est_usd") if c is not None else _NO_TOTAL


def summary_md(run, rows, groups, pdf_report, pdf_note, usage, reasons=None):
    t = run["totals"]
    L_ = [f"# KB articles — run {run['run_id']}", "",
          f"{run['date']} · mode {run.get('mode')} · **{t['drafted']} drafted · {t['partial']} partial "
          f"(screenshots missing) · {t['failed']} failed · {t['skipped']} skipped**", "",
          "The articles are client-clean (no banner, watermark or draft line). A screenshot "
          "appears only where the owner confirmed it on the contact sheet "
          "(`tenant_confirmed_by`); a step whose shot was left out keeps its text.", "",
          "## Every ticket", ""] + table_md(run["tickets"], _total_arg(run)) + ["",
          f"Est. cost is an estimate; main-chat tokens not included.", "",
          "## Articles", "", "| Ticket | Status | Title | Persona · surface | Shots | Tenant | Prior article |",
          "|---|---|---|---|---|---|---|"]
    for r, rec in zip(rows, run["records"]):
        title = rec.get("title") or "—"
        L_.append(f"| {r['ticket']} | {r['status']} | {title} | {rec.get('persona', '')} · "
                  f"{rec.get('surface') or '—'} | {rec.get('screenshots') or '—'} | "
                  f"{rec.get('tenant') or '—'} | {rec.get('prior') or '—'} |")
    failed = [r for r in rows if r["status"] == "failed"]
    if failed:
        L_ += ["", "## Failed", ""] + [f"- {r['ticket']}: {r['reason'] or 'gates refused after retries'}"
                                       for r in failed]
    left = run.get("left_out") or {}
    if left:
        L_ += ["", "## Screenshots left out of the articles", ""] + \
              [f"- {t} step {x['step']}: {x['why']} (tenant {x['tenant']})"
               for t, xs in left.items() for x in xs]
    uncap = shot_reasons(groups) if reasons is None else reasons
    if uncap:
        L_ += ["", "## No screenshots possible on the demo", ""] + \
              [f"- {t}: {w}" for t, w in uncap.items()]
    if run["left_by_expiry"]:
        L_ += ["", "## Left by session expiry", "",
               "Sign in again with `kb_capture.py login --persona <persona>`, then re-run "
               "with `--retry-partial`:", ""] + [f"- {s}" for s in run["left_by_expiry"]]
    skipped = groups.get("skipped") or []
    if skipped:
        L_ += ["", "## Skipped", ""] + [f"- {s['ticket']}: {s['reason']}" for s in skipped]
    L_ += ["", "## PDF check", ""]
    if pdf_note:
        L_.append(pdf_note)
    for p in pdf_report:
        status = "ok" if not p["errors"] else "; ".join(p["errors"])
        warn = f" — warn: {'; '.join(p['warnings'])}" if p["warnings"] else ""
        L_.append(f"- {p['pdf']}: {status}{warn}")
    L_ += ["", "## Tokens and cost (estimate)", "",
           f"Total ~{usage.get('total_tokens', 0):,} tokens across {len(usage.get('rows', []))} agent calls"
           f" · est. cost {U.money(usage.get('est_cost_usd'))} ({U.COST_NOTE}). "
           "Task usage reports one total per call, priced at "
           f"{round(U.INPUT_SHARE * 100)}% input / {round((1 - U.INPUT_SHARE) * 100)}% output at the "
           "model's rate, and the orchestrator's own tokens are not visible, so per-article "
           "figures are apportioned estimates.", ""]
    for u in usage.get("rows", []):
        c = u.get("est_cost_usd")
        L_.append(f"- {u['agent']} · {u['scope']} · {u['model']} · {u['total_tokens']:,} · "
                  f"{U.money(c)}")
    return "\n".join(L_) + "\n"


# ── contact sheet + owner confirmation ───────────────────────────────────────
SHEET_COLS, SHEET_PER_PAGE = 3, 12
SHEET_THUMB_W = 380
SHEET_JSON = "contact_sheet.json"


def candidates(specs_dir):
    """Every screenshot a spec carries, numbered 1..N in assemble order then step
    order: [{n, ticket, step, image, tenant, words}]. Failed specs are skipped."""
    specs_dir = Path(specs_dir)
    order = [r["ticket"] for r in json.loads((specs_dir / "assemble.json").read_text())
             if r.get("status") != "failed"]
    items = []
    for tid in order:
        p = specs_dir / f"{tid}.json"
        if not p.exists():
            continue
        for i, st in enumerate(json.loads(p.read_text())["steps"], 1):
            if st.get("image"):
                ev = st.get("evidence") or {}
                items.append({"n": len(items) + 1, "ticket": tid, "step": i, "image": st["image"],
                              "tenant": ev.get("tenant") or "unscreened",
                              "words": words_text(ev.get("tenant_words")),
                              "confirmed_by": ev.get("tenant_confirmed_by") or "",
                              "sha256": image_sha256(specs_dir / st["image"])})
    return items


def words_text(words, limit=110):
    """Matched tenant words ({kind: [words]}, a list or a string) as one short line."""
    if not words:
        return ""
    if isinstance(words, dict):
        text = "; ".join(f"{k}: {', '.join(str(w) for w in (v if isinstance(v, list) else [v]))}"
                         for k, v in words.items() if v)
    elif isinstance(words, (list, tuple)):
        text = ", ".join(str(w) for w in words)
    else:
        text = str(words)
    return text if len(text) <= limit else text[:limit - 1] + "…"


def render_sheets(items, specs_dir, out):
    """Write the numbered contact sheet(s); returns the PNG paths (a page holds
    SHEET_PER_PAGE shots; later pages are <stem>-2.png, …)."""
    import textwrap
    from PIL import Image, ImageDraw
    import qa_annotate as A
    out = Path(out)
    pad, cap_h = 16, 92
    f_n, f_s = A._font(17), A._font(13)
    red = tuple(int(F.RED[i:i + 2], 16) for i in (0, 2, 4))
    paths = []
    pages = [items[i:i + SHEET_PER_PAGE] for i in range(0, len(items), SHEET_PER_PAGE)] or [[]]
    for pi, page in enumerate(pages, 1):
        thumbs = []
        for it in page:
            with Image.open(Path(specs_dir) / it["image"]) as im:
                im = im.convert("RGB")
                thumbs.append(im.resize((SHEET_THUMB_W, max(1, round(im.height * SHEET_THUMB_W / im.width))),
                                        Image.LANCZOS))
        cell_h = (max((t.height for t in thumbs), default=0)) + cap_h + pad
        rows = -(-len(page) // SHEET_COLS) or 1
        sheet = Image.new("RGB", (pad + SHEET_COLS * (SHEET_THUMB_W + pad), pad + rows * cell_h),
                          (255, 255, 255))
        d = ImageDraw.Draw(sheet)
        for k, (it, th) in enumerate(zip(page, thumbs)):
            x = pad + (k % SHEET_COLS) * (SHEET_THUMB_W + pad)
            y = pad + (k // SHEET_COLS) * cell_h
            sheet.paste(th, (x, y))
            d.rectangle([x - 1, y - 1, x + th.width, y + th.height], outline=(190, 194, 202))
            block = it["tenant"] == "BLOCK"
            d.text((x, y + th.height + 6), f"#{it['n']}  {it['ticket']} · step {it['step']}",
                   font=f_n, fill=red if block else A.INK)
            verdict = "BLOCK — will not ship" if block else it["tenant"]
            line = verdict + (f" · {it['words']}" if it["words"] else "")
            for j, ln in enumerate(textwrap.wrap(line, 56)[:3]):
                d.text((x, y + th.height + 30 + j * 17), ln, font=f_s, fill=red if block else F_MIDRGB)
        path = out if pi == 1 else out.with_name(f"{out.stem}-{pi}{out.suffix}")
        out.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(path)
        paths.append(str(path))
    return paths


F_MIDRGB = tuple(int(F.MID[i:i + 2], 16) for i in (0, 2, 4))


def cmd_contact_sheet(a):
    items = candidates(a.specs_dir)
    run_dir = Path(a.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / SHEET_JSON).write_text(json.dumps(
        {"specs_dir": str(Path(a.specs_dir).resolve()), "items": items}, indent=1, ensure_ascii=False))
    paths = render_sheets(items, a.specs_dir, a.out) if items else []
    shippable = sum(1 for i in items if i["tenant"] != "BLOCK")
    print(f"contact sheet: {len(items)} screenshots ({shippable} can be confirmed, "
          f"{len(items) - shippable} BLOCK) -> {', '.join(paths) or 'nothing to show'}")
    emit({"ok": True, "action": "contact-sheet", "shots": len(items), "sheets": paths,
          "blocked": len(items) - shippable})
    return 0


def parse_ok(text, items):
    """'all' -> every non-BLOCK number; 'none' -> []; '1,3,4' -> those numbers. A BLOCK number
    or an unknown one is refused (ValueError)."""
    nums = {i["n"]: i for i in items}
    if text.strip().lower() == "none":          # the owner ok'd nothing: no screenshot ships
        return []
    if text.strip().lower() == "all":
        return sorted(n for n, i in nums.items() if i["tenant"] != "BLOCK")
    out = []
    for part in re.split(r"[,\s]+", text.strip()):
        if not part:
            continue
        if not part.isdigit() or int(part) not in nums:
            raise ValueError(f"no screenshot #{part} on the contact sheet")
        if nums[int(part)]["tenant"] == "BLOCK":
            raise ValueError(f"screenshot #{part} is BLOCK and can never be confirmed")
        out.append(int(part))
    return sorted(set(out))


def cmd_confirm(a):
    if not (a.by or "").strip():
        raise ValueError("--by is required: who looked at the contact sheet")
    sheet = json.loads((Path(a.run_dir) / SHEET_JSON).read_text())
    items, specs_dir = sheet["items"], Path(sheet["specs_dir"])
    ok = set(parse_ok(a.ok, items))
    stale = [it for it in items if not it.get("sha256") or it["sha256"] != image_sha256(specs_dir / it["image"])]
    if stale:
        raise ValueError("stale sheet: " + ", ".join(f"#{it['n']} ({it['ticket']} step {it['step']})" for it in stale)
                         + " changed (or the sheet has no image hash) since the contact sheet; "
                           "run contact-sheet again and show it to the owner")
    by_ticket = {}
    for it in items:
        by_ticket.setdefault(it["ticket"], []).append(it)
    for tid, its in by_ticket.items():
        p = specs_dir / f"{tid}.json"
        spec = json.loads(p.read_text())
        for it in its:
            st = spec["steps"][it["step"] - 1]
            if st.get("image") != it["image"]:
                raise ValueError(f"{tid} step {it['step']} changed since the contact sheet; "
                                 "run contact-sheet again")
            ev = st.setdefault("evidence", {})
            if it["n"] in ok:
                ev["tenant_confirmed_by"] = a.by.strip()
                ev["tenant_confirmed_sha256"] = it["sha256"]
            else:
                ev.pop("tenant_confirmed_by", None)         # the owner's list is the whole truth
                ev.pop("tenant_confirmed_sha256", None)
        p.write_text(json.dumps(spec, indent=1, ensure_ascii=False))
    left = [i["n"] for i in items if i["n"] not in ok]
    print(f"confirmed {len(ok)} of {len(items)} screenshots by {a.by.strip()}"
          + (f"; left out of the articles: {', '.join('#' + str(n) for n in left)}" if left else ""))
    emit({"ok": True, "action": "confirm", "confirmed": sorted(ok), "left_out": left})
    return 0


def cmd_package(a):
    groups = json.loads(Path(a.groups).read_text())
    run, out, pdf_note = package(a.run_dir, groups, a.specs_dir,
                                 a.output_root or output_root(),
                                 [t for t in a.failed_after_retry.split(",") if t],
                                 requested=[t.strip() for t in a.requested.split(",") if t.strip()])
    for line in table_md(run["tickets"], _total_arg(run)):
        print(line)
    print("Est. cost is an estimate; main-chat tokens not included.")
    t = run["totals"]
    print(f"wrote {out} · {t['drafted']} drafted · {t['partial']} partial · {t['failed']} failed"
          + (f" · {pdf_note}" if pdf_note else ""))
    emit({"ok": True, "output": str(out), "drafted": t["drafted"], "partial": t["partial"],
          "failed": t["failed"], "pdf": not pdf_note})
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "package":
        argv = argv[1:]
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("contact-sheet", help="numbered thumbnails of every candidate screenshot")
    s.add_argument("--run-dir", required=True); s.add_argument("--specs-dir", required=True)
    s.add_argument("--out", required=True)
    s = sub.add_parser("confirm", help="record which screenshots the owner looked at and ok'd")
    s.add_argument("--run-dir", required=True)
    s.add_argument("--ok", required=True, help="comma-separated numbers, all (never BLOCK), or none (clears every confirmation)")
    s.add_argument("--by", required=True, help="owner name or email")
    if argv and argv[0] in ("contact-sheet", "confirm"):
        a = ap.parse_args(argv)
        fn = {"contact-sheet": cmd_contact_sheet, "confirm": cmd_confirm}[a.cmd]
    else:
        pk = argparse.ArgumentParser(description=__doc__.splitlines()[1])
        pk.add_argument("--run-dir", required=True)
        pk.add_argument("--groups", required=True)
        pk.add_argument("--specs-dir", required=True)
        pk.add_argument("--failed-after-retry", default="")
        pk.add_argument("--output-root")
        pk.add_argument("--requested", default="",
                        help="comma-separated ids the run was given, for the every-ticket table")
        a, fn = pk.parse_args(argv), cmd_package
    try:
        return fn(a)
    except (OSError, ValueError, KeyError) as e:       # JSONDecodeError is a ValueError
        emit({"ok": False, "error": f"{type(e).__name__}: {e}"})
        return 1


if __name__ == "__main__":
    sys.exit(main())
