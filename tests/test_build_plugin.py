"""Tests for scripts/build_plugin.py — bundle hygiene and atomic output.

Stdlib-only (unittest). Builds against a temporary fixture tree, never the
real repo.
"""

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "build_plugin", SCRIPTS_DIR / "build_plugin.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_plugin = _load_module()


GOOD_DESCRIPTION = "A minimal fixture plugin for build tests."


class BuildPluginTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "fixture-plugin"
        self.out_dir = Path(self._tmp.name) / "out"
        self.out_dir.mkdir()
        self._make_fixture_tree(description=GOOD_DESCRIPTION)

    def _make_fixture_tree(self, description):
        shutil.rmtree(self.root, ignore_errors=True)
        (self.root / ".claude-plugin").mkdir(parents=True)
        manifest = {
            "name": "fixture-plugin",
            "version": "0.0.1",
            "description": description,
        }
        (self.root / ".claude-plugin" / "plugin.json").write_text(
            json.dumps(manifest))
        (self.root / "commands").mkdir()
        (self.root / "commands" / "hello.md").write_text("# hello\n")
        (self.root / "scripts").mkdir()
        (self.root / "scripts" / "noop.py").write_text("print('ok')\n")
        (self.root / "scripts" / "kb").mkdir()
        (self.root / "scripts" / "kb" / "kb_format.py").write_text("X = 1\n")
        (self.root / "agents").mkdir()
        (self.root / "agents" / "kb-writer.md").write_text("---\n")

    def _build(self):
        return build_plugin.build(self.root, self.out_dir)

    def _zip_names(self, plugin_path):
        with zipfile.ZipFile(plugin_path) as zf:
            return set(zf.namelist())

    # --- Test 1: planted .env files never appear in the bundle ---

    def test_env_files_never_bundled(self):
        (self.root / ".env").write_text("SLACK_BOT_TOKEN=xoxb-secret\n")
        (self.root / ".env.local").write_text("OPTIMUS_API_KEY=secret\n")
        (self.root / ".env.production").write_text("GITLAB_TOKEN=secret\n")
        (self.root / "scripts" / ".env").write_text("NESTED=secret\n")

        plugin_path = self._build()
        names = self._zip_names(plugin_path)

        env_entries = [
            n for n in names
            if n.rstrip("/").rsplit("/", 1)[-1].startswith(".env")
        ]
        self.assertEqual(env_entries, [], f".env leaked into bundle: {env_entries}")
        # Sanity: real content did get bundled.
        self.assertIn(".claude-plugin/plugin.json", names)
        self.assertIn("commands/hello.md", names)

    # --- Test 2: over-long description fails and leaves no artifact ---

    def test_long_description_fails_and_leaves_no_artifact(self):
        self._make_fixture_tree(description="x" * 300)

        with self.assertRaises(build_plugin.BuildError):
            self._build()

        leftovers = (list(self.out_dir.glob("*.plugin"))
                     + list(self.out_dir.glob("*.plugin.tmp"))
                     + list(self.out_dir.glob("*.tmp")))
        self.assertEqual(leftovers, [],
                         f"failed build left artifacts behind: {leftovers}")

    # --- Test 3: planted PDFs never appear in the bundle ---

    def test_pdfs_never_bundled(self):
        (self.root / "Internal-Handoff.pdf").write_bytes(b"%PDF-1.4 fake")
        (self.root / "commands" / "notes.pdf").write_bytes(b"%PDF-1.4 fake")

        plugin_path = self._build()
        names = self._zip_names(plugin_path)

        pdf_entries = [n for n in names if n.lower().endswith(".pdf")]
        self.assertEqual(pdf_entries, [], f"PDF leaked into bundle: {pdf_entries}")

    # --- Extra guards on the generalized exclusions ---

    def test_dev_dirs_and_junk_suffixes_excluded(self):
        for d in ("dist", "build", "node_modules", ".cowork", ".claude", "tests"):
            (self.root / d).mkdir()
            (self.root / d / "junk.txt").write_text("junk\n")
        (self.root / "PLAN.md").write_text("# plan\n")
        (self.root / "ORACLE-PLAN.md").write_text("# plan\n")
        (self.root / "PR-MATCHER-PLAN.md").write_text("# plan\n")
        (self.root / "old.zip").write_bytes(b"PK")
        (self.root / "run.log").write_text("log\n")
        (self.root / "scratch.tmp").write_text("tmp\n")

        plugin_path = self._build()
        names = self._zip_names(plugin_path)

        for banned in ("dist/", "build/", "node_modules/", ".cowork/",
                       ".claude/", "tests/"):
            self.assertFalse(any(n.startswith(banned) for n in names),
                             f"{banned} leaked into bundle")
        for banned_file in ("PLAN.md", "ORACLE-PLAN.md", "PR-MATCHER-PLAN.md",
                            "old.zip", "run.log", "scratch.tmp"):
            self.assertNotIn(banned_file, names)
        # .claude-plugin must survive the .claude exclusion.
        self.assertIn(".claude-plugin/plugin.json", names)
        self.assertIn(".claude-plugin/", names)

    def test_pr_matcher_plan_excluded_but_skills_and_scripts_ship(self):
        (self.root / "PR-MATCHER-PLAN.md").write_text("# plan\n")
        (self.root / "skills" / "pr-overrides").mkdir(parents=True)
        (self.root / "skills" / "pr-overrides" / "SKILL.md").write_text("---\n")
        (self.root / "scripts" / "pr_overrides.py").write_text("print('ok')\n")

        plugin_path = self._build()
        names = self._zip_names(plugin_path)

        self.assertNotIn("PR-MATCHER-PLAN.md", names)
        self.assertIn("skills/pr-overrides/SKILL.md", names)
        self.assertIn("scripts/pr_overrides.py", names)

    # --- KB pipeline: working area and sessions never ship; the code does ---

    def test_kb_working_area_and_sessions_excluded_but_kb_code_ships(self):
        (self.root / "extra" / "kb-pipeline").mkdir(parents=True)
        (self.root / "extra" / "kb_format.py").write_text("X = 0\n")
        (self.root / "extra" / "kb-pipeline" / "README.md").write_text("# hand\n")
        (self.root / ".kb-sessions").mkdir()
        (self.root / ".kb-sessions" / "admin.json").write_text("{}\n")
        (self.root / "KB-PLAN.md").write_text("# plan\n")

        names = self._zip_names(self._build())

        self.assertFalse(any(n.startswith(("extra/", ".kb-sessions/")) for n in names))
        self.assertNotIn("KB-PLAN.md", names)
        self.assertIn("scripts/kb/kb_format.py", names)
        self.assertIn("agents/kb-writer.md", names)

    def test_missing_kb_writer_agent_fails_the_build(self):
        (self.root / "agents" / "kb-writer.md").unlink()
        with self.assertRaises(build_plugin.BuildError):
            self._build()


if __name__ == "__main__":
    unittest.main()
