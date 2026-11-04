"""Tests for scripts/kb/kb_paths.py — the folder, the run dir and the config,
all resolved at run time and never stored as absolute paths."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kb_fixtures  # noqa: F401

import kb_paths as P


def cli(*argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = P.main(list(argv))
    return rc, buf.getvalue()


class Paths(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name).resolve()

    def test_paths_lines_and_empty_py_before_install(self):
        rc, out = cli("--home", str(self.home))
        self.assertEqual(rc, 0)
        self.assertEqual(out.splitlines(), [
            "KB_PY=",
            f"KB_RUNS={self.home}/.kb-runs",
            f"KB_CONFIG={self.home}/.medtrics-release-notes/config.json"])

    def test_py_resolved_now_when_it_exists(self):
        py = self.home / ".kb-env" / "bin" / "python"
        py.parent.mkdir(parents=True)
        py.write_text("")
        _, out = cli("paths", "--home", str(self.home))
        self.assertEqual(out.splitlines()[0], f"KB_PY={py}")

    def test_home_order_arg_then_env_then_cwd(self):
        with mock.patch.dict(os.environ, {"KB_HOME": str(self.home)}):
            self.assertEqual(P.folder(), self.home)
            other = self.home / "x"
            other.mkdir()
            self.assertEqual(P.folder(str(other)), other.resolve())
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KB_HOME", None)
            self.assertEqual(P.folder(), Path.cwd().resolve())

    def test_ensure_creates_self_ignoring_dirs(self):
        (self.home / ".kb-env").mkdir()
        cli("ensure", "--home", str(self.home))
        for d in (".kb-runs", ".medtrics-release-notes", ".kb-env"):
            self.assertEqual((self.home / d / ".gitignore").read_text(), "*\n", d)
        (self.home / ".kb-runs" / ".gitignore").write_text("custom\n")
        cli("ensure", "--home", str(self.home))
        self.assertEqual((self.home / ".kb-runs" / ".gitignore").read_text(), "custom\n")

    def test_paths_alone_creates_nothing(self):
        cli("--home", str(self.home))
        self.assertFalse((self.home / ".kb-runs").exists())

    def test_merge_deep_and_timestamps(self):
        cli("merge", '{"kb": {"setup": "done", "items": {"a": "present"}}}', "--home", str(self.home))
        cli("merge", '{"mem": {"kb_ledger": "abc"}, "kb": {"items": {"b": "installed"}}}',
            "--home", str(self.home))
        cfg = P.load(self.home)
        self.assertEqual(cfg["schema"], 1)
        self.assertEqual(cfg["kb"]["setup"], "done")
        self.assertEqual(cfg["kb"]["items"], {"a": "present", "b": "installed"})
        self.assertEqual(cfg["mem"]["kb_ledger"], "abc")
        self.assertIn("created", cfg)
        self.assertIn("updated", cfg)
        self.assertTrue((self.home / ".medtrics-release-notes" / ".gitignore").exists())

    def test_merge_rejects_non_object(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli("merge", "[1]", "--home", str(self.home))[0], 1)
            self.assertEqual(cli("merge", "nope", "--home", str(self.home))[0], 1)

    def test_config_holds_no_absolute_path(self):
        cli("ensure", "--home", str(self.home))
        cli("merge", '{"kb": {"setup": "done"}}', "--home", str(self.home))
        text = (self.home / ".medtrics-release-notes" / "config.json").read_text()
        self.assertNotIn(str(self.home), text)

    def test_unwritable_folder_falls_back_to_home_config(self):
        fake_home = self.home / "fakehome"
        fake_home.mkdir()
        with mock.patch.object(P, "_writable_dir", return_value=False), \
                mock.patch.object(Path, "home", return_value=fake_home):
            self.assertEqual(P.config_path(self.home),
                             fake_home / ".medtrics-release-notes" / "config.json")

    def test_config_dir_made_by_config_path_is_self_ignoring(self):
        P.config_path(self.home)
        g = self.home / ".medtrics-release-notes" / ".gitignore"
        self.assertEqual(g.read_text(), "*\n")
        self.assertFalse((self.home / ".medtrics-release-notes" / ".write-test").exists())

    def test_env_names_strip_export(self):
        (self.home / ".env").write_text("export OPTIMUS_API_KEY=abc\nexport   GITLAB_TOKEN='t'\nexport EMPTY=\n")
        with mock.patch.dict(os.environ, {}, clear=True):
            found = P.env_names(self.home)
        self.assertTrue(found.get("OPTIMUS_API_KEY") and found.get("GITLAB_TOKEN"))
        self.assertNotIn("export OPTIMUS_API_KEY", found)
        self.assertFalse(found.get("EMPTY"))

    def test_env_check_records_names_never_values(self):
        (self.home / ".env").write_text(
            "OPTIMUS_API_KEY=secret-one\nGITLAB_TOKEN='tok'\n# GITLAB_PROJECT_ID=1\n")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GITLAB_PROJECT_ID", None)
            rc, out = cli("env-check", "--home", str(self.home))
        self.assertEqual(rc, 1)
        self.assertIn("OPTIMUS_API_KEY=set", out)
        self.assertIn("GITLAB_PROJECT_ID=MISSING", out)
        self.assertNotIn("secret-one", out)
        cfg = P.load(self.home)
        self.assertEqual(cfg["env_checked"], {"OPTIMUS_API_KEY": True, "GITLAB_TOKEN": True,
                                              "GITLAB_PROJECT_ID": False})
        self.assertNotIn("secret-one", json.dumps(cfg))


if __name__ == "__main__":
    unittest.main()
