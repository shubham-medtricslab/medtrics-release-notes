#!/usr/bin/env python3
"""Promote learned overrides from a run zip into scripts/kb/kb_routes.json (dev tool, not shipped).

    python3 tools/kb_promote_overrides.py <run.zip | kb_overrides.json> [--apply] [--all]

Reads the kb_overrides.json a run copied into its shots/ folder (0.26.0), lays the
entries over the current kb_routes.json and prints the change per surface. Without
--apply nothing is written. Learned entries only, unless --all also takes the
hand-written ones (clicks, read_posts, ...): look at those before you ship them.
"""

import argparse
import difflib
import json
import os
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "kb"))
import kb_overrides as O  # noqa: E402

ROUTES = ROOT / "scripts" / "kb" / "kb_routes.json"


def read_overrides(src):
    src = Path(src)
    if zipfile.is_zipfile(src):
        with zipfile.ZipFile(src) as z:
            names = [n for n in z.namelist() if n.endswith("/" + O.NAME) or n == O.NAME]
            if not names:
                raise SystemExit(f"no {O.NAME} in {src} (the run used no overrides)")
            return json.loads(z.read(sorted(names)[-1]))
    return json.loads(src.read_text())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--routes", default=str(ROUTES))
    a = ap.parse_args(argv)
    data = read_overrides(a.source)
    ents = [e for e in data.get("entries") or [] if isinstance(e, dict)
            and (a.all or e.get("source") == "learned")]
    routes = json.loads(Path(a.routes).read_text())
    merged, applied, skipped = O.merge(routes, {"entries": ents})
    for e, why in skipped:
        print(f"skipped ({why}): {O.line(e)}")
    for e in applied:
        print(f"takes: {O.line(e)}")
    before = json.dumps(routes, indent=1, ensure_ascii=False).splitlines()
    after = json.dumps(merged, indent=1, ensure_ascii=False).splitlines()
    diff = list(difflib.unified_diff(before, after, "kb_routes.json", "kb_routes.json (promoted)", lineterm="", n=2))
    print("\n".join(diff) if diff else "no change to kb_routes.json")
    if a.apply and diff:
        Path(a.routes).write_text(json.dumps(merged, indent=1, ensure_ascii=False) + "\n")
        print(f"written: {a.routes}")
    elif diff:
        print("\n(not written; pass --apply)")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "scripts"))
    import utf8_mode
    utf8_mode.ensure()          # Windows: rerun in UTF-8 mode
    sys.exit(main())
