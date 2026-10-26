#!/usr/bin/env python3
"""
kb_doctor.py — set up, once, everything /kb-articles needs, and remember it.

  --setup   The first run in a folder creates a private Python environment in
            <home>/.kb-env, installs what is missing into it (python-docx,
            Pillow, numpy, pypdf, pytesseract, Playwright + its Chromium), tries
            the system tools (tesseract, LibreOffice, the Carlito font) with
            apt-get or Homebrew, and writes <home>/.kb-env/kb-setup.json.
            Later runs read that file and only confirm the installed pieces are
            still on disk (file lookups, no imports, no network) — nothing is
            re-checked or re-downloaded. A piece that could not be installed is
            recorded as unavailable and not retried; `--setup --force` retries
            everything. Prints `KB_PY=<python>` for the command to run every KB
            script with.
  (none)    Report only, for the current interpreter. Nothing is installed.

<home> is --home, else $KB_HOME, else the current folder (the folder selected
in Cowork). Exit 0 when drafts can be produced (python-docx and Pillow are the
only hard requirements); everything else degrades: no Playwright -> no
screenshots, no tesseract -> draft-only dom_box anchors, no LibreOffice -> no
PDF, no pypdf -> PDFs unverified.

It also warns when CLAUDE_CODE_SUBAGENT_MODEL is set: that variable overrides
the model each KB agent pins.

    ---KB_DOCTOR_RESULT---
    {"ok", "py", "skipped", "items": {name: "installed"|"present"|"unavailable"},
     "sessions": {...}, "warnings": [...]}
"""

import argparse
import datetime as _dt
import glob
import importlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

ENV_DIR = ".kb-env"
LOGIN_FILE = ".env-kb-login"   # capture-site credentials; checked every run (cheap)
MARKER = "kb-setup.json"
BROWSERS = "ms-playwright"

# name -> (pip package, import dir in site-packages, hard requirement, cost when missing)
PY_PACKAGES = {
    "python-docx": ("python-docx", "docx", True, "no .docx at all"),
    "Pillow": ("pillow", "PIL", True, "no annotation"),
    "numpy": ("numpy", "numpy", True, "the shot tools do not load"),
    "pypdf": ("pypdf", "pypdf", False, "PDFs are not verified"),
    "pytesseract": ("pytesseract", "pytesseract", False, "anchors are draft-only dom_box"),
    "playwright": ("playwright", "playwright", False, "no live screenshots"),
}
# name -> (binary or font probe, apt packages, brew args, cost when missing)
SYSTEM = {
    "tesseract": ("tesseract", ["tesseract-ocr"], ["install", "tesseract"],
                  "anchors are draft-only dom_box; tenant screen reads DOM text only"),
    "LibreOffice": ("soffice", ["libreoffice-writer"], ["install", "--cask", "libreoffice"],
                    "no PDF"),
    "Carlito font": ("font:Carlito", ["fonts-crosextra-carlito"], None,
                     "the PDF may substitute Calibri and reflow"),
}
CHROMIUM = "chromium"


def home(arg=None):
    return Path(arg or os.environ.get("KB_HOME") or os.getcwd()).resolve()


def env_python(env):
    for p in (env / "bin" / "python", env / "Scripts" / "python.exe"):
        if p.exists():
            return p
    return None


def site_packages(env):
    hits = glob.glob(str(env / "lib" / "python*" / "site-packages")) + \
        glob.glob(str(env / "Lib" / "site-packages"))
    return Path(hits[0]) if hits else None


# ── probes (filesystem only: fast, no imports, no network) ───────────────────
def _font(name):
    for d in ("/usr/share/fonts", "/Library/Fonts", "/System/Library/Fonts",
              str(Path.home() / "Library/Fonts")):
        if Path(d).exists():
            for _ in Path(d).rglob(f"*{name}*"):
                return True
    return False


def system_present(probe):
    if probe.startswith("font:"):
        return _font(probe[5:])
    if probe == "soffice":
        import kb_pdf
        return bool(kb_pdf.soffice())
    return bool(shutil.which(probe))


def package_present(env, import_dir):
    sp = site_packages(env)
    return bool(sp and ((sp / import_dir).exists() or (sp / f"{import_dir}.py").exists()))


def chromium_present(env):
    return bool(glob.glob(str(env / BROWSERS / "chromium*")))


def probe_all(env):
    st = {n: package_present(env, d) for n, (_p, d, _h, _c) in PY_PACKAGES.items()}
    st.update({n: system_present(p) for n, (p, _a, _b, _c) in SYSTEM.items()})
    st[CHROMIUM] = chromium_present(env)
    return st


# ── installers ───────────────────────────────────────────────────────────────
def _run(cmd, env_vars=None, timeout=1800):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, **(env_vars or {})})
        return r.returncode == 0, (r.stderr or r.stdout).strip()[-300:]
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"{type(e).__name__}: {e}"


def _privileged():
    """The prefix for a system install, or None when we may not run one."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return []
    if shutil.which("sudo") and _run(["sudo", "-n", "true"], timeout=10)[0]:
        return ["sudo", "-n"]
    return None


def install_packages(env, names):
    if not env_python(env):
        ok, err = _run([sys.executable, "-m", "venv", str(env)])
        if not ok:
            return {n: f"unavailable: could not create {env} ({err})" for n in names}
    py = str(env_python(env))
    pkgs = [PY_PACKAGES[n][0] for n in names]
    ok, err = _run([py, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", *pkgs])
    if ok:
        return {n: "installed" for n in names}
    # one at a time, so one broken wheel does not sink the rest
    out = {}
    for n in names:
        ok, err = _run([py, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                        PY_PACKAGES[n][0]])
        out[n] = "installed" if ok else f"unavailable: pip failed ({err})"
    return out


def install_chromium(env):
    py = env_python(env)
    if not py:
        return "unavailable: no environment"
    cmd = [str(py), "-m", "playwright", "install", "chromium"]
    if sys.platform.startswith("linux") and _privileged() is not None:
        cmd.insert(-1, "--with-deps")
    ok, err = _run(cmd, {"PLAYWRIGHT_BROWSERS_PATH": str(env / BROWSERS)})
    return "installed" if ok else f"unavailable: {err}"


def install_system(names):
    out = {}
    apt, brew, pre = shutil.which("apt-get"), shutil.which("brew"), _privileged()
    apt_pkgs = [p for n in names for p in SYSTEM[n][1]]
    if apt and pre is not None and apt_pkgs:
        _run(pre + ["apt-get", "update", "-qq"], timeout=600)
        ok, err = _run(pre + ["apt-get", "install", "-y", "-qq", *apt_pkgs])
        for n in names:
            out[n] = "installed" if ok and system_present(SYSTEM[n][0]) else \
                f"unavailable: apt-get failed ({err})"
        return out
    for n in names:
        args = SYSTEM[n][2]
        if brew and args:
            ok, err = _run([brew, *args])
            out[n] = "installed" if ok and system_present(SYSTEM[n][0]) else \
                f"unavailable: brew failed ({err})"
        else:
            how = "apt-get without root/sudo" if apt else "no apt-get or Homebrew"
            out[n] = f"unavailable: {how} — install by hand: " + (
                f"apt-get install -y {' '.join(SYSTEM[n][1])}" if SYSTEM[n][1] else "")
    return out


# ── setup ────────────────────────────────────────────────────────────────────
def load_marker(env):
    p = env / MARKER
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def still_there(marker, env):
    """Names recorded as working whose files are gone (e.g. a reset sandbox)."""
    now = probe_all(env)
    return [n for n, s in marker.get("items", {}).items()
            if s in ("installed", "present") and not now.get(n)]


def setup(h, force=False, installers=None):
    """Returns (marker, skipped). installers lets tests replace the real ones."""
    inst = installers or {"packages": install_packages, "chromium": install_chromium,
                          "system": install_system}
    env = h / ENV_DIR
    env.mkdir(parents=True, exist_ok=True)
    marker = None if force else load_marker(env)
    if marker:
        gone = still_there(marker, env)
        if not gone:
            return marker, True
        todo = gone
    else:
        now = probe_all(env)
        marker = {"items": {}}
        for n, ok in now.items():
            if ok:
                marker["items"][n] = "present"
        todo = [n for n, ok in now.items() if not ok]

    pkgs = [n for n in todo if n in PY_PACKAGES]
    if pkgs:
        marker["items"].update(inst["packages"](env, pkgs))
    if CHROMIUM in todo or ("playwright" in pkgs and marker["items"].get("playwright") == "installed"):
        if marker["items"].get("playwright") in ("installed", "present"):
            marker["items"][CHROMIUM] = inst["chromium"](env)
        else:
            marker["items"][CHROMIUM] = "unavailable: playwright is not installed"
    sys_todo = [n for n in todo if n in SYSTEM]
    if sys_todo:
        marker["items"].update(inst["system"](sys_todo))

    py = env_python(env)
    marker.update({"py": str(py) if py else sys.executable,
                   "browsers": str(env / BROWSERS),
                   "updated": _dt.datetime.now().isoformat(timespec="seconds")})
    marker.setdefault("created", marker["updated"])
    (env / MARKER).write_text(json.dumps(marker, indent=1))
    return marker, False


def working(marker, name):
    return marker["items"].get(name) in ("installed", "present")


def sessions(session_dir):
    d = Path(session_dir)
    return {p: ("present" if (d / f"{p}.json").exists() else "missing")
            for p in ("admin", "faculty", "learner")}


def _warnings():
    pinned = os.environ.get("CLAUDE_CODE_SUBAGENT_MODEL")
    return [f"CLAUDE_CODE_SUBAGENT_MODEL={pinned} overrides the KB agents' pinned models "
            "(Haiku classifier, Sonnet writer)"] if pinned else []


def cmd_setup(a):
    h = home(a.home)
    marker, skipped = setup(h, force=a.force)
    if skipped:
        print(f"KB setup: already done ({marker.get('updated')}) — skipping the checks.")
    else:
        for n, s in marker["items"].items():
            print(f"  {'ok ' if s in ('installed', 'present') else '-- '} {n}: {s}")
    hard = [n for n, (_p, _d, is_hard, _c) in PY_PACKAGES.items() if is_hard]
    ok = all(working(marker, n) for n in hard)
    missing = [n for n in list(PY_PACKAGES) + list(SYSTEM) + [CHROMIUM] if not working(marker, n)]
    if missing and not skipped:
        print("  missing (recorded, not retried until --setup --force): " + ", ".join(missing))
    issues = []
    if not (home(a.home) / LOGIN_FILE).exists():
        issues.append(f"{LOGIN_FILE} not found in {home(a.home)} — screenshots need a saved "
                      "session; add the file and run kb_capture.py login --env-file")
    for i in issues:
        print(f"  ISSUE {i}")
    print(f"KB_PY={marker['py']}")
    print(f"PLAYWRIGHT_BROWSERS_PATH={marker['browsers']}")
    sess = sessions(a.session_dir)
    for w in _warnings():
        print(f"  WARN {w}")
    print("---KB_DOCTOR_RESULT---")
    print(json.dumps({"ok": ok, "py": marker["py"], "skipped": skipped,
                      "items": marker["items"], "sessions": sess, "issues": issues,
                      "warnings": _warnings()}))
    return 0 if ok else 1


def cmd_report(a):
    """No --setup: what the CURRENT interpreter can do. Installs nothing."""
    def mod(name):
        try:
            importlib.import_module(name)
            return True
        except Exception:
            return False
    items = {n: ("present" if mod(d) else "missing") for n, (_p, d, _h, _c) in PY_PACKAGES.items()}
    items.update({n: ("present" if system_present(p) else "missing")
                  for n, (p, _a, _b, _c) in SYSTEM.items()})
    for n, s in items.items():
        cost = (PY_PACKAGES.get(n) or (None, None, None, None))[3] or SYSTEM.get(n, ("",) * 4)[3]
        print(f"  {'ok ' if s == 'present' else '-- '} {n}" + ("" if s == "present" else f"  → {cost}"))
    print("  (run with --setup to install what is missing into .kb-env)")
    ok = all(items[n] == "present" for n, v in PY_PACKAGES.items() if v[2])
    print("---KB_DOCTOR_RESULT---")
    print(json.dumps({"ok": ok, "py": sys.executable, "skipped": False, "items": items,
                      "sessions": sessions(a.session_dir), "warnings": _warnings()}))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--setup", action="store_true")
    ap.add_argument("--force", action="store_true", help="with --setup: re-check and retry everything")
    ap.add_argument("--home", help="folder that holds .kb-env (default $KB_HOME, else cwd)")
    ap.add_argument("--session-dir", default=os.environ.get("KB_SESSION_DIR") or ".kb-sessions")
    a = ap.parse_args(argv)
    return cmd_setup(a) if a.setup else cmd_report(a)


if __name__ == "__main__":
    sys.exit(main())
