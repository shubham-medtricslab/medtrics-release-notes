"""Tests for kb_data.py (0.28.0): the deterministic fill plan built from the form the capture
saw (no planner agent), dry_run vs save mode, the choose / request rules, approval with a
content hash, the data-maker brief (Vue-commit field scripts, one-shot allowance call), and the
shipped agent files. Learning, records and demo-data are in test_kb_flow.py. Hermetic: no
browser, no network.
"""

import contextlib
import datetime as _dt
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kb_fixtures  # noqa: F401  (puts scripts/kb on sys.path)
from test_kb_chrome_capture import fields_probe, FORM_TREE

import kb_capture as C
import kb_data as D

ROOT = Path(__file__).resolve().parent.parent
ROUTES = json.loads(C.DEFAULT_ROUTES.read_text())
SESS = "/vadministration/curriculum/sessions/"
TODAY = "2026-10-29"


def fld(label, kind, required=False, type_=None, name=None, **kw):
    return {"label": label, "kind": kind, "required": required, "type": type_ or kind,
            "name": name if name is not None else label.lower(), **kw}


FORM = [fld("Date", "date", True, "date"), fld("Start", "time", True, "time"), fld("End", "time", True, "time"),
        fld("Status", "select", False, "select-one", opts=["Draft", "Published"]),
        fld("Venues", "multiselect", False, "div", name=""), fld("Groups", "duallist", False, "div", name="")]


def run(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = D.main(argv)
    out = buf.getvalue()
    return rc, out, json.loads(out.split("---KB_DATA_RESULT---")[1])


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, name, obj):
        p = self.d / name
        p.write_text(json.dumps(obj))
        return str(p)

    def world(self, fields=FORM, write_gated="New Session Created - notify participants when user clicks on save button",
              persona="admin", clicks=(({"row": True}), {"text": "Add schedule"}), url=SESS, btn=(), found=("Save",),
              tickets=("M1-465",), extra_entries=()):
        """groups.json / calls.json (built by kb_capture) / shots.json (as ingest writes it) / data.json."""
        entries = [{"persona": persona, "surface": "Sessions", "url": url, "clicks": list(clicks),
                    "write_gated": write_gated, "tickets": list(tickets), "session": "unknown"}, *extra_entries]
        groups = {"run_id": "r1", "capture_plan": entries}
        inputs = {t: {"labels": ["Save"]} for t in tickets}
        self.groups, self.inputs = groups, inputs
        calls = C.build_calls(groups, inputs, ROUTES)
        self.calls = calls
        last = [v for v in calls["visits"] if v["entry"] == "v01"][-1]["visit_id"]
        screens = [{"visit_id": v["visit_id"], "entry": v["entry"], "step": v.get("step", 0), "persona": v["persona"],
                    "surface": v["surface"], "tickets": v["tickets"], "reason": None, "detail": ""}
                   for v in calls["visits"]]
        for s in screens:
            if s["visit_id"] == last and fields is not None:
                s.update(fields=fields, found=list(found), **({"btn": list(btn)} if btn else {}))
        return {"groups": self.put("groups.json", groups), "calls": self.put("calls.json", calls),
                "shots": self.put("shots.json", {"shots": [], "screens": screens}),
                "data": self.put("data.json", {"issues": [{"identifier": t, "title": f"Title {t}"} for t in tickets]})}

    def plan(self, w=None, *extra, today=TODAY, out="data_plan.json"):
        w = w or self.world()
        rc, text, res = run(["plan", "--groups", w["groups"], "--calls", w["calls"], "--shots", w["shots"],
                             "--data", w["data"], "--out", str(self.d / out), "--today", today, *extra])
        plan = json.loads((self.d / out).read_text()) if (self.d / out).exists() else None
        return rc, text, res, plan

    def records_of(self, plan):
        return [r for t in plan["tickets"].values() for r in t["records"]]


def flow_note(ticket="M1-465", start=SESS, path=(({"row": True}), {"text": "Add schedule"}), save="Save",
              dialog="Notify Participants?", choose="No, don't send notifications", request="POST /api/schedules/",
              channel="xhr", on="choose", status="learned", **kw):
    rec = {"ticket": ticket, "key": D.flow_key(ticket, start, list(path), save), "start": start,
           "path": json.dumps(list(path), separators=(",", ":")), "save": save, "dialog": dialog,
           "buttons": json.dumps(["Yes, send notifications", choose, "Cancel"]), "choose": choose,
           "request": request, "channel": channel, "request_on": on, "status": status, "reason": "",
           "learned": "2026-10-28", "run": "kb-x", **kw}
    return {"id": "n-flow", "version": 1, "content": D.render_flow_note(rec)}


def demo_note(ticket="M1-465", fingerprint="abc123", status="created", server_id="S-9", **kw):
    rec = {"ticket": ticket, "what": "Sessions — Add schedule", "fingerprint": fingerprint,
           "link": "https://demo.example/vadministration/curriculum/sessions/", "date": "2027-04-27",
           "start_time": "09:00", "end_time": "10:00", "venue": "", "server_id": server_id, "run": "kb-x",
           "status": status, "created": "2026-10-28", "delete_before": "2027-04-20", "deleted": "", **kw}
    return {"id": f"n-{fingerprint}", "version": 3, "content": D.render_demo_note(rec)}


class Plan(Base):
    def test_the_m1_465_form_gets_a_date_two_times_and_nothing_else(self):
        rc, text, res, plan = self.plan()
        self.assertEqual((rc, res["records"], res["stopped"], plan["approved"]), (0, 1, [], False))
        self.assertTrue(res["always_ask"])
        [r] = self.records_of(plan)
        self.assertEqual([(f["label"], f["kind"], f["value"]) for f in r["fields"]],
                         [("Date", "date", "2027-04-27"), ("Start", "time", "09:00"), ("End", "time", "10:00")])
        self.assertEqual((r["id"], r["persona"], r["start"], r["save"], r["mode"]), ("r1", "admin", SESS, "Save", "dry_run"))
        self.assertEqual(r["path"], [{"row": True}, {"text": "Add schedule"}])
        self.assertEqual(r["what"], "Sessions — Add schedule")
        self.assertEqual(r["after"], {"expect": "", "choose": "", "labels": []})
        line = [ln for ln in text.splitlines() if ln.startswith("- M1-465")][0]
        self.assertEqual(line,
            "- M1-465 · r1 · admin · DRY RUN, nothing is saved: /vadministration/curriculum/sessions/ → first row → "
            "\"Add schedule\" · fields: Date=2027-04-27; Start=09:00; End=10:00 · presses \"Save\" (blocked), reads the "
            "prompt's buttons and the request it would send, clicks the safe answer (blocked) · the real save happens "
            "next run once the prompt is learned.")

    def test_required_fields_by_kind(self):
        form = [fld("Name", "text", True, "text"), fld("Notes", "textarea", True, "textarea"),
                fld("Seats", "text", True, "number"), fld("Nickname", "text", False, "text"),
                fld("Status", "select", True, "select-one", opts=["Draft", "Published", "ZZ DEMO Status"], zz="ZZ DEMO Status"),
                fld("Mandatory", "checkbox", False, "checkbox")]
        _, _, res, plan = self.plan(self.world(form))
        [r] = self.records_of(plan)
        self.assertEqual({f["label"]: f["value"] for f in r["fields"]},
                         {"Name": "ZZ KB DEMO – M1-465", "Notes": "ZZ KB DEMO – M1-465", "Seats": "1",
                          "Status": "ZZ DEMO Status"})                       # Nickname / Mandatory: optional -> empty

    def test_stops(self):
        cases = [
            ([fld("Status", "select", True, "select-one", opts=["Draft"])], "needs_owner", "ZZ DEMO"),
            ([fld("Venues", "multiselect", True, "div", name="")], "unsupported_required_picker", "Venues"),
            ([fld("Groups", "duallist", True, "div", name="")], "unsupported_required_picker", "Groups"),
            ([fld("Agree", "checkbox", True, "checkbox")], "unsupported_required_field", "Agree"),
            ([fld("Contact", "text", True, "email")], "unsupported_required_field", "email"),
            ([fld("", "text", True, "text")], "unsupported_required_field", "no label"),
        ]
        for form, reason, word in cases:
            with self.subTest(reason=reason, word=word):
                _, text, res, plan = self.plan(self.world(form))
                self.assertEqual(plan["tickets"], {})
                self.assertEqual((res["stopped"][0]["reason"], word in res["stopped"][0]["detail"]), (reason, True))
                self.assertIn("STOPPED " + reason, text)

    def test_a_required_picker_stops_even_after_fields_that_were_planned(self):
        _, _, res, plan = self.plan(self.world(FORM[:3] + [fld("Groups", "duallist", True, "div", name="")]))
        self.assertEqual((plan["tickets"], res["stopped"][0]["reason"]), ({}, "unsupported_required_picker"))

    def test_text_boxes_that_are_really_date_and_time_pickers(self):
        form = [fld("Date", "text", True, "text"), fld("Start time", "text", True, "text"),
                fld("End time", "text", True, "text"), fld("Title", "text", True, "text")]
        _, _, _, plan = self.plan(self.world(form))
        [r] = self.records_of(plan)
        self.assertEqual([(f["label"], f["kind"], f["value"]) for f in r["fields"]],
                         [("Date", "date", "2027-04-27"), ("Start time", "time", "09:00"), ("End time", "time", "10:00"),
                          ("Title", "text", "ZZ KB DEMO – M1-465")])

    def test_datetime_local_carries_the_start_time(self):
        _, _, _, plan = self.plan(self.world([fld("When", "date", True, "datetime-local")]))
        self.assertEqual(self.records_of(plan)[0]["fields"][0]["value"], "2027-04-27T09:00")

    def test_date_rule_period_end_and_floor(self):
        def date(**kw):
            _, _, _, plan = self.plan(self.world(), *kw.get("extra", ()), today=kw.get("today", TODAY))
            return self.records_of(plan)[0]["fields"][0]["value"]
        self.assertEqual(date(extra=("--period-end", "2026-12-15")), "2026-12-31")
        self.assertEqual(date(extra=("--period-end", "2026-11-10")), "2026-11-30")
        self.assertEqual(date(extra=("--period-end", "2026-10-31")), "2027-04-27")      # too soon: today + 180 days
        self.assertLess(_dt.date.fromisoformat(date(today="2026-10-30")).weekday(), 5)
        self.assertEqual(D.far_date(_dt.date(2026, 10, 29)).isoformat(), "2027-04-27")

    def test_save_text_comes_from_what_the_page_showed(self):
        for btn, found, want in (((), ("Save",), "Save"), (("Cancel", "Create"), (), "Create"),
                                 ((), ("Add schedule", "Submit"), "Submit"), ((), (), "Save"),
                                 (("Save",), ("Create",), "Save")):
            with self.subTest(btn=btn, found=found):
                _, _, _, plan = self.plan(self.world(btn=btn, found=found))
                self.assertEqual(self.records_of(plan)[0]["save"], want)

    def test_tickets_without_a_gated_step_or_a_form_are_not_planned(self):
        _, _, res, plan = self.plan(self.world(write_gated=""))
        self.assertEqual((res["records"], res["stopped"]), (0, []))
        _, _, res, plan = self.plan(self.world(fields=None))
        self.assertEqual((res["records"], res["stopped"][0]["reason"]), (0, "no_form_screen"))
        _, _, res, plan = self.plan(self.world(fields=[fld("Search", "text", False, "text")]))
        self.assertEqual((res["records"], res["stopped"][0]["reason"]), (0, "no_form_screen"))     # nothing to fill

    def test_persona_and_path_and_start_rules(self):
        _, _, res, _ = self.plan(self.world(persona="learner"))
        self.assertEqual(res["stopped"][0]["reason"], "persona")
        for clicks, start in (([{"text": "Delete session"}], SESS), ([{"text": "Send now"}], SESS),
                              ([{"row": True}], "/administration/users/delete/"), ([{"row": True}], "/x/logout/"),
                              ([{"row": True}], "/?portal_change=personal")):
            with self.subTest(clicks=clicks, start=start):
                _, _, res, plan = self.plan(self.world(clicks=clicks, url=start))
                self.assertEqual((plan["tickets"], res["stopped"][0]["reason"]), ({}, "needs_owner"))
        self.assertEqual(D.start_problem(SESS), "")
        self.assertIn("path on the demo", D.start_problem("//evil.example/x"))
        self.assertIn("path on the demo", D.start_problem("https://evil.example/x"))

    def test_the_fields_can_also_come_from_the_raw_folder(self):
        w = self.world()
        raw = self.d / "raw"
        raw.mkdir()
        probe = {"v": 1, "u": SESS, "iw": 1470, "ih": 802, "dpr": 2, "labels": [], "fields": FORM,
                 "tn": {"v": "CLEAR", "k": []}}
        (raw / "b03.json").write_text(json.dumps({"batch_id": "b03", "items": [{"visit_id": "v01-s2", "probe": probe}]}))
        rc, _, res, plan = self.plan({**w, "shots": str(raw)})
        self.assertEqual((rc, res["records"]), (0, 1))

    def test_unreadable_input_is_an_error(self):
        w = self.world()
        rc, _, res = run(["plan", "--groups", w["groups"], "--calls", str(self.d / "nope.json"), "--shots", w["shots"],
                          "--out", str(self.d / "p.json")])
        self.assertEqual((rc, res["ok"]), (1, False))

    def test_one_record_per_entry_and_a_cap_per_ticket(self):
        extra = [{"persona": "admin", "surface": f"S{i}", "url": f"/vadministration/x{i}/", "clicks": [{"text": "Add"}],
                  "write_gated": "g", "tickets": ["M1-465"]} for i in range(2, 6)]
        w = self.world(extra_entries=extra)
        calls = json.loads(Path(w["calls"]).read_text())
        screens = json.loads(Path(w["shots"]).read_text())
        for v in calls["visits"]:
            if v["entry"] != "v01" and v.get("step") == 1:
                screens["screens"].append({"visit_id": v["visit_id"], "fields": FORM})
        Path(w["shots"]).write_text(json.dumps(screens))
        _, _, res, plan = self.plan(w)
        self.assertEqual(len(self.records_of(plan)), D.MAX_RECORDS)
        self.assertEqual([s["reason"] for s in res["stopped"]], ["too_many_records"] * 2)
        self.assertEqual([r["id"] for r in self.records_of(plan)], ["r1", "r2", "r3"])


class Modes(Base):
    def test_a_usable_flow_note_makes_a_real_save_with_the_exact_request(self):
        flows = self.put("flows.json", {"notes": [flow_note()]})
        _, text, res, plan = self.plan(None, "--flows", flows)
        [r] = self.records_of(plan)
        self.assertEqual((r["mode"], res["save"], res["dry_run"]), ("save", 1, 0))
        self.assertEqual(r["flow"], {"dialog": "Notify Participants?", "choose": "No, don't send notifications",
                                     "request": "POST /api/schedules/", "query": "",
                                     "buttons": ["Yes, send notifications", "No, don't send notifications", "Cancel"],
                                     "channel": "xhr", "request_on": "choose", "status": "learned"})
        self.assertEqual(r["after"], {"expect": "Notify Participants?", "choose": "No, don't send notifications",
                                      "labels": ["Notify Participants?", "No, don't send notifications"]})
        line = [ln for ln in text.splitlines() if ln.startswith("- M1-465")][0]
        self.assertEqual(line,
            "- M1-465 · r1 · admin · WILL BE SAVED on the demo site (a demo record; you delete it afterwards, it is logged "
            "in Mem): /vadministration/curriculum/sessions/ → first row → \"Add schedule\" · fields: Date=2027-04-27; "
            "Start=09:00; End=10:00 · presses \"Save\", prompt \"Notify Participants?\", then clicks \"No, don't send "
            "notifications\" · exactly one request is allowed: POST /api/schedules/; every other write stays blocked.")

    def test_no_flow_for_this_ticket_start_path_or_save_means_dry_run(self):
        for note in (flow_note(ticket="M1-999"), flow_note(start="/vadministration/other/"),
                     flow_note(path=({"row": True},)), flow_note(save="Create")):
            _, _, _, plan = self.plan(None, "--flows", self.put("flows.json", {"notes": [note]}))
            self.assertEqual(self.records_of(plan)[0]["mode"], "dry_run")
        _, _, _, plan = self.plan(None, "--flows", str(self.d / "missing.json"))        # a missing dump is an empty one
        self.assertEqual(self.records_of(plan)[0]["mode"], "dry_run")

    def test_an_unusable_flow_keeps_the_dry_run_and_says_why(self):
        bad = {"status": ("unusable", "flow status is unusable"), "channel": ({"channel": "form"}, "fetch/xhr"),
               "on": ({"on": "save"}, "not sent by the prompt"), "choose": ({"choose": "Yes, send notifications"}, "choose"),
               "request": ({"request": "DELETE /api/schedules/1/"}, "POST / PUT / PATCH"),
               "path": ({"request": "POST /api/schedules/notify/"}, "notify"),
               "dialog": ({"dialog": ""}, "no prompt title"), "get": ({"request": "GET /api/x/"}, "POST")}
        for name, (kw, word) in bad.items():
            with self.subTest(name):
                kw = kw if isinstance(kw, dict) else {}
                note = flow_note(**({"status": "unusable"} if name == "status" else kw))
                _, text, _, plan = self.plan(None, "--flows", self.put("flows.json", {"notes": [note]}))
                [r] = self.records_of(plan)
                self.assertEqual(r["mode"], "dry_run")
                self.assertIn("stored flow not usable", r["flow_note"])
                self.assertIn(word.lower(), r["flow_note"].lower())
                self.assertNotIn("flow", {k for k in r if k == "flow"})

    def test_flow_problem_rules(self):
        base = D.parse_flow_note(flow_note())
        self.assertEqual(D.flow_problem(base), "")
        for edit, word in (({"choose": "OK"}, "cancel-type"), ({"choose": "No, send notifications"}, "cancel-type"),
                           ({"request": "PUT /a/delete/1/"}, "delete"), ({"request": "POST /a/?x=1"}, "plain path"),
                           ({"channel": "beacon"}, "beacon"), ({"channel": ""}, "?")):
            self.assertIn(word, D.flow_problem({**base, **edit}), edit)
        for req in ("POST /api/v2/schedules/", "PUT /s/9/", "PATCH /s/9/"):
            self.assertEqual(D.request_problem(req), "", req)
        self.assertEqual(D.request_problem("POST /api/schedules/save/"), "")          # "save" in a path is normal

    def test_a_live_demo_record_with_the_same_fingerprint_is_reused(self):
        _, _, _, first = self.plan(None)
        fp = self.records_of(first)[0]["fingerprint"]
        recs = self.put("records.json", {"notes": [demo_note(fingerprint=fp)]})
        _, text, res, plan = self.plan(None, "--records", recs)
        self.assertEqual((plan["tickets"], res["records"]), ({}, 0))
        self.assertEqual(plan["reused"][0]["server_id"], "S-9")
        self.assertIn("REUSED the live demo record S-9", text)
        for status in ("deleted", "not_saved"):                    # a deleted record is not live: save it again
            recs = self.put("records2.json", {"notes": [demo_note(fingerprint=fp, status=status)]})
            _, _, res, plan = self.plan(None, "--records", recs)
            self.assertEqual((res["records"], plan["reused"]), (1, []), status)
        recs = self.put("records3.json", {"notes": [demo_note(fingerprint=fp, ticket="M1-999")]})     # another ticket's
        self.assertEqual(self.plan(None, "--records", recs)[2]["records"], 1)

    def test_the_reuse_key_has_no_date_or_other_derived_value(self):
        _, _, _, a = self.plan(None, today="2026-10-29")
        _, _, _, b = self.plan(None, today="2026-10-29", out="b.json")
        _, _, _, c = self.plan(None, "--period-end", "2026-12-15", out="c.json")          # another date, times as well
        _, _, _, d = self.plan(None, today="2027-02-10", out="d.json")                    # another day
        fa, fb, fc, fd = (self.records_of(x)[0]["fingerprint"] for x in (a, b, c, d))
        self.assertEqual((fa, fb, fc, fd), (fa,) * 4)
        self.assertNotEqual(self.records_of(a)[0]["fields"][0]["value"], self.records_of(c)[0]["fields"][0]["value"])
        self.assertEqual(len(fa), 12)
        # what it IS made of: ticket, start, path, save text and the record kind (the form's shape)
        r = self.records_of(a)[0]
        f = D.fingerprint("M1-465", SESS, r["path"], r["fields"], "Save")
        self.assertEqual(f, fa)
        for other in (D.fingerprint("M1-466", SESS, r["path"], r["fields"], "Save"),
                      D.fingerprint("M1-465", SESS + "x/", r["path"], r["fields"], "Save"),
                      D.fingerprint("M1-465", SESS, r["path"][:1], r["fields"], "Save"),
                      D.fingerprint("M1-465", SESS, r["path"], r["fields"], "Create"),
                      D.fingerprint("M1-465", SESS, r["path"], r["fields"][:2], "Save")):
            self.assertNotEqual(other, fa)

    def test_two_plans_on_different_days_reuse_the_live_record(self):
        _, _, _, first = self.plan(None, today="2026-10-29")
        fp = self.records_of(first)[0]["fingerprint"]
        recs = self.put("records.json", {"notes": [demo_note(fingerprint=fp)]})
        _, text, res, plan = self.plan(None, "--records", recs, today="2027-01-12", out="later.json")
        self.assertEqual((plan["tickets"], res["records"]), ({}, 0))
        self.assertEqual((plan["reused"][0]["fingerprint"], plan["reused"][0]["server_id"]), (fp, "S-9"))
        self.assertIn("REUSED", text)

    def test_the_plan_binds_each_record_to_its_capture_entry(self):
        _, _, _, plan = self.plan(None)
        self.assertEqual(self.records_of(plan)[0]["entry"], "v01")

    def test_a_learned_query_travels_into_the_flow_the_preview_and_the_brief_allow(self):
        _, text, res, plan = self.plan(None, "--flows", self.put("flows.json", {"notes": [flow_note(query="?v=2")]}))
        [r] = self.records_of(plan)
        self.assertEqual((r["mode"], r["flow"]["query"]), ("save", "?v=2"))
        self.assertIn("POST /api/schedules/?v=2;", text)
        # a learned query that says delete is not a usable flow
        _, _, _, plan = self.plan(None, "--flows", self.put("flows2.json", {"notes": [flow_note(query="?action=delete_all")]}),
                                  out="p2.json")
        [r] = self.records_of(plan)
        self.assertEqual(r["mode"], "dry_run")
        self.assertIn("query contains \"delete\"", r["flow_note"])
        self.assertIn("query", D.request_problem("POST /api/s/", "a b"))


class Approval(Base):
    def test_hash_and_changed_plan(self):
        _, _, _, plan0 = self.plan()
        self.assertEqual(plan0["checked_sha256"], D.plan_hash(plan0))
        p = str(self.d / "data_plan.json")
        rc, _, res = run(["approve", "--plan", p])
        stored = json.loads(Path(p).read_text())
        self.assertEqual((rc, stored["approved"], stored["approved_sha256"]), (0, True, res["sha256"]))
        self.assertEqual(D.plan_hash(stored), res["sha256"])
        for mutate in (lambda r: r.update(save="Create"), lambda r: r["fields"][1].update(value="23:00"),
                       lambda r: r.update(mode="save"), lambda r: r["after"].update(choose="Cancel")):
            edited = json.loads(Path(p).read_text())
            mutate(self.records_of(edited)[0])
            rc, _, res = run(["brief", "--plan", self.put("e.json", edited), "--calls", self.put("c.json", {}),
                              "--out", str(self.d / "x.md")])
            self.assertEqual((rc, res["error"]), (2, "plan_changed"))
        # the stopped / reused lists are not part of what was approved
        edited = json.loads(Path(p).read_text())
        edited["stopped"] = [{"ticket": "X-1", "id": "r", "reason": "x", "detail": ""}]
        self.assertNotEqual(run(["brief", "--plan", self.put("e2.json", edited), "--calls", self.put("c2.json", {}),
                                 "--out", str(self.d / "y.md")])[2].get("error"), "plan_changed")

    def test_approve_refuses_a_plan_that_plan_did_not_produce(self):
        _, _, _, plan = self.plan()
        no_hash = {k: v for k, v in plan.items() if k != "checked_sha256"}
        rc, _, res = run(["approve", "--plan", self.put("nohash.json", no_hash)])
        self.assertEqual((rc, res["error"]), (2, "plan_not_checked"))
        forged = json.loads(json.dumps(plan))
        self.records_of(forged)[0]["save"] = "Create"
        rc, _, res = run(["approve", "--plan", self.put("forged.json", forged)])
        self.assertEqual((rc, res["error"]), (2, "plan_changed"))
        self.assertNotIn("approved_sha256", json.loads((self.d / "forged.json").read_text()))
        rc, _, res = run(["approve", "--plan", self.put("wrong.json", {**plan, "checked_sha256": "0" * 64})])
        self.assertEqual((rc, res["error"]), (2, "plan_changed"))

    def test_hash_agrees_with_kb_capture(self):
        _, _, _, plan = self.plan()
        self.assertEqual(C.data_plan_sha256(plan), D.plan_hash(plan))

    def test_brief_refuses_an_unapproved_plan(self):
        self.plan()
        rc, _, res = run(["brief", "--plan", str(self.d / "data_plan.json"), "--calls", self.put("c.json", {}),
                          "--out", str(self.d / "data.md")])
        self.assertEqual((rc, res["error"]), (2, "plan_not_approved"))
        self.assertFalse((self.d / "data.md").exists())
        stored = json.loads((self.d / "data_plan.json").read_text())
        stored["approved"] = True                                   # a hand-set flag without a hash is not an approval
        rc, _, res = run(["brief", "--plan", self.put("p2.json", stored), "--calls", self.put("c.json", {}),
                          "--out", str(self.d / "a.md")])
        self.assertEqual(res["error"], "plan_not_approved")


class Brief(Base):
    def approved(self, w=None, *extra):
        w = w or self.world()
        _, _, _, plan = self.plan(w, *extra)
        run(["approve", "--plan", str(self.d / "data_plan.json")])
        approved = json.loads((self.d / "data_plan.json").read_text())
        calls = C.build_calls(self.groups, self.inputs, ROUTES, data_plan=approved)
        self.calls_path = self.put("calls2.json", calls)
        return approved

    def brief(self, *extra):
        out = self.d / "data.md"
        rc, text, res = run(["brief", "--plan", str(self.d / "data_plan.json"), "--calls", self.calls_path,
                             "--out", str(out), *extra])
        return rc, res, (out.read_text() if out.exists() else "")

    @staticmethod
    def block(md):
        return json.loads(re.search(r"```json\n(\{\n \"ticket\".*?)\n```", md, re.S).group(1))

    def test_dry_run_block(self):
        self.approved()
        rc, res, md = self.brief()
        self.assertEqual((rc, res["records"], res["missing"]), (0, 1, []))
        b = self.block(md)
        self.assertEqual((b["mode"], b["visit_id"], b["navigate"]), ("dry_run", "v01-w1", SESS))
        self.assertEqual(b["gif"], "kb-capture/r1/v01-w1.gif")
        self.assertEqual(len(b["fingerprint"]), 12)
        self.assertEqual([p["click"] for p in b["path_picks"]], [{"row": True}, {"text": "Add schedule"}])
        self.assertIn('"write": ["Save"]', b["save_pick"]["js"])
        self.assertIn('"choose": true', b["choose_pick"]["js"])
        self.assertNotIn("allow", b["choose_pick"]["js"])                  # a dry run never asks for an allowance
        self.assertNotIn("pending", b)
        self.assertNotIn("expect", b)
        self.assertIn('"labels": ["Save"]', b["pre_save_probe_js"])
        self.assertIn('"labels": []', b["post_probe_js"])
        self.assertTrue(b["needs_demo_context"])
        self.assertEqual([(f["label"], f["fill"]) for f in b["fields"]],
                         [("Date", "native"), ("Start", "native"), ("End", "native")])
        self.assertIn("**`dry_run`**: NOTHING IS STORED", md)
        self.assertIn("### M1-465 / r1: Sessions — Add schedule (dry_run)", md)

    def test_save_block_arms_exactly_the_learned_request(self):
        flows = self.put("flows.json", {"notes": [flow_note()]})
        self.approved(None, "--flows", flows)
        rc, res, md = self.brief()
        self.assertEqual(rc, 0)
        b = self.block(md)
        self.assertEqual((b["mode"], b["expect"], b["request"]), ("save", "Notify Participants?", "POST /api/schedules/"))
        js = b["choose_pick"]["js"]
        self.assertIn('"text": "No, don\'t send notifications"', js)
        self.assertIn('"write": ["Save", "No, don\'t send notifications"]', js)
        self.assertIn('"allow": {"method": "POST", "path": "/api/schedules/", "dlg": "Notify Participants?", "query": ""}', js)
        self.assertNotIn("allow", b["save_pick"]["js"])                   # Save never carries an allowance
        self.assertNotIn('"choose": true', js)
        self.assertEqual(b["pending"], {"visit_id": "v01-w1", "record_id": "r1", "ticket": "M1-465", "mode": "save",
                                        "pending": b["fingerprint"]})
        self.assertIn('"labels": ["Notify Participants?", "No, don\'t send notifications"]', b["probe_js"])
        self.assertIn("### M1-465 / r1: Sessions — Add schedule (save)", md)

    def test_a_second_entry_of_the_ticket_gets_its_own_write_visit(self):
        # e1 /sessions (not gated) and e2 /sessions/new (gated), same ticket and persona: the record built
        # from e2 must be driven on e2's write visit, not on the first entry of the ticket
        e1 = {"persona": "admin", "surface": "Sessions", "url": SESS, "clicks": [], "tickets": ["M1-465"],
              "session": "unknown"}
        e2 = {"persona": "admin", "surface": "Sessions", "url": SESS + "new/", "clicks": [{"text": "Add schedule"}],
              "write_gated": "gate", "tickets": ["M1-465"], "session": "unknown"}
        groups = {"run_id": "r1", "capture_plan": [e1, e2]}
        inputs = {"M1-465": {"labels": ["Save"]}}
        self.groups, self.inputs = groups, inputs
        calls = C.build_calls(groups, inputs, ROUTES)
        last = [v for v in calls["visits"] if v["entry"] == "v02"][-1]["visit_id"]
        screens = [{"visit_id": v["visit_id"], "entry": v["entry"], "step": v.get("step", 0), "persona": v["persona"],
                    "surface": v["surface"], "tickets": v["tickets"], "reason": None, "detail": "",
                    **({"fields": FORM, "found": ["Save"]} if v["visit_id"] == last else {})} for v in calls["visits"]]
        w = {"groups": self.put("groups.json", groups), "calls": self.put("calls.json", calls),
             "shots": self.put("shots.json", {"shots": [], "screens": screens}),
             "data": self.put("data.json", {"issues": []})}
        self.approved(w)
        plan = json.loads((self.d / "data_plan.json").read_text())
        [rec] = self.records_of(plan)
        self.assertEqual(rec["entry"], "v02")
        calls2 = json.loads(Path(self.calls_path).read_text())
        self.assertEqual([(v["entry"], v["record_id"], v["url"]) for v in calls2["write_visits"]],
                         [("v02", "r1", SESS + "new/")])
        rc, res, md = self.brief()
        self.assertEqual((rc, res["records"], res["missing"], res["start_mismatch"]), (0, 1, [], []))
        # a record bound to an entry that is gone gets no write visit (not a guessed one)
        rec["entry"] = "v09"
        plan["tickets"]["M1-465"]["records"][0] = rec
        plan["approved_sha256"] = D.plan_hash(plan)
        self.assertEqual(C.build_calls(groups, inputs, ROUTES, data_plan=plan)["write_visits"], [])
        # one bound to the other entry of the ticket follows that entry
        rec["entry"] = "v01"
        plan["approved_sha256"] = D.plan_hash(plan)
        self.assertEqual([v["entry"] for v in C.build_calls(groups, inputs, ROUTES, data_plan=plan)["write_visits"]], ["v01"])

    def edited_brief(self, mutate, flows=True):
        """An approved plan, hand-edited, with the approval hash recomputed so the hash check passes."""
        extra = ("--flows", self.put("flows.json", {"notes": [flow_note()]})) if flows else ()
        self.approved(None, *extra)
        p = self.d / "data_plan.json"
        plan = json.loads(p.read_text())
        mutate(self.records_of(plan)[0])
        plan["approved_sha256"] = D.plan_hash(plan)
        p.write_text(json.dumps(plan))
        (self.d / "data.md").unlink(missing_ok=True)
        return self.brief()

    def test_brief_reruns_the_save_mode_safety_checks_on_the_approved_plan(self):
        edits = {
            "choose": (lambda r: r["flow"].update(choose="Yes, send notifications"), "cancel-type"),
            "choose_send": (lambda r: r["flow"].update(choose="No, send notifications"), "cancel-type"),
            "request_delete": (lambda r: r["flow"].update(request="DELETE /api/schedules/1/"), "POST / PUT / PATCH"),
            "request_path": (lambda r: r["flow"].update(request="POST /api/schedules/delete/"), "delete"),
            "request_query": (lambda r: r["flow"].update(query="?action=delete_all"), "delete"),
            "channel": (lambda r: r["flow"].update(channel="form"), "fetch/xhr"),
            "request_on": (lambda r: r["flow"].update(request_on="save"), "prompt's choice"),
            "ambiguous": (lambda r: r["flow"].update(request_on="ambiguous"), "ambiguous_request"),
            "no_dialog": (lambda r: r["flow"].update(dialog=""), "no prompt title"),
            "no_flow": (lambda r: r.pop("flow"), "no learned flow"),
            "start": (lambda r: r.update(start="/administration/curriculum/sessions/delete/"), "start"),
            "start_abs": (lambda r: r.update(start="https://evil.test/"), "start must be a path"),
            "path_click": (lambda r: r.update(path=[{"row": True}, {"text": "Delete session"}]), "path click"),
            "mode": (lambda r: r.update(mode="live"), "unknown mode"),
        }
        for name, (mutate, word) in edits.items():
            with self.subTest(name):
                rc, res, md = self.edited_brief(mutate)
                self.assertEqual((rc, res["error"]), (2, "unsafe_plan"), name)
                self.assertTrue(any(word in x for x in res["problems"]), (word, res["problems"]))
                self.assertEqual(md, "")                                    # nothing written
        # an untouched approved plan still briefs
        self.assertEqual(self.edited_brief(lambda r: None)[0], 0)
        # a dry-run record is checked too (start rule)
        rc, res, md = self.edited_brief(lambda r: r.update(start="//evil.test/x/"), flows=False)
        self.assertEqual((rc, res["error"]), (2, "unsafe_plan"))

    def test_rules_text_for_the_maker(self):
        self.approved()
        _, _, md = self.brief()
        for need in ("dry_run", "save", "one-shot allowance", "exactly one request", "10 seconds", "pending", "created",
                     "Never press", "kbclick", "Tab", "Escape", "data-<ticket>.json", "write_error", "save_did_nothing",
                     "prompt_not_seen", "`wb`", "`dlg`", "25 tool", "kbf:", "post_probe", "Never click twice",
                     "choose_pick", "NOTHING IS STORED"):
            self.assertIn(need, md)
        self.assertNotIn("{{", md)
        self.assertNotIn("kbopt", md)                                      # no pick fields any more
        self.assertNotIn("planner", md.lower())
        installs = re.findall(r"```json\n(\{\n \"action\": \"javascript_exec\".*?)\n```", md, re.S)
        self.assertEqual(len(installs), 2)
        self.assertIn("var S=", json.loads(installs[0])["text"])
        self.assertIn("sessionStorage.setItem('kbf'", json.loads(installs[1])["text"])

    def test_brief_starts_on_the_admin_portal_and_guards_every_page(self):
        self.approved()
        _, _, md = self.brief()
        entry = ROUTES["demo"]["portal_entry"]["admin"]
        start = json.loads(re.search(r"## Start.*?```json\n(.*?)\n```", md, re.S).group(1))
        self.assertEqual(start["navigate"], entry)
        self.assertTrue(start["probe_js"].startswith("/*KB*/(sessionStorage.getItem('kbp')?"))
        self.assertLess(md.index("## Start"), md.index("### M1-465 / r1"))
        b = self.block(md)
        self.assertTrue(b["guard_js"].startswith("/*KB*/(sessionStorage.getItem('kbp')?"))
        self.assertNotIn("portal_entry", b)
        for need in ("then\n   run `guard_js` (the probe", "run `guard_js` again", "before any field"):
            self.assertIn(need, md)
        routes = json.loads(C.DEFAULT_ROUTES.read_text())
        routes["demo"]["portal_entry"]["admin"] = "/?portal_change=other"
        self.brief("--routes", self.put("r.json", routes))
        self.assertIn('"navigate": "/?portal_change=other"', (self.d / "data.md").read_text())

    def test_a_start_that_differs_from_its_capture_entry_is_refused(self):
        approved = self.approved()
        for url in ("/vadministration/curriculum/courses/", "/administration/curriculum/sessions/"):
            calls = json.loads(Path(self.calls_path).read_text())
            calls["entries"]["v01"]["url"] = url
            self.calls_path = self.put("c3.json", calls)
            rc, res, _ = self.brief()
            self.assertEqual((rc, res["error"], res["missing"]), (1, "no_records", ["M1-465:r1"]), url)
            self.assertEqual(res["start_mismatch"][0]["reason"], "start_mismatch")
        calls["entries"]["v01"]["url"] = SESS + "#x"                       # a fragment is not part of the start
        self.calls_path = self.put("c4.json", calls)
        self.assertEqual(self.brief()[0], 0)

    def test_record_without_a_write_visit_is_reported(self):
        self.approved()
        calls = json.loads(Path(self.calls_path).read_text())
        calls["write_visits"] = []
        self.calls_path = self.put("c5.json", calls)
        rc, res, _ = self.brief()
        self.assertEqual((rc, res["error"], res["missing"]), (1, "no_records", ["M1-465:r1"]))

    def test_every_generated_script_is_marked_guarded_and_parses(self):
        flows = self.put("flows.json", {"notes": [flow_note()]})
        for extra in ((), ("--flows", flows)):
            self.approved(None, *extra)
            _, _, md = self.brief()
            b = self.block(md)
            scripts = [b["guard_js"], b["pre_save_probe_js"], b["probe_js"], b["post_probe_js"], b["save_pick"]["js"],
                       b["choose_pick"]["js"], *[p["js"] for p in b["path_picks"]]]
            for f in b["fields"]:
                scripts += [f["read_js"]] + ([f["fill_js"]] if "fill_js" in f else [])
            for js in scripts:
                self.assertTrue(js.startswith(C.JS_MARKER), js[:60])
            self.assertEqual(len(md.split(C.JS_MARKER)) - 1, len(re.findall(r"/\*KB\*/", md)))
            if shutil.which("node"):
                for js in scripts + [D.field_install_action()["text"], C.install_action()["text"]]:
                    r = subprocess.run([shutil.which("node"), "--check", "-"], input=js, capture_output=True, text=True)
                    self.assertEqual(r.returncode, 0, r.stderr)
        for js in (D.field_js("A", "b"), D.field_js("A", mode="read")):
            self.assertIn("sessionStorage.getItem('kbf')", js)
            self.assertLess(len(js), 700)                                     # a short stub, not a 9 KB script

    def test_typed_pickers_and_selects_in_the_brief(self):
        form = [fld("Date", "text", True, "text"), fld("Status", "select", True, "select-one",
                                                       opts=["ZZ DEMO Status"], zz="ZZ DEMO Status")]
        self.approved(self.world(form))
        _, _, md = self.brief()
        b = self.block(md)
        self.assertEqual([(f["label"], f["kind"], f["fill"]) for f in b["fields"]],
                         [("Date", "date", "typed"), ("Status", "select", "native")])
        self.assertNotIn("fill_js", b["fields"][0])
        self.assertIn('"value": "ZZ DEMO Status"', b["fields"][1]["fill_js"])


@unittest.skipUnless(shutil.which("node"), "node not available")
class FieldScript(unittest.TestCase):
    """The fill script finds a control by the label the PROBE reported (shared reader), Vue-commits
    the value, and refuses what it must not touch."""

    def go(self, tree, **args):
        r = fields_probe(tree, src=D.FIELD_JS, **args)
        return json.loads(r) if isinstance(r, str) else r

    def test_text_date_time_and_select_fills_stick(self):
        for label, value, want in (("Date", "2027-04-27", "2027-04-27"), ("Start", "09:00", "09:00"),
                                   ("Title", "ZZ KB DEMO – M1-465", "ZZ KB DEMO – M1-465"), ("Status", "ZZ DEMO Status", "ZZ DEMO Status")):
            with self.subTest(label):
                r = self.go(FORM_TREE, label=label, mode="set", value=value, side="nav")
                self.assertEqual(r, {"ok": True, "value": want})

    def test_every_label_the_probe_reports_is_found_by_the_fill_script(self):
        probe = fields_probe(FORM_TREE)
        for f in probe["fields"]:
            if f["kind"] in ("multiselect", "duallist", "checkbox"):
                r = self.go(FORM_TREE, label=f["label"], mode="read", side="nav")
                self.assertIn("KB_FIELD_REFUSED", r.get("threw", ""), f["label"])        # found, then refused
            else:
                r = self.go(FORM_TREE, label=f["label"], mode="read", side="nav")
                self.assertNotIn("threw", r, f["label"])

    def test_errors(self):
        self.assertIn("KB_FIELD_NOTFOUND:Nope", self.go(FORM_TREE, label="Nope", mode="set", value="x", side="nav")["threw"])
        self.assertIn("KB_FIELD_NOTFOUND:Search", self.go(FORM_TREE, label="Search", mode="set", value="x", side="nav")["threw"])   # sidebar
        self.assertIn("KB_FIELD_NOOPTION:Status", self.go(FORM_TREE, label="Status", mode="set", value="Archived", side="nav")["threw"])
        self.assertIn("KB_FIELD_REFUSED:Venues", self.go(FORM_TREE, label="Venues", mode="set", value="x", side="nav")["threw"])
        self.assertIn("KB_FIELD_REFUSED:Mandatory", self.go(FORM_TREE, label="Mandatory", mode="set", value="x", side="nav")["threw"])

    def test_name_breaks_a_label_tie(self):
        tree = [{"tag": "input", "type": "text", "attrs": {"name": "a", "placeholder": "Time"}, "top": 100},
                {"tag": "input", "type": "text", "attrs": {"name": "b", "placeholder": "Time"}, "top": 140}]
        r = self.go(tree, label="Time", mode="set", value="9", name="b", side="nav")
        self.assertEqual(r, {"ok": True, "value": "9"})

    def test_the_field_script_installs_the_write_guard_first(self):
        js = D.field_script()
        g = C.guard_source()
        body = js[js.index("(function(a){") + len("(function(a){"):].lstrip()
        self.assertTrue(body.startswith(g))
        self.assertNotIn("__GUARD__", js)
        self.assertNotIn("__FIELDS__", js)
        self.assertNotIn("__DLG__", js)

    def test_the_field_install_carries_the_run_token_loaded_after_import(self):
        import tempfile
        saved = C.ARM_TOKEN
        self.addCleanup(setattr, C, "ARM_TOKEN", saved)
        with tempfile.TemporaryDirectory() as d:
            tok = C.use_run_token(d)
            self.assertIn(tok, D.field_install_action()["text"])


class Agents(unittest.TestCase):
    def front(self, name):
        text = (ROOT / "agents" / name).read_text()
        fm = dict(re.findall(r"^(\w+): (.*)$", text.split("---")[1], re.M))
        return text, fm

    def test_the_planner_agent_is_gone(self):
        self.assertFalse((ROOT / "agents" / "kb-data-planner.md").exists())
        for p in (ROOT / "scripts" / "kb" / "kb_data.py", ROOT / "scripts" / "build_plugin.py"):
            self.assertNotIn("kb-data-planner", p.read_text())
        self.assertFalse(hasattr(D, "cmd_bundle"))
        self.assertFalse(hasattr(D, "cmd_check"))

    def test_maker(self):
        text, fm = self.front("kb-data-maker.md")
        self.assertEqual((fm["name"], fm["model"]), ("kb-data-maker", "sonnet"))
        tools = {t.strip() for t in fm["tools"].split(",")}
        self.assertEqual(tools, {"Read", "Write", "ToolSearch", "mcp__claude-in-chrome__tabs_context_mcp",
                                 "mcp__claude-in-chrome__navigate", "mcp__claude-in-chrome__find",
                                 "mcp__claude-in-chrome__read_page", "mcp__claude-in-chrome__form_input",
                                 "mcp__claude-in-chrome__computer", "mcp__claude-in-chrome__javascript_tool",
                                 "mcp__claude-in-chrome__gif_creator"})
        self.assertIn("Press Enter", text)
        self.assertIn("KB_DATA_DONE", text)
        flat = " ".join(text.split())
        for s in ("Escape", "data-<ticket>.json", "25 tool uses", "dry_run", "pending", "created", "choose_pick",
                  "one-shot allowance", "never click `choose_pick` a second time"):
            self.assertIn(s, flat)
        self.assertNotIn("kbopt", text)
        self.assertNotIn("__OPTION__", text)


if __name__ == "__main__":
    unittest.main()
