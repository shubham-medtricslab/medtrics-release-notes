#!/usr/bin/env python3
"""
kb_pdf.py — the PDF of each KB article, converted from its .docx so the two
files can never disagree.

  convert  One LibreOffice batch call for every .docx (a private profile, so a
           LibreOffice the person has open is never touched).
  verify   Each PDF against its spec: at least one Letter page, one embedded
           image per screenshot, the title, the section headings and every
           bold label in its text. Calibri or its metric twin Carlito pass;
           any other font is a warning (the page may have reflowed), never a
           failure.

    python3 kb_pdf.py convert --docx a.docx b.docx --out-dir pdf/
    python3 kb_pdf.py verify  --pairs pairs.json   # [{"pdf", "spec"}, ...]

    ---KB_PDF_RESULT---
    {"ok", "action", "converted"|"checked", "failed", "warnings", "error"}
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import kb_format as F                   # noqa: E402

LETTER_PT = (612, 792)
OK_FONTS = ("calibri", "carlito")


def emit(payload):
    print("---KB_PDF_RESULT---")
    print(json.dumps(payload))


def soffice():
    for name in ("soffice", "libreoffice"):
        p = shutil.which(name)
        if p:
            return p
    mac = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
    return mac if os.path.exists(mac) else None


def convert(docx_paths, out_dir, timeout=600):
    exe = soffice()
    if not exe:
        raise RuntimeError("LibreOffice (soffice) not found")
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    profile = Path(tempfile.gettempdir()) / "kb" / "lo-profile"
    profile.mkdir(parents=True, exist_ok=True)
    cmd = [exe, f"-env:UserInstallation=file://{profile}", "--headless",
           "--convert-to", "pdf", "--outdir", str(out_dir), *map(str, docx_paths)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)
    return [Path(out_dir) / (Path(d).stem + ".pdf") for d in docx_paths]


def expected_strings(spec):
    want = [spec["title"], F.H_OVERVIEW, F.H_CONSIDERATIONS, F.H_CONCLUSION]
    for st in spec["steps"]:
        want += re.findall(r"\*\*(.+?)\*\*", st.get("text", ""))
    return want


def _norm(s):
    return re.sub(r"\s+", " ", s or "").strip().lower()


def verify(pdf_path, spec):
    """(errors, warnings) for one PDF."""
    from pypdf import PdfReader
    errors, warnings = [], []
    r = PdfReader(str(pdf_path))
    if not r.pages:
        return ["no pages"], []
    w, h = (round(float(v)) for v in r.pages[0].mediabox[2:4])
    if (w, h) != LETTER_PT:
        errors.append(f"page is {w}x{h}pt, not Letter")
    text = _norm(" ".join(p.extract_text() or "" for p in r.pages))
    for s in expected_strings(spec):
        if _norm(s) not in text:
            errors.append(f"missing text: {s[:50]!r}")
    images = sum(len(p.images) for p in r.pages)
    want = sum(1 for st in spec["steps"] if st.get("image"))
    if images != want:
        errors.append(f"{images} images, spec has {want}")
    fonts = set()
    for p in r.pages:
        res = p.get("/Resources") or {}
        for f in (res.get("/Font") or {}).values():
            name = str(f.get_object().get("/BaseFont", ""))
            fonts.add(name.split("+")[-1].lstrip("/"))
    odd = sorted(f for f in fonts if f and not f.lower().startswith(OK_FONTS))
    if odd:
        warnings.append("font substituted: " + ", ".join(odd))
    return errors, warnings


def cmd_convert(a):
    try:
        out = convert(a.docx, a.out_dir)
    except (RuntimeError, subprocess.SubprocessError) as e:
        emit({"ok": False, "action": "convert", "error": str(e)})
        return 1
    made = [p for p in out if p.exists()]
    print(f"converted {len(made)} of {len(out)} documents")
    emit({"ok": len(made) == len(out), "action": "convert", "converted": len(made),
          "failed": len(out) - len(made)})
    return 0 if len(made) == len(out) else 1


def cmd_verify(a):
    pairs = json.loads(Path(a.pairs).read_text())
    report, bad, warn = [], 0, 0
    for p in pairs:
        spec = json.loads(Path(p["spec"]).read_text())
        errs, warns = verify(p["pdf"], spec) if Path(p["pdf"]).exists() else (["no PDF"], [])
        bad += bool(errs)
        warn += bool(warns)
        report.append({"pdf": p["pdf"], "errors": errs, "warnings": warns})
    if a.out:
        Path(a.out).write_text(json.dumps(report, indent=1))
    print(f"verified {len(pairs)} PDFs · {bad} failed · {warn} with warnings")
    emit({"ok": not bad, "action": "verify", "checked": len(pairs), "failed": bad,
          "warnings": warn})
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("convert"); s.add_argument("--docx", nargs="+", required=True)
    s.add_argument("--out-dir", required=True)
    s = sub.add_parser("verify"); s.add_argument("--pairs", required=True); s.add_argument("--out")
    a = ap.parse_args(argv)
    return {"convert": cmd_convert, "verify": cmd_verify}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
