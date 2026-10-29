"""kb_pdf.convert runs LibreOffice in a temp dir (docx copies, profile, outdir) so
no lock / scratch file ever lands in the output folder; only PDFs are copied
out and the temp dir is removed. soffice is mocked."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kb_fixtures  # noqa: F401

import kb_pdf as P


class Convert(unittest.TestCase):
    def test_only_pdfs_reach_the_output_folder(self):
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            outdir = Path(cmd[cmd.index("--outdir") + 1])
            for src in map(Path, cmd[cmd.index("pdf") + 3:]):
                (outdir / (src.stem + ".pdf")).write_bytes(b"%PDF")
                (outdir / f".~lock.{src.stem}.pdf#").write_text("x")      # lock files, as LO makes them
                (src.parent / f".~lock.{src.name}#").write_text("x")
            (outdir / "lu42abc.tmp").write_text("x")

        with tempfile.TemporaryDirectory() as d:
            src, out = Path(d, "src"), Path(d, "out")
            src.mkdir()
            docs = []
            for n in ("A", "B"):
                (src / f"{n}.docx").write_bytes(b"docx")
                docs.append(src / f"{n}.docx")
            with mock.patch.object(P, "soffice", return_value="/x/soffice"), \
                    mock.patch.object(P.subprocess, "run", side_effect=fake_run):
                got = P.convert(docs, out)
            self.assertEqual(sorted(p.name for p in out.iterdir()), ["A.pdf", "B.pdf"])
            self.assertEqual([p.name for p in got], ["A.pdf", "B.pdf"])
            self.assertEqual(sorted(p.name for p in src.iterdir()), ["A.docx", "B.docx"])
            cmd = calls[0]
            tmp = Path(cmd[cmd.index("--outdir") + 1]).parent
            self.assertFalse(tmp.exists())                                # temp dir removed
            self.assertNotIn(str(out), " ".join(cmd))
            self.assertNotIn(str(src), " ".join(cmd))
            prof = [c for c in cmd if c.startswith("-env:UserInstallation=")][0]
            self.assertTrue(prof.split("=", 1)[1].startswith("file://" + str(tmp)))

    def test_missing_pdf_is_reported_by_absence_and_temp_removed(self):
        seen = []

        def fake_run(cmd, **kw):
            seen.append(Path(cmd[cmd.index("--outdir") + 1]).parent)

        with tempfile.TemporaryDirectory() as d:
            doc = Path(d, "A.docx")
            doc.write_bytes(b"x")
            with mock.patch.object(P, "soffice", return_value="/x/soffice"), \
                    mock.patch.object(P.subprocess, "run", side_effect=fake_run):
                got = P.convert([doc], Path(d, "o"))
            self.assertFalse(got[0].exists())
            self.assertFalse(seen[0].exists())


if __name__ == "__main__":
    unittest.main()
