#!/usr/bin/env python3
"""
build_plugin.py — bundle the plugin directory into a versioned .plugin file.

Reads the version from .claude-plugin/plugin.json and produces
medtrics-release-notes-<version>.plugin in the repo root, excluding:

  - .git, .gitignore, .gitattributes
  - .mypy_cache, .ruff_cache, .pytest_cache, __pycache__
  - dist/, build/, node_modules/, .cowork/, .claude/
    (.claude-plugin/ is NOT excluded — it must ship)
  - .DS_Store, *.pyc
  - tests/                       (not shipped in the plugin)
  - requirements*.txt            (dev-side; ip_guard.py is stdlib-only)
  - CONTRIBUTING.md, PLAN.md, ORACLE-PLAN.md, PR-MATCHER-PLAN.md  (dev-side)
  - .env, .env.local, and any filename starting with .env
  - *.pdf, *.tmp, *.zip, *.log
  - any *.plugin (previous build outputs)

The bundle is written to <name>.plugin.tmp, verified, then atomically
renamed to the final name. A failed build leaves no artifact behind.

Usage:
    python3 scripts/build_plugin.py
    python3 scripts/build_plugin.py --output-dir /path/to/out
    python3 scripts/build_plugin.py --include-tests   # for QA bundles only
"""

import argparse
import json
import os
import sys
import zipfile
from pathlib import Path


# NOTE: ".claude" (the directory) is excluded; ".claude-plugin" is a different
# directory name and is NOT excluded — the manifest must ship in the bundle.
EXCLUDE_DIRS_COMMON = {
    ".git", ".mypy_cache", ".ruff_cache", ".pytest_cache", "__pycache__",
    "dist", "build", "node_modules", ".cowork", ".claude",
    # extra/ is the unshipped KB working area (hand-run pipeline, reference
    # PDFs); .kb-sessions/ holds demo-site storage states, which are credentials.
    "extra", ".kb-sessions", ".kb-env",
}
EXCLUDE_DIRS_DEFAULT = EXCLUDE_DIRS_COMMON | {"tests"}
EXCLUDE_DIRS_INCLUDE_TESTS = set(EXCLUDE_DIRS_COMMON)
EXCLUDE_FILES = {
    ".DS_Store", ".gitignore", ".gitattributes",
    "requirements.txt", "requirements-dev.txt", "CONTRIBUTING.md",
    "PLAN.md", "ORACLE-PLAN.md", "PR-MATCHER-PLAN.md", "RELEASE-ARCHIVE-PLAN.md",
    "KB-PLAN.md", ".env", ".env.local",
    "build_plugin.py",  # the bundler itself is a dev tool; never ship it
}
# Any filename starting with one of these prefixes is excluded (.env, .env.*, ...).
EXCLUDE_PREFIXES = (".env",)
EXCLUDE_SUFFIXES = {".pyc", ".plugin", ".pdf", ".tmp", ".zip", ".log"}


class BuildError(Exception):
    """Raised when the build or its post-build verification fails."""


def repo_root() -> Path:
    """The plugin root. This script lives at <root>/scripts/build_plugin.py."""
    return Path(__file__).resolve().parent.parent


def read_version(root: Path) -> str:
    manifest = root / ".claude-plugin" / "plugin.json"
    if not manifest.exists():
        sys.exit(f"ERROR: manifest not found at {manifest}")
    return json.loads(manifest.read_text())["version"]


def _write_dir_entry(zf: zipfile.ZipFile, arcname: str) -> None:
    """Write an explicit directory entry into the zip.

    Some plugin validators (including the Cowork plugin loader) require
    explicit directory records, not just inferred-from-file-paths directories.
    The entry is a 0-byte record with the directory bit set in external_attr.
    """
    if not arcname.endswith("/"):
        arcname = arcname + "/"
    info = zipfile.ZipInfo(arcname)
    # 0o40755 = directory mode with rwxr-xr-x. Bit shift into the high 16
    # bits of external_attr per the zip spec.
    info.external_attr = (0o40755 << 16) | 0x10  # 0x10 = MS-DOS directory flag
    zf.writestr(info, b"")


def build(root: Path, out_dir: Path, include_tests: bool = False) -> Path:
    version = read_version(root)
    out_path = out_dir / f"medtrics-release-notes-{version}.plugin"
    # Atomic output: build into a .tmp alongside the final name, verify, then
    # os.replace into place. A failed build leaves neither artifact behind.
    # (.tmp is in EXCLUDE_SUFFIXES, so an in-progress/orphaned tmp is never
    # bundled into a build.)
    tmp_path = out_dir / f"medtrics-release-notes-{version}.plugin.tmp"
    excludes = EXCLUDE_DIRS_INCLUDE_TESTS if include_tests else EXCLUDE_DIRS_DEFAULT

    files_added = 0
    dirs_added = 0
    written_dirs: set[str] = set()

    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for current_root, dirs, files in os.walk(root):
                dirs[:] = sorted(d for d in dirs if d not in excludes)
                rel_root = Path(current_root).relative_to(root)

                # Write a directory entry for every subdir (skip the plugin root).
                if str(rel_root) != ".":
                    arc = rel_root.as_posix() + "/"
                    if arc not in written_dirs:
                        _write_dir_entry(zf, arc)
                        written_dirs.add(arc)
                        dirs_added += 1

                for f in sorted(files):
                    if f in EXCLUDE_FILES:
                        continue
                    if f.startswith(EXCLUDE_PREFIXES):
                        continue
                    if any(f.endswith(suffix) for suffix in EXCLUDE_SUFFIXES):
                        continue
                    full = Path(current_root) / f
                    rel = full.relative_to(root)
                    zf.write(full, rel.as_posix())
                    files_added += 1

        size = tmp_path.stat().st_size

        # Post-build self-check. The Cowork plugin loader rejects a bundle that
        # is missing either the explicit .claude-plugin/ directory entry or the
        # plugin.json file inside it. Catch the regression here, not at install.
        _verify_bundle(tmp_path)

        os.replace(tmp_path, out_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

    print(f"wrote {out_path}")
    print(f"  version: {version}")
    print(f"  dirs:    {dirs_added}")
    print(f"  files:   {files_added}")
    print(f"  size:    {size:,} bytes")
    return out_path


def _verify_bundle(plugin_path: Path) -> None:
    """Raise BuildError if the freshly-built bundle is invalid.

    Raising (instead of sys.exit) lets the caller clean up the .tmp artifact.
    """
    required_dir = ".claude-plugin/"
    required_file = ".claude-plugin/plugin.json"
    with zipfile.ZipFile(plugin_path) as zf:
        names = set(zf.namelist())
        # Hard assertion: no secrets and no PDFs may ever ship in a bundle.
        for entry in sorted(names):
            basename = entry.rstrip("/").rsplit("/", 1)[-1]
            if basename.startswith(".env"):
                raise BuildError(f"BUILD FAILED: bundle contains env file: {entry}")
            if basename.endswith(".pdf"):
                raise BuildError(f"BUILD FAILED: bundle contains a PDF: {entry}")
        # Hard assertion: the KB working area and demo sessions never ship.
        for entry in sorted(names):
            top = entry.split("/", 1)[0]
            if top in ("extra", ".kb-sessions", ".kb-env"):
                raise BuildError(f"BUILD FAILED: bundle contains {top}/: {entry}")
        # The KB pipeline must ship whole: its format module and writer agent.
        # ...and its click guard: the hook that keeps the capturer read-only.
        for required in ("scripts/kb/kb_format.py", "agents/kb-writer.md",
                         "hooks/hooks.json", "hooks/kb_guard.py"):
            if required not in names:
                raise BuildError(f"BUILD FAILED: bundle is missing {required}")
        # plugin.json must be present and parseable
        if required_file not in names:
            raise BuildError(f"BUILD FAILED: bundle is missing {required_file}")
        # explicit directory entry must be present (some validators require it)
        if required_dir not in names:
            raise BuildError(
                f"BUILD FAILED: bundle is missing explicit dir entry {required_dir}")
        # plugin.json must contain version + name + description
        try:
            manifest = json.loads(zf.read(required_file).decode("utf-8"))
        except json.JSONDecodeError as e:
            raise BuildError(f"BUILD FAILED: {required_file} is not valid JSON: {e}")
        for key in ("name", "version", "description"):
            if key not in manifest:
                raise BuildError(
                    f"BUILD FAILED: {required_file} missing required key: {key}")
        # description length cap (matches the loader's strict cap)
        if len(manifest["description"]) > 256:
            raise BuildError(
                f"BUILD FAILED: plugin.json description is "
                f"{len(manifest['description'])} chars; cap is 256."
            )


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--output-dir", default=None,
                    help="Where to write the .plugin file (default: repo root)")
    ap.add_argument("--include-tests", action="store_true",
                    help="Include tests/ in the bundle (for QA validation builds)")
    args = ap.parse_args()

    root = repo_root()
    out_dir = Path(args.output_dir) if args.output_dir else root
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        build(root, out_dir, include_tests=args.include_tests)
    except BuildError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
