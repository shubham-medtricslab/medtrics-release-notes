"""Tests for the 0.27.0 capture plan: per-step labels (the page label + the next
button, the last screen gets the ticket's labels), account groups (main = admin +
faculty, learner, external) with the sign-in stop marker and --from-batch text,
the external link (path+query only, never into shots.json), and write visits from
the approved data plan (not in any batch; replace needs_write; ingest takes them
from any raw file, records write_failed). Hermetic: no browser, no network.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kb_fixtures import has_module
from test_kb_chrome_capture import Base, run_cli, lab, probe_for
from test_kb_gif_capture import make_gif

import kb_capture as C
import kb_format as F

SESS = "/vadministration/curriculum/sessions/"
AFTER = {"expect": "Notify Participants?", "choose": "No, don't send notifications",
         "labels": ["Notify Participants?", "No, don't send notifications"]}
PLAN = {"run_id": "r1", "capture_plan": [
    {"persona": "faculty", "surface": "Notes", "url": "/notes/", "tickets": ["M1-6"]},
    {"persona": "admin", "surface": "Sessions", "url": SESS, "clicks": [{"row": True}, {"text": "Add schedule"}],
     "write_gated": "confirm dialog after Save", "tickets": ["M1-465"]},
    {"persona": "learner", "surface": "My Schedule", "url": "/learner/schedule/", "tickets": ["M1-7"]},
    {"persona": "external", "surface": "Evaluation", "url": "", "tickets": ["M1-8"]},
]}
INPUTS = {"M1-465": {"labels": ["Date", "Notify Participants?"]}, "M1-6": {"labels": ["Notes"]},
          "M1-7": {"labels": ["Today"]}, "M1-8": {"labels": ["Submit evaluation"]}}
DATA = {"approved": True, "tickets": {"M1-465": {"records": [
    {"id": "r1", "what": "session schedule", "persona": "admin", "start": SESS, "after": AFTER},
    {"id": "r2", "what": "no after block", "persona": "admin", "start": SESS}]},
    "M1-999": {"records": [{"id": "r9", "persona": "admin", "after": AFTER}]}}}
DATA["approved_sha256"] = C.data_plan_sha256(DATA)          # an approval carries the hash of its tickets
LINK = "https://medtrics.medtricslab.com/ev/abc?token=SECRET123#frag"


class Plan(Base):
    def go(self, plan=PLAN, data=None, link=None, inputs=INPUTS):
        (self.tmp / "plan.json").write_text(json.dumps(plan))
        (self.tmp / "inputs.json").write_text(json.dumps(inputs))
        argv = ["plan-calls", "--plan", self.tmp / "plan.json", "--inputs", self.tmp / "inputs.json",
                "--out-dir", self.tmp / "shots"]
        if data is not None:
            (self.tmp / "data_plan.json").write_text(json.dumps(data))
            argv += ["--data-plan", self.tmp / "data_plan.json"]
        if link:
            argv += ["--external-link", link]
        rc, res, out = run_cli(*argv)
        self.assertEqual(rc, 0)
        self.shots = self.tmp / "shots"
        return json.loads((self.shots / "calls.json").read_text()), res


class StepLabels(Plan):
    def test_page_label(self):
        for url, want in ((SESS, "Sessions"), ("/administration/users/?a=1#x", "Users"),
                          ("/x/course-groups/12/", "Course Groups"), ("/", ""), ("", "")):
            self.assertEqual(C.page_label(url), want, url)

    def test_row_then_text_click(self):
        calls, _ = self.go()
        by = {v["visit_id"]: v for v in calls["visits"]}
        self.assertEqual(by["v02-s0"]["labels"]["M1-465"], ["Sessions"])          # first click is a row
        self.assertEqual(by["v02-s1"]["labels"]["M1-465"], ["Add schedule"])       # the next button
        self.assertEqual(by["v02-s2"]["labels"]["M1-465"], ["Date", "Notify Participants?"])   # last: inputs
        self.assertEqual(by["v02-s0"]["args"]["labels"], ["Sessions"])
        self.assertEqual(by["v02-s1"]["args"]["labels"], ["Add schedule"])
        self.assertTrue(by["v02-s1"]["click_args"]["row"])                          # clicks[0] is the row
        self.assertEqual(by["v02-s1"]["click_args"]["pa"]["labels"], [])

    def test_first_click_text_is_on_the_start_screen(self):
        plan = {"run_id": "r1", "capture_plan": [
            {"persona": "admin", "surface": "S", "url": SESS, "tickets": ["M1-1"],
             "clicks": [{"text": "Scheduling"}, {"text": "Add schedule"}, {"text": "Edit"}]}]}
        calls, _ = self.go(plan, inputs={"M1-1": {"labels": ["Save"]}})
        got = [v["labels"]["M1-1"] for v in calls["visits"]]
        self.assertEqual(got, [["Sessions", "Scheduling"], ["Add schedule"], ["Edit"], ["Save"]])

    def test_entries_without_clicks_keep_the_ticket_labels(self):
        calls, _ = self.go()
        v = next(x for x in calls["visits"] if x["visit_id"] == "v01")
        self.assertEqual((v["labels"], v["args"]["labels"]), ({"M1-6": ["Notes"]}, ["Notes"]))

    def test_two_tickets_share_the_step_labels(self):
        plan = {"run_id": "r1", "capture_plan": [
            {"persona": "admin", "surface": "S", "url": SESS, "tickets": ["M1-1", "M1-2"],
             "clicks": [{"text": "Add"}]}]}
        calls, _ = self.go(plan, inputs={"M1-1": {"labels": ["A"]}, "M1-2": {"labels": ["B", "A"]}})
        self.assertEqual(calls["visits"][0]["labels"], {"M1-1": ["Sessions", "Add"], "M1-2": ["Sessions", "Add"]})
        self.assertEqual(calls["visits"][1]["labels"], {"M1-1": ["A"], "M1-2": ["B", "A"]})
        self.assertEqual(calls["visits"][1]["args"]["labels"], ["A", "B"])


class Accounts(Plan):
    def test_order_and_groups(self):
        calls, _ = self.go()
        self.assertEqual([(a["account"], a["personas"]) for a in calls["accounts"]],
                         [("main", ["admin", "faculty"]), ("learner", ["learner"]), ("main", ["admin"])])
        main, learner, back = calls["accounts"]
        self.assertTrue(back["return"] and back["signin"] == "main")       # sign back in as admin
        self.assertNotIn("signin", main)
        self.assertEqual(learner["signin"], "learner")
        by = {b["batch_id"]: b for b in calls["batches"]}
        self.assertEqual([by[b]["persona"] for b in main["batches"]],
                         ["admin", "admin", "admin", "faculty"])      # admin before faculty, then learner
        learner_b, ret = calls["batches"][-2], calls["batches"][-1]
        self.assertEqual(learner_b["persona"], "learner")
        self.assertEqual(learner["batches"], [learner_b["batch_id"]])
        self.assertEqual(learner_b["needs_signin"], "learner")
        self.assertEqual((ret["kind"], ret["account"], ret["needs_signin"], ret["visit_ids"]),
                         ("restore", "main", "main", []))
        self.assertEqual(ret["actions"], calls["restore"])                 # the "return to Admin portal" batch
        self.assertEqual(back["batches"], [ret["batch_id"]])
        self.assertEqual([b.get("needs_signin") for b in calls["batches"][:-2]], [None] * (len(calls["batches"]) - 2))
        self.assertEqual({b["account"] for b in calls["batches"][:-2]}, {"main"})
        self.assertEqual(sum(len(a["batches"]) for a in calls["accounts"]), len(calls["batches"]))

    def test_external_needs_the_link(self):
        calls, res = self.go()
        sc = [x for x in calls["screens"] if x["persona"] == "external"]
        self.assertEqual([(x["reason"], x["tickets"]) for x in sc], [("external_link_missing", ["M1-8"])])
        self.assertNotIn("external", [v["persona"] for v in calls["visits"]])
        self.assertEqual(res["accounts"], ["main", "learner", "main"])

    def test_external_link_is_path_and_query_only(self):
        calls, _ = self.go(link=LINK)
        ext = next(v for v in calls["visits"] if v["persona"] == "external")
        self.assertEqual((ext["url"], ext["private"]), ("/ev/abc?token=SECRET123", True))
        self.assertEqual([(a["account"], a.get("signin")) for a in calls["accounts"]],
                         [("main", None), ("learner", "learner"), ("external", "external_link"), ("main", "main")])
        last = calls["batches"][-2]
        self.assertEqual((last["persona"], last["needs_signin"]), ("external", "external_link"))
        self.assertEqual(calls["batches"][-1]["kind"], "restore")
        self.assertNotIn("screens", {x["reason"] for x in calls["screens"]})
        self.assertFalse([x for x in calls["screens"] if x["persona"] == "external"])

    def test_capture_md_tells_the_stop_and_from_batch(self):
        calls, _ = self.go(link=LINK)
        md = (self.shots / "capture.md").read_text()
        for word in ("Accounts and sign-in", "stop=needs_signin:<account>", "--from-batch", "needs_signin",
                     "`learner` — personas learner; sign-in `learner`", "`external` — personas external; sign-in `external_link`",
                     "First batch of account `learner`", "stop=needs_signin:main", "Return batch", "restore batch",
                     "Group rows", "`\"group\":true`"):
            self.assertIn(word, md)
        self.assertEqual(md.count("```json"), len(calls["batches"]) + sum(b["kind"] == "click" for b in calls["batches"])
                         + 3)           # + abort, raw example, restore (accounts are text)

    def test_main_only_run_has_one_account_and_no_signin(self):
        plan = {"run_id": "r1", "capture_plan": [PLAN["capture_plan"][0]]}
        calls, _ = self.go(plan)
        self.assertEqual(calls["accounts"], [{"account": "main", "personas": ["faculty"], "batches": ["b01"]}])
        self.assertNotIn("needs_signin", calls["batches"][0])

    def test_no_return_batch_when_the_last_account_is_main(self):
        plan = {"run_id": "r1", "capture_plan": [PLAN["capture_plan"][2], PLAN["capture_plan"][0]]}
        calls, _ = self.go(plan)
        self.assertEqual([a["account"] for a in calls["accounts"]], ["main", "learner", "main"])
        self.assertEqual(calls["batches"][-1]["kind"], "restore")           # learner last: return batch
        plan = {"run_id": "r1", "capture_plan": [PLAN["capture_plan"][0]]}
        calls, _ = self.go(plan)
        self.assertNotIn("restore", [b["kind"] for b in calls["batches"]])

    def test_persona_order_helper(self):
        self.assertEqual(C.persona_order(["external", "learner", "faculty", "admin", "other"]),
                         ["admin", "faculty", "other", "learner", "external"])


class ReasonTexts(unittest.TestCase):
    def test_words(self):
        self.assertEqual(F.missing_reason_text("write_failed"), "the form step could not be completed")
        self.assertEqual(F.missing_reason_text("write_failed", "Save button not found"),
                         "the form step could not be completed")            # no internals in the marker
        self.assertEqual(F.missing_reason_text("external_link_missing"), "external link not given")
        self.assertIn("nothing was stored", F.missing_reason_text("write_blocked_ok"))
        self.assertEqual(C.REASON_NO_EXTERNAL_LINK, "external_link_missing")


class WriteVisits(Plan):
    def test_planned_not_batched_and_replace_needs_write(self):
        calls, res = self.go(data=DATA)
        self.assertEqual(res["write_visits"], 1)
        [w] = calls["write_visits"]
        self.assertEqual((w["visit_id"], w["kind"], w["ticket"], w["record_id"], w["persona"]),
                         ("v02-w1", "write", "M1-465", "r1", "admin"))
        self.assertEqual(w["labels"], AFTER["labels"])
        self.assertEqual((w["gif"], w["entry"], w["url"]), ("kb-capture/r1/v02-w1.gif", "v02", SESS))
        self.assertEqual(w["args"]["labels"], AFTER["labels"])
        self.assertNotIn("v02-w1", [v["visit_id"] for v in calls["visits"]])
        self.assertNotIn("v02-w1", [i for b in calls["batches"] for i in b["visit_ids"]])
        self.assertNotIn("v02-w1", json.dumps(calls["batches"]))
        self.assertFalse([x for x in calls["screens"] if x["reason"] == "needs_write"])

    def test_without_a_data_plan_needs_write_stays(self):
        calls, _ = self.go()
        self.assertEqual(calls["write_visits"], [])
        self.assertEqual([x["reason"] for x in calls["screens"] if x["entry"] == "v02"], ["needs_write"])

    def test_unapproved_plan_is_ignored(self):
        calls, res = self.go(data={**DATA, "approved": False})
        self.assertEqual((calls["write_visits"], res["write_visits"]), ([], 0))
        self.assertEqual([x["reason"] for x in calls["screens"] if x["entry"] == "v02"], ["needs_write"])

    def test_approval_needs_a_matching_hash(self):
        """kb_capture plan-calls --data-plan: approved is True AND approved_sha256 matches the tickets."""
        nohash = {k: v for k, v in DATA.items() if k != "approved_sha256"}
        for name, data in (("no hash", nohash), ("wrong hash", {**DATA, "approved_sha256": "0" * 64}),
                           ("approved 'yes'", {**DATA, "approved": "yes"}),
                           ("edited after approval", json.loads(json.dumps(DATA)))):
            if name == "edited after approval":
                data["tickets"]["M1-465"]["records"][0]["after"]["choose"] = "Yes, send notifications"
            with self.subTest(name):
                calls, res = self.go(data=data)
                self.assertEqual((calls["write_visits"], res["write_visits"]), ([], 0))
                self.assertEqual([x["reason"] for x in calls["screens"] if x["entry"] == "v02"], ["needs_write"])
        calls, res = self.go(data=DATA)                          # the matching hash is accepted
        self.assertEqual(res["write_visits"], 1)

    def test_hash_function_agrees_with_kb_data(self):
        import kb_data as D
        for plan in (DATA, {"tickets": {}}, {}, {"approved": True, "tickets": {"é": {"records": [{"id": "ü"}]}}},
                     {**DATA, "approved": False, "rejected": [{"x": 1}], "checked_sha256": "a"}):
            self.assertEqual(C.data_plan_sha256(plan), D.plan_hash(plan))

    def test_numbering_and_order_per_entry(self):
        data = {"approved": True, "tickets": {"M1-465": {"records": [
            {"id": "r1", "persona": "admin", "after": AFTER}, {"id": "r2", "persona": "admin", "after": AFTER}]},
            "M1-6": {"records": [{"id": "r3", "persona": "faculty", "after": {"labels": ["Notes"]}}]}}}
        data["approved_sha256"] = C.data_plan_sha256(data)
        calls, _ = self.go(data=data)
        self.assertEqual([(w["visit_id"], w["record_id"]) for w in calls["write_visits"]],
                         [("v01-w1", "r3"), ("v02-w1", "r1"), ("v02-w2", "r2")])


@unittest.skipUnless(has_module("PIL"), "Pillow not installed")
class WriteIngest(Plan):
    PLAN = {"run_id": "r1", "capture_plan": [PLAN["capture_plan"][1]]}

    def setUp(self):
        super().setUp()
        self.calls, _ = self.go(self.PLAN, data=DATA)
        self.raw = self.tmp / "raw"
        self.raw.mkdir()
        self.dl = self.tmp / "Downloads"
        (self.dl / "kb-capture" / "r1").mkdir(parents=True)
        for bid, vid in (("b01", "v01-s0"), ("b02", "v01-s1"), ("b03", "v01-s2")):
            self.raw_file(bid, [self.item(vid, [lab("Sessions"), lab("Add schedule")])])

    def raw_file(self, name, items):
        (self.raw / f"{name}.json").write_text(json.dumps(
            {"batch_id": name, "status": "ok", "error": None, "items": items}))

    def item(self, vid, labels):
        make_gif(self.dl / f"kb-capture/r1/{vid}.gif")
        return {"visit_id": vid, "probe": probe_for(labels), "gif": f"kb-capture/r1/{vid}.gif"}

    def ingest(self):
        rc, res, _ = run_cli("ingest", "--calls", self.shots / "calls.json", "--raw-dir", self.raw,
                             "--out-dir", self.shots, "--downloads-dir", self.dl, "--no-ocr")
        return rc, res, json.loads((self.shots / "shots.json").read_text())

    def test_write_visit_from_any_raw_file(self):
        self.raw_file("data-M1-465", [self.item("v01-w1", [lab("Notify Participants?"),
                                                       lab("No, don't send notifications")])])
        rc, res, m = self.ingest()
        self.assertEqual((rc, res["left"]), (0, 0))
        w = [s for s in m["shots"] if s.get("visit_id") == "v01-w1"]
        self.assertEqual([s["label"] for s in w], ["Notify Participants?", "No, don't send notifications"])
        self.assertEqual({s["ticket"] for s in w}, {"M1-465"})
        self.assertTrue((self.shots / "gifs" / "v01-w1.gif").is_file())
        self.assertNotIn("M1-465", m["missing"])                 # no needs_write, no write_failed
        self.assertIn("kb-capture/r1/v01-w1.gif", res["downloads_left"])

    def test_data_raw_file_is_items_only_and_a_blocked_save_is_noted(self):
        it = self.item("v01-w1", [lab("Notify Participants?")])
        (self.raw / "data-M1-465.json").write_text(json.dumps(
            {"batch_id": "b01", "status": "ok", "items": [{**it, "blocked": 1}]}))      # even a clashing batch_id
        items, batches = C._load_raw(self.raw)
        self.assertIn("v01-w1", items)
        self.assertEqual(sorted(batches), ["b01", "b02", "b03"])           # data files are never batches
        self.assertEqual(batches["b01"]["items"][0]["visit_id"], "v01-s0")
        rc, res, m = self.ingest()
        self.assertEqual(rc, 0)
        sc = next(x for x in m["screens"] if x["visit_id"] == "v01-w1")
        self.assertEqual((sc["reason"], sc["note"]), (None, "write_blocked_ok"))

    FORM = [{"label": "Date", "kind": "date", "required": True, "type": "date", "name": "date"},
            {"label": "Venues", "kind": "multiselect", "required": False, "type": "div", "name": ""}]
    DLG = {"t": "Notify Participants?", "x": "Notify Participants?", "bt": ["Yes, send notifications", "Cancel"]}

    def wprobe(self, **kw):
        return probe_for([lab("Notify Participants?")], dlg=self.DLG, tw={"institution_string": ["School of"]}, **kw)

    def test_screens_carry_the_forms_fields_for_kb_data_plan(self):
        it = self.item("v01-s2", [lab("Save")])
        it["probe"] = probe_for([lab("Save"), lab("Nope", f=False)], fields=self.FORM, tw={"tenant_slug": ["xymed"]},
                                dlg=self.DLG)
        self.raw_file("b03", [it])
        rc, res, m = self.ingest()
        sc = next(x for x in m["screens"] if x["visit_id"] == "v01-s2")
        self.assertEqual(sc["fields"], self.FORM)
        self.assertEqual(sc["tw"], {"tenant_slug": ["xymed"]})
        self.assertEqual((sc["btn"], sc["found"]), (self.DLG["bt"], ["Save"]))
        plain = next(x for x in m["screens"] if x["visit_id"] == "v01-s0")
        self.assertNotIn("fields", plain)                              # a screen without a form stays small

    def test_dry_run_item_learns_only_it_is_needs_write_with_no_shots(self):
        it = self.item("v01-w1", [lab("Notify Participants?")])
        it.update(probe=self.wprobe(wb=0), ticket="M1-465", record_id="r1", mode="dry_run", blocked=1,
                  choose={"ok": True, "x": 1, "y": 2, "label": "No, don't send notifications"},
                  post_probe=self.wprobe(wb=1, wl="POST /api/s/", wk="xhr"))
        self.raw_file("data-M1-465", [it])
        rc, res, m = self.ingest()
        self.assertEqual((rc, res["left"]), (0, 0))
        self.assertEqual([x for x in m["shots"] if x.get("visit_id") == "v01-w1"], [])        # not an article shot
        sc = next(x for x in m["screens"] if x["visit_id"] == "v01-w1")
        self.assertEqual((sc["reason"], sc["detail"]), ("needs_write", "dry_run"))
        self.assertEqual(sc["btn"], self.DLG["bt"])
        self.assertEqual(m["missing"]["M1-465"], {"reason": "needs_write", "detail": "dry_run"})

    def test_saved_item_is_noted_and_ingests_like_a_write_screen(self):
        it = self.item("v01-w1", [lab("Notify Participants?")])
        it.update(mode="save", blocked=0, created={"fingerprint": "abc", "id": "S-1", "status": "201", "page_url": SESS})
        self.raw_file("data-M1-465", [it])
        rc, res, m = self.ingest()
        sc = next(x for x in m["screens"] if x["visit_id"] == "v01-w1")
        self.assertEqual((sc["reason"], sc["note"]), (None, "saved"))
        self.assertTrue([x for x in m["shots"] if x.get("visit_id") == "v01-w1"])

    def test_pending_without_created_is_flagged_unverified(self):
        it = self.item("v01-w1", [lab("Notify Participants?")])
        it.update(mode="save", pending="abc")
        it["probe"] = "[BLOCKED: x]"                                   # the run died before the post-click probe
        self.raw_file("data-M1-465", [it])
        rc, res, m = self.ingest()
        self.assertEqual(m["missing"]["M1-465"], {"reason": "write_failed", "detail": "record_unverified"})

    def test_write_error_is_recorded(self):
        self.raw_file("data-M1-465", [{"visit_id": "v01-w1", "write_error": "Save button not found"}])
        rc, res, m = self.ingest()
        self.assertEqual(rc, 0)
        self.assertEqual(m["missing"]["M1-465"], {"reason": "write_failed", "detail": "Save button not found"})
        self.assertEqual(res["missing"]["M1-465"], "write_failed")

    def test_write_visit_never_run(self):
        _, _, m = self.ingest()
        self.assertEqual(m["missing"]["M1-465"], {"reason": "write_failed", "detail": "not run"})

    def test_reingest_replaces_by_visit_id(self):
        self.raw_file("data-M1-465", [self.item("v01-w1", [lab("Notify Participants?")])])
        self.ingest()
        _, _, m = self.ingest()
        self.assertEqual(len([s for s in m["shots"] if s.get("visit_id") == "v01-w1"]), 1)

    def test_cleanup_names_include_write_gifs(self):
        self.raw_file("data-M1-465", [self.item("v01-w1", [lab("Notify Participants?")])])
        rc, res, _ = run_cli("cleanup", "--downloads-dir", self.dl, "--names-from", self.shots / "calls.json")
        self.assertIn("kb-capture/r1/v01-w1.gif", res["deleted"])


@unittest.skipUnless(has_module("PIL"), "Pillow not installed")
class HeadingFallback(Plan):
    """A click/write screen with none of its labels keeps its shot, anchored on the
    page heading as a draft-only dom_box."""
    PLAN = {"run_id": "r1", "capture_plan": [PLAN["capture_plan"][1]]}
    H = {"t": "Add schedule", "b": [100, 80, 300, 30]}

    def setUp(self):
        super().setUp()
        self.calls, _ = self.go(self.PLAN)
        self.raw, self.dl = self.tmp / "raw", self.tmp / "Downloads"
        self.raw.mkdir()
        (self.dl / "kb-capture" / "r1").mkdir(parents=True)

    def item(self, vid, probe):
        make_gif(self.dl / f"kb-capture/r1/{vid}.gif")
        return {"visit_id": vid, "probe": probe, "gif": f"kb-capture/r1/{vid}.gif"}

    def run_ingest(self, items):
        for i, it in enumerate(items):
            (self.raw / f"b0{i + 1}.json").write_text(json.dumps(
                {"batch_id": f"b0{i + 1}", "status": "ok", "error": None, "items": [it]}))
        rc, res, _ = run_cli("ingest", "--calls", self.shots / "calls.json", "--raw-dir", self.raw,
                             "--out-dir", self.shots, "--downloads-dir", self.dl, "--no-ocr")
        return json.loads((self.shots / "shots.json").read_text())

    def test_click_screens_without_a_label_use_the_heading(self):
        calls = json.loads((self.shots / "calls.json").read_text())
        self.assertEqual({v["visit_id"]: v["labels"]["M1-465"] for v in calls["visits"]},
                         {"v01-s0": ["Sessions"], "v01-s1": ["Add schedule"],
                          "v01-s2": ["Date", "Notify Participants?"]})
        m = self.run_ingest([self.item("v01-s0", probe_for([lab("Sessions")])),
                             self.item("v01-s1", probe_for([], h=self.H)),
                             self.item("v01-s2", probe_for([], h=self.H))])
        got = {s_["visit_id"]: s_ for s_ in m["shots"]}
        self.assertEqual(sorted(got), ["v01-s0", "v01-s1", "v01-s2"])
        e = got["v01-s1"]
        self.assertEqual((e["anchor"], e["label"], e["fallback"], e["ticket"], e["verified"]),
                         ("dom_box", "Add schedule", "heading", "M1-465", True))
        self.assertEqual(e["dom_box"], list(C.to_image_box({"x": 100, "y": 80, "width": 300, "height": 30}, 1)))
        self.assertIn(e["anchor"], F.DRAFT_ONLY_ANCHORS)               # G3 refuses it on publish
        self.assertNotIn("fallback", got["v01-s0"])

    def test_a_found_label_or_a_start_screen_gets_no_fallback(self):
        m = self.run_ingest([self.item("v01-s0", probe_for([], h=self.H)),            # a start visit: no fallback
                             self.item("v01-s1", probe_for([lab("Add schedule")], h=self.H)),
                             self.item("v01-s2", probe_for([lab("Date")], h=self.H))])
        self.assertFalse([s_ for s_ in m["shots"] if s_.get("fallback")])
        self.assertEqual({s_["visit_id"] for s_ in m["shots"]}, {"v01-s1", "v01-s2"})

    def test_no_heading_no_fallback(self):
        m = self.run_ingest([self.item("v01-s1", probe_for([])), self.item("v01-s2", probe_for([], h=None))])
        self.assertEqual(m["shots"], [])


@unittest.skipUnless(has_module("PIL"), "Pillow not installed")
class ExternalIngest(Plan):
    def test_the_link_stays_out_of_shots_json(self):
        plan = {"run_id": "r1", "capture_plan": [PLAN["capture_plan"][3]]}
        calls, _ = self.go(plan, link=LINK)
        raw, dl = self.tmp / "raw", self.tmp / "Downloads"
        raw.mkdir()
        (dl / "kb-capture" / "r1").mkdir(parents=True)
        make_gif(dl / "kb-capture/r1/v01.gif")
        (raw / "b01.json").write_text(json.dumps({"batch_id": "b01", "status": "ok", "error": None, "items": [
            {"visit_id": "v01", "probe": probe_for([lab("Submit evaluation")], u="/ev/abc?token=SECRET123"),
             "gif": "kb-capture/r1/v01.gif"}]}))
        rc, res, _ = run_cli("ingest", "--calls", self.shots / "calls.json", "--raw-dir", raw,
                             "--out-dir", self.shots, "--downloads-dir", dl, "--no-ocr")
        self.assertEqual(rc, 0)
        text = (self.shots / "shots.json").read_text()
        self.assertIn("external-link", text)
        self.assertNotIn("SECRET123", text)
        self.assertNotIn("/ev/abc", text)


if __name__ == "__main__":
    unittest.main()
