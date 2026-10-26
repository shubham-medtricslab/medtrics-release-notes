#!/usr/bin/env python3
"""
kb_package.py — render, convert and package one /kb-articles run.

From the assembled specs it writes, per ticket, the draft .docx, the Intercom
markdown and (via ONE LibreOffice batch) the PDF; the provenance table goes to
a sidecar so the article CX forwards carries no internal audit table. It then
writes RUN-SUMMARY.md, run.json (what kb_ledger.py writes turns into Mem
notes) and a zip of the lot.

Output root: $KB_OUTPUT_DIR, else /mnt/user-data/outputs (Cowork), else
.claude/outputs/kb/.

    <root>/kb-articles/<run_id>/
      RUN-SUMMARY.md
      <ticket>/CX-DRAFT_<title>.{docx,pdf,md}
      <ticket>/assets/NN.jpg
      _provenance/<ticket>.md
      kb-articles-<run_id>.zip

  python3 kb_package.py --run-dir /tmp/kb/<run> --groups groups.json \
      --specs-dir /tmp/kb/<run>/specs [--failed-after-retry M1-1,M1-2]

    ---KB_PACKAGE_RESULT---
    {"ok", "output", "drafted", "partial", "failed", "pdf", "error"}
"""

import argparse
import datetime as _dt
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
import kb_ledger as L                   # noqa: E402
import kb_pdf as P                      # noqa: E402
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
    clean = re.sub(r'[\\/:*?"<>|]+', "", title).strip()
    return f"CX-DRAFT_{clean[:120]}"


def shot_counts(spec):
    shown = sum(1 for s in spec["steps"] if s.get("image"))
    missing = [s["missing_shot"] for s in spec["steps"] if s.get("missing_shot")]
    tenant = {"CLEAR": 0, "REVIEW": 0, "BLOCK": 0}
    for s in spec["steps"]:
        t = (s.get("evidence") or {}).get("tenant")
        if s.get("image") and t in tenant:
            tenant[t] += 1
    return shown, missing, tenant


def _ticket_record(tid, spec, status, files, run_id, date, tokens, group):
    rec = {"ticket": tid, "type": "feature", "status": status, "generated": date,
           "run": run_id, "render": "draft", "persona": (group or {}).get("persona", "").title()}
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
    return rec


def package(run_dir, groups, specs_dir, out_root, failed_after_retry=(), date=None):
    run_id = groups["run_id"]
    date = date or groups.get("date") or _dt.date.today().isoformat()
    out = Path(out_root) / "kb-articles" / run_id
    out.mkdir(parents=True, exist_ok=True)
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
        files = []
        if spec and status != "failed":
            ok, fails = V.check(spec, "draft", str(specs_dir))
            if not ok:
                status = "failed"
                r["reason"] = "; ".join(str(f) for f in fails[:3])
        if spec and status != "failed":
            tdir = out / tid
            tdir.mkdir(exist_ok=True)
            if (Path(specs_dir) / "assets" / tid).exists():
                shutil.copytree(Path(specs_dir) / "assets" / tid, tdir / "assets" / tid,
                                dirs_exist_ok=True)
            stem = file_stem(spec["title"])
            B.render(spec, tdir / f"{stem}.docx", "draft", root=str(tdir))
            B.render_markdown(spec, tdir / f"{stem}.md", "draft", asset_base=f"assets/{tid}/")
            B.render_provenance_md(spec, out / "_provenance" / f"{tid}.md")
            docx.append(tdir / f"{stem}.docx")
            pairs.append({"pdf": str(tdir / f"{stem}.pdf"), "spec": str(spec_path)})
            files = [f"{stem}.docx", ".pdf", ".md"]
        rows.append({"ticket": tid, "status": status, "spec": spec, "files": files,
                     "reason": r.get("reason", ""), "group": group_of.get(tid)})

    pdf_note, pdf_report = "", []
    if docx:
        by_dir = {}
        for d in docx:
            by_dir.setdefault(d.parent, []).append(d)
        try:
            for folder, ds in by_dir.items():
                P.convert(ds, folder)
            for p in pairs:
                spec = json.loads(Path(p["spec"]).read_text())
                errs, warns = P.verify(p["pdf"], spec) if Path(p["pdf"]).exists() else (["no PDF"], [])
                pdf_report.append({"pdf": Path(p["pdf"]).name, "errors": errs, "warnings": warns})
        except Exception as e:
            pdf_note = f"PDF step skipped: {type(e).__name__}: {e}"

    records = [_ticket_record(r["ticket"], r["spec"], r["status"], r["files"], run_id, date,
                              (usage.get("per_ticket") or {}).get(r["ticket"]), r["group"])
               for r in rows]
    totals = {s: sum(1 for r in rows if r["status"] == s) for s in ("drafted", "partial", "failed")}
    totals["skipped"] = len(groups.get("skipped") or [])
    totals["tokens"] = usage.get("total_tokens", 0)
    resume = Path(run_dir) / "shots" / "resume.json"
    left = []
    if resume.exists():
        left = sorted({c["surface"] for c in json.loads(resume.read_text())["capture_plan"]})
    run = {"run_id": run_id, "date": date, "mode": groups.get("mode"), "totals": totals,
           "records": records, "left_by_expiry": left, "output": str(out),
           "usage": [{"agent": u["agent"], "scope": u["scope"], "model": u["model"],
                      "total_tokens": u["total_tokens"]} for u in usage.get("rows", [])]}
    (Path(run_dir) / "run.json").write_text(json.dumps(run, indent=1, ensure_ascii=False))
    (out / "RUN-SUMMARY.md").write_text(summary_md(run, rows, groups, pdf_report, pdf_note, usage))

    zpath = out / f"kb-articles-{run_id}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(out.rglob("*")):
            if f.is_file() and f != zpath:
                z.write(f, f.relative_to(out).as_posix())
    return run, out, pdf_note


def summary_md(run, rows, groups, pdf_report, pdf_note, usage):
    t = run["totals"]
    L_ = [f"# KB articles — run {run['run_id']}", "",
          f"{run['date']} · mode {run.get('mode')} · **{t['drafted']} drafted · {t['partial']} partial "
          f"(screenshots missing) · {t['failed']} failed · {t['skipped']} skipped**", "",
          "Every file here is a **draft**. Publishing needs a person to check each screenshot "
          "for tenant data (`tenant_confirmed_by`).", "",
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
    uncap = groups.get("uncapturable") or []
    if uncap:
        L_ += ["", "## No screenshots possible on the demo", ""] + \
              [f"- {u['ticket']}: {u['reason']}" for u in uncap]
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
    L_ += ["", "## Tokens (estimate)", "",
           f"Total ~{usage.get('total_tokens', 0):,} across {len(usage.get('rows', []))} agent calls. "
           "Task usage reports one total per call and the orchestrator's own tokens are not "
           "visible, so per-article figures are apportioned estimates.", ""]
    for u in usage.get("rows", []):
        L_.append(f"- {u['agent']} · {u['scope']} · {u['model']} · {u['total_tokens']:,}")
    return "\n".join(L_) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--groups", required=True)
    ap.add_argument("--specs-dir", required=True)
    ap.add_argument("--failed-after-retry", default="")
    ap.add_argument("--output-root")
    a = ap.parse_args(argv)
    try:
        groups = json.loads(Path(a.groups).read_text())
        run, out, pdf_note = package(a.run_dir, groups, a.specs_dir,
                                     a.output_root or output_root(),
                                     [t for t in a.failed_after_retry.split(",") if t])
    except (OSError, json.JSONDecodeError, KeyError) as e:
        emit({"ok": False, "error": f"{type(e).__name__}: {e}"})
        return 1
    t = run["totals"]
    print(f"wrote {out} · {t['drafted']} drafted · {t['partial']} partial · {t['failed']} failed"
          + (f" · {pdf_note}" if pdf_note else ""))
    emit({"ok": True, "output": str(out), "drafted": t["drafted"], "partial": t["partial"],
          "failed": t["failed"], "pdf": not pdf_note})
    return 0


if __name__ == "__main__":
    sys.exit(main())
