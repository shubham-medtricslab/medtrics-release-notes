"""kb_package — LibreOffice leftovers are removed before zipping; the contact sheet
and owner confirmation gate which screenshots reach the client-clean articles."""

import hashlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kb_fixtures  # noqa: F401
from kb_fixtures import golden, has_module

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


def run_cli(*argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = PK.main(list(argv))
    out = buf.getvalue()
    return code, json.loads(out.split("---KB_PACKAGE_RESULT---")[1]), out


@unittest.skipUnless(has_module("PIL") and has_module("docx"), "Pillow / python-docx not installed")
class ContactSheetAndConfirm(unittest.TestCase):
    """Two shots (#1 CLEAR, #2 BLOCK) and a third ticket's REVIEW shot (#3)."""

    def setUp(self):
        from PIL import Image
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.run_dir = Path(self._tmp.name) / "run"
        self.specs = self.run_dir / "specs"
        (self.specs / "assets" / "M1-1").mkdir(parents=True)
        (self.specs / "assets" / "M1-2").mkdir(parents=True)
        shots = {"M1-1": (("CLEAR", None), ("BLOCK", {"foreign_email_domain": ["a@x.edu"]})),
                 "M1-2": (("REVIEW", {"institution_string": ["School of Medicine"]}),)}
        results = []
        for tid, entries in shots.items():
            spec = golden()
            spec["meta"]["ticket"] = tid
            for n, (st, (verdict, words)) in enumerate(zip(spec["steps"], entries), 1):
                rel = f"assets/{tid}/{n:02d}.jpg"
                Image.new("RGB", (1200, 750), (240, 240, 245)).save(self.specs / rel)
                st["image"] = rel
                st["evidence"]["tenant"] = verdict
                if words:
                    st["evidence"]["tenant_words"] = words
            for st in spec["steps"][len(entries):]:
                st.pop("image", None), st.pop("evidence", None), st.pop("caption", None)
            (self.specs / f"{tid}.json").write_text(json.dumps(spec))
            results.append({"ticket": tid, "status": "drafted"})
        (self.specs / "assemble.json").write_text(json.dumps(results))
        self.groups = Path(self._tmp.name) / "groups.json"
        self.groups.write_text(json.dumps({
            "run_id": "kb-test", "date": "2026-10-29", "skipped": [],
            "writer_groups": [{"group_id": "a-01", "tickets": ["M1-1", "M1-2"], "persona": "admin"}]}))
        self.sheet = self.run_dir / "contact_sheet.png"

    def sheet_cmd(self):
        return run_cli("contact-sheet", "--run-dir", str(self.run_dir), "--specs-dir", str(self.specs),
                       "--out", str(self.sheet))

    def test_contact_sheet_numbers_every_candidate_and_marks_block(self):
        from PIL import Image
        code, res, _ = self.sheet_cmd()
        self.assertEqual((code, res["shots"], res["blocked"]), (0, 3, 1))
        self.assertTrue(self.sheet.exists())
        with Image.open(self.sheet) as im:
            self.assertGreater(im.width, 3 * 380)
        items = json.loads((self.run_dir / "contact_sheet.json").read_text())["items"]
        self.assertEqual([(i["n"], i["ticket"], i["step"], i["tenant"]) for i in items],
                         [(1, "M1-1", 1, "CLEAR"), (2, "M1-1", 2, "BLOCK"), (3, "M1-2", 1, "REVIEW")])
        self.assertIn("foreign_email_domain: a@x.edu", items[1]["words"])
        self.assertIn("School of Medicine", items[2]["words"])

    def test_words_are_truncated(self):
        self.assertTrue(len(PK.words_text({"k": ["x" * 300]})) <= 110)
        self.assertEqual(PK.words_text(None), "")

    def test_many_shots_paginate(self):
        items = [{"n": n, "ticket": "M1-1", "step": 1, "image": "assets/M1-1/01.jpg",
                  "tenant": "CLEAR", "words": ""} for n in range(1, PK.SHEET_PER_PAGE + 2)]
        paths = PK.render_sheets(items, self.specs, self.sheet)
        self.assertEqual([Path(p).name for p in paths], ["contact_sheet.png", "contact_sheet-2.png"])

    def test_confirm_sets_the_owner_on_those_frames_only(self):
        self.sheet_cmd()
        code, res, _ = run_cli("confirm", "--run-dir", str(self.run_dir), "--ok", "1", "--by", "owner@x.com")
        self.assertEqual((code, res["confirmed"], res["left_out"]), (0, [1], [2, 3]))
        s1 = json.loads((self.specs / "M1-1.json").read_text())
        s2 = json.loads((self.specs / "M1-2.json").read_text())
        self.assertEqual(s1["steps"][0]["evidence"]["tenant_confirmed_by"], "owner@x.com")
        self.assertNotIn("tenant_confirmed_by", s1["steps"][1]["evidence"])
        self.assertNotIn("tenant_confirmed_by", s2["steps"][0]["evidence"])
        # the owner's list is the whole truth: a second confirm replaces the first
        run_cli("confirm", "--run-dir", str(self.run_dir), "--ok", "3", "--by", "owner@x.com")
        s1 = json.loads((self.specs / "M1-1.json").read_text())
        self.assertNotIn("tenant_confirmed_by", s1["steps"][0]["evidence"])

    def test_all_skips_block_and_naming_a_block_frame_is_refused(self):
        self.sheet_cmd()
        code, res, _ = run_cli("confirm", "--run-dir", str(self.run_dir), "--ok", "all", "--by", "o")
        self.assertEqual(res["confirmed"], [1, 3])
        code, res, _ = run_cli("confirm", "--run-dir", str(self.run_dir), "--ok", "1,2", "--by", "o")
        self.assertEqual(code, 1)
        self.assertIn("BLOCK", res["error"])
        code, res, _ = run_cli("confirm", "--run-dir", str(self.run_dir), "--ok", "9", "--by", "o")
        self.assertEqual(code, 1)

    def test_confirm_refuses_a_stale_sheet(self):
        self.sheet_cmd()
        p = self.specs / "M1-1.json"
        spec = json.loads(p.read_text())
        spec["steps"][0]["image"] = "assets/M1-1/other.jpg"
        p.write_text(json.dumps(spec))
        code, res, _ = run_cli("confirm", "--run-dir", str(self.run_dir), "--ok", "1", "--by", "o")
        self.assertEqual(code, 1)
        self.assertIn("contact-sheet again", res["error"])

    def no_office(self):
        old = PK.P.convert                                  # no LibreOffice in the test
        PK.P.convert = lambda docs, folder: None
        self.addCleanup(setattr, PK.P, "convert", old)

    def package(self, *argv):
        self.no_office()
        out_root = Path(self._tmp.name) / "out"
        code, res, text = run_cli("package", "--run-dir", str(self.run_dir), "--groups", str(self.groups),
                                  "--specs-dir", str(self.specs), "--output-root", str(out_root),
                                  "--requested", "M1-1,M1-2", *argv)
        return code, res, text, out_root / "kb-articles" / "kb-test"

    def test_package_ships_only_confirmed_frames_client_clean(self):
        from docx import Document
        self.sheet_cmd()
        run_cli("confirm", "--run-dir", str(self.run_dir), "--ok", "all", "--by", "owner@x.com")
        code, res, text, out = self.package()
        self.assertEqual(code, 0, text)
        docx = out / "M1-1" / "How to Filter an Object Picker to Selected Items.docx"
        self.assertTrue(docx.exists(), sorted(p.name for p in (out / "M1-1").iterdir()))
        self.assertFalse(any("CX-DRAFT" in p.name for p in out.rglob("*")))
        self.assertEqual(len(Document(docx).inline_shapes), 1)           # #2 was BLOCK
        body = "\n".join(p.text for p in Document(docx).paragraphs)
        for bad in ("Generated from", "draft", "Screenshot missing", "BLOCK", "tenant"):
            self.assertNotIn(bad, body)
        self.assertIn("Click **Filter to Selected**".replace("**", ""), body)    # the step keeps its text
        md = (out / "M1-1" / "How to Filter an Object Picker to Selected Items.md").read_text()
        self.assertEqual(md.count("!["), 1)
        # only shipped screenshots are copied (and zipped)
        self.assertEqual(sorted(p.name for p in (out / "M1-1" / "assets" / "M1-1").iterdir()), ["01.jpg"])
        with zipfile.ZipFile(out / "kb-articles-kb-test.zip") as z:
            self.assertFalse([n for n in z.namelist() if n.endswith("02.jpg")])
        # the audit trail lives in the sidecar and RUN-SUMMARY, with who confirmed
        prov = (out / "_provenance" / "M1-1.md").read_text()
        self.assertIn("BLOCK (left out)", prov)
        self.assertIn("owner@x.com", prov)
        summary = (out / "RUN-SUMMARY.md").read_text()
        self.assertIn("## Screenshots left out of the articles", summary)
        self.assertIn("M1-1 step 2: tenant screen BLOCK", summary)
        run = json.loads((self.run_dir / "run.json").read_text())
        self.assertEqual(run["left_out"]["M1-1"][0]["step"], 2)
        self.assertEqual({r["ticket"]: r["status"] for r in run["records"]},
                         {"M1-1": "partial", "M1-2": "drafted"})

    def test_package_without_confirmation_ships_no_screenshot_at_all(self):
        from docx import Document
        self.sheet_cmd()
        code, res, text, out = self.package()
        self.assertEqual(code, 0, text)
        for tid in ("M1-1", "M1-2"):
            docx = next((out / tid).glob("*.docx"))
            self.assertEqual(len(Document(docx).inline_shapes), 0)
            self.assertFalse((out / tid / "assets").exists())
        run = json.loads((self.run_dir / "run.json").read_text())
        self.assertEqual(run["totals"]["partial"], 2)
        self.assertTrue(all(r["detail"].startswith("screenshots missing") for r in run["tickets"]))

    def replace_image(self, rel):
        """The same file name, different pixels: a frame re-assembled after the owner looked."""
        from PIL import Image
        Image.new("RGB", (1200, 750), (10, 120, 30)).save(self.specs / rel)

    def test_contact_sheet_records_each_images_sha256(self):
        self.sheet_cmd()
        items = json.loads((self.run_dir / "contact_sheet.json").read_text())["items"]
        for it in items:
            self.assertEqual(it["sha256"], hashlib.sha256((self.specs / it["image"]).read_bytes()).hexdigest())
        self.assertEqual(len({it["sha256"] for it in items}), 1)         # the three test frames are the same picture

    def test_confirm_stores_the_hash_it_confirmed(self):
        self.sheet_cmd()
        run_cli("confirm", "--run-dir", str(self.run_dir), "--ok", "1", "--by", "o")
        ev = json.loads((self.specs / "M1-1.json").read_text())["steps"][0]["evidence"]
        self.assertEqual(ev["tenant_confirmed_sha256"], PK.image_sha256(self.specs / "assets/M1-1/01.jpg"))
        run_cli("confirm", "--run-dir", str(self.run_dir), "--ok", "3", "--by", "o")           # replaced: #1 loses both
        ev = json.loads((self.specs / "M1-1.json").read_text())["steps"][0]["evidence"]
        self.assertNotIn("tenant_confirmed_sha256", ev)

    def test_confirm_refuses_a_re_assembled_frame_with_the_same_name(self):
        self.sheet_cmd()
        self.replace_image("assets/M1-1/01.jpg")
        code, res, _ = run_cli("confirm", "--run-dir", str(self.run_dir), "--ok", "all", "--by", "o")
        self.assertEqual(code, 1)
        self.assertIn("stale sheet", res["error"])
        self.assertIn("#1", res["error"])
        for tid in ("M1-1", "M1-2"):                                     # nothing was confirmed
            spec = json.loads((self.specs / f"{tid}.json").read_text())
            self.assertFalse([st for st in spec["steps"] if (st.get("evidence") or {}).get("tenant_confirmed_by")])
        # a sheet from before the hashes existed is stale too
        self.sheet_cmd()
        sheet = json.loads((self.run_dir / "contact_sheet.json").read_text())
        for it in sheet["items"]:
            it.pop("sha256")
        (self.run_dir / "contact_sheet.json").write_text(json.dumps(sheet))
        code, res, _ = run_cli("confirm", "--run-dir", str(self.run_dir), "--ok", "1", "--by", "o")
        self.assertEqual((code, "stale sheet" in res["error"]), (1, True))

    def test_package_ships_only_frames_whose_current_hash_is_the_confirmed_one(self):
        from docx import Document
        self.sheet_cmd()
        run_cli("confirm", "--run-dir", str(self.run_dir), "--ok", "all", "--by", "owner@x.com")
        self.replace_image("assets/M1-1/01.jpg")                         # swapped after the owner's OK
        code, res, text, out = self.package()
        self.assertEqual(code, 0, text)
        self.assertEqual(len(Document(next((out / "M1-1").glob("*.docx"))).inline_shapes), 0)
        self.assertEqual(len(Document(next((out / "M1-2").glob("*.docx"))).inline_shapes), 1)    # untouched: ships
        run = json.loads((self.run_dir / "run.json").read_text())
        self.assertEqual(run["left_out"]["M1-1"][0]["why"], PK.LEFT_OUT_CHANGED)
        self.assertFalse((out / "M1-1" / "assets").exists())
        # an owner name without the confirmed hash (hand-set) is not a confirmation either
        spec = json.loads((self.specs / "M1-2.json").read_text())
        spec["steps"][0]["evidence"].pop("tenant_confirmed_sha256")
        (self.specs / "M1-2.json").write_text(json.dumps(spec))
        code, res, text, out = self.package()
        self.assertEqual(len(Document(next((out / "M1-2").glob("*.docx"))).inline_shapes), 0)

    def test_package_clears_the_run_folder_so_a_renamed_title_leaves_nothing_stale(self):
        self.sheet_cmd()
        run_cli("confirm", "--run-dir", str(self.run_dir), "--ok", "all", "--by", "owner@x.com")
        code, res, text, out = self.package()
        old = "How to Filter an Object Picker to Selected Items"
        self.assertTrue((out / "M1-1" / f"{old}.docx").exists())
        (out / "M1-1" / f"{old}.pdf").write_text("old pdf")               # as a real run leaves it
        (out / "kb-articles-older.zip").write_text("old zip")
        (out / "M1-9").mkdir()
        (out / "M1-9" / "Gone.docx").write_text("a ticket that is no longer in the run")
        spec = json.loads((self.specs / "M1-1.json").read_text())
        spec["title"] = "How to Pick Selected Items"
        (self.specs / "M1-1.json").write_text(json.dumps(spec))
        code, res, text, out = self.package()
        self.assertEqual(code, 0, text)
        self.assertEqual(sorted(p.name for p in (out / "M1-1").iterdir() if p.is_file()),
                         ["How to Pick Selected Items.docx", "How to Pick Selected Items.md"])
        self.assertFalse((out / "M1-9").exists())
        self.assertEqual([p.name for p in out.glob("*.zip")], ["kb-articles-kb-test.zip"])
        with zipfile.ZipFile(out / "kb-articles-kb-test.zip") as z:
            names = z.namelist()
        self.assertFalse([n for n in names if (n.startswith("M1-1/") and old in n) or "Gone" in n or n.endswith(".pdf")], names)
        self.assertIn("M1-1/How to Pick Selected Items.docx", names)

    def test_the_pdf_check_counts_the_published_spec_not_the_raw_one(self):
        # M1-1 has two shots, #2 is BLOCK and is left out: the PDF has one image, and that is right
        from docx import Document
        self.sheet_cmd()
        run_cli("confirm", "--run-dir", str(self.run_dir), "--ok", "all", "--by", "owner@x.com")
        seen = {}

        def convert(docs, folder):
            for d in docs:
                d.with_suffix(".pdf").write_text("pdf")

        def verify(pdf, spec):                  # what kb_pdf.verify does with images: PDF count vs the spec's
            images = len(Document(str(Path(pdf).with_suffix(".docx"))).inline_shapes)
            want = sum(1 for st in spec["steps"] if st.get("image"))
            seen[Path(pdf).parent.name] = (images, want)
            return ([f"{images} images, spec has {want}"] if images != want else []), []
        old_c, old_v = PK.P.convert, PK.P.verify
        PK.P.convert, PK.P.verify = convert, verify
        self.addCleanup(setattr, PK.P, "convert", old_c)
        self.addCleanup(setattr, PK.P, "verify", old_v)
        out_root = Path(self._tmp.name) / "out"
        code, res, text = run_cli("package", "--run-dir", str(self.run_dir), "--groups", str(self.groups),
                                  "--specs-dir", str(self.specs), "--output-root", str(out_root))
        self.assertEqual(code, 0, text)
        self.assertEqual(seen, {"M1-1": (1, 1), "M1-2": (1, 1)})
        summary = (out_root / "kb-articles" / "kb-test" / "RUN-SUMMARY.md").read_text()
        self.assertNotIn("images, spec has", summary)

    def test_package_table_has_cost_column(self):
        (self.run_dir / "usage_report.json").write_text(json.dumps(
            {"total_tokens": 1000, "est_cost_usd": 0.31, "per_ticket": {}, "per_ticket_cost": {"M1-1": 0.04},
             "rows": []}))
        code, res, text, out = self.package()
        self.assertIn("| Ticket | Result | Files | Details | Est. cost |", text)
        self.assertIn("$0.04 |", text)
        self.assertIn("| **Run total** |  |  | all agent calls | $0.31 |", text)
        run = json.loads((self.run_dir / "run.json").read_text())
        self.assertEqual(run["totals"]["est_cost_usd"], 0.31)
        self.assertEqual(run["cost"]["per_ticket"], {"M1-1": 0.04})

    def test_default_invocation_still_packages(self):
        self.no_office()
        out_root = Path(self._tmp.name) / "o2"
        code, res, _ = run_cli("--run-dir", str(self.run_dir), "--groups", str(self.groups),
                               "--specs-dir", str(self.specs), "--output-root", str(out_root))
        self.assertEqual((code, res["ok"]), (0, True))


if __name__ == "__main__":
    unittest.main()
