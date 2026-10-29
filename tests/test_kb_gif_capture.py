"""Tests for the GIF capture path (0.24.0) of scripts/kb/kb_capture.py: the per-visit
gif_creator actions, calls.json gif/canary/abort fields, capture.md, wait_for_file,
`pickup`, `sweep`, and `ingest --downloads-dir`. Hermetic: Pillow-made GIFs placed
in a fake Downloads folder; no browser, OCR never used.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kb_fixtures import has_module
from test_kb_chrome_capture import (Base, INPUTS, PLAN, lab, make_frame, probe_for, run_cli)

import kb_capture as C

HAVE_PIL = has_module("PIL")


def make_gif(path, w=1470, h=802, frames=3):
    """Multi-frame GIF whose LAST frame is solid (200, 30, 30)."""
    from PIL import Image
    colours = [(10, 10, 10), (90, 90, 200), (200, 30, 30)]
    ims = [Image.new("RGB", (w, h), colours[i % 3]) for i in range(frames)]
    ims[-1] = Image.new("RGB", (w, h), (200, 30, 30))
    ims[0].save(path, "GIF", save_all=True, append_images=ims[1:], duration=100)


class Actions(Base):
    def test_visit_order_and_options(self):
        calls, _ = self.plan_calls()
        v = calls["batches"][0]["actions"][2:9]                   # first visit, after the entry (no install: the orchestrator sends it)
        self.assertEqual([(a["name"], a["input"].get("action")) for a in v],
                         [("gif_creator", "start_recording"), ("navigate", None),
                          ("computer", "wait"), ("computer", "hover"),
                          ("javascript_tool", "javascript_exec"),
                          ("gif_creator", "stop_recording"), ("gif_creator", "export")])
        self.assertEqual(v[2]["input"]["duration"], 4)           # demo.settle_s
        self.assertEqual(v[3]["input"]["coordinate"], [1465, 740])
        ex = v[6]["input"]
        self.assertEqual((ex["download"], ex["filename"], ex["tabId"]),
                         (True, "kb-capture/r1/v01.gif", "$TAB"))
        self.assertEqual(ex["options"], {"showWatermark": False, "showProgressBar": False,
                                         "showActionLabels": False, "showClickIndicators": False,
                                         "showDragPaths": False, "quality": 1})
        self.assertEqual(ex["options"], C.EXPORT_OPTS)
        self.assertIsNot(ex["options"], C.EXPORT_OPTS)
        for b in calls["batches"]:
            for a in b["actions"]:
                self.assertNotEqual(a["input"].get("action"), "screenshot")
                self.assertEqual(a["input"]["tabId"], "$TAB")

    def test_wait_override(self):
        plan = json.loads(json.dumps(PLAN))
        plan["capture_plan"][0]["wait_s"] = 7                    # Users
        plan["capture_plan"][1]["wait_s"] = 99                   # Groups: clamped
        calls, _ = self.plan_calls(plan)
        waits = [a["input"]["duration"] for a in calls["batches"][0]["actions"]
                 if a["input"].get("action") == "wait"]
        self.assertEqual(waits, [2, 7, 10, 4])                   # entry, v01, v02, v03
        self.assertEqual(calls["visits"][0]["wait_s"], 7)
        self.assertNotIn("wait_s", calls["visits"][2])

    def test_wait_from_routes_surface(self):
        routes = json.loads(json.dumps(C.json.loads(C.DEFAULT_ROUTES.read_text())))
        routes["surfaces"].append({"surface": "Users", "persona": "admin", "wait_s": 5})
        calls = C.build_calls(PLAN, INPUTS, routes)
        self.assertEqual(calls["visits"][0]["wait_s"], 5)
        self.assertEqual(C.settle_seconds({"settle_s": 4}), 4)
        self.assertEqual(C.settle_seconds({}), 4)

    def test_filenames_and_dirs(self):
        calls, _ = self.plan_calls()
        self.assertEqual(calls["gif_dir"], "kb-capture/r1")
        self.assertEqual([v["gif"] for v in calls["visits"]],
                         [f"kb-capture/r1/v0{i}.gif" for i in range(1, 6)])

    def test_canary_and_abort(self):
        calls, _ = self.plan_calls()
        c = calls["canary"]
        self.assertEqual(c["gif"], "kb-capture/r1/canary.gif")
        self.assertEqual([(a["name"], a["input"].get("action")) for a in c["actions"]],
                         [("gif_creator", "start_recording"), ("navigate", None),
                          ("computer", "wait"), ("computer", "hover"),
                          ("gif_creator", "stop_recording"), ("gif_creator", "export")])
        self.assertTrue(c["actions"][1]["input"]["url"].endswith("/?portal_change=administration"))
        self.assertEqual(c["actions"][2]["input"]["duration"], 3)
        self.assertEqual(c["actions"][5]["input"]["filename"], "kb-capture/r1/canary.gif")
        self.assertEqual(c["actions"][5]["input"]["options"], C.EXPORT_OPTS)
        self.assertEqual(calls["abort"], [{"name": "gif_creator",
                                           "input": {"action": "clear", "tabId": "$TAB"}}])

    def test_capture_md(self):
        calls, _ = self.plan_calls()
        md = (self.shots / "capture.md").read_text()
        for word in ("gif_creator", "mcp__claude-in-chrome__gif_creator", "no screenshots",
                     "Abort rule", '"gif"', "Downloaded", "stop_recording"):
            self.assertIn(word, md)
        self.assertNotIn("screenshot_path", md)
        self.assertNotIn("save_to_disk", md)
        self.assertIn(json.dumps(calls["abort"], indent=1), md)


class WaitForFile(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.f = Path(self._tmp.name) / "x.gif"

    def test_ready(self):
        self.f.write_bytes(b"GIF89a-data")
        self.assertEqual(C.wait_for_file(self.f, 2, 0.01), ("ready", 0))

    def test_timeout_when_absent(self):
        self.assertEqual(C.wait_for_file(self.f, 0.1, 0.01), ("timeout", 1))

    def test_empty(self):
        self.f.write_bytes(b"")
        self.assertEqual(C.wait_for_file(self.f, 0.1, 0.01), ("empty", 2))

    def test_still_growing_times_out(self):
        import threading
        stop = threading.Event()

        def grow():
            n = 0
            while not stop.is_set():
                n += 1
                with open(self.f, "ab") as fh:
                    fh.write(b"x" * n)
                stop.wait(0.005)
        t = threading.Thread(target=grow)
        t.start()
        try:
            self.assertEqual(C.wait_for_file(self.f, 0.3, 0.05), ("timeout", 1))
        finally:
            stop.set()
            t.join()


@unittest.skipUnless(HAVE_PIL, "Pillow not installed")
class PickupSweep(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dl = Path(self._tmp.name) / "Downloads"
        (self.dl / "kb-capture" / "r1").mkdir(parents=True)
        self.name = "kb-capture/r1/canary.gif"

    def pick(self):
        return run_cli("pickup", "--downloads-dir", self.dl, "--name", self.name, "--timeout", 0.3)

    def test_ok_keeps_canary_for_cleanup(self):
        make_gif(self.dl / self.name)
        rc, res, out = self.pick()
        self.assertEqual((rc, res["ok"], res["width"], res["height"]), (0, True, 1470, 802))
        self.assertIn("1470x802", out)
        self.assertTrue((self.dl / self.name).exists())     # deleting needs approval; cleanup does it

    def test_missing_empty_bad(self):
        rc, res, _ = self.pick()
        self.assertEqual((rc, res["reason"]), (1, "download_missing"))
        (self.dl / self.name).write_bytes(b"")
        rc, res, _ = self.pick()
        self.assertEqual((rc, res["reason"]), (2, "empty_export"))
        (self.dl / self.name).write_bytes(b"this is not a gif at all")
        rc, res, _ = self.pick()
        self.assertEqual((rc, res["reason"]), (3, "bad_gif"))
        self.assertTrue((self.dl / self.name).exists())          # kept on failure

    def test_sweep_moves_never_deletes(self):
        (self.dl / "kb-capture" / "r1" / "v01.gif").write_bytes(b"tenant data")
        (self.dl / "kb-capture" / "r0").mkdir()
        (self.dl / "kb-capture" / "r0" / "v02.gif").write_bytes(b"more")
        into = Path(self._tmp.name) / "shots" / "stale"
        rc, res, _ = run_cli("sweep", "--downloads-dir", self.dl, "--into", into)
        self.assertEqual((rc, res["ok"], len(res["moved"])), (0, True, 2))
        self.assertEqual((into / "r1" / "v01.gif").read_bytes(), b"tenant data")
        self.assertEqual((into / "r0" / "v02.gif").read_bytes(), b"more")
        self.assertEqual(list((self.dl / "kb-capture").glob("*")) if (self.dl / "kb-capture").exists() else [], [])
        rc, res, _ = run_cli("sweep", "--downloads-dir", self.dl, "--into", into)   # nothing left
        self.assertEqual((rc, res["moved"]), (0, []))

    def test_sweep_does_not_overwrite(self):
        into = Path(self._tmp.name) / "stale"
        (into / "r1").mkdir(parents=True)
        (into / "r1" / "keep").write_bytes(b"old")
        (self.dl / "kb-capture" / "r1" / "v01.gif").write_bytes(b"new")
        run_cli("sweep", "--downloads-dir", self.dl, "--into", into)
        self.assertEqual((into / "r1" / "keep").read_bytes(), b"old")
        self.assertEqual((into / "r1-2" / "v01.gif").read_bytes(), b"new")


@unittest.skipUnless(HAVE_PIL, "Pillow not installed")
class IngestGif(Base):
    def setUp(self):
        super().setUp()
        self.orig = (C.FIRST_FILE_TIMEOUT_S, C.NEXT_FILE_TIMEOUT_S)
        C.FIRST_FILE_TIMEOUT_S = C.NEXT_FILE_TIMEOUT_S = 0.3
        self.addCleanup(lambda: setattr(C, "FIRST_FILE_TIMEOUT_S", self.orig[0]) or
                        setattr(C, "NEXT_FILE_TIMEOUT_S", self.orig[1]))
        plan = {"run_id": "r1", "capture_plan": [
            {"persona": "admin", "surface": "Users", "url": "/users/", "tickets": ["M1-1"]},
            {"persona": "admin", "surface": "Groups", "url": "/groups/", "tickets": ["M1-3"]}]}
        self.calls, _ = self.plan_calls(plan)
        self.raw = self.tmp / "raw"
        self.raw.mkdir()
        self.dl = self.tmp / "Downloads"
        (self.dl / "kb-capture" / "r1").mkdir(parents=True)

    def write_raw(self, items, status="ok"):
        (self.raw / "b01.json").write_text(json.dumps(
            {"batch_id": "b01", "status": status, "error": None, "items": items}))

    def item(self, vid, probe=None, **kw):
        return {"visit_id": vid, "probe": probe or probe_for([lab("Groups")]),
                "gif": f"kb-capture/r1/{vid}.gif", **kw}

    def ingest(self, *extra):
        rc, res, out = run_cli("ingest", "--calls", self.shots / "calls.json", "--raw-dir", self.raw,
                               "--out-dir", self.shots, "--downloads-dir", self.dl, "--no-ocr", *extra)
        return rc, res, json.loads((self.shots / "shots.json").read_text())["shots"]

    def test_last_frame_copy_then_cleanup(self):
        from PIL import Image
        make_gif(self.dl / "kb-capture/r1/v01.gif")
        make_gif(self.dl / "kb-capture/r1/v02.gif")
        self.write_raw([self.item("v01"), self.item("v02", probe_for([lab("Save")]))])
        rc, res, shots = self.ingest()
        self.assertEqual((rc, res["ok"], res["left"]), (0, True, 0))
        s = next(x for x in shots if x["visit_id"] == "v01")
        self.assertEqual(s["frame_source"], "gif")
        self.assertEqual(s["dom_box"], [300, 100, 60, 20])       # scale 1 at 1470 wide
        self.assertTrue(s["verified"])
        frame = Path(s["frame"])
        self.assertEqual(frame.name, "v01.png")
        with Image.open(frame) as im:
            self.assertEqual(im.size, (1470, 802))
            self.assertEqual(im.mode, "RGB")
            self.assertEqual(im.getpixel((5, 5)), (200, 30, 30))   # the LAST frame
        self.assertTrue((self.shots / "gifs" / "v01.gif").is_file())
        self.assertTrue((self.dl / "kb-capture/r1/v01.gif").exists())   # copied, not moved
        self.assertEqual(res["downloads_left"], ["kb-capture/r1/v01.gif", "kb-capture/r1/v02.gif"])
        self.assertEqual(Path(self.shots / "gifs" / "v01.gif").read_bytes(),
                         (self.dl / "kb-capture/r1/v01.gif").read_bytes())
        rc, res, _ = run_cli("cleanup", "--downloads-dir", self.dl,
                             "--names-from", self.shots / "calls.json")
        self.assertEqual((rc, sorted(res["deleted"])),
                         (0, ["kb-capture/r1/v01.gif", "kb-capture/r1/v02.gif"]))
        self.assertFalse((self.dl / "kb-capture").exists())      # run folder + root removed
        self.assertTrue(self.dl.exists())
        self.assertTrue((self.shots / "gifs" / "v01.gif").is_file())

    def test_reingest_after_cleanup_uses_the_kept_copy(self):
        make_gif(self.dl / "kb-capture/r1/v01.gif")
        make_gif(self.dl / "kb-capture/r1/v02.gif")
        self.write_raw([self.item("v01"), self.item("v02", probe_for([lab("Save")]))])
        self.ingest()
        run_cli("cleanup", "--downloads-dir", self.dl, "--names-from", self.shots / "calls.json")
        rc, res, shots = self.ingest()
        self.assertEqual((rc, len(shots)), (0, 2))

    def test_cleanup_never_leaves_kb_capture(self):
        outside = self.tmp / "keep.gif"
        outside.write_bytes(b"x")
        calls = json.loads((self.shots / "calls.json").read_text())
        calls["visits"][0]["gif"] = "../keep.gif"
        (self.shots / "calls2.json").write_text(json.dumps(calls))
        run_cli("cleanup", "--downloads-dir", self.dl, "--names-from", self.shots / "calls2.json")
        self.assertTrue(outside.exists())

    def test_run_folder_kept_when_not_empty(self):
        make_gif(self.dl / "kb-capture/r1/v01.gif")
        (self.dl / "kb-capture/r1/other.txt").write_text("x")
        self.write_raw([self.item("v01")])
        self.ingest()
        self.assertTrue((self.dl / "kb-capture/r1/other.txt").exists())

    def test_missing_gif(self):
        make_gif(self.dl / "kb-capture/r1/v02.gif")
        self.write_raw([self.item("v01"), self.item("v02", probe_for([lab("Save")]))])
        rc, res, shots = self.ingest()
        self.assertEqual(rc, 3)
        self.assertEqual(res["left_reasons"], {"admin/Users": "download_missing"})
        self.assertEqual([s["visit_id"] for s in shots], ["v02"])
        resume = json.loads((self.shots / "resume.json").read_text())
        self.assertEqual([c["surface"] for c in resume["capture_plan"]], ["Users"])

    def test_no_downloads_dir_is_download_missing(self):
        make_gif(self.dl / "kb-capture/r1/v01.gif")
        self.write_raw([self.item("v01")])
        rc, res, _ = run_cli("ingest", "--calls", self.shots / "calls.json", "--raw-dir", self.raw,
                             "--out-dir", self.shots, "--no-ocr")
        self.assertEqual(res["left_reasons"]["admin/Users"], "download_missing")

    def test_empty_export(self):
        (self.dl / "kb-capture/r1/v01.gif").write_bytes(b"")
        self.write_raw([self.item("v01")])
        rc, res, _ = self.ingest()
        self.assertEqual((rc, res["left_reasons"]["admin/Users"]), (3, "empty_export"))

    def test_bad_gif(self):
        (self.dl / "kb-capture/r1/v01.gif").write_bytes(b"definitely not a gif")
        self.write_raw([self.item("v01")])
        rc, res, shots = self.ingest()
        self.assertEqual((rc, res["left_reasons"]["admin/Users"]), (3, "bad_gif"))
        self.assertEqual(shots, [])

    def test_probe_blocked_still_retry(self):
        make_gif(self.dl / "kb-capture/r1/v01.gif")
        self.write_raw([self.item("v01", "[BLOCKED: x]")])
        _, res, _ = self.ingest()
        self.assertEqual(res["left_reasons"]["admin/Users"], "retry")

    def test_legacy_screenshot_path_still_ingests(self):
        png = self.tmp / "old.jpg"
        make_frame(png)
        self.write_raw([{"visit_id": "v01", "probe": probe_for([lab("Groups")]),
                         "screenshot_path": str(png)},
                        {"visit_id": "v02", "probe": probe_for([lab("Save")]),
                         "screenshot_path": str(png)}])
        rc, res, shots = self.ingest()
        self.assertEqual((rc, res["ok"]), (0, True))
        self.assertEqual({s["frame_source"] for s in shots}, {"disk"})
        self.assertEqual(Path(shots[0]["frame"]).name, "v01.jpg")


class BoxInFrame(unittest.TestCase):
    def test_pure_rule(self):
        self.assertTrue(C.box_in_frame((10, 10, 50, 20), 1470, 802))
        self.assertTrue(C.box_in_frame((0, 0, 1470, 802), 1470, 802))
        for box in ((10, 790, 50, 40), (-1, 10, 5, 5), (10, -1, 5, 5), (1460, 10, 20, 5), (10, 10, 0, 5), (10, 10, 5, 0)):
            self.assertFalse(C.box_in_frame(box, 1470, 802), box)


@unittest.skipUnless(HAVE_PIL, "Pillow not installed")
class OffScreenLabelDoesNotCrashOcr(IngestGif):
    """0.27.0 evidence: an off-screen "Save" label (box below the frame) crashed ingest in the OCR
    crop. Such a label is now skipped BEFORE any crop; an OCR error is a miss, never a crash."""

    def setUp(self):
        super().setUp()
        self.calls_ = json.loads((self.shots / "calls.json").read_text())
        self.crops = []
        orig = C.Q.verify_crop

        def spy(im, box, labels):
            x, y, w, h = box
            self.crops.append(box)
            if x < 0 or y < 0 or x + w > im.width or y + h > im.height:
                raise ValueError("crop box outside the frame")           # what the real OCR crop did
            return [{"verdict": "MATCH", "score": 0.95}]
        C.Q.verify_crop = spy
        orig_sf = C.Q.screen_frame
        C.Q.screen_frame = lambda *a, **k: {"verdict": "CLEAR"}          # no tesseract here
        self.addCleanup(lambda: (setattr(C.Q, "verify_crop", orig), setattr(C.Q, "screen_frame", orig_sf)))

    def ingest_one(self, labels, use_ocr=True):
        make_gif(self.dl / "kb-capture/r1/v02.gif")
        v = next(x for x in self.calls_["visits"] if x["visit_id"] == "v02")
        v = {**v, "labels": {t: [l["l"] for l in labels] for t in v["tickets"]}}
        it = {"visit_id": "v02", "probe": probe_for(labels), "gif": "kb-capture/r1/v02.gif"}
        return C.ingest_visit(v, it, self.calls_, {"domain": "medtricslab.com", "allow_strings": []},
                              self.shots, use_ocr, "now", str(self.dl), 0.3)

    def test_offscreen_and_covered_labels_are_skipped_before_the_crop(self):
        entries, reason = self.ingest_one([lab("Groups", box=(300, 100, 60, 20)),
                                           lab("Save", box=(300, 900, 60, 20), iv=False),          # below the fold
                                           lab("Cover", box=(300, 200, 60, 20), oc=True),
                                           lab("Wide", box=(1450, 300, 80, 20), iv=True)])         # past the right edge
        self.assertIsNone(reason)
        by = {e["label"]: e for e in entries}
        self.assertEqual(sorted(by), ["Cover", "Groups", "Save", "Wide"])
        self.assertEqual([by[k]["verified"] for k in ("Groups", "Save", "Cover", "Wide")], [True, False, False, False])
        self.assertEqual(self.crops, [(300, 100, 60, 20)])               # only the visible, uncovered label was cropped

    def test_an_ocr_error_is_a_miss_not_a_crash(self):
        def boom(im, box, labels):
            raise RuntimeError("tesseract fell over")
        C.Q.verify_crop = boom
        entries, reason = self.ingest_one([lab("Groups")])
        self.assertIsNone(reason)
        self.assertEqual([(e["label"], e["verified"]) for e in entries], [("Groups", False)])


if __name__ == "__main__":
    unittest.main()
