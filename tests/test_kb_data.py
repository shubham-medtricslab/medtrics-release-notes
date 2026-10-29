"""Tests for kb_data.py (0.27.0): the demo-data bundle for the planner, the plan
check (cancel-type choose rule, far-future dates, forbidden words, personal data,
save text, pick fields), the preview line, approval with a content hash, the
data-maker brief (Vue-commit field scripts, kbopt), and the shipped agent files.
Hermetic: no browser, no network.
"""

import contextlib
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

import kb_capture as C
import kb_data as D

ROOT = Path(__file__).resolve().parent.parent
CHECKLIST = """Manual QA Checklist
Branch: m1-465-sessions-a-session-schedule-change-email-notifications

Branch under test
- Main UI path: open a session, go to the `Scheduling` section, then use `Add schedule` or the row `Edit` action.

Direct Actions
- Open one existing curriculum session in the SPA and use the `Scheduling` section only.
- Run one create without notifications and one create with notifications.

Required Test Data
- One program admin account that can edit curriculum sessions.
- One course/session that already belongs to a real course rotation and has at least one group.
- One venue that is easy to recognize so time/location changes are obvious.

Blocked / Assumed Conditions
- A worker path for queued mail must be running.

Numbered test steps
2. Verify the create prompt path without sending notifications
   Action: Click `Add schedule`, fill a valid date, then click `Save` and choose the non-notify path.
   Expected: The schedule is created.
"""
DRAFT = {"tickets": {"M1-465": {"records": [{
    "id": "r1", "what": "session schedule", "persona": "admin",
    "start": "/vadministration/curriculum/sessions/",
    "path": [{"row": True}, {"text": "Add schedule"}],
    "fields": [{"label": "Date", "value": "<YYYY-MM-DD far future>"},
               {"label": "Start time", "value": "09:00"},
               {"label": "Audience", "pick": "anything the planner wrote"}],
    "save": "Save",
    "after": {"expect": "Notify Participants?", "choose": "No, don't send notifications",
              "labels": ["Notify Participants?", "No, don't send notifications"]}}]}}}


def rec(**kw):
    r = json.loads(json.dumps(DRAFT["tickets"]["M1-465"]["records"][0]))
    r.update(kw)
    return r


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


class Bundle(Base):
    def files(self, checklist=CHECKLIST, write_gated="Add schedule > Save", skip=False):
        issue = {"identifier": "M1-465", "title": "Session schedule change emails"}
        if checklist:
            issue["qa_checklist"] = {"text": checklist, "source": "mr_attachment"}
        entry = {"persona": "admin", "surface": "Curriculum > Sessions", "tickets": ["M1-465"],
                 "url": "/vadministration/curriculum/sessions/", "clicks": [{"row": True}],
                 "write_gated": write_gated}
        if skip:
            entry["skip_reason"] = "page_not_found"
        return (self.put("groups.json", {"run_id": "r1", "capture_plan": [entry]}),
                self.put("data.json", {"issues": [issue]}))

    def test_bundle_has_the_checklist_parts(self):
        g, d = self.files()
        out = self.d / "data_planner.md"
        rc, text, res = run(["bundle", "--groups", g, "--data", d, "--out", str(out)])
        self.assertEqual((rc, res["tickets"]), (0, 1))
        md = out.read_text()
        for need in ("=== TICKET M1-465 ===", "Required Test Data:", "One venue that is easy",
                     "Direct Actions:", "UI path", "Add schedule", "start=/vadministration/curriculum/sessions/",
                     "clicks=[row]", "write-gated step", str(self.d / "data_plan_draft.json")):
            self.assertIn(need, md)
        self.assertNotIn("Blocked / Assumed", md)

    def test_ticket_with_nothing_needed_makes_no_file(self):
        g, d = self.files(checklist="Manual QA\n\nDirect Actions\n- look\n", write_gated="")
        out = self.d / "data_planner.md"
        rc, text, res = run(["bundle", "--groups", g, "--data", d, "--out", str(out)])
        self.assertEqual((rc, res["tickets"]), (0, 0))
        self.assertFalse(out.exists())

    def test_write_gated_without_checklist_and_skipped_entries(self):
        g, d = self.files(checklist="")
        self.assertEqual(run(["bundle", "--groups", g, "--data", d, "--out", str(self.d / "p.md")])[2]["tickets"], 1)
        g, d = self.files(skip=True)
        self.assertEqual(run(["bundle", "--groups", g, "--data", d, "--out", str(self.d / "q.md")])[2]["tickets"], 0)


class Check(Base):
    def check(self, draft, today="2026-10-29", extra=()):
        rc, text, res = run(["check", "--draft", self.put("draft.json", draft), "--out", str(self.d / "plan.json"),
                             "--today", today, *extra])
        return rc, text, res, json.loads((self.d / "plan.json").read_text())

    def test_valid_draft_resolves_date_and_preview_line(self):
        rc, text, res, plan = self.check(DRAFT)            # no period known: today + 180 days (a Tuesday)
        self.assertEqual((rc, res["records"], res["rejected"], plan["approved"]), (0, 1, [], False))
        self.assertTrue(res["always_ask"])
        r = plan["tickets"]["M1-465"]["records"][0]
        self.assertEqual(r["fields"][0]["value"], "2027-04-27")
        self.assertNotIn("name", r)                            # a name is optional now
        self.assertEqual(r["fields"][2], {"label": "Audience", "pick": D.PICK_RULE})
        line = [ln for ln in text.splitlines() if ln.startswith("- M1-465")][0]
        self.assertEqual(line,
            "- M1-465 · r1 · admin · form fill (nothing is saved — the save request is blocked): "
            "/vadministration/curriculum/sessions/ → first row → \"Add schedule\" · fields: Date=2027-04-27; "
            "Start time=09:00; Audience=first option starting 'ZZ DEMO' · presses \"Save\" — the save request "
            "is blocked; then clicks \"No, don't send notifications\" (prompt \"Notify Participants?\") · "
            "nothing is stored on the demo site.")

    def test_date_rule_period_end_and_floor(self):
        _, _, _, plan = self.check(DRAFT, extra=("--period-end", "2026-12-15"))   # last weekday of Dec 2026
        self.assertEqual(plan["tickets"]["M1-465"]["records"][0]["fields"][0]["value"], "2026-12-31")
        _, _, _, plan = self.check(DRAFT, extra=("--period-end", "2026-11-10"))   # Nov 30 is past the 14-day floor
        self.assertEqual(plan["tickets"]["M1-465"]["records"][0]["fields"][0]["value"], "2026-11-30")
        _, _, _, plan = self.check(DRAFT, today="2026-10-29", extra=("--period-end", "2026-10-31"))
        self.assertEqual(plan["tickets"]["M1-465"]["records"][0]["fields"][0]["value"], "2027-04-27")
        _, _, _, plan = self.check(DRAFT, today="2026-10-30")      # Friday + 180 days -> a weekday
        v = plan["tickets"]["M1-465"]["records"][0]["fields"][0]["value"]
        self.assertLess(__import__("datetime").date.fromisoformat(v).weekday(), 5)
        # the old token still resolves to the far date, never tomorrow
        old = {"tickets": {"M1-465": {"records": [rec(fields=[{"label": "Date", "value": "<YYYY-MM-DD next weekday>"}])]}}}
        self.assertEqual(self.check(old)[3]["tickets"]["M1-465"]["records"][0]["fields"][0]["value"], "2027-04-27")

    def test_a_typed_near_date_is_rejected(self):
        self.assertIn("too soon", self.rejected(fields=[{"label": "Date", "value": "2026-10-30"}]))
        self.assertIn("not a valid", self.rejected(fields=[{"label": "Date", "value": "2026-13-45"}]))
        _, _, res, _ = self.check({"tickets": {"M1-465": {"records": [rec(fields=[{"label": "Date", "value": "2027-01-15"}])]}}})
        self.assertEqual(res["records"], 1)

    def rejected(self, **kw):
        d = {"tickets": {"M1-465": {"records": [rec(**kw)]}}}
        _, _, res, plan = self.check(d)
        self.assertEqual(plan["tickets"], {}, kw)
        return res["rejected"][0]["why"]

    def test_name_is_optional_but_name_fields_carry_the_prefix(self):
        _, _, res, plan = self.check({"tickets": {"M1-465": {"records": [rec(name="ZZ KB DEMO - M1-465 a")]}}})
        self.assertEqual(plan["tickets"]["M1-465"]["records"][0]["name"], "ZZ KB DEMO – M1-465 a")
        self.assertIn("must start", self.rejected(name="Demo schedule"))
        self.assertIn("must start", self.rejected(name="ZZ KB DEMO – M1-999"))
        self.assertIn("Name", self.rejected(fields=[{"label": "Name", "value": "Clinic"}]))
        _, _, res, plan = self.check({"tickets": {"M1-465": {"records": [
            rec(fields=[{"label": "Name", "value": "ZZ KB DEMO - M1-465"}])]}}})
        self.assertEqual(plan["tickets"]["M1-465"]["records"][0]["fields"][0]["value"], "ZZ KB DEMO – M1-465")

    def test_choose_rule(self):
        for choose in ("Cancel", "cancel", "Close", "No", "no", "No, don't send notifications", "Don't send",
                       "Do not send", "Skip", "Skip notifications", "Don’t notify", "No, do not notify"):
            _, _, res, _ = self.check({"tickets": {"M1-465": {"records": [
                rec(after={"expect": "Notify Participants?", "choose": choose})]}}})
            self.assertEqual(res["records"], 1, choose)
        for choose in ("Yes", "OK", "Continue", "Confirm", "Save anyway", "Notify participants", "Not now",
                       "Yes, send notifications", "Send", "Delete it", "Publish", "No, delete it",
                       "No notify", "No, send notifications", "No, save and continue", "No thanks", "Noted"):
            self.assertIn("cancel-type", self.rejected(after={"expect": "x", "choose": choose}), choose)
        for choose in ("Cancel and invite", "Close and send everyone email", "Skip approve", "Cancel and confirm",
                       "Close and save", "Skip and continue", "Cancel, OK", "Close and proceed", "Cancel and send",
                       "Cancel, then notify", "Do not save", "Don't send or notify"):
            self.assertIn("contains", self.rejected(after={"expect": "x", "choose": choose}), choose)

    def test_choose_rule_agrees_with_the_page_side_write_mode(self):
        """Whatever kb_data accepts, the page's write mode (kbclick write list / kbopt) allows;
        a send / notify / invite / delete answer the plan rejects is refused page-side too."""
        for choose in ("Cancel", "Close", "No", "No, don't send notifications", "Don't send", "Skip", "Do not notify"):
            self.assertEqual(D.choose_problem(choose), "", choose)
            self.assertFalse(C.write_danger(choose), choose)
            self.assertEqual(C.option_refusal(choose), "", choose)
        for choose in ("No, send notifications", "No notify", "Yes, send notifications", "Send", "No, invite them",
                       "No, delete it", "Skip and send", "Cancel and invite"):
            self.assertNotEqual(D.choose_problem(choose), "", choose)
            self.assertTrue(C.write_danger(choose), choose)
            self.assertEqual(C.option_refusal(choose), "write_danger", choose)
        for choose in ("No, save and continue", "Cancel and confirm", "Close and save", "Yes", "OK"):
            self.assertNotEqual(D.choose_problem(choose), "", choose)       # plan time catches what the page cannot

    def test_start_must_be_a_clean_demo_path(self):
        for start in ("/administration/users/delete/", "/x/remove/3/", "/?portal_change=personal", "/users/logout/",
                      "/sessions/5/copy/", "/sessions/5/resend/", "/sessions/5/unpublish/", "/x/send/",
                      "/x/approve/", "/x/toggle/"):
            self.assertIn("start", self.rejected(start=start), start)
        for start in ("/vadministration/curriculum/sessions/", "/administration/assignments/", "/x/designation/"):
            _, _, res, _ = self.check({"tickets": {"M1-465": {"records": [rec(start=start)]}}})
            self.assertEqual(res["records"], 1, start)

    def test_check_writes_the_checked_hash(self):
        _, _, _, plan = self.check(DRAFT)
        self.assertEqual(plan["checked_sha256"], D.plan_hash(plan))
        self.assertEqual(len(plan["checked_sha256"]), 64)

    def test_forbidden_save_and_path(self):
        self.assertIn("Save, Create", self.rejected(save="Send"))
        self.assertIn("Save, Create", self.rejected(save="Delete"))
        self.assertIn("Save, Create", self.rejected(save="Update"))
        for ok in ("Save", "create", "ADD", "Submit"):
            _, _, res, _ = self.check({"tickets": {"M1-465": {"records": [rec(save=ok)]}}})
            self.assertEqual(res["records"], 1, ok)
        self.assertIn("path click", self.rejected(path=[{"text": "Delete session"}]))
        self.assertIn("path click", self.rejected(path=[{"text": "Send now"}]))

    def test_no_personal_data_and_no_foreign_urls(self):
        self.assertIn("email", self.rejected(fields=[{"label": "Contact", "value": "a@b.com"}]))
        self.assertIn("phone", self.rejected(fields=[{"label": "Contact", "value": "555-123-4567"}]))
        self.assertIn("placeholder", self.rejected(fields=[{"label": "Date", "value": "<tomorrow>"}]))
        self.assertIn("path on the demo", self.rejected(start="https://evil.example/x"))
        self.assertIn("path on the demo", self.rejected(start="//evil.example/x"))
        self.assertIn("persona", self.rejected(persona="learner"))

    def test_limits_and_bad_input(self):
        many = {"tickets": {"M1-465": {"records": [rec(id=f"r{i}") for i in range(5)]}}}
        _, _, res, _ = self.check(many)
        self.assertEqual((res["records"], len(res["rejected"])), (3, 2))
        _, _, res, _ = self.check({"tickets": {"bad id": {"records": [rec()]}}})
        self.assertEqual(res["records"], 0)
        rc, _, res = run(["check", "--draft", str(self.d / "missing.json"), "--out", str(self.d / "x.json")])
        self.assertEqual((rc, res["ok"]), (1, False))


class Brief(Base):
    def calls(self, with_write=True, entry_url="/vadministration/curriculum/sessions/"):
        args = {"v": 1, "login": "/accounts/login/", "portal": "Administration portal", "labels": ["Sessions"],
                "side": "#sidebar", "allow": [], "domain": "medtrics.sg"}
        c = {"run_id": "r1", "base_url": "https://demo.example", "install": C.install_action(),
             "entries": {"v01": {"url": entry_url, "persona": "admin"}},
             "visits": [{"visit_id": "v01-s0", "entry": "v01", "persona": "admin", "args": args, "labels": {}}]}
        if with_write:
            c["write_visits"] = [{"visit_id": "v01-w1", "entry": "v01", "kind": "write", "ticket": "M1-465",
                                  "record_id": "r1",
                                  "persona": "admin", "labels": ["Notify Participants?", "No, don't send notifications"],
                                  "gif": "kb-capture/r1/v01-w1.gif"}]
        return self.put("calls.json", c)

    def plan(self):
        run(["check", "--draft", self.put("draft.json", DRAFT), "--out", str(self.d / "plan.json"),
             "--today", "2026-10-29"])
        return str(self.d / "plan.json")

    def test_brief_refuses_an_unapproved_plan(self):
        rc, _, res = run(["brief", "--plan", self.plan(), "--calls", self.calls(), "--out", str(self.d / "data.md")])
        self.assertEqual((rc, res["error"]), (2, "plan_not_approved"))
        self.assertFalse((self.d / "data.md").exists())

    def test_approval_hash_and_changed_plan(self):
        plan = self.plan()
        rc, _, res = run(["approve", "--plan", plan])
        stored = json.loads(Path(plan).read_text())
        self.assertEqual(stored["approved_sha256"], res["sha256"])
        self.assertEqual(len(res["sha256"]), 64)
        self.assertEqual(D.plan_hash(stored), res["sha256"])
        # a hand-set approved flag without a hash is not an approval
        stored2 = dict(stored); stored2.pop("approved_sha256")
        p2 = self.put("p2.json", stored2)
        self.assertEqual(run(["brief", "--plan", p2, "--calls", self.calls(), "--out", str(self.d / "a.md")])[2]["error"],
                         "plan_not_approved")
        # the plan edited after approval is refused (a save text, a choose, a field value)
        for mutate in (lambda t: t[0]["records"][0].update(save="Create"),
                       lambda t: t[0]["records"][0]["after"].update(choose="Cancel"),
                       lambda t: t[0]["records"][0]["fields"][1].update(value="23:00")):
            edited = json.loads(Path(plan).read_text())
            mutate(list(edited["tickets"].values()))
            p3 = self.put("p3.json", edited)
            rc, _, res = run(["brief", "--plan", p3, "--calls", self.calls(), "--out", str(self.d / "b.md")])
            self.assertEqual((rc, res["error"]), (2, "plan_changed"))
        # re-checking alters the rejected list / flags only: the hash ignores them
        edited = json.loads(Path(plan).read_text())
        edited["rejected"] = [{"ticket": "X-1", "id": "r", "why": "x"}]
        rc, _, res = run(["brief", "--plan", self.put("p4.json", edited), "--calls", self.calls(),
                          "--out", str(self.d / "c.md")])
        self.assertEqual(rc, 0)

    def test_approve_refuses_a_plan_check_did_not_produce(self):
        plan = self.plan()
        stored = json.loads(Path(plan).read_text())
        self.assertEqual(run(["approve", "--plan", plan])[0], 0)           # a checked plan is approved
        no_hash = {k: v for k, v in stored.items() if k != "checked_sha256"}
        rc, _, res = run(["approve", "--plan", self.put("nohash.json", no_hash)])
        self.assertEqual((rc, res["ok"], res["error"]), (2, False, "plan_not_checked"))
        forged = {**stored, "approved": True}
        forged["tickets"]["M1-465"]["records"][0]["save"] = "Create"        # hand-edited after check
        rc, _, res = run(["approve", "--plan", self.put("edited.json", forged)])
        self.assertEqual((rc, res["error"]), (2, "plan_changed"))
        self.assertNotIn("approved_sha256", json.loads((self.d / "edited.json").read_text()))
        rc, _, res = run(["approve", "--plan", self.put("wrong.json", {**stored, "checked_sha256": "0" * 64})])
        self.assertEqual((rc, res["error"]), (2, "plan_changed"))

    def test_brief_refuses_a_start_that_differs_from_its_capture_entry(self):
        plan = self.plan()
        run(["approve", "--plan", plan])
        for url in ("/vadministration/curriculum/courses/", "/administration/curriculum/sessions/", ""):
            rc, _, res = run(["brief", "--plan", plan, "--calls", self.calls(entry_url=url),
                              "--out", str(self.d / "m.md")])
            self.assertEqual((rc, res["error"], res["missing"]), (1, "no_records", ["M1-465:r1"]), url)
            self.assertEqual(res["start_mismatch"][0]["reason"], "start_mismatch")
        rc, _, res = run(["brief", "--plan", plan, "--calls", self.calls(entry_url="/vadministration/curriculum/sessions/#x"),
                          "--out", str(self.d / "ok.md")])          # a fragment is not part of the start
        self.assertEqual((rc, res["start_mismatch"]), (0, []))

    def test_brief_starts_on_the_admin_portal_and_guards_every_page(self):
        plan = self.plan()
        run(["approve", "--plan", plan])
        out = self.d / "data.md"
        run(["brief", "--plan", plan, "--calls", self.calls(), "--out", str(out)])
        md = out.read_text()
        entry = json.loads((Path(C.__file__).parent / "kb_routes.json").read_text())["demo"]["portal_entry"]["admin"]
        start = json.loads(re.search(r"## Start.*?```json\n(.*?)\n```", md, re.S).group(1))
        self.assertEqual(start["navigate"], entry)
        self.assertTrue(start["probe_js"].startswith("/*KB*/(sessionStorage.getItem('kbp')?"))
        self.assertIn('"labels": []', start["probe_js"])
        self.assertLess(md.index("## Start"), md.index("### M1-465 / r1"))
        block = json.loads(re.search(r"```json\n(\{\n \"ticket\".*?)\n```", md, re.S).group(1))
        self.assertTrue(block["guard_js"].startswith("/*KB*/(sessionStorage.getItem('kbp')?"))
        self.assertNotIn("portal_entry", block)                          # an admin record needs no switch
        for need in ("then\n   run `guard_js` (the probe", "run `guard_js` again", "before any field", "/*KB*/"):
            self.assertIn(need, md)
        # an explicit routes file is honoured, and a faculty record switches to its own portal
        routes = json.loads((Path(C.__file__).parent / "kb_routes.json").read_text())
        routes["demo"]["portal_entry"]["admin"] = "/?portal_change=other"
        rc, _, _ = run(["brief", "--plan", plan, "--calls", self.calls(), "--routes", self.put("r.json", routes),
                        "--out", str(out)])
        self.assertIn('"navigate": "/?portal_change=other"', out.read_text())

    def test_every_generated_script_is_marked_and_guarded(self):
        plan = self.plan()
        run(["approve", "--plan", plan])
        out = self.d / "data.md"
        run(["brief", "--plan", plan, "--calls", self.calls(), "--out", str(out)])
        block = json.loads(re.search(r"```json\n(\{\n \"ticket\".*?)\n```", out.read_text(), re.S).group(1))
        scripts = [block["guard_js"], block["pre_save_probe_js"], block["probe_js"], block["save_pick"]["js"],
                   block["choose_pick"]["js"], *[p["js"] for p in block["path_picks"]]]
        for f in block["fields"]:
            scripts += [f[k] for k in ("fill_js", "read_js", "open_js", "option_js") if k in f]
        self.assertGreaterEqual(len(scripts), 9)
        for js in scripts:
            self.assertTrue(js.startswith(C.JS_MARKER), js[:60])
        g = C.guard_source()
        for js in (D.field_js("A", "b"), D.field_js("A", mode="read")):     # the field scripts embed the guard, first
            self.assertIn(g, js)
            body = js[js.index("(function(a){") + len("(function(a){"):].lstrip()
            self.assertTrue(body.startswith(g), "the guard must run before anything else")
        self.assertNotIn("__GUARD__", D.FIELD_JS)

    @unittest.skipUnless(shutil.which("node"), "node not available")
    def test_field_script_installs_the_guard_before_it_fills(self):
        js = D.field_js("Start time", "09:00")
        scen = r"""
        const vm=require('vm');
        const log=[];
        const input={type:'text',tagName:'INPUT',value:'',placeholder:'',focus(){},dispatchEvent(){return true;}};
        const lab={textContent:'Start time',control:input,getBoundingClientRect:()=>({width:5,height:5}),};
        input.getBoundingClientRect=lab.getBoundingClientRect;
        const ctx={URL,log,Event:class{constructor(t){this.t=t;}},FocusEvent:class{constructor(t){this.t=t;}},
          HTMLInputElement:{prototype:{}},HTMLTextAreaElement:{prototype:{}},
          getComputedStyle:()=>({visibility:'visible'}),navigator:{}};
        Object.defineProperty(ctx.HTMLInputElement.prototype,'value',{set(v){input.value=v;},get(){return input.value;},configurable:true});
        ctx.window=ctx;ctx.location={href:'https://d.example/a/'};
        ctx.fetch=()=>Promise.resolve('real');
        ctx.document={querySelectorAll:s=>/label/.test(s)?[lab]:[],getElementById:()=>null,addEventListener(){},body:{}};
        vm.createContext(ctx);
        const before=ctx.fetch;
        const out=vm.runInContext(process.argv[1],ctx);
        ctx.fetch('/w',{method:'POST'}).then(()=>console.log('SENT'),e=>console.log(JSON.stringify({out,blocked:ctx.__kbBlocked,msg:e.message,patched:ctx.fetch!==before})));
        """
        r = subprocess.run([shutil.which("node"), "-e", scen, js], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(r.stdout)
        self.assertEqual((got["msg"], got["blocked"], got["patched"]), ("KB_WRITE_BLOCKED", 1, True))
        self.assertIn('"ok":true', got["out"])

    def test_approve_then_brief(self):
        plan = self.plan()
        rc, _, res = run(["approve", "--plan", plan])
        self.assertEqual((rc, res["approved"]), (0, True))
        out = self.d / "data.md"
        rc, text, res = run(["brief", "--plan", plan, "--calls", self.calls(), "--out", str(out)])
        self.assertEqual((rc, res["records"], res["missing"]), (0, 1, []))
        md = out.read_text()
        self.assertIn("### M1-465 / r1: session schedule", md)
        block = json.loads(re.search(r"```json\n(\{\n \"ticket\".*?)\n```", md, re.S).group(1))
        self.assertEqual((block["visit_id"], block["gif"]), ("v01-w1", "kb-capture/r1/v01-w1.gif"))
        self.assertEqual(block["navigate"], "/vadministration/curriculum/sessions/")
        self.assertTrue(block["needs_demo_context"])
        self.assertEqual([p["click"] for p in block["path_picks"]], [{"row": True}, {"text": "Add schedule"}])
        save_js = block["save_pick"]["js"]
        self.assertIn("sessionStorage.getItem('kbc')", save_js)
        self.assertIn('"write": ["Save", "No, don\'t send notifications"]', save_js)
        self.assertIn('"text": "Save"', save_js)
        self.assertIn('"write"', block["choose_pick"]["js"])
        self.assertIn('"text": "No, don\'t send notifications"', block["choose_pick"]["js"])
        self.assertIn('"row": true', block["path_picks"][0]["js"])
        self.assertNotIn('"write"', block["path_picks"][1]["js"])
        self.assertIn("sessionStorage.getItem('kbp')", block["probe_js"])
        self.assertIn("Notify Participants?", block["probe_js"])
        self.assertIn('"labels": ["Save"]', block["pre_save_probe_js"])
        self.assertEqual(block["export"]["name"], "gif_creator")
        self.assertNotIn("name", block)                      # no name requirement any more
        date, time_, pick = block["fields"]
        self.assertEqual((date["kind"], date["value"]), ("date", "2027-04-27"))
        self.assertIn('"mode": "read"', date["read_js"])
        self.assertEqual((time_["kind"], time_["value"]), ("text", "09:00"))
        for need in ("dispatchEvent(new Event('input'", "new Event('change'", "new Event('blur'", "KB_FIELD_NOSTICK",
                     '"value": "09:00"'):
            self.assertIn(need, time_["fill_js"])
        self.assertEqual((pick["kind"], pick["rule"], pick["search"]), ("pick", D.PICK_RULE, "ZZ DEMO"))
        self.assertIn('"text": "Audience"', pick["open_js"])
        self.assertIn('"option": "__OPTION__"', pick["option_js"])
        for rule in ("Never press", "kbclick", "kbopt", "ZZ DEMO", "data-<ticket>.json", "write_error", "Tab",
                     "Escape", "NOTHING IS", "save_did_nothing", "`wb`", "`h`", "25 tool",
                     "Cancel, Close"):
            self.assertIn(rule, md)
        self.assertNotIn("{{", md)
        self.assertNotIn("d<ticket>.json", md.replace("data-<ticket>.json", ""))
        self.assertEqual(json.loads(re.search(r"```json\n(\{\n \"action\".*?)\n```", md, re.S).group(1))["action"],
                         "javascript_exec")

    def test_field_scripts_are_valid_javascript(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not available")
        for js in (D.field_js("Start time", "09:00"), D.field_js("Date", mode="read")):
            r = subprocess.run([node, "-e", f"new Function('return ' + {json.dumps(js)})"],
                               capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_record_without_a_write_visit_is_reported(self):
        plan = self.plan()
        run(["approve", "--plan", plan])
        rc, _, res = run(["brief", "--plan", plan, "--calls", self.calls(with_write=False),
                          "--out", str(self.d / "data.md")])
        self.assertEqual((rc, res["error"], res["missing"]), (1, "no_records", ["M1-465:r1"]))


class Agents(unittest.TestCase):
    def front(self, name):
        text = (ROOT / "agents" / name).read_text()
        fm = dict(re.findall(r"^(\w+): (.*)$", text.split("---")[1], re.M))
        return text, fm

    def test_planner(self):
        text, fm = self.front("kb-data-planner.md")
        self.assertEqual((fm["name"], fm["model"], fm["tools"]), ("kb-data-planner", "sonnet", "Read, Write"))
        for s in ("ZZ KB DEMO – <ticket>", "No, don't send notifications", "data_plan_draft.json",
                  "<YYYY-MM-DD far future>", "NOTHING IS EVER", "first option starting 'ZZ DEMO'", "Cancel",
                  "form fill"):
            self.assertIn(s, text)

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
        for s in ("Escape", "kbopt", "data-<ticket>.json", "25 tool uses", "wb", "blocked"):
            self.assertIn(s, text)


if __name__ == "__main__":
    unittest.main()
