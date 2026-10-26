"""Tests for scripts/kb/kb_validate.py — the gates G1–G7 that decide whether a
KB spec may be rendered. Stdlib-only, hermetic.

Pins: the golden spec passes the draft gates; each deliberate mutation trips
exactly the gate it targets (one assertion per rule); G1 short-circuits; the
variable-slot generalisation; draft-only anchors and missing shots are refused
on publish (G3, G7b).
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kb_fixtures import golden, write_assets

import kb_format as F
import kb_validate as V


def _gates(fails):
    return {f.gate for f in fails}


class GoldenSpec(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = write_assets(self._tmp.name)

    def check(self, spec, mode="draft"):
        return V.check(spec, mode, self.root)

    def test_golden_passes_draft(self):
        ok, fails = self.check(golden())
        self.assertTrue(ok, V.report(fails, "draft"))

    def test_golden_publish_needs_a_human_tenant_confirmation(self):
        ok, fails = self.check(golden(), "publish")
        self.assertFalse(ok)
        self.assertTrue(all("confirmed" in f.message for f in fails))
        spec = golden()
        for st in spec["steps"]:
            if st.get("image"):
                st["evidence"]["tenant_confirmed_by"] = "cx-reviewer"
        ok, fails = self.check(spec, "publish")
        self.assertTrue(ok, V.report(fails, "publish"))


class Mutations(unittest.TestCase):
    """Each case breaks one rule on purpose and expects its gate to refuse."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = write_assets(self._tmp.name)

    def refuse(self, mutate, gate, mode="draft", where=None):
        spec = golden()
        mutate(spec)
        ok, fails = V.check(spec, mode, self.root)
        self.assertFalse(ok, f"expected {gate} to refuse")
        hits = [f for f in fails if f.gate == gate]
        self.assertTrue(hits, f"no {gate} failure in: {[str(f) for f in fails]}")
        if where:
            self.assertTrue(any(f.where == where for f in hits),
                            [str(f) for f in hits])

    # ── G1 schema
    def test_g1_each_required_key(self):
        for key in ("title", "overview", "steps", "considerations", "conclusion", "meta"):
            with self.subTest(key=key):
                self.refuse(lambda s, k=key: s.pop(k), "G1")

    def test_g1_each_meta_key(self):
        for key in ("ticket", "audience", "portal"):
            with self.subTest(key=key):
                self.refuse(lambda s, k=key: s["meta"].pop(k), "G1", where="meta")

    def test_g1_short_circuits(self):
        spec = golden()
        spec.pop("meta")
        spec["title"] = "bad"
        _, fails = V.check(spec, "draft", self.root)
        self.assertEqual(_gates(fails), {"G1"})

    # ── G2 anatomy
    def test_g2_title_too_long(self):
        self.refuse(lambda s: s.update(title="How to " + "x" * F.MAX_TITLE_CHARS), "G2", where="title")

    def test_g2_title_not_a_task_phrase(self):
        self.refuse(lambda s: s.update(title="Filtering an Object Picker"), "G2", where="title")

    def test_g2_overview_too_short(self):
        self.refuse(lambda s: s.update(overview="Too short."), "G2", where="overview")

    def test_g2_too_few_steps(self):
        self.refuse(lambda s: s.update(steps=s["steps"][2:]), "G2", where="steps")

    def test_g2_empty_step(self):
        self.refuse(lambda s: s["steps"][2].update(text=" "), "G2", where="step 3")

    def test_g2_image_without_caption(self):
        self.refuse(lambda s: s["steps"][1].pop("caption"), "G2", where="step 2")

    def test_g2_no_considerations(self):
        self.refuse(lambda s: s.update(considerations=[]), "G2", where="considerations")

    def test_g2_consideration_without_bold_lead_in(self):
        self.refuse(lambda s: s.update(considerations=["No lead-in here."]), "G2",
                    where="consideration 1")

    def test_g2_empty_conclusion(self):
        self.refuse(lambda s: s.update(conclusion=""), "G2", where="conclusion")

    # ── G3 evidence
    def test_g3_bad_anchor(self):
        self.refuse(lambda s: s["steps"][1]["evidence"].update(anchor="guess"), "G3")

    def test_g3_no_anchor(self):
        self.refuse(lambda s: s["steps"][1]["evidence"].update(anchor="none"), "G3")

    def test_g3_no_label(self):
        self.refuse(lambda s: s["steps"][1]["evidence"].pop("label"), "G3")

    def test_g3_no_score(self):
        self.refuse(lambda s: s["steps"][1]["evidence"].pop("label_score"), "G3")

    def test_g3_score_below_floor(self):
        self.refuse(lambda s: s["steps"][1]["evidence"].update(label_score=0.81), "G3")

    def test_g3_label_as_written_not_a_generalisation(self):
        self.refuse(lambda s: s["steps"][0]["evidence"].update(label_as_written="N chosen"), "G3")

    def test_g3_ringed_label_not_bold(self):
        self.refuse(lambda s: s["steps"][1].update(text="Click Filter to Selected."), "G3")

    def test_g3_dom_box_allowed_in_draft_only(self):
        spec = golden()
        spec["steps"][1]["evidence"]["anchor"] = "dom_box"
        ok, fails = V.check(spec, "draft", self.root)
        self.assertTrue(ok, V.report(fails, "draft"))
        self.refuse(lambda s: s["steps"][1]["evidence"].update(anchor="dom_box"), "G3",
                    mode="publish")

    # ── G4 tenant
    def test_g4_unscreened(self):
        self.refuse(lambda s: s["steps"][1]["evidence"].pop("tenant"), "G4")

    def test_g4_publish_review(self):
        self.refuse(lambda s: s["steps"][1]["evidence"].update(tenant="REVIEW"), "G4",
                    mode="publish")

    def test_g4_publish_clear_without_confirmer(self):
        self.refuse(lambda s: None, "G4", mode="publish")

    def test_g4_draft_block_not_watermarked(self):
        self.refuse(lambda s: s["steps"][1]["evidence"].update(tenant="BLOCK"), "G4")

    def test_g4_draft_block_watermarked_passes(self):
        spec = golden()
        spec["steps"][1]["evidence"].update(tenant="BLOCK", watermarked=True)
        ok, fails = V.check(spec, "draft", self.root)
        self.assertTrue(ok, V.report(fails, "draft"))

    # ── G5 assets
    def test_g5_missing_file(self):
        self.refuse(lambda s: s["steps"][1].update(image="assets/nope.jpg"), "G5")

    # ── G6 voice
    def test_g6_each_banned_substring(self):
        for bad in F.BANNED_SUBSTRINGS:
            with self.subTest(bad=bad):
                self.refuse(lambda s, b=bad: s.update(conclusion=s["conclusion"] + f" {b} here."),
                            "G6", where="conclusion")

    def test_g6_each_banned_pattern(self):
        samples = ["See MR !1234.", "It returned a 500 error.", "Edit /Users/x/file now.",
                   "Lives in views.py today.", "Moved to QA1 lane."]
        self.assertEqual(len(samples), len(F.BANNED_PATTERNS))
        for text in samples:
            with self.subTest(text=text):
                self.refuse(lambda s, t=text: s.update(conclusion=s["conclusion"] + " " + t),
                            "G6", where="conclusion")

    def test_g6_checks_captions(self):
        self.refuse(lambda s: s["steps"][1].update(caption="Fixed the XSS"), "G6", where="caption 2")

    # ── G7 mode
    def test_g7_draft_without_provenance(self):
        self.refuse(lambda s: s.pop("provenance"), "G7", where="provenance")

    def test_g7_publish_with_banner_override(self):
        self.refuse(lambda s: s.update(banner_override="x"), "G7", mode="publish", where="banner")

    def test_g7b_publish_refuses_missing_shot(self):
        def mutate(s):
            s["steps"][2]["missing_shot"] = "no demo data"
        spec = golden()
        mutate(spec)
        ok, fails = V.check(spec, "draft", self.root)
        self.assertTrue(ok, V.report(fails, "draft"))
        self.refuse(mutate, "G7", mode="publish", where="step 3")


class Generalise(unittest.TestCase):
    def test_variable_slot_collapses(self):
        self.assertEqual(V._generalise("2 selected"), V._generalise("N selected"))
        self.assertEqual(V._generalise("17 selected"), V._generalise("n selected"))
        self.assertNotEqual(V._generalise("2 selected"), V._generalise("2 chosen"))


class Format(unittest.TestCase):
    def test_palette_is_hex(self):
        for name, value in F.palette().items():
            with self.subTest(name=name):
                self.assertRegex(value, r"^[0-9A-F]{6}$")

    def test_anatomy_order(self):
        self.assertEqual([k for k, _, _ in F.ANATOMY],
                         ["title", "subtitle", "banner", "overview", "steps",
                          "considerations", "conclusion", "provenance"])

    def test_enums(self):
        self.assertEqual(F.TENANT_VERDICTS, ("CLEAR", "REVIEW", "BLOCK"))
        self.assertIn("dom_box", F.ANCHORS)
        self.assertTrue(set(F.DRAFT_ONLY_ANCHORS) <= set(F.ANCHORS))

    def test_summary_names_version(self):
        self.assertIn(F.FORMAT_VERSION, F.summary())


if __name__ == "__main__":
    unittest.main()
