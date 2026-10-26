#!/usr/bin/env python3
"""
kb_doctor.py — can /kb-articles run here, and with what?

Checks every dependency the pipeline touches and prints the install line for
anything missing. Nothing is installed. Exit 0 when the pipeline can produce
drafts (python-docx and Pillow are the only hard requirements); everything
else degrades: no Playwright -> no screenshots (`--no-capture` drafts), no
tesseract -> draft-only dom_box anchors, no LibreOffice -> no PDF, no pypdf ->
PDFs unverified.

It also warns when CLAUDE_CODE_SUBAGENT_MODEL is set: that variable overrides
the model each KB agent pins (Haiku classifies, Sonnet writes).

    ---KB_DOCTOR_RESULT---
    {"ok", "checks": {name: bool}, "sessions": {...}, "warnings": [...]}
"""

import argparse
import importlib
import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

CHECKS = [
    # name, kind, target, hard, install hint, what it costs when missing
    ("python-docx", "module", "docx", True, "pip install python-docx", "no .docx at all"),
    ("Pillow", "module", "PIL", True, "pip install pillow", "no annotation"),
    ("numpy", "module", "numpy", False, "pip install numpy", "no QA-GIF segmentation"),
    ("pypdf", "module", "pypdf", False, "pip install pypdf", "PDFs are not verified"),
    ("pytesseract", "module", "pytesseract", False, "pip install pytesseract",
     "anchors are draft-only dom_box"),
    ("tesseract", "binary", "tesseract", False,
     "apt-get install -y tesseract-ocr  |  brew install tesseract",
     "anchors are draft-only dom_box; tenant screen reads DOM text only"),
    ("playwright", "module", "playwright", False,
     "pip install playwright && python -m playwright install --with-deps chromium",
     "no live screenshots (drafts carry missing-shot markers)"),
    ("LibreOffice", "soffice", "", False,
     "apt-get install -y libreoffice-writer  |  brew install --cask libreoffice", "no PDF"),
    ("Carlito font", "font", "Carlito", False, "apt-get install -y fonts-crosextra-carlito",
     "PDF may substitute Calibri and reflow"),
]


def _module(name):
    try:
        importlib.import_module(name)
        return True
    except Exception:
        return False


def _font(name):
    for d in ("/usr/share/fonts", "/Library/Fonts", "/System/Library/Fonts",
              str(Path.home() / "Library/Fonts")):
        for _ in Path(d).rglob(f"*{name}*") if Path(d).exists() else []:
            return True
    return False


def run_checks():
    import kb_pdf
    res = {}
    for name, kind, target, _hard, _hint, _cost in CHECKS:
        if kind == "module":
            res[name] = _module(target)
        elif kind == "binary":
            res[name] = bool(shutil.which(target))
        elif kind == "soffice":
            res[name] = bool(kb_pdf.soffice())
        else:
            res[name] = _font(target)
    return res


def sessions(session_dir):
    d = Path(session_dir)
    return {p: ("present" if (d / f"{p}.json").exists() else "missing")
            for p in ("admin", "faculty", "learner")}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--session-dir", default=os.environ.get("KB_SESSION_DIR") or ".kb-sessions")
    a = ap.parse_args(argv)
    res = run_checks()
    warnings = []
    for name, _k, _t, hard, hint, cost in CHECKS:
        mark = "ok " if res[name] else ("ERR" if hard else "-- ")
        line = f"  {mark} {name}"
        if not res[name]:
            line += f"  → {cost}. Install: {hint}"
        print(line)
    pinned = os.environ.get("CLAUDE_CODE_SUBAGENT_MODEL")
    if pinned:
        warnings.append(f"CLAUDE_CODE_SUBAGENT_MODEL={pinned} overrides the KB agents' "
                        "pinned models (Haiku classifier, Sonnet writer)")
    sess = sessions(a.session_dir)
    print("  sessions: " + " · ".join(f"{k} {v}" for k, v in sess.items()) +
          f"  ({a.session_dir}; sign in with kb_capture.py login --persona <p>)")
    for w in warnings:
        print(f"  WARN {w}")
    ok = all(res[n] for n, _k, _t, hard, _h, _c in CHECKS if hard)
    print("---KB_DOCTOR_RESULT---")
    print(json.dumps({"ok": ok, "checks": res, "sessions": sess, "warnings": warnings}))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
