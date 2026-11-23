#!/usr/bin/env python3
"""Local overrides for kb_routes.json (0.26.0): page quirks that need no plugin release.

File: <folder>/.medtrics-release-notes/kb_overrides.json (next to config.json; the
folder is KB_HOME or the working directory, as in kb_paths). Shape:

    {"version": 1, "entries": [
      {"persona": "admin", "surface": "Benefits > Time Off", "key": "quiet_cap_s",
       "value": 20, "source": "learned", "run": "kb-20261123-2158", "date": "2026-11-23"},
      {"persona": "admin", "surface": "Cases > Case logs", "key": "alias",
       "label": "Reassign Reviewer", "value": "Reassign Case Log Reviewer", "source": "manual"}]}

Keys (KEYS): wait_s, quiet_cap_s, alias (needs "label"), loader_sel, clicks, read_posts.
`clicks` and `read_posts` change what the capture may press or send: they count only
with "source": "manual" (HAND_ONLY) and the run never writes them. `read_posts` still
passes kb_capture.read_post_ok where it is used.

Every script that reads kb_routes.json reads it through load_routes(), which lays the
entries over the matching surface (persona + surface). An entry for a surface the
routes file does not have is ignored (`check` lists it).

  kb_overrides.py path  [--home F]          print the file's path
  kb_overrides.py check [--home F]          validate; list what applies and what does not
  kb_overrides.py show  [--home F]          print the entries, one line each
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_ROUTES = HERE / "kb_routes.json"
CONFIG_DIR = ".medtrics-release-notes"
NAME = "kb_overrides.json"
VERSION = 1
KEYS = ("wait_s", "quiet_cap_s", "alias", "loader_sel", "clicks", "read_posts")
HAND_ONLY = ("clicks", "read_posts")          # never learned, never applied unless manual
LEARNABLE = ("wait_s", "quiet_cap_s", "alias")
MAX_WAIT_S = 10                               # kb_capture.MAX_WAIT_S
MAX_QUIET_CAP_S = 20                          # kb_capture.QUIET_CAP_MAX_S
MAX_FAILS = 2                                 # a learned entry that fails this often in a row is dropped
SEL_RE = re.compile(r"^[\w\-.#\[\]=*^$~|\"' ,>:()]{1,120}$")     # a plain CSS selector, no braces / scripts


def folder(home=None):
    return Path(home or os.environ.get("KB_HOME") or os.getcwd()).resolve()


def path(home=None):
    return folder(home) / CONFIG_DIR / NAME


def load(home=None):
    """{"version", "entries": [...]}; a missing or unreadable file is an empty set."""
    p = path(home)
    try:
        d = json.loads(p.read_text())
    except (OSError, ValueError):
        return {"version": VERSION, "entries": []}
    ents = d.get("entries") if isinstance(d, dict) else None
    return {"version": VERSION, "entries": [e for e in ents or [] if isinstance(e, dict)]}


def save(data, home=None):
    p = path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"version": VERSION, "entries": data.get("entries") or []},
                            indent=1, ensure_ascii=False) + "\n")
    return p


def problem(e):
    """Why an entry cannot apply, or ""."""
    key, v = e.get("key"), e.get("value")
    if key not in KEYS:
        return f"unknown key {key!r}"
    if not e.get("persona") or not e.get("surface"):
        return "persona and surface are required"
    if key in HAND_ONLY and e.get("source") != "manual":
        return f"{key} counts only with \"source\": \"manual\""
    if key in ("wait_s", "quiet_cap_s"):
        top = MAX_WAIT_S if key == "wait_s" else MAX_QUIET_CAP_S
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 <= v <= top:
            return f"{key} must be a number from 0 to {top}"
    elif key == "alias":
        if not (isinstance(e.get("label"), str) and e["label"].strip()
                and isinstance(v, str) and v.strip()):
            return "alias needs a \"label\" and a text \"value\""
    elif key == "loader_sel":
        if not (isinstance(v, str) and SEL_RE.match(v)):
            return "loader_sel must be a plain CSS selector"
    elif key == "clicks":
        if not (isinstance(v, list) and all(isinstance(c, dict) and (c.get("text") or c.get("check") or c.get("row"))
                                            for c in v)):
            return "clicks must be a list of {\"text\"} / {\"check\"} / {\"row\": true}"
    elif key == "read_posts":
        if not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
            return "read_posts must be a list of paths"
    return ""


def _surface(routes, e):
    for s in routes.get("surfaces") or []:
        if s.get("surface") == e.get("surface") and \
                e.get("persona") in [s.get("persona")] + list(s.get("also_personas") or []):
            return s
    return None


def merge(routes, data):
    """routes with the valid entries laid over their surfaces (a deep copy);
    -> (routes, applied entries, [(entry, why not)])."""
    routes = json.loads(json.dumps(routes))
    applied, skipped = [], []
    for e in (data or {}).get("entries") or []:
        why = problem(e)
        s = None if why else _surface(routes, e)
        if not why and s is None:
            why = "no such surface in kb_routes.json"
        if why:
            skipped.append((e, why))
            continue
        key, v = e["key"], e["value"]
        if key in ("wait_s", "quiet_cap_s", "clicks"):
            s[key] = v
        elif key == "alias":
            lst = s.setdefault("aliases", {}).setdefault(e["label"].strip(), [])
            if v.strip() not in lst:
                lst.append(v.strip())
        elif key == "loader_sel":
            if v not in s.setdefault("loader_sel", []):
                s["loader_sel"].append(v)
        elif key == "read_posts":
            s["read_posts"] = sorted(set(list(s.get("read_posts") or []) + v))
        applied.append(e)
    return routes, applied, skipped


def load_routes(routes_path=None, home=None):
    """kb_routes.json (or `routes_path`) with the folder's overrides applied."""
    routes = json.loads(Path(routes_path or DEFAULT_ROUTES).read_text())
    if os.environ.get("KB_NO_OVERRIDES"):
        return routes
    return merge(routes, load(home))[0]


def same(a, b):
    return all(a.get(k) == b.get(k) for k in ("persona", "surface", "key", "label")) \
        and (a.get("key") != "alias" or a.get("value") == b.get("value"))


def add_learned(data, persona, surface, key, value, run, date, label=None):
    """Add or refresh one learned entry; never touches a manual entry for the same thing,
    never writes a HAND_ONLY key. -> the entry written, or None."""
    if key not in LEARNABLE:
        return None
    e = {"persona": persona, "surface": surface, "key": key, "value": value,
         "source": "learned", "run": run, "date": date, "fails": 0}
    if label:
        e["label"] = label
    if problem(e):
        return None
    for old in data["entries"]:
        if same(old, e):
            if old.get("source") == "manual":
                return None
            if key != "alias" and isinstance(old.get("value"), (int, float)) and old["value"] >= value:
                return None                        # already at least as generous
            old.update(e)
            return old
    data["entries"].append(e)
    return e


def note_result(data, entry, ok):
    """Count a learned entry's outcome this run; -> True when it was dropped (MAX_FAILS)."""
    if entry.get("source") != "learned":
        return False
    entry["fails"] = 0 if ok else int(entry.get("fails") or 0) + 1
    if entry["fails"] >= MAX_FAILS:
        data["entries"] = [x for x in data["entries"] if x is not entry]
        return True
    return False


def line(e):
    lab = f" \"{e['label']}\" ->" if e.get("label") else ""
    src = e.get("source") or "?"
    if src == "learned":
        src += f" {e.get('run') or ''} {e.get('date') or ''}".rstrip()
    return f"{e.get('persona')} / {e.get('surface')}: {e.get('key')}{lab} {json.dumps(e.get('value'), ensure_ascii=False)} ({src})"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("path", "check", "show"))
    ap.add_argument("--home")
    ap.add_argument("--routes", default=str(DEFAULT_ROUTES))
    a = ap.parse_args(argv)
    if a.cmd == "path":
        print(path(a.home))
        return 0
    data = load(a.home)
    if a.cmd == "show":
        for e in data["entries"]:
            print(line(e))
        print(f"{len(data['entries'])} override(s) in {path(a.home)}")
        return 0
    _, applied, skipped = merge(json.loads(Path(a.routes).read_text()), data)
    for e in applied:
        print("applies: " + line(e))
    for e, why in skipped:
        print(f"IGNORED ({why}): " + line(e))
    print(json.dumps({"ok": not skipped, "action": "overrides-check", "applied": len(applied),
                      "ignored": len(skipped), "path": str(path(a.home))}))
    return 1 if skipped else 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import utf8_mode
    utf8_mode.ensure()          # Windows: rerun in UTF-8 mode
    sys.exit(main())
