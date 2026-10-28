#!/usr/bin/env python3
"""
kb_paths.py — where /kb-articles keeps its files, resolved at run time.

Everything lives in one folder: --home, else $KB_HOME, else the current folder
(in Cowork, the selected folder). Nothing absolute is ever stored in a file,
because a Cowork chat's folder path changes from chat to chat.

  paths      (default) print the three lines a command needs:
               KB_PY=<folder>/.kb-env/bin/python   (empty when not installed yet)
               KB_RUNS=<folder>/.kb-runs
               KB_CONFIG=<folder>/.medtrics-release-notes/config.json
  ensure     create .kb-runs/ and .medtrics-release-notes/ (and give .kb-env/ a
             .gitignore) — each with a self-ignoring .gitignore ("*") so nothing
             in them ever reaches a commit — then print the same lines.
  config     print the config as JSON ({} when none yet).
  merge      deep-merge a JSON object (an argument, or stdin) into the config,
             e.g.  merge '{"mem": {"kb_ledger": "<collection id>"}}'
  env-check  record which of OPTIMUS_API_KEY, GITLAB_TOKEN, GITLAB_PROJECT_ID
             are set in <folder>/.env or the environment (names only, never the
             values) under env_checked, and print them.

The config is <folder>/.medtrics-release-notes/config.json. Only when that
folder cannot be written (a read-only Claude Code checkout) it falls back to
~/.medtrics-release-notes/config.json. Shape:

    {"schema": 1, "created", "updated",
     "kb": {"setup": "done", "items": {name: status}},
     "mem": {"kb_ledger": id, "kb_queue": id, "kb_catalogue": id, "release_archive": id},
     "env_checked": {"OPTIMUS_API_KEY": true, ...}}

Other scripts import: folder(), kb_py(), runs_dir(), config_path(), load(),
save(), merge().
"""

import argparse
import datetime as _dt
import json
import os
import sys
from pathlib import Path

CONFIG_DIR = ".medtrics-release-notes"
CONFIG_NAME = "config.json"
RUNS_DIR = ".kb-runs"
ENV_DIR = ".kb-env"
ENV_KEYS = ("OPTIMUS_API_KEY", "GITLAB_TOKEN", "GITLAB_PROJECT_ID")
SCHEMA = 1


def folder(arg=None):
    return Path(arg or os.environ.get("KB_HOME") or os.getcwd()).resolve()


def kb_py(h):
    """The environment's python if it exists now, else None."""
    for p in (h / ENV_DIR / "bin" / "python", h / ENV_DIR / "Scripts" / "python.exe"):
        if p.exists():
            return p
    return None


def runs_dir(h):
    return h / RUNS_DIR


def _writable_dir(d):
    try:
        self_ignoring(d)        # never leave the dir without its own .gitignore
        probe = d / ".write-test"
        probe.write_text("")
        probe.unlink()
        return True
    except OSError:
        return False


def config_path(h):
    """In the folder; the home fallback only when the folder cannot be written."""
    primary = h / CONFIG_DIR / CONFIG_NAME
    if primary.exists() or _writable_dir(h / CONFIG_DIR):
        return primary
    return Path.home() / CONFIG_DIR / CONFIG_NAME


def self_ignoring(d):
    """Create d with a .gitignore that ignores everything in it, itself included."""
    d.mkdir(parents=True, exist_ok=True)
    g = d / ".gitignore"
    if not g.exists():
        g.write_text("*\n")


def ensure(h):
    for name in (RUNS_DIR, CONFIG_DIR):
        try:
            self_ignoring(h / name)
        except OSError:
            pass
    if (h / ENV_DIR).exists():
        try:
            self_ignoring(h / ENV_DIR)
        except OSError:
            pass


def load(h):
    try:
        data = json.loads(config_path(h).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _deep(base, extra):
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep(base[k], v)
        else:
            base[k] = v
    return base


def save(h, cfg):
    cfg = dict(cfg)
    now = _dt.datetime.now().isoformat(timespec="seconds")
    cfg["schema"] = SCHEMA
    cfg.setdefault("created", now)
    cfg["updated"] = now
    p = config_path(h)
    self_ignoring(p.parent)
    p.write_text(json.dumps(cfg, indent=1, ensure_ascii=False))
    return cfg


def merge(h, extra):
    return save(h, _deep(load(h), extra))


def env_names(h):
    """{key: bool} — set in <folder>/.env or the environment. Never the values."""
    found = {}
    try:
        for line in (h / ".env").read_text().splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:].lstrip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                if v.strip().strip("'\""):
                    found[k.strip()] = True
    except OSError:
        pass
    return {k: bool(found.get(k) or os.environ.get(k)) for k in ENV_KEYS}


def lines(h):
    py = kb_py(h)
    return [f"KB_PY={py or ''}", f"KB_RUNS={runs_dir(h)}", f"KB_CONFIG={config_path(h)}"]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("cmd", nargs="?", default="paths",
                    choices=("paths", "ensure", "config", "merge", "env-check"))
    ap.add_argument("json", nargs="?", help="merge: the JSON object (default: stdin)")
    ap.add_argument("--home", help="the folder (default $KB_HOME, else cwd)")
    a = ap.parse_args(argv)
    h = folder(a.home)
    if a.cmd == "config":
        print(json.dumps(load(h), indent=1))
        return 0
    if a.cmd == "merge":
        try:
            extra = json.loads(a.json if a.json is not None else sys.stdin.read())
            if not isinstance(extra, dict):
                raise ValueError("not an object")
        except ValueError as e:
            print(f"merge: need a JSON object ({e})", file=sys.stderr)
            return 1
        ensure(h)
        print(json.dumps(merge(h, extra), indent=1))
        return 0
    if a.cmd == "env-check":
        ensure(h)
        found = env_names(h)
        merge(h, {"env_checked": found})
        for k, ok in found.items():
            print(f"{k}={'set' if ok else 'MISSING'}")
        return 0 if all(found.values()) else 1
    if a.cmd == "ensure":
        ensure(h)
    for line in lines(h):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
