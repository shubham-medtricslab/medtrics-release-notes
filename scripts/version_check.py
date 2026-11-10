#!/usr/bin/env python3
"""
version_check.py — Step 0 of every medtrics-release-notes command.

Compares the installed plugin's version (this copy's .claude-plugin/plugin.json)
with the latest version on GitHub (the repo's main branch). Claude Code keeps an
installed plugin at the version it was installed at — a restart or a new chat
does not update it — so an old copy silently runs an old pipeline. This makes
that visible before anything else runs.

It never blocks on its own and never fails the command: no network, a blocked
host (some Cowork sandboxes) or a bad response all report status "unknown".

Prints a human line on stderr and a machine-readable block on stdout:

    ---VERSION_CHECK_RESULT---
    {"ok": true, "status": "current|behind|ahead|unknown",
     "installed": "0.23.1", "latest": "0.23.1", "message": "...", "error": null}

Exit code is always 0.

Usage:
    python3 version_check.py
    python3 version_check.py --latest-file plugin.json   # tests / offline
"""

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
LATEST_URL = ("https://raw.githubusercontent.com/shubham-medtricslab/"
              "medtrics-release-notes/main/.claude-plugin/plugin.json")
TIMEOUT = 5  # seconds — a slow network must not hold up a release

UPDATE_STEPS = (
    "To update: in Claude Code run /plugin → Marketplaces → medtrics → Update "
    "marketplace, then update medtrics-release-notes and restart Claude Code "
    "(a restart or new chat alone does not update a plugin). In Cowork, "
    "install the newest medtrics-release-notes-<version>.plugin file."
)

VERSION_RE = re.compile(r"^\s*v?(\d+)\.(\d+)\.(\d+)")


def parse_version(value):
    """'0.23.1' → (0, 23, 1); None when it is not a version."""
    m = VERSION_RE.match(str(value or ""))
    return tuple(int(x) for x in m.groups()) if m else None


def read_version(text):
    data = json.loads(text)
    version = data.get("version") if isinstance(data, dict) else None
    if parse_version(version) is None:
        raise ValueError(f"no valid version in manifest: {version!r}")
    return version.strip()


def fetch_latest(url=LATEST_URL, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={
        "User-Agent": "medtrics-release-notes-version-check",
        "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return read_version(resp.read().decode("utf-8"))


def compare(installed, latest):
    a, b = parse_version(installed), parse_version(latest)
    if a is None or b is None:
        return "unknown"
    if a < b:
        return "behind"
    if a > b:
        return "ahead"
    return "current"


def build_result(installed, latest, error=None):
    status = compare(installed, latest) if not error else "unknown"
    if status == "behind":
        message = (f"medtrics-release-notes is out of date: installed "
                   f"{installed}, latest {latest}. This run would use the old "
                   f"pipeline. {UPDATE_STEPS}")
    elif status == "unknown":
        message = (f"Could not check for a newer medtrics-release-notes "
                   f"(installed {installed or '?'}): {error or 'bad version'}. "
                   "Continuing.")
    elif status == "ahead":
        message = (f"medtrics-release-notes {installed} is newer than GitHub "
                   f"main ({latest}) — a local development copy.")
    else:
        message = f"medtrics-release-notes {installed} is up to date."
    return {"ok": True, "status": status, "installed": installed or None,
            "latest": latest or None, "message": message, "error": error}


def run(latest_file="", url=LATEST_URL, manifest=MANIFEST):
    try:
        installed = read_version(Path(manifest).read_text())
    except (OSError, ValueError) as e:
        return build_result("", "", f"installed manifest unreadable: {e}")
    try:
        if latest_file:
            latest = read_version(Path(latest_file).read_text())
        else:
            latest = fetch_latest(url)
    except Exception as e:  # network, HTTP, sandbox block, bad JSON
        return build_result(installed, "", f"{type(e).__name__}: {e}")
    return build_result(installed, latest)


def main():
    ap = argparse.ArgumentParser(description="Is this plugin copy the latest?")
    ap.add_argument("--latest-file", default="",
                    help="Read the latest manifest from a file instead of GitHub")
    ap.add_argument("--url", default=LATEST_URL, help=argparse.SUPPRESS)
    args = ap.parse_args()
    result = run(args.latest_file, args.url)
    print(result["message"], file=sys.stderr)
    print()
    print("---VERSION_CHECK_RESULT---")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import utf8_mode
    utf8_mode.ensure()
    sys.exit(main())
