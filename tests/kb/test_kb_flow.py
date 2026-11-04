"""Tests for the 0.28.0 two-step saving flow: a blocked DRY RUN learns the real prompt and the
request (`kb_data.py learn` -> Mem "form flow" notes), the next run SAVES with a one-shot
allowance for exactly that request and logs the demo record (`records` -> Mem "demo record"
notes), a re-run reuses it, and `demo-data` / `demo-data-done` list and retire the records
(Claude never deletes). The page scripts are run in node against a fake DOM so the loop
closes: generated JS -> raw items -> learned flow -> plan -> brief -> generated JS.
Hermetic: no browser, no network.
"""

import json
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kb_fixtures  # noqa: F401  (puts scripts/kb on sys.path)
from test_kb_data import Base as DataBase, ROUTES, SESS, TODAY, demo_note, flow_note, run
from test_kb_write_guard import DIALOG, page_store, run_dom

import kb_capture as C
import kb_data as D

NODE = shutil.which("node")
DLG = {"t": "Notify Participants?", "x": "Notify Participants? Send emails about this schedule?",
       "bt": ["Yes, send notifications", "No, don't send notifications", "Cancel"]}


def probe(wb=0, wl="", wk="", dlg=DLG, **kw):
    return {"v": 1, "u": SESS, "iw": 1470, "ih": 802, "dpr": 2, "labels": [], "wb": wb, "wl": wl, "wk": wk,
            "dlg": dlg, "tn": {"v": "CLEAR", "k": []}, **kw}


def dry_item(ticket="M1-465", rid="r1", request="POST /api/schedules/", kind="xhr", choose="No, don't send notifications",
             on_save=False, dlg=DLG):
    """A dry-run raw item as the maker writes it."""
    after_save = probe(wb=1, wl=request, wk=kind, dlg=dlg) if on_save else probe(dlg=dlg)
    post = probe(wb=1, wl=request, wk=kind, dlg=None) if request else probe(dlg=None)    # on_save: Save fired it, the answer sent nothing more
    return {"visit_id": "v01-w1", "ticket": ticket, "record_id": rid, "mode": "dry_run", "probe": after_save,
            "gif": "kb-capture/r1/v01-w1.gif", "blocked": 1,
            "choose": {"ok": True, "x": 1, "y": 2, "label": choose}, "post_probe": post}


def save_item(ticket="M1-465", rid="r1", fp="fp1", status="201", sid="S-5", created=True, page=SESS):
    it = {"visit_id": "v01-w1", "ticket": ticket, "record_id": rid, "mode": "save", "probe": probe(),
          "gif": "kb-capture/r1/v01-w1.gif", "blocked": 0}
    if created:
        it["created"] = {"fingerprint": fp, "id": sid, "status": status, "page_url": page}
        it["post_probe"] = probe(saved={"m": "POST", "p": "/api/schedules/", "s": int(status) if status.isdigit() else 0,
                                        "id": sid}, al=["POST /api/schedules/"])
    else:
        it["pending"] = fp
    return it


class Flow(DataBase):
    def raw(self, *items, ticket="M1-465"):
        raw = self.d / "raw"
        raw.mkdir(exist_ok=True)
        (raw / f"data-{ticket}.json").write_text(json.dumps({"batch_id": f"data-{ticket}", "status": "ok", "items": list(items)}))
        return str(raw)

    def planned(self, *extra):
        self.plan(None, *extra)
        return str(self.d / "data_plan.json")

    def learn(self, *items, flows=None):
        out = str(self.d / "flow_notes.json")
        argv = ["learn", "--raw", self.raw(*items), "--plan", self.planned(), "--out", out, "--run-id", "kb-r1", "--today", TODAY]
        if flows:
            argv += ["--flows", flows]
        rc, text, res = run(argv)
        return rc, text, res, json.loads(Path(out).read_text())


class Learn(Flow):
    def test_learned_from_a_dry_run(self):
        L = D.learned_from(dry_item())
        self.assertEqual(L, {"dialog_title": "Notify Participants?", "buttons": DLG["bt"],
                             "choose_text": "No, don't send notifications", "request": "POST /api/schedules/",
                             "query": "", "channel": "xhr", "request_on": "choose", "blocked": 1})
        self.assertEqual(D.learned_status(L), ("learned", ""))

    def test_a_request_fired_by_save_itself_is_not_saveable(self):
        L = D.learned_from(dry_item(on_save=True))
        self.assertEqual((L["request_on"], L["request"]), ("save", "POST /api/schedules/"))
        status, why = D.learned_status(L)
        self.assertEqual(status, "unusable")
        self.assertIn("not sent by the prompt's choice", why)

    def test_the_learned_request_must_be_the_one_new_write_after_the_choose_click(self):
        def ev(n, path, q="", src="xhr"):
            return {"n": n, "m": "POST", "p": path, "q": q, "s": src, "t": 1793249736000 + n}
        item = dry_item()
        item["probe"]["wb"] = 1                                                  # one write was blocked before the click
        item["probe"]["bl"] = [ev(1, "/api/old/")]
        item["post_probe"] = probe(wb=2, wl="POST /api/schedules/", wq="?v=2", wk="xhr", dlg=None,
                                   bl=[ev(1, "/api/old/"), ev(2, "/api/schedules/", "?v=2")])
        L = D.learned_from(item)
        self.assertEqual((L["request"], L["query"], L["channel"], L["request_on"]),
                         ("POST /api/schedules/", "?v=2", "xhr", "choose"))
        self.assertEqual(D.learned_status(L), ("learned", ""))
        # a background POST in the gap: two new writes, so nothing can be learned
        item["post_probe"] = probe(wb=3, wl="POST /api/schedules/", wk="xhr", dlg=None,
                                   bl=[ev(1, "/api/old/"), ev(2, "/api/background/"), ev(3, "/api/schedules/")])
        L = D.learned_from(item)
        self.assertEqual(L["request_on"], "ambiguous")
        self.assertEqual(D.learned_status(L), ("unusable", "ambiguous_request"))
        # the counter alone (no log, an older page script) says the same
        item["post_probe"] = probe(wb=3, wl="POST /api/schedules/", wk="xhr", dlg=None)
        self.assertEqual(D.learned_from(item)["request_on"], "ambiguous")

    def test_an_ambiguous_dry_run_writes_an_unusable_note_and_is_never_planned_as_a_save(self):
        item = dry_item()
        item["post_probe"] = probe(wb=2, wl="POST /api/schedules/", wk="xhr", dlg=None,
                                   bl=[{"n": 1, "m": "POST", "p": "/api/bg/", "q": "", "s": "xhr", "t": 1},
                                       {"n": 2, "m": "POST", "p": "/api/schedules/", "q": "", "s": "xhr", "t": 2}])
        rc, text, res, [w] = self.learn(item)
        self.assertEqual((res["notes"], res["usable"]), (1, 0))
        self.assertIn("**Status:** unusable", w["content"])
        self.assertIn("**Reason:** ambiguous_request", w["content"])
        self.assertIn("UNUSABLE (ambiguous_request)", text)
        dump = self.put("flows.json", {"notes": [{"id": "n1", "version": 1, "content": w["content"]}]})
        _, _, _, plan = self.plan(None, "--flows", dump, out="p2.json")
        [r] = self.records_of(plan)
        self.assertEqual(r["mode"], "dry_run")
        self.assertIn("ambiguous_request", r["flow_note"])
        # ... and the flow note keeps the learned query of a clean run
        _, _, _, [w] = self.learn(dry_item(), flows=None)
        self.assertIn("**Request query:** —", w["content"])

    def test_unusable_reasons(self):
        for item, word in ((dry_item(request=""), "no request was seen"), (dry_item(kind="form"), "not fetch/xhr"),
                           (dry_item(kind="beacon"), "beacon"), (dry_item(request="DELETE /api/schedules/1/"), "POST / PUT / PATCH"),
                           (dry_item(request="POST /api/schedules/send/"), "send"), (dry_item(dlg=None), "no prompt"),
                           (dry_item(choose="Yes, send notifications"), "cancel-type"),
                           (dry_item(choose=""), "no safe answer")):
            status, why = D.learned_status(D.learned_from(item))
            self.assertEqual(status, "unusable", item["choose"])
            self.assertIn(word, why)

    def test_notes_are_written_for_the_demo_collection(self):
        rc, text, res, writes = self.learn(dry_item())
        self.assertEqual((rc, res["notes"], res["usable"], res["collection"]), (0, 1, 1, "KB Demo Data"))
        [w] = writes
        self.assertEqual((w["action"], w["collection"], w["note_id"]), ("create", "demo", ""))
        plan = json.loads((self.d / "data_plan.json").read_text())
        r = self.records_of(plan)[0]
        key = D.flow_key("M1-465", SESS, r["path"], "Save")
        self.assertEqual(w["title"], f"KB flow: M1-465 · {key}")
        self.assertEqual(w["content"].splitlines()[0], w["title"])
        for line in ("**Kind:** form flow", "**Ticket:** M1-465", f"**Key:** {key}", f"**Start:** {SESS}",
                     '**Path:** [{"row":true},{"text":"Add schedule"}]', "**Save:** Save",
                     "**Dialog title:** Notify Participants?",
                     '**Buttons:** ["Yes, send notifications","No, don\'t send notifications","Cancel"]',
                     "**Choose:** No, don't send notifications", "**Request:** POST /api/schedules/",
                     "**Request channel:** xhr", "**Request on:** choose", "**Status:** learned", "**Reason:** —",
                     "**Learned:** 2026-10-29", "**Run:** kb-r1"):
            self.assertIn(line, w["content"].splitlines())
        back = D.parse_flow_note({"id": "n1", "version": 2, "content": w["content"]})
        self.assertEqual((back["ticket"], back["key"], back["path"], back["buttons"], back["note_id"], back["version"]),
                         ("M1-465", key, r["path"], DLG["bt"], "n1", 2))
        self.assertIn("learned: the next run can save for real", text)

    def test_an_existing_note_is_updated_not_duplicated(self):
        _, _, _, first = self.learn(dry_item())
        note = {"id": "n-77", "version": 4, "content": first[0]["content"]}
        _, _, res, writes = self.learn(dry_item(request="PUT /api/schedules/"), flows=self.put("flows.json", {"notes": [note]}))
        [w] = writes
        self.assertEqual((w["action"], w["note_id"], w["version"]), ("update", "n-77", 4))
        self.assertIn("**Request:** PUT /api/schedules/", w["content"])

    def test_unusable_flows_are_still_recorded_with_the_reason(self):
        _, text, res, [w] = self.learn(dry_item(on_save=True))
        self.assertEqual(res["usable"], 0)
        self.assertIn("**Status:** unusable", w["content"])
        self.assertIn("**Reason:** the request is not sent by the prompt's choice", w["content"])
        self.assertIn("UNUSABLE", text)

    def test_only_dry_run_items_of_planned_records_are_learned(self):
        rc, _, res, writes = self.learn(save_item(), {"visit_id": "v01-w1", "write_error": "KB_FIELD_NOSTICK"},
                                        dry_item(rid="r9"))
        self.assertEqual((writes, res["skipped"][0]["why"]), ([], "no such record in the plan"))

    def test_the_loop_closes_learn_then_the_next_plan_saves(self):
        _, _, _, writes = self.learn(dry_item())
        dump = self.put("flows.json", {"notes": [{"id": "n1", "version": 1, "content": writes[0]["content"]}]})
        _, text, res, plan = self.plan(None, "--flows", dump, out="plan2.json")
        [r] = self.records_of(plan)
        self.assertEqual((r["mode"], r["flow"]["request"], r["flow"]["choose"], r["flow"]["dialog"]),
                         ("save", "POST /api/schedules/", "No, don't send notifications", "Notify Participants?"))
        self.assertIn("WILL BE SAVED", text)
        self.assertIn("POST /api/schedules/", text)


class Records(Flow):
    def records(self, *items, existing=None, **kw):
        out = str(self.d / "record_notes.json")
        argv = ["records", "--raw", self.raw(*items), "--plan", self.planned(), "--out", out, "--run-id", "kb-r1",
                "--today", TODAY, "--base-url", "https://demo.example/"]
        if existing:
            argv += ["--records", existing]
        rc, text, res = run(argv)
        return rc, text, res, json.loads(Path(out).read_text())

    def fp(self):
        return self.records_of(json.loads((self.d / "data_plan.json").read_text()))[0]["fingerprint"]

    def test_a_created_record_is_logged_in_mem_only(self):
        self.planned()
        fp = self.fp()
        rc, text, res, [w] = self.records(save_item(fp=fp))
        self.assertEqual((rc, res["notes"], res["live"], w["action"], w["collection"]), (0, 1, 1, "create", "demo"))
        self.assertEqual(w["title"], f"KB demo: M1-465 · 2027-04-27 · {fp}")
        for line in ("**Kind:** demo record", "**Ticket:** M1-465", "**What:** Sessions — Add schedule",
                     f"**Fingerprint:** {fp}", "**Link:** https://demo.example/vadministration/curriculum/sessions/",
                     "**Date:** 2027-04-27", "**Start time:** 09:00", "**End time:** 10:00", "**Server id:** S-5",
                     "**Run:** kb-r1", "**Status:** created", "**Created:** 2026-10-29", "**Delete before:** 2027-04-20",
                     "**Deleted:** —"):
            self.assertIn(line, w["content"].splitlines())
        self.assertIn("print these lines in chat", text)
        self.assertIn("S-5", res["lines"][0])
        self.assertEqual(sorted(p.name for p in self.d.iterdir() if p.is_file()),
                         sorted(["calls.json", "data.json", "data_plan.json", "groups.json", "record_notes.json", "shots.json"]))
        back = D.parse_demo_note({"id": "n2", "version": 1, "content": w["content"]})
        self.assertEqual((back["status"], back["server_id"], back["delete_before"]), ("created", "S-5", "2027-04-20"))

    def test_delete_before_is_a_week_before_the_record_date(self):
        self.assertEqual(D.delete_before("2027-04-27"), "2027-04-20")
        self.assertEqual(D.delete_before("2027-03-03"), "2027-02-24")
        self.assertEqual(D.delete_before(""), "")

    def test_a_save_that_died_before_the_verdict_is_unverified_and_still_live(self):
        self.planned()
        rc, text, res, [w] = self.records(save_item(fp=self.fp(), created=False))
        self.assertIn("**Status:** unverified", w["content"])
        self.assertEqual(res["live"], 1)
        self.assertIn("UNVERIFIED", text)

    def test_a_failed_http_status_is_not_a_live_record(self):
        self.planned()
        rc, text, res, [w] = self.records(save_item(fp=self.fp(), status="500", sid=""))
        self.assertIn("**Status:** not_saved", w["content"])
        self.assertEqual(res["live"], 0)
        _, _, res, [w] = self.records(save_item(fp=self.fp(), status="unknown", sid=""))
        self.assertIn("**Status:** unverified", w["content"])

    def test_a_blocked_or_expired_allowance_is_not_saved_and_not_live(self):
        # the maker could not know: the allowance was never armed / expired, so the guard logged no allowed
        # request and no response came back (post_probe.al empty, saved null)
        self.planned()
        it = save_item(fp=self.fp(), status="unknown", sid="")
        it["post_probe"] = probe(wb=3, al=[], saved=None)
        rc, text, res, [w] = self.records(it)
        self.assertIn("**Status:** not_saved", w["content"])
        self.assertEqual(res["live"], 0)
        self.assertIn("NOT_SAVED", text)
        self.assertEqual(D._status_of({"status": "unknown"}, {"wb": 3, "al": [], "saved": None}), "not_saved")
        # not listed for deletion, never reused
        dump = self.put("rec.json", {"notes": [{"id": "n-1", "version": 1, "content": w["content"]}]})
        self.assertEqual(run(["demo-data", "--records", dump])[2]["count"], 0)
        _, _, res, plan = self.plan(None, "--records", dump, out="plan4.json")
        self.assertEqual((res["records"], plan["reused"]), (1, []))
        # unverified only when an allowed request WAS logged but no id / status came back
        it["post_probe"] = probe(wb=3, al=["POST /api/schedules/"], saved=None)
        _, _, res, [w] = self.records(it)
        self.assertIn("**Status:** unverified", w["content"])
        self.assertEqual(res["live"], 1)
        self.assertEqual(D._status_of({}, {"al": ["POST /x/"], "saved": {"s": 0, "id": ""}}), "unverified")
        self.assertEqual(D._status_of({"status": "201"}, {"al": ["POST /x/"], "saved": {"s": 201, "id": "7"}}), "created")
        self.assertEqual(D._status_of({"status": "201"}, {"al": [], "saved": None}), "not_saved")   # the maker's word alone is not evidence
        self.assertEqual(D._status_of({"status": "500"}, {"al": ["POST /x/"], "saved": {"s": 500}}), "not_saved")
        self.assertEqual(D._status_of({"status": "201"}), "created")             # no probe at all: the maker's status
        self.assertEqual(D._status_of({}), "unverified")

    def test_dry_runs_and_errors_make_no_record_note(self):
        _, _, res, writes = self.records(dry_item(), {"visit_id": "v01-w1", "write_error": "x"})
        self.assertEqual((res["notes"], writes), (0, []))

    def test_same_fingerprint_updates_instead_of_a_second_note(self):
        self.planned()
        fp = self.fp()
        _, _, _, [first] = self.records(save_item(fp=fp))
        dump = self.put("rec.json", {"notes": [{"id": "n-1", "version": 2, "content": first["content"]}]})
        _, _, _, [w] = self.records(save_item(fp=fp, sid="S-6"), existing=dump)
        self.assertEqual((w["action"], w["note_id"], w["version"]), ("update", "n-1", 2))

    def test_then_the_next_plan_reuses_the_record(self):
        self.planned()
        fp = self.fp()
        _, _, _, [w] = self.records(save_item(fp=fp))
        dump = self.put("rec.json", {"notes": [{"id": "n-1", "version": 1, "content": w["content"]}]})
        _, text, res, plan = self.plan(None, "--records", dump, out="plan3.json")
        self.assertEqual((res["records"], plan["reused"][0]["server_id"]), (0, "S-5"))
        self.assertEqual(plan["reused"][0]["link"], "https://demo.example/vadministration/curriculum/sessions/")


class DemoData(DataBase):
    def dump(self, *notes):
        return self.put("records.json", {"notes": list(notes)})

    def test_the_live_records_table(self):
        dump = self.dump(demo_note(fingerprint="a1", server_id="S-1", delete_before="2027-05-01"),
                         demo_note(fingerprint="b2", server_id="S-2", delete_before="2027-04-20"),
                         demo_note(fingerprint="c3", status="deleted"), demo_note(fingerprint="d4", status="not_saved"),
                         demo_note(fingerprint="e5", status="unverified", server_id="", delete_before="2027-06-01"),
                         {"id": "x", "content": "KB: M1-1 · some ledger note\n\n**Ticket:** M1-1"},
                         flow_note())
        rc, text, res = run(["demo-data", "--records", dump])
        self.assertEqual((rc, res["count"], res["collection"]), (0, 3, "KB Demo Data"))
        self.assertEqual([r["fingerprint"] for r in res["records"]], ["b2", "a1", "e5"])        # earliest delete-before first
        self.assertIn("| Ticket | What | Link | Date | Server id | Delete before | Status |", text)
        self.assertIn("| M1-465 | Sessions — Add schedule | https://demo.example/vadministration/curriculum/sessions/ | "
                      "2027-04-27 | S-2 | 2027-04-20 | created |", text)
        self.assertIn("Claude never deletes demo data", text)
        rc, text, res = run(["demo-data", "--records", str(self.d / "none.json")])
        self.assertEqual((res["count"], "no live demo records" in text), (0, True))

    def test_done_marks_records_deleted_by_any_id(self):
        dump = self.dump(demo_note(fingerprint="a1", server_id="S-1"), demo_note(fingerprint="b2", server_id="S-2"),
                         demo_note(fingerprint="c3", server_id="S-3"), demo_note(fingerprint="d4", server_id="S-4"))
        out = str(self.d / "updates.json")
        rc, text, res = run(["demo-data-done", "--records", dump, "--ids", "S-1,n-b2", "M1-465:S-3", "d4",
                             "--out", out, "--today", "2027-04-21"])
        self.assertEqual((rc, res["updated"], res["unknown"]), (0, 4, []))
        writes = json.loads(Path(out).read_text())
        self.assertEqual({w["note_id"] for w in writes}, {"n-a1", "n-b2", "n-c3", "n-d4"})
        for w in writes:
            self.assertEqual((w["action"], w["collection"], w["version"]), ("update", "demo", 3))
            self.assertIn("**Status:** deleted", w["content"])
            self.assertIn("**Deleted:** 2027-04-21", w["content"])
            self.assertIn("**Server id:**", w["content"])                              # the rest of the note is kept
        # a note marked deleted is no longer live, so a second `done` finds nothing
        updated = [{"id": w["note_id"], "version": 4, "content": w["content"]} for w in writes]
        rc, text, res = run(["demo-data-done", "--records", self.dump(*updated), "--ids", "S-1", "--out", out])
        self.assertEqual((rc, res["updated"], res["unknown"]), (1, 0, ["S-1"]))
        self.assertEqual(run(["demo-data", "--records", self.dump(*updated)])[2]["count"], 0)

    def test_unknown_ids_are_reported_and_nothing_else_is_touched(self):
        dump = self.dump(demo_note(fingerprint="a1", server_id="S-1"))
        rc, text, res = run(["demo-data-done", "--records", dump, "--ids", "S-1", "NOPE-1", "--out", str(self.d / "u.json")])
        self.assertEqual((rc, res["updated"], res["unknown"]), (1, 1, ["NOPE-1"]))
        self.assertIn("no live record matches: NOPE-1", text)


@unittest.skipUnless(NODE, "node not installed")
class LoopThroughThePageScripts(Flow):
    """The generated JS, run against a fake DOM, produces exactly what kb_data reads."""

    def block(self, flows=None):
        self.plan(None, *(("--flows", flows) if flows else ()))
        run(["approve", "--plan", str(self.d / "data_plan.json")])
        approved = json.loads((self.d / "data_plan.json").read_text())
        calls = C.build_calls(self.groups, self.inputs, ROUTES, data_plan=approved)
        run(["brief", "--plan", str(self.d / "data_plan.json"), "--calls", self.put("calls2.json", calls),
             "--out", str(self.d / "data.md")])
        import re
        md = (self.d / "data.md").read_text()
        return json.loads(re.search(r"```json\n(\{\n \"ticket\".*?)\n```", md, re.S).group(1))

    def test_dry_run_scripts_learn_the_prompt_and_the_blocked_request(self):
        b = self.block()
        data = {"probe": b["probe_js"], "choose": b["choose_pick"]["js"], "post": b["post_probe_js"]}
        r = run_dom("""
          const after=(0,eval)(DATA.probe);                              // the probe after Save: the prompt is open
          const pick=(0,eval)(DATA.choose);                              // the page picks the safe answer
          let sent;try{await fetch('/api/schedules/',{method:'POST',body:'{}'});sent='sent';}catch(e){sent=e.message;}
          const post=(0,eval)(DATA.post);
          return {after,pick,post,sent};""", DIALOG, data, page_store())
        out = r["out"]
        self.assertEqual((out["pick"]["ok"], out["pick"]["label"]), (True, "No, don't send notifications"))
        self.assertEqual(out["sent"], "KB_WRITE_BLOCKED")
        self.assertEqual(r["log"], [])                                         # nothing reached the server
        item = {"visit_id": "v01-w1", "ticket": "M1-465", "record_id": "r1", "mode": "dry_run",
                "probe": out["after"], "choose": out["pick"], "post_probe": out["post"]}
        L = D.learned_from(item)
        self.assertEqual((L["dialog_title"], L["choose_text"], L["request"], L["channel"], L["request_on"]),
                         ("", "No, don't send notifications", "POST /api/schedules/", "fetch", "choose"))
        self.assertEqual(L["buttons"], ["Yes, send notifications", "No, don't send notifications", "Cancel"])

    def test_save_scripts_let_exactly_the_learned_request_through(self):
        # the fake page has no .modal-title, so the title comes from the stored flow; teach it that
        flows = self.put("flows.json", {"notes": [flow_note()]})
        b = self.block(flows)
        self.assertEqual(b["mode"], "save")
        data = {"probe": b["probe_js"], "choose": b["choose_pick"]["js"], "post": b["post_probe_js"],
                "save": b["save_pick"]["js"]}
        scen = """
          const after=(0,eval)(DATA.probe);
          const pick=(0,eval)(DATA.choose);
          fire(window.cur);                                              // the owner's click on the answer
          const one=await fetch('/api/schedules/',{method:'POST',body:'{}'});
          let two;try{await fetch('/api/schedules/',{method:'POST',body:'{}'});two='sent';}catch(e){two='blocked';}
          let other;try{await fetch('/api/other/',{method:'POST'});other='sent';}catch(e){other='blocked';}
          await new Promise(res=>setTimeout(res,5));
          const post=(0,eval)(DATA.post);
          return {pick,one:one.status,two,other,post};"""
        r = run_dom(scen, DIALOG, data, page_store())
        out = r["out"]
        self.assertEqual((out["pick"]["ok"], out["pick"]["armed"]), (True, "POST /api/schedules/"))
        self.assertEqual((out["one"], out["two"], out["other"]), (201, "blocked", "blocked"))
        self.assertEqual(r["log"], ["fetch POST /api/schedules/"])
        self.assertEqual(out["post"]["saved"], {"m": "POST", "p": "/api/schedules/", "s": 201, "id": "S-77"})
        self.assertEqual(out["post"]["al"], ["POST /api/schedules/"])
        self.assertEqual(out["post"]["wb"], 2)
        # and the maker's created block reads straight off that probe
        item = {"visit_id": "v01-w1", "ticket": "M1-465", "record_id": "r1", "mode": "save",
                "created": {"fingerprint": b["fingerprint"], "id": out["post"]["saved"]["id"],
                            "status": str(out["post"]["saved"]["s"]), "page_url": out["post"]["u"]}}
        self.assertEqual(D._status_of(item["created"]), "created")

    def test_the_save_button_script_arms_nothing(self):
        flows = self.put("flows.json", {"notes": [flow_note()]})
        b = self.block(flows)
        r = run_dom("""
          const pick=(0,eval)(DATA.save);
          let x;try{await fetch('/api/schedules/',{method:'POST'});x='sent';}catch(e){x='blocked';}
          return [pick.err||pick.armed||null,x,window.__kbA];""", {"outside": ["Save"]}, {"save": b["save_pick"]["js"]},
                    page_store())
        self.assertEqual(r["out"][1:], ["blocked", None])


if __name__ == "__main__":
    unittest.main()
