"""0.26.1 KB fixes, from run kb-20261124-2041 (0.26.0 in Cowork, OCR on):

1. Retry recordings were named "<visit>~r1a.gif"; Chrome answered "Invalid filename",
   so no retry screenshot was ever saved. Names are letters, digits, hyphens only.
2. The OCR check read nothing from small white-on-dark buttons ("View", "Submit") and
   dropped their rings although they were on screen. A second, tight read (element only,
   inverted when dark, enlarged) follows a miss; if that misses too and the page shows
   exactly this text, visible and uncovered, the ring stays, marked "not OCR-checked".
3. The diagnosis called those "outside the visible part". In frame + uncovered + no ring
   is now "unreadable", and is not retried.
4. A navigating visit whose page was scrolled but whose GIF has one frame is
   under-recorded (the frame is the page before the scroll): retried, never ringed.
5. The output zip carries _debug/ (diagnosis, shot list, raw probes, retry plans,
   overrides; no images).
6. `kb_ledger.py writes --retry-partial` updates the note of a re-drafted partial ticket.
Hermetic: no browser, no network; OCR is a fake pytesseract module.
"""

import json
import shutil
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kb_fixtures import has_module

import kb_capture as C
import kb_format as F
import kb_heal as H
import kb_ledger as L

HAVE_PIL = has_module("PIL")
FIX = Path(__file__).resolve().parent / "fixtures" / "runs" / "kb-20261123-2158"
IW, IH = 1536, 639


class Names(unittest.TestCase):
    def test_gif_name_rule(self):
        for ok in ("kb-capture/kb-20261124-2041/v03-r1a.gif", "kb-capture/r1/v01-s0.gif", "canary.gif"):
            self.assertTrue(C.gif_name_ok(ok), ok)
        for bad in ("kb-capture/r1/v03~r1a.gif", "kb-capture/r 1/v03.gif", "../x.gif", "a/b.png",
                    "kb-capture/r1/v03:r1.gif", "", None):
            self.assertFalse(C.gif_name_ok(bad), bad)

    def test_retry_names_on_the_real_run_are_safe(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        shots = d / "shots"
        shutil.copytree(FIX, shots)
        calls, items, manifest, _ = H._load(shots / "calls.json", shots / "raw", shots)
        rows = H.worth_retrying(H.diagnose(calls, items, manifest, 1))
        plan = H.build_retry(calls, {"portal_entry": {}}, rows, 1)
        self.assertTrue(plan["visits"])
        for v in plan["visits"]:
            self.assertTrue(C.gif_name_ok(v["gif"]), v["gif"])
            self.assertRegex(v["visit_id"], r"^v\d+(-s\d+)?-r1[abc]$")
        first = [v for v in plan["visits"] if not v["step"] and v["visit_id"].endswith("a")]
        self.assertTrue(first and all(v.get("nav") for v in first))


class FakeTess(types.ModuleType):
    """Reads nothing from a normal crop and the label from the tight one (config --psm 6)."""

    def __init__(self, tight="view"):
        super().__init__("pytesseract")
        self.tight, self.calls = tight, []

    def image_to_string(self, img, config="", **kw):
        self.calls.append((img.size, config))
        return self.tight if "--psm 6" in config else ""


@unittest.skipUnless(HAVE_PIL, "Pillow not installed")
class TightRead(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        self.img = Image.new("RGB", (IW, IH), "white")
        self.img.paste((30, 40, 80), (1345, 314, 1410, 344))        # the dark "View" button
        self.old = sys.modules.get("pytesseract")
        self.addCleanup(lambda: sys.modules.pop("pytesseract", None) if self.old is None
                        else sys.modules.__setitem__("pytesseract", self.old))

    def test_a_miss_gets_a_second_tight_read(self):
        fake = sys.modules["pytesseract"] = FakeTess("® view")
        r = C.Q.verify_crop(self.img, (1345, 314, 65, 30), ["View"])[0]
        self.assertEqual((r["verdict"], r["read"]), ("EXACT", "tight"))
        self.assertEqual(len(fake.calls), 2)
        w, h = fake.calls[1][0]                                      # element only, enlarged, with a margin
        self.assertEqual((w, h), ((65 - 2) * C.Q.TIGHT_SCALE + 2 * C.Q.TIGHT_BORDER * C.Q.TIGHT_SCALE,
                                  (30 - 2) * C.Q.TIGHT_SCALE + 2 * C.Q.TIGHT_BORDER * C.Q.TIGHT_SCALE))

    def test_a_dark_element_is_inverted_for_the_read(self):
        seen = {}

        class Spy(FakeTess):
            def image_to_string(self, img, config="", **kw):
                if "--psm 6" in config:
                    seen["corner"] = img.getpixel((img.width // 2, img.height // 2))
                return ""
        sys.modules["pytesseract"] = Spy()
        self.assertEqual(C.Q.verify_crop(self.img, (1345, 314, 65, 30), ["View"])[0]["verdict"], "MISS")
        self.assertGreater(seen["corner"], 128)                      # the dark button reads as light paper

    def test_a_hit_needs_no_second_read(self):
        class Hit(FakeTess):
            def image_to_string(self, img, config="", **kw):
                self.calls.append(config)
                return "View"
        fake = sys.modules["pytesseract"] = Hit()
        r = C.Q.verify_crop(self.img, (1345, 314, 65, 30), ["View"])[0]
        self.assertEqual((r["verdict"], "read" in r, len(fake.calls)), ("EXACT", False, 1))


def probe(labels, h=None, sy=0):
    return {"v": 1, "u": "/x/", "iw": IW, "ih": IH, "sy": sy, "dpr": 1, "nf": False, "labels": labels,
            "h": h, "tn": {"v": "CLEAR", "k": []}, "tw": {}, "ld": ""}


def lab(l, b=(1345, 314, 65, 30), t=None, iv=True, oc=False):
    return {"l": l, "f": True, "t": t or l, "b": list(b), "iv": iv, "oc": oc, "sb": False}


def gif(path, frames, size=(IW, IH)):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    ims = [Image.new("RGB", size, (i * 60, 90, 200)) for i in range(frames)]
    ims[0].save(path, "GIF", save_all=True, append_images=ims[1:], duration=100)


@unittest.skipUnless(HAVE_PIL, "Pillow not installed")
class Ingest(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, True)
        self.calls = {"base_url": "https://medtrics.medtricslab.com"}
        self.demo = {"domain": "medtricslab.com", "allow_strings": []}
        self.orig = (C.Q.verify_crop, C.Q.screen_frame)
        C.Q.screen_frame = lambda *a, **k: {"verdict": "CLEAR"}
        self.addCleanup(lambda: (setattr(C.Q, "verify_crop", self.orig[0]),
                                 setattr(C.Q, "screen_frame", self.orig[1])))

    def visit(self, kind="click", vid="v02-s2", **kw):
        return {"visit_id": vid, "kind": kind, "step": 2, "entry": "v02", "url": "/x/", "persona": "admin",
                "surface": "Cases > Case logs", "tickets": ["M1-1459"], "labels": {"M1-1459": ["View"]}, **kw}

    def ingest(self, v, pr, frames=2):
        gif(self.d / "gifs" / f"{v['visit_id']}.gif", frames)
        it = {"visit_id": v["visit_id"], "probe": pr, "gif": f"kb-capture/r/{v['visit_id']}.gif"}
        entries, reason = C.ingest_visit(v, it, self.calls, self.demo, self.d, True, "now")
        self.assertIsNone(reason)
        return entries

    def test_ocr_miss_on_a_visible_exact_label_keeps_the_ring(self):
        C.Q.verify_crop = lambda im, box, labels: [{"verdict": "MISS", "score": 0.0}]
        start = self.visit("start", "v03")
        e = self.ingest(start, probe([lab("View")]))[0]
        self.assertEqual((e["verified"], e["anchor"], e["ocr"]), (True, "dom_box", "miss"))
        e = self.ingest(start, probe([lab("View", t="View all")]))[0]             # not this text: no ring
        self.assertFalse(e["verified"])
        pm = {**lab("View", t="View all"), "pm": True}                            # a starts-with match: no ring
        self.assertFalse(self.ingest(start, probe([pm]))[0]["verified"])
        # a screen reached by a click: a stale frame could not be noticed there, so OCR decides
        self.assertFalse(self.ingest(self.visit("click", "v02-s2"), probe([lab("View")]))[0]["verified"])
        self.assertFalse(self.ingest(self.visit("retry", "v02-s2-r1b"), probe([lab("View")]))[0]["verified"])
        self.assertTrue(self.ingest(self.visit("retry", "v03-r1a", nav=True), probe([lab("View")]))[0]["verified"])

    def test_under_recorded_frame_is_never_ringed(self):
        C.Q.verify_crop = lambda im, box, labels: [{"verdict": "EXACT", "score": 1.0}]
        v = self.visit("start", "v03")
        e = self.ingest(v, probe([lab("View")], sy=833), frames=1)                  # scrolled, one frame
        self.assertEqual([(x["verified"], x.get("reason")) for x in e], [(False, "under_recorded")])
        self.assertTrue(C.frame_under_recorded(v, probe([], sy=833), self.d))
        self.assertTrue(self.ingest(v, probe([lab("View")], sy=833), frames=2)[0]["verified"])
        self.assertTrue(self.ingest(v, probe([lab("View")], sy=0), frames=1)[0]["verified"])     # never scrolled
        click = self.visit("click", "v02-s2")                                        # no navigation: not judged
        self.assertTrue(self.ingest(click, probe([lab("View")], sy=1013), frames=1)[0]["verified"])
        retry = self.visit("retry", "v03-r1a", nav=True)                            # a retry that navigates is
        self.assertFalse(self.ingest(retry, probe([lab("View")], sy=833), frames=1)[0]["verified"])
        self.assertIn("recording missed", F.missing_reason_text("under_recorded"))


class Diagnose(unittest.TestCase):
    def rows(self, pl, shots=(), screen=None):
        v = {"visit_id": "v02-s2", "entry": "v02", "step": 2, "persona": "admin", "surface": "S",
             "tickets": ["M1-1459"], "labels": {"M1-1459": ["View"]}}
        items = {"v02-s2": {"probe": probe([pl])}}
        man = {"shots": list(shots), "screens": [screen or {"visit_id": "v02-s2", "tickets": ["M1-1459"],
                                                           "reason": None}]}
        return H.diagnose({"visits": [v]}, items, man, 1)

    def test_in_frame_without_a_ring_is_unreadable_not_offscreen(self):
        r = self.rows(lab("View"))[0]
        self.assertEqual((r["cause"], r["retry"], r["fallback"]), ("unreadable", False, True))
        self.assertIn("text check could not read", H.CAUSES["unreadable"])
        self.assertEqual(self.rows(lab("View", iv=False))[0]["cause"], "offscreen")
        self.assertEqual(self.rows(lab("View", oc=True))[0]["cause"], "covered")

    @unittest.skipUnless(HAVE_PIL, "Pillow not installed")
    def test_under_recorded_screen_is_retried_whole(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        gif(d / "gifs" / "v03.gif", 1)
        v = {"visit_id": "v03", "kind": "start", "entry": "v03", "step": 0, "persona": "admin", "surface": "S",
             "url": "/x/", "tickets": ["M1-1413"], "labels": {"M1-1413": ["Automatic Alerts"]},
             "args": {"labels": ["Automatic Alerts"], "side": ""}, "gif": "kb-capture/r/v03.gif"}
        calls = {"visits": [v], "base_url": "https://x", "gif_dir": "kb-capture/r"}
        items = {"v03": {"probe": probe([lab("Automatic Alerts")], sy=833)}}
        man = {"shots": [], "screens": [{"visit_id": "v03", "tickets": ["M1-1413"], "reason": None}]}
        for n in (1, 2):
            r = H.diagnose(calls, items, man, n, out_dir=d)[0]
            self.assertEqual((r["cause"], r["retry"], r["fallback"]), ("under_recorded", True, True))
        plan = H.build_retry(calls, {"portal_entry": {}}, H.diagnose(calls, items, man, 1, out_dir=d), 1)
        self.assertEqual(plan["visits"][0]["labels"], {"M1-1413": ["Automatic Alerts"]})
        self.assertEqual(plan["visits"][0]["visit_id"], "v03-r1a")


@unittest.skipUnless(HAVE_PIL, "Pillow not installed")
class SheetFlag(unittest.TestCase):
    def test_not_ocr_checked_flag(self):
        from PIL import Image
        import kb_package as P
        import kb_spec as S
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        frame = d / "f.png"
        Image.new("RGB", (IW, IH), "white").save(frame)
        shot = {"shot_id": "s7", "ticket": "M1-1459", "verified": True, "frame": str(frame),
                "anchor": "dom_box", "ocr": "miss", "label": "View", "label_score": 1.0,
                "dom_box": [1345, 314, 65, 30], "tenant": "REVIEW", "surface": "S", "source": "chrome:x"}
        prose = {"ticket": "M1-1459", "title": "T", "steps": [{"text": "Click **View**.", "shot": "s7",
                                                               "caption": "c"}]}
        specs = d / "specs"
        spec = S.build_spec(prose, [shot], {}, None, specs)
        (specs / "M1-1459.json").write_text(json.dumps(spec))
        (specs / "assemble.json").write_text(json.dumps([{"ticket": "M1-1459", "status": "ok"}]))
        self.assertEqual([i["flag"] for i in P.candidates(specs)], ["not OCR-checked"])


class DebugFolder(unittest.TestCase):
    def test_text_files_only_and_no_install_script(self):
        import kb_package as P
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        shots = d / "run" / "shots"
        shutil.copytree(FIX, shots)
        (shots / "diagnosis.md").write_text("# Capture diagnosis\n")
        (shots / "kb_overrides.json").write_text("{}")
        (shots / "frames").mkdir()
        (shots / "frames" / "v01.png").write_bytes(b"png")
        calls = json.loads((shots / "calls.json").read_text())
        calls["install"] = [{"name": "javascript_tool", "input": {"text": "x" * 50000}}]
        (shots / "calls.json").write_text(json.dumps(calls))
        out = d / "out"
        out.mkdir()
        done = P.debug_files(d / "run", out)
        self.assertIn("diagnosis.md", done)
        self.assertIn("raw/b06.json", done)
        self.assertEqual(json.loads((out / "_debug" / "calls.json").read_text())["install"], [])
        names = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
        self.assertFalse([n for n in names if n.endswith((".png", ".gif", ".jpg"))])
        self.assertIn("_debug/kb_overrides.json", names)
        self.assertEqual(P.debug_files(d / "nowhere", out), [])               # no shots folder: nothing, no error


class LedgerRetryPartial(unittest.TestCase):
    REC = {"ticket": "M1-1459", "type": "feature", "status": "partial", "generated": "2026-11-24",
           "run": "kb-2", "render": "final", "persona": "Admin", "title": "T", "surface": "S",
           "screenshots": "1 of 2", "tenant": "t", "prior": "—", "files": "f", "tokens": "1", "cost": "$0"}

    def writes(self, status, **kw):
        ledger = {"M1-1459": {"note_id": "n1", "version": 3, "status": status}}
        return L.ledger_writes([dict(self.REC)], ledger, **kw)

    def test_a_redrafted_partial_ticket_is_updated(self):
        self.assertEqual(self.writes("partial"), [])                           # as before without the flag
        w = self.writes("partial", retry_partial=True)
        self.assertEqual([(x["action"], x["note_id"], x["version"]) for x in w], [("update", "n1", 3)])
        self.assertEqual(self.writes("drafted", retry_partial=True), [])       # a drafted note still needs --force
        self.assertEqual(len(self.writes("drafted", force=True)), 1)


# ── second round: what the independent check of 0.26.1 found ────────────────
class SecondRound(unittest.TestCase):
    def tmp(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        return d

    def test_debug_folder_has_no_external_link_and_no_page_scripts(self):
        import kb_package as P
        d = self.tmp()
        shots = d / "run" / "shots"
        (shots / "raw").mkdir(parents=True)
        link = "/evaluations/external/FAKETOKEN123abc/?k=SECRETQ99"
        calls = {"install": [{"input": {"text": "x" * 5000}}],
                 "visits": [{"visit_id": "v01", "private": True, "url": link, "gif": "kb-capture/r/v01.gif"}]}
        (shots / "calls.json").write_text(json.dumps(calls))
        (shots / "raw" / "b01.json").write_text(json.dumps(
            {"batch_id": "b01", "items": [{"visit_id": "v01", "probe": {"u": link}}]}))
        (shots / "retry-1.json").write_text(json.dumps({"install": [{"input": {"text": "y" * 5000}}],
                                                        "visits": [{"url": link}]}))
        (shots / "diagnosis.md").write_text(f"opened {link}\n")
        out = d / "out"
        out.mkdir()
        P.debug_files(d / "run", out)
        blob = "".join(p.read_text() for p in (out / "_debug").rglob("*") if p.is_file())
        for secret in ("FAKETOKEN123abc", "SECRETQ99"):
            self.assertNotIn(secret, blob)
        self.assertIn(P.EXTERNAL_MARK, blob)
        self.assertNotIn("xxxxxxxxxx", blob)
        self.assertNotIn("yyyyyyyyyy", blob)                                  # retry plans are stripped too
        self.assertIn("FAKETOKEN123abc", (shots / "calls.json").read_text())    # the run folder is untouched

    def test_shortened_retry_id_and_foreign_raw_files(self):
        self.assertEqual(C.item_visit_id("v01", ["v01-r1a"]), "v01-r1a")
        self.assertEqual(C.item_visit_id("v01-s2", ["v01-s2-r2b"]), "v01-s2-r2b")
        self.assertEqual(C.item_visit_id("v01", ["v01-s0-r1a"]), "v01-s0-r1a")
        self.assertEqual(C.item_visit_id("v01", ["v01-r1a", "v01-r1b"]), "v01")           # ambiguous: unchanged
        d = self.tmp()
        (d / "b01.json").write_text(json.dumps({"batch_id": "b01", "status": "ok",
                                                "items": [{"visit_id": "v01", "probe": {"first": 1}}]}))
        (d / "r1b01.json").write_text(json.dumps({"batch_id": "r1b01", "status": "ok",
                                                  "items": [{"visit_id": "v01", "probe": {"retry": 1}}]}))
        items, _ = C._load_raw(d, {"b01": ["v01"]})                # the first plan never sees the retry file
        self.assertEqual(items["v01"]["probe"], {"first": 1})
        items, _ = C._load_raw(d, {"b01": ["v01"], "r1b01": ["v01-r1a"]})
        self.assertEqual((items["v01"]["probe"], items["v01-r1a"]["probe"]), ({"first": 1}, {"retry": 1}))

    def test_an_ingested_pass_is_not_planned_again(self):
        import contextlib
        import io
        d = self.tmp()
        shots = d / "shots"
        shutil.copytree(FIX, shots)
        args = ["--calls", str(shots / "calls.json"), "--raw-dir", str(shots / "raw"), "--out-dir", str(shots)]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(H.main(["plan-retry", "--pass", "1", *args]), 0)
            plan = json.loads((shots / "retry-1.json").read_text())
            self.assertEqual(plan["install"], [])                             # no page scripts in the plan
            self.assertEqual(H.main(["plan-retry", "--pass", "1", *args]), 0)    # not ingested yet: may re-plan
            plan["results"] = {"v03-r1a": {"ok": True, "reason": ""}}
            (shots / "retry-1.json").write_text(json.dumps(plan))
            self.assertEqual(H.main(["plan-retry", "--pass", "1", *args]), 4)
        self.assertEqual(json.loads((shots / "retry-1.json").read_text())["results"], plan["results"])

    @unittest.skipUnless(HAVE_PIL, "Pillow not installed")
    def test_under_recorded_retry_frame_is_recorded_again(self):
        d = self.tmp()
        v = {"visit_id": "v03", "kind": "start", "entry": "v03", "step": 0, "persona": "admin", "surface": "S",
             "url": "/x/", "tickets": ["M1-1413"], "labels": {"M1-1413": ["Automatic Alerts"]},
             "args": {"labels": ["Automatic Alerts"], "side": ""}, "gif": "kb-capture/r/v03.gif"}
        rv = {**v, "visit_id": "v03-r1a", "of": "v03", "kind": "retry", "retry": 1, "cause": "offscreen",
              "nav": True, "gif": "kb-capture/r/v03-r1a.gif"}
        gif(d / "gifs" / "v03.gif", 2)
        gif(d / "gifs" / "v03-r1a.gif", 1)                                       # the retry frame: one frame
        calls = {"visits": [v], "retry_visits": [rv], "base_url": "https://x", "gif_dir": "kb-capture/r"}
        items = {"v03": {"probe": probe([lab("Automatic Alerts", iv=False)], sy=0)},
                 "v03-r1a": {"probe": probe([lab("Automatic Alerts")], sy=900)}}
        man = {"shots": [], "screens": [{"visit_id": "v03", "tickets": ["M1-1413"], "reason": None}]}
        tried = {("v03", "M1-1413", "Automatic Alerts")}
        rows = H.diagnose(calls, items, man, 2, tried, out_dir=d)
        under = [r for r in rows if r["cause"] == "under_recorded"]
        self.assertEqual([(r["target"], r["retry"], r["detail"]) for r in under],
                         [("Automatic Alerts", True, "the retry recording")])
        plan = H.build_retry(calls, {"portal_entry": {}}, H.worth_retrying(rows), 2)
        self.assertEqual([x["visit_id"] for x in plan["visits"]], ["v03-r2a"])
        self.assertEqual(H.diagnose(calls, items, man, 1, tried, out_dir=d)[0]["cause"], "offscreen")   # not in its own pass

    def test_diagnosis_wording(self):
        base = {"ticket": "M1-1", "surface": "S", "visit_id": "v01", "persona": "admin", "entry": "v01", "step": 0}
        plans = [{"pass": 1, "results": {"v01-r1a": {"ok": False}},
                  "rows": [{**base, "cause": "under_recorded", "label": "", "target": "", "retry_visit": "v01-r1a"}]},
                 {"pass": 2, "results": {"v01-r2a": {"ok": False}},
                  "rows": [{**base, "cause": "under_recorded", "label": "", "target": "", "retry_visit": "v01-r2a"}]}]
        shots = [{"ticket": "M1-1", "visit_id": "v01", "verified": True, "fallback": "unringed"}]
        rows = [{**base, "cause": "under_recorded", "label": "", "target": "", "retry": False, "fallback": True},
                {**base, "cause": "other_frame", "label": "Fourth", "target": "Fourth", "retry": False,
                 "fallback": False, "skipped": "limit", "detail": "at y=900 in a 600 px window"}]
        L = H.diagnosis_lines(rows, plans, {"shots": shots})
        self.assertEqual(L[0], "M1-1 · S (screen v01): the recording missed the final state of the screen; "
                               "retry 1 recorded the screen again; still no screenshot.")
        self.assertTrue(L[1].endswith("retry 2 recorded the screen again; no ring, so an unringed screenshot "
                                      "was kept for you to judge."))
        self.assertEqual(len(L), 3)                                              # the over-limit label gets its line
        self.assertIn("\"Fourth\" was on the page but outside the screenshot", L[2])
        self.assertIn("Not retried: over the limit", L[2])
        self.assertNotIn("the screen the", " ".join(L))

    def test_a_failed_redraft_keeps_the_partial_note(self):
        ledger = {"M1-1459": {"note_id": "n1", "version": 3, "status": "partial"}}
        rec = dict(LedgerRetryPartial.REC, status="failed")
        self.assertEqual(L.ledger_writes([rec], ledger, retry_partial=True), [])
        self.assertEqual(len(L.ledger_writes([rec], ledger, force=True)), 1)

    def test_retry_file_explains_its_batch_kinds_up_front(self):
        d = self.tmp()
        shots = d / "shots"
        shutil.copytree(FIX, shots)
        calls, items, manifest, _ = H._load(shots / "calls.json", shots / "raw", shots)
        plan = H.build_retry(calls, {"portal_entry": {}}, H.worth_retrying(H.diagnose(calls, items, manifest, 1)), 1)
        md = C.capture_md(plan)
        intro = md[:md.index("### r1b01")]
        self.assertIn("This file also has other kinds of batch", intro)
        self.assertIn("replay", intro)
        self.assertIn("this batch's own rule replaces section 3 step 4", md)
        self.assertIn("`-r1a` / `-r2b` retry suffix", md)
        first = C.capture_md({**plan, "batches": [b for b in plan["batches"] if b["kind"] in ("visits", "click")]})
        self.assertNotIn("other kinds of batch", first)                          # a plain capture file is unchanged


if __name__ == "__main__":
    unittest.main()
