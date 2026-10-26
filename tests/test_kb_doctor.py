"""Tests for scripts/kb/kb_doctor.py --setup — the one-time install and its
done-marker. Hermetic: the installers and probes are replaced, nothing is
downloaded or installed.

Pins: the first run installs only what is missing and writes the marker; the
next run reads the marker and skips (no installer called); a piece that
vanished (reset sandbox) is reinstalled alone; a piece recorded unavailable
is not retried until --force; Chromium is fetched only once Playwright works;
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

    def run_setup(self, fake, force=False):
        with mock.patch.object(D, "probe_all", fake.probe):
            return D.setup(self.home, force=force, installers=fake.installers())

    def test_first_run_installs_missing_and_writes_marker(self):
        present = {n: n in ("Pillow", "numpy", "LibreOffice") for n in ALL}
        fake = Fake(present)
        marker, skipped = self.run_setup(fake)
        self.assertFalse(skipped)
        self.assertEqual(marker["items"]["Pillow"], "present")
        self.assertEqual(marker["items"]["python-docx"], "installed")
        self.assertEqual(marker["items"][D.CHROMIUM], "installed")
        kinds = [k for k, _ in fake.calls]
        self.assertEqual(kinds, ["packages", "chromium", "system"])
        self.assertNotIn("Pillow", fake.calls[0][1])
        self.assertNotIn("LibreOffice", fake.calls[2][1])
        on_disk = json.loads((self.home / D.ENV_DIR / D.MARKER).read_text())
        self.assertEqual(on_disk["items"], marker["items"])
        self.assertIn("py", on_disk)

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
        marker, _ = self.run_setup(fake)
        self.assertTrue(marker["items"][D.CHROMIUM].startswith("unavailable"))
        self.assertNotIn("chromium", [k for k, _ in fake.calls])

    def test_cli_prints_kb_py(self):
        fake = Fake({n: True for n in ALL})
        import contextlib
        import io
        buf = io.StringIO()
        with mock.patch.object(D, "probe_all", fake.probe), contextlib.redirect_stdout(buf):
            rc = D.main(["--setup", "--home", str(self.home)])
        self.assertEqual(rc, 0)
        self.assertIn("KB_PY=", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
