"""0.26.0 self-healing capture (docs/plans/KB-SELF-HEALING-PLAN.md).

1. First attempt: kbw (wait until quiet) before the scroll step; the probe offers near
   candidates for a label it cannot find; one screen's error never stops the run.
2. kb_heal: diagnose every missed screenshot, retry the fixable ones in two passes
   (same file format as capture.md, plus open / replay / frames batches), fall back to
   an unringed screenshot (anchor "screen"), write diagnosis.md.
3. kb_overrides: a local file laid over kb_routes.json; the run learns aliases and
   longer waits, never clicks or read_posts; a learned line that fails twice is dropped.
4. check-routes plan + report, and the promotion tool.

Replay corpus: fixtures/runs/kb-20261123-2158 is the raw data of the 0.25.0 run that
came back partial; `diagnose` must name the known cause of each miss.
Hermetic: no browser, no network (node only for the JS, skipped without it).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kb_fixtures import has_module

import kb_capture as C
import kb_format as F
import kb_heal as H
import kb_overrides as O
import kb_validate as V

NODE = shutil.which("node")
HAVE_PIL = has_module("PIL")
FIX = Path(__file__).resolve().parent / "fixtures" / "runs" / "kb-20261123-2158"
ROOT = Path(__file__).resolve().parents[2]
IW, IH = 1536, 639


class Tmp(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.d, True)
        old = os.environ.get("KB_HOME")
        os.environ["KB_HOME"] = str(self.d)            # overrides live under the temp folder
        self.addCleanup(lambda: os.environ.pop("KB_HOME", None) if old is None
                        else os.environ.__setitem__("KB_HOME", old))

    def run_copy(self):
        shots = self.d / "shots"
        shutil.copytree(FIX, shots)
        return shots

    def heal(self, *argv):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = H.main(list(argv))
        out = buf.getvalue()
        return rc, json.loads(out.split("---KB_HEAL_RESULT---")[1]), out


# ── overrides ────────────────────────────────────────────────────────────────
ROUTES = {"demo": {}, "surfaces": [
    {"surface": "Benefits > Time Off", "persona": "admin", "also_personas": ["faculty"],
     "url": "/budgets/benefits/time-off/", "clicks": [{"text": "Requests"}]}]}


def ent(key, value, source="manual", **kw):
    return {"persona": "admin", "surface": "Benefits > Time Off", "key": key, "value": value,
            "source": source, **kw}


class Overrides(Tmp):
    def test_merge_lays_valid_entries_over_the_surface(self):
        data = {"entries": [ent("wait_s", 8), ent("quiet_cap_s", 20), ent("loader_sel", ".hamster"),
                            ent("alias", "Time Off Requests", label="Requests"),
                            ent("read_posts", ["/api/x/list/"]),
                            ent("clicks", [{"text": "Accounts"}])]}
        merged, applied, skipped = O.merge(ROUTES, data)
        s = merged["surfaces"][0]
        self.assertEqual((len(applied), skipped), (6, []))
        self.assertEqual((s["wait_s"], s["quiet_cap_s"], s["loader_sel"]), (8, 20, [".hamster"]))
        self.assertEqual(s["aliases"], {"Requests": ["Time Off Requests"]})
        self.assertEqual((s["read_posts"], s["clicks"]), (["/api/x/list/"], [{"text": "Accounts"}]))
        self.assertNotIn("wait_s", ROUTES["surfaces"][0])           # the input is not touched

    def test_clicks_and_read_posts_only_by_hand(self):
        for key, v in (("clicks", [{"text": "Delete"}]), ("read_posts", ["/api/x/"])):
            _, applied, skipped = O.merge(ROUTES, {"entries": [ent(key, v, source="learned")]})
            self.assertEqual(applied, [])
            self.assertIn("manual", skipped[0][1])
            self.assertIsNone(O.add_learned({"entries": []}, "admin", "Benefits > Time Off", key, v, "r", "d"))

    def test_bad_entries_are_ignored_with_a_reason(self):
        bad = [ent("wait_s", 99), ent("nope", 1), ent("alias", "x"), ent("loader_sel", "a{b}"),
               {**ent("wait_s", 3), "surface": "No Such Page"}]
        _, applied, skipped = O.merge(ROUTES, {"entries": bad})
        self.assertEqual((applied, len(skipped)), ([], 5))
        self.assertIn("no such surface", skipped[-1][1])

    def test_load_routes_reads_the_folder_file_and_learned_lines_expire(self):
        data = O.load()
        self.assertEqual(data["entries"], [])
        e = O.add_learned(data, "admin", "Benefits > Time Off", "alias", "Time Off Requests",
                          "kb-1", "2026-11-23", label="Requests")
        self.assertEqual(O.path(), self.d / ".medtrics-release-notes" / "kb_overrides.json")
        O.save(data)
        rp = self.d / "routes.json"
        rp.write_text(json.dumps(ROUTES))
        self.assertEqual(O.load_routes(rp)["surfaces"][0]["aliases"], {"Requests": ["Time Off Requests"]})
        self.assertFalse(O.note_result(data, e, False))
        self.assertTrue(O.note_result(data, e, False))               # second failure in a row: dropped
        self.assertEqual(data["entries"], [])
        manual = ent("alias", "Y", label="X")
        self.assertFalse(O.note_result({"entries": [manual]}, manual, False))    # never a manual line

    def test_a_learned_line_never_replaces_a_manual_one(self):
        data = {"entries": [ent("quiet_cap_s", 15)]}
        self.assertIsNone(O.add_learned(data, "admin", "Benefits > Time Off", "quiet_cap_s", 20, "r", "d"))
        self.assertEqual(data["entries"][0]["value"], 15)

    def test_planner_uses_aliases_quiet_cap_and_loader(self):
        routes = json.loads(C.DEFAULT_ROUTES.read_text())
        merged, _, _ = O.merge(routes, {"entries": [
            ent("alias", "Time Off Requests", label="Requests"), ent("quiet_cap_s", 20),
            ent("loader_sel", ".hamster")]})
        plan = {"run_id": "r1", "capture_plan": [{"persona": "admin", "surface": "Benefits > Time Off",
                                                  "url": "/budgets/benefits/time-off/", "tickets": ["M1-1"]}]}
        calls = C.build_calls(plan, {"M1-1": {"labels": ["Requests"]}}, merged)
        v = calls["visits"][0]
        self.assertEqual(v["labels"]["M1-1"], ["Requests", "Time Off Requests"])
        self.assertEqual(v["near"], {"Time Off Requests": "Requests"})
        self.assertEqual(v["quiet"], {"cap_s": 20.0, "ld": ".hamster"})
        self.assertEqual(v["args"]["ld"], ".hamster")
        kbw = calls["batches"][0]["actions"][5]["input"]["text"]
        self.assertIn('"cap": 20000', kbw)
        self.assertIn('"ld": ".hamster"', kbw)


# ── diagnosis on the real run ────────────────────────────────────────────────
class Replay(Tmp):
    def load(self):
        shots = self.run_copy()
        return H._load(shots / "calls.json", shots / "raw", shots)[:3] + (shots,)

    def test_known_causes_of_run_2158(self):
        calls, items, manifest, _ = self.load()
        rows = H.diagnose(calls, items, manifest, 1)
        got = {(r["visit_id"], r["ticket"], r["cause"], r.get("target", "")) for r in rows}
        # M1-1413: the alert radios were on the page at y=1147 in a 639 px window
        self.assertIn(("v03", "M1-1413", "offscreen", "Automatic Alerts"), got)
        # M1-1459: View below the fold; the modal's labels not found (0.25.0 had no candidates)
        self.assertIn(("v02-s2", "M1-1459", "offscreen", "View"), got)
        self.assertIn(("v02-s3", "M1-1459", "not_found", ""), got)
        # the start page's second label sits lower: an extra frame, not a failure
        self.assertIn(("v02-s0", "M1-1459", "other_frame", "Submit"), got)
        by = {(r["visit_id"], r["cause"]): r for r in rows}
        self.assertIn("y=1147 in a 639 px window", by[("v03", "offscreen")]["detail"])
        self.assertTrue(by[("v03", "offscreen")]["retry"])
        self.assertFalse(by[("v02-s3", "not_found")]["retry"])
        self.assertTrue(by[("v02-s3", "not_found")]["fallback"])
        # 0.25.0 wrote the off-screen heading as a verified shot; it is no ringed label
        self.assertNotIn(("v02-s2", "M1-1459", "captured", ""), got)

    def test_pass_1_plan_replays_the_entry_and_records_only_the_failed_screens(self):
        shots = self.run_copy()
        rc, res, _ = self.heal("plan-retry", "--pass", "1", "--calls", str(shots / "calls.json"),
                               "--raw-dir", str(shots / "raw"), "--out-dir", str(shots))
        self.assertEqual((rc, res["screens"], res["first_batch"]), (0, 3, "r1b01"))
        plan = json.loads((shots / "retry-1.json").read_text())
        self.assertEqual([(b["batch_id"], b["kind"], b["visit_ids"]) for b in plan["batches"]],
                         [("r1b01", "visits", ["v02-s0~r1a"]), ("r1b02", "replay", []),
                          ("r1b03", "click", ["v02-s2~r1a"]), ("r1b04", "visits", ["v03~r1a"])])
        rv = {v["visit_id"]: v for v in plan["visits"]}
        self.assertEqual(rv["v03~r1a"]["labels"], {"M1-1413": ["Automatic Alerts"]})
        self.assertEqual((rv["v03~r1a"]["kind"], rv["v03~r1a"]["of"], rv["v03~r1a"]["retry"]), ("retry", "v03", 1))
        replay = plan["batches"][1]
        self.assertEqual([a["name"] for a in replay["pick"]], ["javascript_tool"])          # no recording
        self.assertEqual([a["input"].get("action") for a in replay["act"]], ["left_click", "wait", "javascript_exec"])
        names = [(a["name"], a["input"].get("action")) for b in plan["batches"]
                 for a in b.get("actions", []) + b.get("pick", []) + b.get("act", [])]
        self.assertTrue(set(n for n, _ in names) <= {"gif_creator", "navigate", "computer", "javascript_tool"})
        self.assertTrue(set(x for n, x in names if n == "computer") <= {"wait", "hover", "left_click"})
        md = (shots / "retry-1.md").read_text()
        self.assertIn("# KB capture, retry pass 1", md)
        self.assertIn("replay click 1 of entry v02 (\"Submit\", no recording)", md)
        self.assertIn('"visit_id": "v02-s0~r1a"', md)                                       # the raw example
        calls = json.loads((shots / "calls.json").read_text())
        self.assertEqual(len(calls["retry_visits"]), 3)                                    # cleanup knows their GIFs

    def test_extra_frames_only_where_they_are_cheap(self):
        r = lambda cause, entry, step: {"cause": cause, "entry": entry, "step": step, "retry": True}   # noqa: E731
        rows = [r("other_frame", "v01", 0), r("other_frame", "v02", 2), r("other_frame", "v03", 1),
                r("offscreen", "v03", 2), {**r("loading", "v04", 0), "retry": False}]
        kept = [(x["cause"], x["entry"]) for x in H.worth_retrying(rows)]
        self.assertEqual(kept, [("other_frame", "v01"), ("other_frame", "v03"), ("offscreen", "v03")])

    def test_limits_and_sign_in(self):
        calls, items, manifest, _ = self.load()
        rows = [r for r in H.diagnose(calls, items, manifest, 1) if r["retry"]]
        old = H.MAX_RETRY_SCREENS
        H.MAX_RETRY_SCREENS = 1
        self.addCleanup(setattr, H, "MAX_RETRY_SCREENS", old)
        plan = H.build_retry(calls, {"portal_entry": {}}, rows, 1)
        self.assertEqual(len({r["visit_id"] for r in plan["rows"]}), 1)
        self.assertTrue(all(s["skipped"] == "limit" for s in plan["skipped"]))
        for v in calls["visits"]:
            v["persona"] = "learner"                       # another sign-in: never retried
        plan = H.build_retry(calls, {"portal_entry": {}}, rows, 1)
        self.assertEqual((plan["batches"], {s["skipped"] for s in plan["skipped"]}), ([], {"needs_signin"}))


class Near(unittest.TestCase):
    def test_floors(self):
        c = ["Reassign Log Reviewer", "Reviewer", "Case Log Reviewer: Reassign now"]
        self.assertEqual(H.near_pick("Reassign Case Log Reviewer", c, 1), "Reassign Log Reviewer")
        self.assertIsNone(H.near_pick("Enable Review Process", ["Review"], 1))
        self.assertIsNone(H.near_pick("Reassign Case Log Reviewer", c[2:], 1))
        self.assertEqual(H.near_pick("Reassign Case Log Reviewer", c[2:], 2), c[2])          # every word present
        self.assertIsNone(H.near_pick("Save", ["Save"], 1))                                  # the label itself
        self.assertIsNone(H.near_pick("Reassign Case Log Reviewer", c, 1, exclude=set(c)))


# ── retry ingest, unringed fallback, learning, diagnosis ─────────────────────
def probe(labels, h=None, ld=""):
    return {"v": 1, "u": "/x/", "iw": IW, "ih": IH, "dpr": 1.25, "nf": False, "labels": labels,
            "h": h, "tn": {"v": "CLEAR", "k": []}, "tw": {}, "ld": ld}


def lab(l, b=(300, 200, 120, 24)):
    return {"l": l, "f": True, "t": l, "b": list(b), "iv": True, "oc": False, "sb": False}


def miss(l, c=()):
    o = {"l": l, "f": False, "t": "", "b": None, "iv": False, "oc": False, "sb": False}
    if c:
        o["c"] = list(c)
    return o


@unittest.skipUnless(HAVE_PIL, "Pillow not installed")
class EndToEnd(Tmp):
    """One screen, three passes of trouble: loading -> near match -> nothing."""

    def setUp(self):
        super().setUp()
        from PIL import Image
        self.shots = self.d / "shots"
        (self.shots / "raw").mkdir(parents=True)
        (self.shots / "frames").mkdir()
        self.png = self.d / "frame.png"
        Image.new("RGB", (IW, IH), (250, 250, 250)).save(self.png)
        routes = json.loads(C.DEFAULT_ROUTES.read_text())
        plan = {"run_id": "kb-t", "capture_plan": [
            {"persona": "admin", "surface": "Benefits > Time Off", "url": "/budgets/benefits/time-off/",
             "tickets": ["M1-1"]},
            {"persona": "admin", "surface": "Calendar > Institution events", "url": "/institution/calendar/",
             "tickets": ["M1-2"]},
            {"persona": "admin", "surface": "Cases > Case logs", "url": "/cases/procedures/logs/",
             "tickets": ["M1-3"]}]}
        inputs = {"M1-1": {"labels": ["Requests"]}, "M1-2": {"labels": ["Reassign Case Log Reviewer"]},
                  "M1-3": {"labels": ["Nowhere At All"]}}
        self.calls = C.build_calls(plan, inputs, routes)
        (self.shots / "calls.json").write_text(json.dumps(self.calls))
        self.raw("b01", [("v01", probe([miss("Requests")], ld="loader-txt")),
                         ("v02", probe([miss("Reassign Case Log Reviewer", ["Reassign Log Reviewer"])])),
                         ("v03", probe([miss("Nowhere At All")], h={"t": "Case Logs", "b": [195, 90, 1315, 62]}))])
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            rc = C.main(["ingest", "--calls", str(self.shots / "calls.json"), "--raw-dir", str(self.shots / "raw"),
                         "--out-dir", str(self.shots), "--no-ocr"])
        self.assertEqual(rc, 0)

    def raw(self, bid, items):
        (self.shots / "raw" / f"{bid}.json").write_text(json.dumps(
            {"batch_id": bid, "status": "ok", "error": None,
             "items": [{"visit_id": v, "probe": p, "screenshot_path": str(self.png)} for v, p in items]}))

    def args(self, *extra):
        return ["--calls", str(self.shots / "calls.json"), "--raw-dir", str(self.shots / "raw"),
                "--out-dir", str(self.shots), *extra]

    def test_two_passes_then_fallback_then_learning(self):
        rc, res, _ = self.heal("plan-retry", "--pass", "1", *self.args())
        self.assertEqual((rc, res["screens"]), (0, 2))                       # loading + near match
        p1 = json.loads((self.shots / "retry-1.json").read_text())
        rv = {v["of"]: v for v in p1["visits"]}
        self.assertEqual(rv["v02"]["labels"], {"M1-2": ["Reassign Log Reviewer"]})
        self.assertEqual(rv["v02"]["near"], {"Reassign Log Reviewer": "Reassign Case Log Reviewer"})
        self.assertEqual(rv["v01"]["quiet"]["cap_s"], C.QUIET_CAP_S)
        # pass 1: the near match is found; the Time Off page is still loading
        b = {v["visit_id"]: bt["batch_id"] for bt in p1["batches"] for v in p1["visits"]
             if v["visit_id"] in bt["visit_ids"]}
        self.raw(b[rv["v01"]["visit_id"]], [(rv["v01"]["visit_id"], probe([miss("Requests")], ld="loader-txt"))])
        self.raw(b[rv["v02"]["visit_id"]], [(rv["v02"]["visit_id"], probe([lab("Reassign Log Reviewer")]))])
        rc, res, _ = self.heal("ingest-retry", "--pass", "1", *self.args("--no-ocr"))
        self.assertEqual((rc, res["captured"], res["frames"]), (0, 1, 2))
        shots = json.loads((self.shots / "shots.json").read_text())["shots"]
        near = next(s for s in shots if s.get("label_match") == "near")
        self.assertEqual((near["label"], near["label_wanted"], near["retry"], near["of"], near["verified"]),
                         ("Reassign Log Reviewer", "Reassign Case Log Reviewer", 1, "v02", True))
        # pass 2: only the loading screen is left, now with the 20 s cap
        rc, res, _ = self.heal("plan-retry", "--pass", "2", *self.args())
        self.assertEqual((rc, res["screens"]), (0, 1))
        p2 = json.loads((self.shots / "retry-2.json").read_text())
        r2 = p2["visits"][0]
        self.assertEqual((r2["of"], r2["quiet"]["cap_s"]), ("v01", C.QUIET_CAP_MAX_S))
        self.raw(p2["batches"][0]["batch_id"], [(r2["visit_id"], probe([lab("Requests")]))])
        rc, res, _ = self.heal("ingest-retry", "--pass", "2", *self.args("--no-ocr"))
        self.assertEqual(res["captured"], 1)
        # finish: the screen with nothing to ring gets an unringed shot; two things were learned
        rc, res, out = self.heal("finish", *self.args("--no-ocr"))
        self.assertEqual((rc, res["healed"], res["unringed"]), (0, 2, 1))
        shots = json.loads((self.shots / "shots.json").read_text())
        un = next(s for s in shots["shots"] if s.get("fallback") == "unringed")
        self.assertEqual((un["ticket"], un["anchor"], un["label"], un["verified"]),
                         ("M1-3", "screen", "Case Logs", True))
        self.assertEqual(shots["missing"], {})
        ov = O.load()["entries"]
        self.assertEqual({(e["key"], e.get("label"), e["value"], e["source"], e["run"]) for e in ov},
                         {("alias", "Reassign Case Log Reviewer", "Reassign Log Reviewer", "learned", "kb-t"),
                          ("quiet_cap_s", None, 20, "learned", "kb-t")})
        self.assertTrue((self.shots / "kb_overrides.json").is_file())           # travels in the run zip
        md = (self.shots / "diagnosis.md").read_text()
        self.assertIn("the page was still loading (loader-txt); retry 1 waited longer; still no screenshot.", md)
        self.assertIn("retry 2 waited longer; captured.", md)
        self.assertIn("\"Reassign Case Log Reviewer\" was not on the page under that exact text; retry 1 "
                      "used the closest text on the page (\"Reassign Log Reviewer\"); captured.", md)
        self.assertIn("\"Nowhere At All\" was not found on the page", md)
        self.assertIn("an unringed screenshot was kept for you to judge", md)
        self.assertIn("## Learned", md)
        # nothing left: a third plan has nothing to do
        rc, _, _ = self.heal("plan-retry", "--pass", "2", *self.args())
        self.assertEqual(rc, 4)

    def test_finish_without_retries_and_no_learn(self):
        rc, res, _ = self.heal("finish", *self.args("--no-ocr", "--no-learn"))
        self.assertEqual((rc, res["healed"], res["learned"]), (0, 0, []))
        self.assertEqual(res["unringed"], 2)              # the near-match and the not-found screen; not the loading one
        self.assertFalse(O.path().exists())

    def test_one_screen_error_is_not_the_end(self):
        orig = C.ingest_visit
        C.ingest_visit = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        self.addCleanup(setattr, C, "ingest_visit", orig)
        entries, reason = C.safe_ingest_visit({"visit_id": "v09"}, {}, {}, {}, self.shots)
        self.assertEqual((entries, reason), ([], "internal_error"))
        self.assertIn("v09: RuntimeError: boom", (self.shots / "errors.log").read_text())


# ── the unringed shot downstream: spec, gate, contact sheet ──────────────────
@unittest.skipUnless(HAVE_PIL, "Pillow not installed")
class ScreenAnchor(Tmp):
    def test_spec_gate_and_sheet_flag(self):
        from PIL import Image
        import kb_package as P
        import kb_spec as S
        frame = self.d / "f.png"
        Image.new("RGB", (IW, IH), "white").save(frame)
        shots = [{"shot_id": "s1", "ticket": "M1-3", "verified": True, "frame": str(frame),
                  "anchor": "screen", "fallback": "unringed", "label": "Case Logs", "label_score": 1.0,
                  "tenant": "CLEAR", "surface": "Cases > Case logs", "source": "chrome:x"},
                 {"shot_id": "s2", "ticket": "M1-3", "verified": True, "frame": str(frame),
                  "anchor": "dom_box", "label": "Reassign Log Reviewer", "label_match": "near",
                  "label_wanted": "Reassign Case Log Reviewer", "label_score": 1.0,
                  "dom_box": [300, 200, 120, 24], "tenant": "CLEAR", "surface": "Cases > Case logs",
                  "source": "chrome:x"}]
        prose = {"ticket": "M1-3", "title": "T", "steps": [
            {"text": "Open the case logs page.", "shot": "s1", "caption": "The page"},
            {"text": "Click **Reassign Log Reviewer**.", "shot": "s2", "caption": "The button"}]}
        specs = self.d / "specs"
        spec = S.build_spec(prose, shots, {}, None, specs)
        self.assertTrue((specs / spec["steps"][0]["image"]).is_file())
        self.assertEqual(spec["steps"][0]["evidence"]["anchor"], "screen")
        self.assertEqual(V.g3_evidence(spec, "draft"), [])                     # no bold label needed for it
        pub = [f.where for f in V.g3_evidence(spec, "publish")]
        self.assertEqual(pub, ["step 1", "step 2"])                             # both need the owner's ok
        (specs / "M1-3.json").write_text(json.dumps(spec))
        (specs / "assemble.json").write_text(json.dumps([{"ticket": "M1-3", "status": "ok"}]))
        items = P.candidates(specs)
        self.assertEqual([i["flag"] for i in items], ["no ring", "near match: Reassign Log Reviewer"])
        self.assertTrue(P.render_sheets(items, specs, self.d / "sheet.png"))

    def test_writer_bundle_lines(self):
        import inspect
        import kb_spec as S
        src = inspect.getsource(S)
        self.assertIn("the whole screen, nothing ringed", src)
        self.assertIn("the ticket calls it", src)
        self.assertIn(F.SCREEN_ANCHOR, F.DRAFT_ONLY_ANCHORS)


# ── route check and promotion ────────────────────────────────────────────────
class RouteCheck(Tmp):
    def test_plan_is_probe_only_and_main_sign_in_only(self):
        chk = H.build_check(json.loads(C.DEFAULT_ROUTES.read_text()))
        self.assertGreater(len(chk["visits"]), 10)
        self.assertTrue(all(C.account_of(v["persona"]) == "main" for v in chk["visits"]))
        acts = [(a["name"], a["input"].get("action")) for b in chk["batches"] for a in b["actions"]]
        self.assertEqual({n for n, _ in acts}, {"navigate", "computer", "javascript_tool"})
        self.assertEqual({x for n, x in acts if n == "computer"}, {"wait"})         # no click, no recording
        logs = next(v for v in chk["visits"] if v["surface"] == "Cases > Case logs")
        self.assertEqual((logs["first_click"], logs["args"]["labels"]), ("Submit", ["Submit"]))
        md = H.check_md(chk)
        self.assertIn("KB_CHECK_DONE", md)
        self.assertNotIn("gif_creator", md)

    def test_report_rows(self):
        chk = {"visits": [{"visit_id": f"c0{i}", "persona": "admin", "surface": f"S{i}", "url": "/x/",
                           "first_click": "Go" if i == 5 else ""} for i in range(1, 8)], "batches": []}
        p = lambda **kw: {"visit_id": "x", "probe": {**probe([]), **kw}}        # noqa: E731
        items = {"c01": p(), "c02": p(nf=True), "c03": p(ld="loader-txt"),
                 "c04": p(tn={"v": "BLOCK", "k": ["tenant_slug"]}), "c05": p(labels=[miss("Go")]),
                 "c06": p(wb=1, wl="POST /api/list/")}
        got = [r["result"] for r in H.check_rows(chk, items)]
        self.assertEqual(got[0], "ok")
        self.assertEqual(got[1], "PAGE NOT FOUND")
        self.assertIn("still loading", got[2])
        self.assertIn("tenant data", got[3])
        self.assertIn("first click \"Go\" is not on the page", got[4])
        self.assertIn("POST /api/list/", got[5])
        self.assertIn("not checked", got[6])

    def test_promotion_tool_takes_learned_lines_only(self):
        import zipfile
        routes = self.d / "kb_routes.json"
        routes.write_text(json.dumps(ROUTES, indent=1) + "\n")
        z = self.d / "run.zip"
        with zipfile.ZipFile(z, "w") as f:
            f.writestr("kb-1/shots/kb_overrides.json", json.dumps({"entries": [
                ent("alias", "Time Off Requests", source="learned", label="Requests"),
                ent("clicks", [{"text": "Accounts"}])]}))
        tool = [sys.executable, str(ROOT / "tools" / "kb_promote_overrides.py"), str(z), "--routes", str(routes)]
        r = subprocess.run(tool, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Time Off Requests", r.stdout)
        self.assertIn("not written", r.stdout)
        self.assertNotIn("aliases", routes.read_text())
        r = subprocess.run(tool + ["--apply"], capture_output=True, text=True, timeout=60)
        s = json.loads(routes.read_text())["surfaces"][0]
        self.assertEqual(s["aliases"], {"Requests": ["Time Off Requests"]})
        self.assertEqual(s["clicks"], [{"text": "Requests"}])                    # the hand-written line stayed out


# ── JS: kbw and the loader text ──────────────────────────────────────────────
FAKE = r"""
var mk=function(cls,text,box){return {className:cls,tagName:'DIV',children:[],textContent:text||'',
 getBoundingClientRect:function(){return {left:box[0],top:box[1],width:box[2],height:box[3],right:box[0]+box[2],bottom:box[1]+box[3]};}};};
var window={innerWidth:1536,innerHeight:639,getComputedStyle:function(){return {visibility:'visible',opacity:'1'};}};
var EL=[];
var document={body:{},querySelectorAll:function(sel){if(sel.indexOf('{')>=0)throw new Error('bad selector');
 return EL.filter(function(e){return e.sel.split(',').some(function(x){return sel.split(',').indexOf(x)>=0;});});}};
"""


@unittest.skipUnless(NODE, "node not installed")
class Js(unittest.TestCase):
    def kbw(self, setup, args):
        body = FAKE + setup + f"var f={C.wait_source()};f({json.dumps(args)}).then(function(r){{console.log(JSON.stringify(r));}});"
        r = subprocess.run([NODE, "-e", body], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_quiet_page_answers_fast(self):
        out = self.kbw("", {"cap": 5000, "quiet": 200})
        self.assertTrue(out["q"])
        self.assertLess(out["ms"], 1500)

    def test_waits_for_the_apps_loader_to_go(self):
        setup = ("var l=mk('loader-txt font-italic','Please wait',[600,300,200,30]);l.sel=\"[class^=loader]\";"
                 "EL.push(l);setTimeout(function(){EL.length=0;},700);")
        out = self.kbw(setup, {"cap": 5000, "quiet": 200})
        self.assertTrue(out["q"])
        self.assertGreaterEqual(out["ms"], 700)

    def test_gives_up_at_the_cap_and_names_the_loader(self):
        setup = "var l=mk('spinner-border','',[600,300,30,30]);l.sel=\"[class^=spinner]\";EL.push(l);"
        out = self.kbw(setup, {"cap": 600, "quiet": 200})
        self.assertEqual((out["q"], out["ld"]), (False, "spinner-border"))

    def test_loading_text_and_a_pages_own_selector(self):
        setup = "var l=mk('','Loading…',[600,300,90,20]);l.sel='h3';EL.push(l);"
        self.assertEqual(self.kbw(setup, {"cap": 500, "quiet": 100})["ld"], "text:Loading…")
        setup = "var l=mk('','Loading data may take a while',[600,300,90,20]);l.sel='h3';EL.push(l);"
        self.assertTrue(self.kbw(setup, {"cap": 800, "quiet": 100})["q"])                # prose is no loader
        setup = "var l=mk('hamster','',[600,300,90,20]);l.sel='.hamster';EL.push(l);"
        self.assertEqual(self.kbw(setup, {"cap": 500, "quiet": 100, "ld": ".hamster"})["ld"], "hamster")
        self.assertTrue(self.kbw(setup, {"cap": 800, "quiet": 100, "ld": "a{b}"})["q"])  # a bad selector is ignored

    def test_install_stores_kbw_and_the_stub_awaits_it(self):
        text = C.install_action()["text"]
        self.assertIn("sessionStorage.setItem('kbw'", text)
        stub = C._quiet({})["input"]["text"]
        self.assertTrue(stub.startswith("/*KB*/await (sessionStorage.getItem('kbw')"))
        self.assertIn('"cap": 10000', stub)
        r = subprocess.run([NODE, "--check", "-"], input="var f=" + C.probe_source() + ";",
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("near(l)", C.probe_source())
        # live finding: a radio's name is a <b> inside a long <label>, so leaf elements are candidates too
        self.assertIn(json.dumps(C.CAND_LEAF_SEL), C.probe_source())


if __name__ == "__main__":
    unittest.main()
