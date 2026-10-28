"""Tests for scripts/kb/kb_doctor.py --setup — the one-time install and its
done-marker. Hermetic: the installers and probes are replaced, nothing is
downloaded or installed.

Pins: the first run installs only what is missing and writes the marker; the
next run reads the marker and skips (no installer called); a piece that
vanished (reset sandbox) is reinstalled alone; a piece recorded unavailable
is not retried until --force; Playwright and Chromium are installed only with --with-playwright (and
Chromium only once Playwright works); no .env-kb-login issue is raised;
the marker carries the environment's python.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kb_fixtures  # noqa: F401

import kb_doctor as D

ALL = list(D.PY_PACKAGES) + list(D.SYSTEM) + [D.CHROMIUM]


class Fake:
    """Installers that record calls and flip the simulated disk state."""

    def __init__(self, present, fail=()):
        self.present, self.fail, self.calls = dict(present), set(fail), []

    def probe(self, env):
        return dict(self.present)

    def _do(self, kind, names):
        self.calls.append((kind, tuple(names)))
        out = {}
        for n in names:
            if n in self.fail:
                out[n] = "unavailable: test"
            else:
                self.present[n] = True
                out[n] = "installed"
        return out

    def installers(self):
        return {"packages": lambda env, names: self._do("packages", names),
                "chromium": lambda env: self._do("chromium", [D.CHROMIUM])[D.CHROMIUM],
                "system": lambda names: self._do("system", names)}


class Setup(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)

    def run_setup(self, fake, force=False, with_playwright=False):
        with mock.patch.object(D, "probe_all", fake.probe):
            return D.setup(self.home, force=force, installers=fake.installers(),
                           with_playwright=with_playwright)

    def test_first_run_installs_missing_and_writes_marker(self):
        present = {n: n in ("Pillow", "numpy", "LibreOffice") for n in ALL}
        fake = Fake(present)
        marker, skipped = self.run_setup(fake)
        self.assertFalse(skipped)
        self.assertEqual(marker["items"]["Pillow"], "present")
        self.assertEqual(marker["items"]["python-docx"], "installed")
        self.assertEqual(marker["items"][D.CHROMIUM], D.NOT_REQUESTED)
        self.assertEqual(marker["items"]["playwright"], D.NOT_REQUESTED)
        kinds = [k for k, _ in fake.calls]
        self.assertEqual(kinds, ["packages", "system"])
        self.assertNotIn("playwright", fake.calls[0][1])
        self.assertNotIn("Pillow", fake.calls[0][1])
        self.assertNotIn("LibreOffice", fake.calls[1][1])
        on_disk = json.loads((self.home / D.ENV_DIR / D.MARKER).read_text())
        self.assertEqual(on_disk["items"], marker["items"])
        self.assertIn("py", on_disk)

    def test_with_playwright_installs_playwright_and_chromium(self):
        present = {n: n in ("Pillow", "numpy", "LibreOffice") for n in ALL}
        fake = Fake(present)
        marker, skipped = self.run_setup(fake, with_playwright=True)
        self.assertFalse(skipped)
        self.assertEqual(marker["items"][D.CHROMIUM], "installed")
        self.assertEqual(marker["items"]["playwright"], "installed")
        self.assertEqual([k for k, _ in fake.calls], ["packages", "chromium", "system"])
        self.assertIn("playwright", fake.calls[0][1])

    def test_with_playwright_later_adds_only_the_legacy_pieces(self):
        fake = Fake({n: False for n in ALL})
        self.run_setup(fake)
        fake.calls.clear()
        marker, skipped = self.run_setup(fake, with_playwright=True)
        self.assertFalse(skipped)
        self.assertEqual(fake.calls, [("packages", ("playwright",)), ("chromium", (D.CHROMIUM,))])
        self.assertEqual(marker["items"][D.CHROMIUM], "installed")
        fake.calls.clear()
        _, skipped = self.run_setup(fake, with_playwright=True)
        self.assertTrue(skipped)

    def test_second_run_skips_without_installing(self):
        fake = Fake({n: False for n in ALL})
        self.run_setup(fake)
        fake.calls.clear()
        marker, skipped = self.run_setup(fake)
        self.assertTrue(skipped)
        self.assertEqual(fake.calls, [])

    def test_vanished_piece_reinstalled_alone(self):
        fake = Fake({n: False for n in ALL})
        self.run_setup(fake)
        fake.calls.clear()
        fake.present["pypdf"] = False          # e.g. the sandbox was reset
        marker, skipped = self.run_setup(fake)
        self.assertFalse(skipped)
        self.assertEqual(fake.calls, [("packages", ("pypdf",))])

    def test_unavailable_not_retried_until_force(self):
        fake = Fake({n: False for n in ALL}, fail={"tesseract", "LibreOffice"})
        marker, _ = self.run_setup(fake)
        self.assertTrue(marker["items"]["tesseract"].startswith("unavailable"))
        fake.calls.clear()
        _, skipped = self.run_setup(fake)
        self.assertTrue(skipped)
        self.assertEqual(fake.calls, [])
        fake.fail.clear()
        marker, skipped = self.run_setup(fake, force=True)
        self.assertFalse(skipped)
        self.assertEqual(marker["items"]["tesseract"], "installed")

    def test_no_chromium_without_playwright(self):
        fake = Fake({n: False for n in ALL}, fail={"playwright"})
        marker, _ = self.run_setup(fake, with_playwright=True)
        self.assertTrue(marker["items"][D.CHROMIUM].startswith("unavailable"))
        self.assertNotIn("chromium", [k for k, _ in fake.calls])

    def test_no_login_file_issue(self):
        import contextlib
        import io
        fake = Fake({n: True for n in ALL})
        buf = io.StringIO()
        with mock.patch.object(D, "probe_all", fake.probe), contextlib.redirect_stdout(buf):
            D.main(["--setup", "--home", str(self.home)])
        out = buf.getvalue()
        self.assertNotIn(".env-kb-login", out)
        self.assertNotIn("ISSUE", out)
        self.assertEqual(json.loads(out.split("---KB_DOCTOR_RESULT---")[1])["issues"], [])

    def test_cli_with_playwright_flag(self):
        import contextlib
        import io
        fake = Fake({n: False for n in ALL})
        buf = io.StringIO()
        with mock.patch.object(D, "probe_all", fake.probe), \
                mock.patch.object(D, "install_packages", fake.installers()["packages"]), \
                mock.patch.object(D, "install_chromium", fake.installers()["chromium"]), \
                mock.patch.object(D, "install_system", fake.installers()["system"]), \
                contextlib.redirect_stdout(buf):
            D.main(["--setup", "--with-playwright", "--home", str(self.home)])
        self.assertIn(("chromium", (D.CHROMIUM,)), fake.calls)

    def test_cli_prints_kb_py(self):
        fake = Fake({n: True for n in ALL})
        import contextlib
        import io
        buf = io.StringIO()
        with mock.patch.object(D, "probe_all", fake.probe), contextlib.redirect_stdout(buf):
            rc = D.main(["--setup", "--home", str(self.home)])
        self.assertEqual(rc, 0)
        self.assertIn("KB_PY=", buf.getvalue())

    def run_check(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = D.main(["--check", "--home", str(self.home)])
        return rc, buf.getvalue()

    def test_check_without_setup_points_to_setup_command(self):
        rc, out = self.run_check()
        self.assertEqual(rc, 2)
        self.assertIn("/release-note-setup", out)
        self.assertNotIn("KB_PY=", out)

    def test_check_after_setup_installs_nothing(self):
        fake = Fake({n: True for n in ALL})
        with mock.patch.object(D, "probe_all", fake.probe):
            self.run_setup(fake)
            fake.calls.clear()
            rc, out = self.run_check()
        self.assertEqual(rc, 0)
        self.assertIn("KB_PY=", out)
        self.assertEqual(fake.calls, [])

    def test_check_hard_piece_gone_needs_setup(self):
        fake = Fake({n: True for n in ALL})
        with mock.patch.object(D, "probe_all", fake.probe):
            self.run_setup(fake)
        fake2 = Fake({n: n != "python-docx" for n in ALL})
        with mock.patch.object(D, "probe_all", fake2.probe):
            rc, out = self.run_check()
        self.assertEqual(rc, 2)
        self.assertIn("reset", out)


if __name__ == "__main__":
    unittest.main()
