"""Tests for scripts/version_check.py — Step 0 of every command.

Pins: version comparison (behind / current / ahead / unknown); a missing or
blocked GitHub never fails the run (status unknown, exit 0); the result block
shape; the installed version is read from this repo's own manifest; and every
command file runs the check before its first step.
"""

import contextlib
import importlib.util
import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "version_check", ROOT / "scripts" / "version_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


vc = _load_module()


def _manifest(d, version, name="plugin.json"):
    p = Path(d) / name
    p.write_text(json.dumps({"name": "medtrics-release-notes", "version": version}))
    return str(p)


class CompareTests(unittest.TestCase):
    def test_numeric_not_string_order(self):
        self.assertEqual(vc.compare("0.9.0", "0.23.1"), "behind")
        self.assertEqual(vc.compare("0.23.1", "0.23.1"), "current")
        self.assertEqual(vc.compare("0.24.0", "0.23.9"), "ahead")
        self.assertEqual(vc.compare("v0.23.0", "0.23.1"), "behind")
        self.assertEqual(vc.compare("abc", "0.23.1"), "unknown")


class RunTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.installed = _manifest(self.d, "0.21.0", "installed.json")

    def test_behind_message_names_both_versions_and_update_steps(self):
        latest = _manifest(self.d, "0.23.1")
        r = vc.run(latest_file=latest, manifest=self.installed)
        self.assertEqual(r["status"], "behind")
        self.assertIn("installed 0.21.0, latest 0.23.1", r["message"])
        self.assertIn("/plugin", r["message"])
        self.assertIn("does not update", r["message"])

    def test_network_failure_is_unknown_not_an_error(self):
        with mock.patch.object(vc.urllib.request, "urlopen",
                               side_effect=OSError("blocked by sandbox")):
            r = vc.run(manifest=self.installed)
        self.assertEqual((r["ok"], r["status"]), (True, "unknown"))
        self.assertIn("blocked by sandbox", r["error"])
        self.assertIn("Continuing", r["message"])

    def test_bad_latest_manifest_is_unknown(self):
        bad = Path(self.d) / "bad.json"
        bad.write_text("<html>rate limited</html>")
        r = vc.run(latest_file=str(bad), manifest=self.installed)
        self.assertEqual(r["status"], "unknown")

    def test_reads_this_repos_manifest_by_default(self):
        own = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
        latest = _manifest(self.d, own["version"])
        r = vc.run(latest_file=latest)
        self.assertEqual((r["status"], r["installed"]), ("current", own["version"]))

    def test_main_prints_block_and_exits_zero(self):
        latest = _manifest(self.d, "99.0.0")
        out, err = io.StringIO(), io.StringIO()
        argv = ["version_check.py", "--latest-file", latest]
        with mock.patch.object(vc.sys, "argv", argv), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = vc.main()
        self.assertEqual(code, 0)
        block = json.loads(out.getvalue().split("---VERSION_CHECK_RESULT---")[1])
        self.assertEqual(block["status"], "behind")
        self.assertIn("out of date", err.getvalue())


class CommandWiringTests(unittest.TestCase):
    def test_every_command_runs_the_check_first(self):
        commands = sorted((ROOT / "commands").glob("*.md"))
        self.assertGreaterEqual(len(commands), 8)
        for cmd in commands:
            text = cmd.read_text()
            with self.subTest(command=cmd.name):
                self.assertIn("scripts/version_check.py", text, "no version check")
                # The check (or the line saying every mode starts with it)
                # comes before the first script call of any mode.
                marks = [i for i in (text.find("scripts/version_check.py"),
                                     text.find("Version check")) if i != -1]
                calls = [m.start() for m in re.finditer(r"python3 \S+\.py", text)
                         if "version_check.py" not in text[m.start():m.end()]]
                if calls:
                    self.assertLess(min(marks), calls[0],
                                    "a script runs before the version check")


if __name__ == "__main__":
    unittest.main()
