"""kb_package — LibreOffice leftovers are removed from the output folders before
they are zipped or listed."""

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kb_fixtures  # noqa: F401

import kb_package as PK


class OfficeLeftovers(unittest.TestCase):
    def test_lock_and_tmp_files_removed_deliverables_kept(self):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d, "M1-1")
            t.mkdir()
            keep = ["CX-DRAFT_How to A.docx", "CX-DRAFT_How to A.pdf", "CX-DRAFT_How to A.md",
                    "lucky.txt", "tmp.tmp"]
            junk = [".~lock.CX-DRAFT_How to A.docx#", "lu123abc.tmp"]
            for n in keep + junk:
                (t / n).write_text("x")
            (Path(d) / "lu9.tmp").write_text("x")
            gone = PK.clean_office_leftovers(d)
            self.assertEqual(sorted(gone), sorted(junk + ["lu9.tmp"]))
            self.assertEqual(sorted(p.name for p in t.iterdir()), sorted(keep))

    def test_clean_before_zip(self):
        # the cleanup runs between the PDF step and the zip in package()
        src = Path(PK.__file__).read_text()
        self.assertLess(src.rindex("clean_office_leftovers(out)"), src.index("zipfile.ZipFile("))
        self.assertGreater(src.rindex("clean_office_leftovers(out)"), src.index("P.convert("))


class ShotReasons(unittest.TestCase):
    def rows(self, d, *tickets):
        Path(d, "M1-1").mkdir(exist_ok=True)
        return [{"ticket": t, "status": s, "files": [], "reason": ""} for t, s in tickets]

    def test_table_and_summary_use_words_not_codes(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "shots").mkdir()
            (Path(d) / "shots" / "shots.json").write_text(json.dumps({"missing": {
                "M1-2": {"reason": "needs_write"},
                "M1-3": {"reason": "click_refused", "detail": "onclick"}}}))
            groups = {"uncapturable": [{"ticket": "M1-1", "reason": "no_screen"}]}
            reasons = PK.shot_reasons(groups, d)
            self.assertEqual(reasons["M1-1"], "no screen (text only)")
            self.assertIn("after you save", reasons["M1-2"])
            self.assertIn("(onclick)", reasons["M1-3"])
            rows = [{"ticket": t, "status": s, "files": [], "reason": ""}
                    for t, s in (("M1-1", "drafted"), ("M1-2", "partial"), ("M1-3", "partial"))]
            t = {r["ticket"]: r["detail"] for r in PK.ticket_table(["M1-1", "M1-2", "M1-3"], rows, groups, d, reasons)}
            self.assertEqual(t["M1-1"], "no screen (text only)")
            self.assertTrue(t["M1-2"].startswith("screenshots missing: "))
            self.assertNotIn("needs_write", t["M1-2"])
            md = PK.summary_md({"run_id": "r", "date": "d", "totals": dict(drafted=1, partial=2, failed=0, skipped=0),
                                "tickets": [], "records": [], "left_by_expiry": []},
                               [], groups, [], "", {}, reasons)
            self.assertIn("- M1-1: no screen (text only)", md)
            for raw in ("no_screen", "needs_write", "click_refused"):
                self.assertNotIn(raw, md)

    def test_no_shots_json_is_fine(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(PK.shot_reasons({"uncapturable": [{"ticket": "m1-9", "reason": "no demo data"}]}, d),
                             {"M1-9": "no demo data"})


if __name__ == "__main__":
    unittest.main()
