"""Tests for the 0.26.0 click capture: the kbclick refusal rules (Python mirror
click_refusal() and the JS built from the same constants, run in node against a
fake DOM), click visits in plan-calls / capture.md, ingest of click results
(refused click, skipped rest of the entry, needs_write screens, shots.json
`missing`), and the kb_guard PreToolUse hook. Hermetic: no browser, no network.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kb_fixtures import has_module
from test_kb_chrome_capture import Base, run_cli, lab, probe_for
from test_kb_gif_capture import make_gif

import kb_capture as C
import kb_format as F
import kb_spec as SP

HOOK = Path(C.__file__).resolve().parents[2] / "hooks" / "kb_guard.py"
HOOKS_JSON = HOOK.parent / "hooks.json"
NODE = shutil.which("node")


def el(tag="a", text="View", visible=True, in_sidebar=False, in_post_form=False,
       occluded=False, **attrs):
    if tag == "a":
        attrs.setdefault("href", "/administration/curriculum/sessions/12/")
    return {"tag": tag, "text": text, "visible": visible, "in_sidebar": in_sidebar,
            "in_post_form": in_post_form, "occluded": occluded, "attrs": attrs}


CASES = [
    ("plain link", el(), ""),
    ("modal button", el("button", "Add Schedule", **{"data-bs-toggle": "modal"}), ""),
    ("legacy modal", el("button", "Add Schedule", **{"data-toggle": "modal"}), ""),
    ("tab link", el("a", "Details", href="#details", **{"data-toggle": "tab"}), ""),
    ("get link in post form", el(in_post_form=True), ""),
    ("submit", el("button", "Go", type="submit", **{"data-toggle": "modal"}), "submit"),
    ("submit input", el("input", "Go", type="submit"), "submit"),
    ("button w/o toggle", el("button", "Add Schedule"), "button_no_toggle"),
    ("post form button", el("button", "Add", in_post_form=True, **{"data-toggle": "modal"}), "post_form"),
    ("post form anchor #", el("a", "Add", href="#", in_post_form=True), "post_form"),
    ("data-href", el(**{"data-href": "/x/"}), "data_href"),
    ("onclick", el(onclick="go()"), "onclick"),
    ("btn-danger", el(**{"class": "btn btn-danger"}), "danger_class"),
    ("text-danger", el(**{"class": "text-danger"}), "danger_class"),
    ("delete class", el(**{"class": "js-delete-row"}), "danger_class"),
    ("delete href", el(href="/x/delete/3/"), "danger_attr"),
    ("remove href", el(href="/x/remove/3/"), "danger_attr"),
    ("toggle href", el(href="/x/toggle/3/"), "danger_attr"),
    ("archive href", el(href="/x/archive/3/"), "danger_attr"),
    ("portal_change", el(href="/?portal_change=personal"), "danger_attr"),
    ("logout", el(href="/users/logout/"), "danger_attr"),
    ("data target delete", el("button", "Open", **{"data-bs-toggle": "modal",
                                                    "data-bs-target": "#deleteModal"}), "danger_attr"),
    ("title save", el(title="Save draft"), "write_verb"),
    ("aria remove", el(**{"aria-label": "Remove item"}), "write_verb"),
    ("text save", el(text="Save changes"), "write_verb"),
    ("text confirm", el(text="Confirm"), "write_verb"),
    ("text signed", el(text="Signed forms"), "write_verb"),
    ("anchor # no toggle", el("a", "More", href="#"), "anchor_no_target"),
    ("anchor js", el("a", "More", href="javascript:void(0)"), "anchor_no_target"),
    ("anchor no href", {**el("a", "More"), "attrs": {}}, "anchor_no_target"),
    ("occluded", el(occluded=True), "occluded"),
    ("approve href", el(href="/x/approve/3/"), "danger_attr"),
    ("deactivate href", el(href="/x/deactivate/3/"), "danger_attr"),
    ("activate href", el(href="/x/activate/3/"), "danger_attr"),
    ("login_as href", el(href="/users/login-as/3/"), "danger_attr"),
    ("loginas href", el(href="/users/loginas/"), "danger_attr"),
    ("switch href", el(href="/x/switch-portal/"), "danger_attr"),
    ("cancel data attr", el("button", "Open", **{"data-bs-toggle": "modal", "data-url": "/x/cancel/"}), "danger_attr"),
    ("assign href stays", el(href="/x/assignments/"), ""),
    ("js- class", el(**{"class": "btn js-open"}), "ajax_link"),
    ("ajax class", el(**{"class": "ajax"}), "ajax_link"),
    ("post class", el(**{"class": "post"}), "ajax_link"),
    ("data-method", el(**{"data-method": "post"}), "ajax_link"),
    ("data-remote", el(**{"data-remote": "true"}), "ajax_link"),
    ("data-ajax", el(**{"data-ajax": "1"}), "ajax_link"),
    ("posts class stays", el(**{"class": "posts-link"}), ""),
]


class Mirror(unittest.TestCase):
    def test_rules(self):
        for name, e, want in CASES:
            with self.subTest(name):
                self.assertEqual(C.click_refusal(e), want)

    def test_invisible_and_sidebar(self):
        self.assertEqual(C.click_refusal(el(visible=False)), "not_visible")
        self.assertEqual(C.click_refusal(el(in_sidebar=True)), "")                 # menu label by text
        self.assertEqual(C.click_refusal(el(in_sidebar=True), text_click=False), "sidebar")

    def test_row_link_without_words_is_refused(self):
        self.assertEqual(C.click_refusal(el(text=""), text_click=False), "icon_only")
        self.assertEqual(C.click_refusal(el(text="  "), text_click=False), "icon_only")
        self.assertEqual(C.click_refusal(el(text="Intro"), text_click=False), "")

    def test_words_that_are_not_write_verbs(self):
        for t in ("Assign", "Design", "Designation", "Add Schedule", "View", "Session", "Open"):
            self.assertEqual(C.click_refusal(el(text=t)), "", t)

    def test_constants_feed_the_js(self):
        js = C.click_source()
        for k in ("write", "cls", "attr", "tog"):
            self.assertIn(json.dumps(C.CLICK_RX[k])[1:-1], js.replace("\\\\", "\\\\"))
        self.assertTrue(js.rstrip().endswith("})"))
        self.assertNotIn("//", js.replace("javascript:", "").replace("(#|", ""))     # no comments
        self.assertLess(len(js), 4000)


DRIVER = r"""
const fs=require('fs');const sc=JSON.parse(fs.readFileSync(0,'utf8'));
class El{constructor(o){this.o=o;this.tagName=o.tag.toUpperCase();this.innerText=o.text;this.textContent=o.text;
 this.attributes=Object.keys(o.attrs).map(k=>({name:k,value:String(o.attrs[k])}));
 this.className=o.attrs['class']||'';this.value=o.attrs.value;}
 getAttribute(n){return n in this.o.attrs?String(this.o.attrs[n]):null;}
 hasAttribute(n){return n in this.o.attrs;}
 getBoundingClientRect(){return this.o.visible?{left:100,top:50,width:40,height:20,right:140,bottom:70}:{left:0,top:0,width:0,height:0,right:0,bottom:0};}
 closest(s){if(s==='form')return this.o.in_post_form?{getAttribute:()=>'post'}:null;return this.o.in_sidebar?this:null;}
 contains(x){return x===this;}
 scrollIntoView(){global.cur=this;}}
const run=(c)=>{
 const els=c.els.map(e=>new El(e));
 const tbl=c.table?{getBoundingClientRect:()=>({left:0,top:0,width:900,height:300}),
   querySelector:s=>s==='tbody tr'?(c.table.row===null?null:{querySelector:()=>els[c.table.row]}):null}:null;
 global.document={querySelectorAll:s=>s==='table'?(tbl?[tbl]:[]):els,
   elementFromPoint:()=>c.occlude==='parent'?{contains:()=>true}:c.occlude?{contains:()=>false}:global.cur};
 global.sessionStorage={getItem:()=>'(function(a){return {tn:{v:'+JSON.stringify(c.tn||'CLEAR')+'}};})'};
 const f=(0,eval)(sc.src);
 try{return f(c.args);}catch(e){return {threw:e.message};}
};
console.log(JSON.stringify(sc.cases.map(run)));
"""


@unittest.skipUnless(NODE, "node not installed")
class KbclickRuns(unittest.TestCase):
    def run_cases(self, cases):
        r = subprocess.run([NODE, "-e", DRIVER], input=json.dumps({"src": C.click_source(), "cases": cases}),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_js_agrees_with_the_mirror(self):
        cases = [{"els": [e], "args": {"text": e["text"], "side": "nav"}, "occlude": e["occluded"]}
                 for _, e, _ in CASES]
        for (name, e, want), got in zip(CASES, self.run_cases(cases)):
            with self.subTest(name):
                self.assertEqual(got.get("threw", ""), "KB_CLICK_REFUSED:" + want if want else "", name)
                if not want:
                    self.assertTrue(got["ok"])

    def test_js_occlusion_uses_the_point(self):
        e = el()
        [got] = self.run_cases([{"els": [e], "args": {"text": "View", "side": "nav"}, "occlude": True}])
        self.assertEqual(got["threw"], "KB_CLICK_REFUSED:occluded")

    def test_js_occluded_by_an_ancestor_is_refused(self):
        [got] = self.run_cases([{"els": [el()], "args": {"text": "View", "side": "nav"}, "occlude": "parent"}])
        self.assertEqual(got["threw"], "KB_CLICK_REFUSED:occluded")

    def test_js_takes_first_allowed_candidate(self):
        bad = el(text="View", onclick="x()")
        good = el(text="View", href="/ok/")
        got = self.run_cases([
            {"els": [bad, good], "args": {"text": "View", "side": "nav"}},
            {"els": [bad], "args": {"text": "View", "side": "nav"}},
            {"els": [good, el(text="View", href="/other/")], "args": {"text": "View", "side": "nav"}},
            {"els": [good, el(text="View", href="/ok/")], "args": {"text": "View", "side": "nav"}},
            {"els": [good, el(text="View", href="/other/", onclick="x()")], "args": {"text": "View", "side": "nav"}},
        ])
        self.assertTrue(got[0]["ok"])
        self.assertEqual(got[1]["threw"], "KB_CLICK_REFUSED:onclick")
        self.assertEqual(got[2]["threw"], "KB_CLICK_REFUSED:ambiguous")
        self.assertTrue(got[3]["ok"] and got[4]["ok"])

    def test_js_row_icon_only_refused(self):
        args = {"row": True, "side": "nav", "pa": {"labels": []}}
        [got] = self.run_cases([{"els": [el(text="")], "args": args, "table": {"row": 0}}])
        self.assertEqual(got["threw"], "KB_CLICK_REFUSED:icon_only")

    def test_coordinates_and_label(self):
        [got] = self.run_cases([{"els": [el()], "args": {"text": "view", "side": "nav"}}])
        self.assertEqual(got, {"ok": True, "x": 120, "y": 60, "label": "View"})

    def test_invisible_is_not_found_and_missing_text(self):
        got = self.run_cases([{"els": [el(visible=False)], "args": {"text": "View", "side": "nav"}},
                              {"els": [el()], "args": {"text": "Nope", "side": "nav"}}])
        self.assertEqual([g["threw"] for g in got], ["KB_CLICK_NOTFOUND:View", "KB_CLICK_NOTFOUND:Nope"])

    def test_write_verb_in_requested_text(self):
        [got] = self.run_cases([{"els": [el(text="Save")], "args": {"text": "Save", "side": "nav"}}])
        self.assertEqual(got["threw"], "KB_CLICK_REFUSED:write_verb")

    def test_sidebar_prefers_main_content(self):
        side = el(text="Sessions", in_sidebar=True, href="/side/")
        main = el(text="Sessions", href="/main/")
        got = self.run_cases([{"els": [side, main], "args": {"text": "Sessions", "side": "nav"}},
                              {"els": [side], "args": {"text": "Sessions", "side": "nav"}}])
        self.assertTrue(got[0]["ok"] and got[1]["ok"])

    def test_row(self):
        args = {"row": True, "side": "nav", "pa": {"labels": []}}
        row_link = el(text="Intro Course")
        got = self.run_cases([
            {"els": [row_link], "args": args, "table": {"row": 0}},
            {"els": [row_link], "args": args, "table": {"row": 0}, "tn": "REVIEW"},
            {"els": [row_link], "args": args, "table": {"row": 0}, "tn": "BLOCK"},
            {"els": [row_link], "args": args},
            {"els": [el(in_sidebar=True)], "args": args, "table": {"row": 0}},
            {"els": [el(onclick="x()")], "args": args, "table": {"row": 0}},
        ])
        self.assertEqual(got[0], {"ok": True, "x": 120, "y": 60, "label": "Intro Course"})
        self.assertEqual(got[1], got[0])                      # REVIEW (soft) does not stop a row click
        self.assertEqual([g["threw"] for g in got[2:]],
                         ["KB_TENANT_ROW", "KB_CLICK_NOTFOUND:row",
                          "KB_CLICK_REFUSED:sidebar", "KB_CLICK_REFUSED:onclick"])

    def test_row_gate_python_mirror(self):
        self.assertEqual([C.row_tenant_refusal(v) for v in ("CLEAR", "REVIEW", "BLOCK", None)],
                         ["", "", "tenant_row", ""])


CLICK_PLAN = {"run_id": "r1", "capture_plan": [
    {"persona": "admin", "surface": "Sessions", "url": "/administration/curriculum/sessions/",
     "clicks": [{"row": True}, {"text": "View"}, {"text": "Add Schedule"}],
     "write_gated": "confirm dialog after Save", "tickets": ["M1-465"], "route_source": "url_page"},
    {"persona": "admin", "surface": "Users", "url": "/users/", "clicks": [], "tickets": ["M1-1"]},
]}


class ClickPlan(Base):
    def test_visits_and_batches(self):
        calls, res = self.plan_calls(CLICK_PLAN)
        self.assertEqual([v["visit_id"] for v in calls["visits"]],
                         ["v01-s0", "v01-s1", "v01-s2", "v01-s3", "v02"])
        self.assertEqual([b["batch_id"] for b in calls["batches"]], ["b01", "b02", "b03", "b04", "b05"])
        self.assertEqual([b["kind"] for b in calls["batches"]],
                         ["visits", "click", "click", "click", "visits"])
        self.assertEqual(calls["batches"][0]["visit_ids"], ["v01-s0"])      # start page ends its chunk
        b = calls["batches"][1]
        self.assertEqual([(a["name"], a["input"].get("action")) for a in b["pick"]],
                         [("gif_creator", "start_recording"), ("javascript_tool", "javascript_exec")])
        self.assertEqual([(a["name"], a["input"].get("action")) for a in b["act"]],
                         [("computer", "left_click"), ("computer", "wait"), ("computer", "hover"),
                          ("javascript_tool", "javascript_exec"), ("gif_creator", "stop_recording"),
                          ("gif_creator", "export")])
        self.assertEqual(b["act"][0]["input"]["coordinate"], ["$X", "$Y"])
        self.assertEqual(b["act"][5]["input"]["filename"], "kb-capture/r1/v01-s1.gif")
        self.assertIn("(0,eval)(sessionStorage.getItem('kbc'))", b["pick"][1]["input"]["text"])
        row_args = json.loads(b["pick"][1]["input"]["text"].rsplit(")(", 1)[1][:-1])
        self.assertTrue(row_args["row"] and row_args["pa"]["labels"] == [] and "text" not in row_args)
        txt_args = json.loads(calls["batches"][2]["pick"][1]["input"]["text"].rsplit(")(", 1)[1][:-1])
        self.assertEqual((txt_args["text"], "pa" in txt_args), ("View", False))
        self.assertEqual(calls["entries"]["v01"]["clicks"], [{"row": True}, {"text": "View"},
                                                              {"text": "Add Schedule"}])
        self.assertEqual(calls["screens"], [{"visit_id": "v01-write", "entry": "v01", "persona": "admin",
                                             "surface": "Sessions", "tickets": ["M1-465"],
                                             "reason": "needs_write", "detail": "confirm dialog after Save"}])
        self.assertEqual(res["visits"], 5)
        self.assertEqual(len(json.dumps(calls["batches"][1]["pick"][1]["input"]["text"])) < 1500, True)

    def test_clicks_stay_with_their_start_page(self):
        plan = json.loads(json.dumps(CLICK_PLAN))
        plan["capture_plan"].insert(0, {"persona": "admin", "surface": "Groups", "url": "/groups/",
                                        "tickets": ["M1-3"]})
        calls, _ = self.plan_calls(plan, per_batch=5)
        ids = [b["visit_ids"] for b in calls["batches"]]
        self.assertEqual(ids[0], ["v01", "v02-s0"])         # no other page between start and clicks
        self.assertEqual(ids[1:4], [["v02-s1"], ["v02-s2"], ["v02-s3"]])

    def test_capture_md_tells_how_to_substitute(self):
        calls, _ = self.plan_calls(CLICK_PLAN)
        md = (self.shots / "capture.md").read_text()
        for word in ('`"$X"`', '`"$Y"`', "KB_CLICK_REFUSED", "KB_CLICK_NOTFOUND", "KB_TENANT_ROW",
                     "kbclick", "click_error", "same `entry`", "KB_GUARD"):
            self.assertIn(word, md)
        for b in calls["batches"]:
            if b["kind"] == "click":
                self.assertIn(json.dumps(b["pick"], indent=1), md)
                self.assertIn(json.dumps(b["act"], indent=1), md)
        # 2 visit batches + 3 click batches (pick, act) + install, abort, raw example, restore
        self.assertEqual(md.count("```json"), 2 + 3 * 2 + 4)

    def test_no_write_verb_clicks_are_planned_as_given(self):
        calls, _ = self.plan_calls(CLICK_PLAN)
        self.assertEqual(calls["visits"][3]["click"], {"text": "Add Schedule"})


@unittest.skipUnless(has_module("PIL"), "Pillow not installed")
class ClickIngest(Base):
    def setUp(self):
        super().setUp()
        self.calls, _ = self.plan_calls(CLICK_PLAN)
        self.raw = self.tmp / "raw"
        self.raw.mkdir()
        self.dl = self.tmp / "Downloads"
        (self.dl / "kb-capture" / "r1").mkdir(parents=True)

    def raw_file(self, bid, items, status="ok"):
        (self.raw / f"{bid}.json").write_text(json.dumps(
            {"batch_id": bid, "status": status, "error": None, "items": items}))

    def item(self, vid, **kw):
        make_gif(self.dl / f"kb-capture/r1/{vid}.gif")
        return {"visit_id": vid, "probe": probe_for([lab("Add Schedule")]),
                "gif": f"kb-capture/r1/{vid}.gif", **kw}

    def ingest(self):
        rc, res, _ = run_cli("ingest", "--calls", self.shots / "calls.json", "--raw-dir", self.raw,
                             "--out-dir", self.shots, "--downloads-dir", self.dl, "--no-ocr")
        return rc, res, json.loads((self.shots / "shots.json").read_text())

    def test_full_run_records_needs_write(self):
        self.raw_file("b01", [self.item("v01-s0")])
        for n, vid in ((2, "v01-s1"), (3, "v01-s2"), (4, "v01-s3")):
            self.raw_file(f"b0{n}", [self.item(vid, click={"ok": True, "x": 1, "y": 2})])
        self.raw_file("b05", [self.item("v02")])
        rc, res, m = self.ingest()
        self.assertEqual((rc, res["left"]), (0, 0))
        self.assertEqual(m["missing"]["M1-465"], {"reason": "needs_write",
                                                  "detail": "confirm dialog after Save"})
        self.assertEqual(m["missing"]["M1-1"]["reason"], "label_not_found")   # loaded, no label shown
        self.assertEqual(sorted(res["downloads_left"])[0], "kb-capture/r1/v01-s0.gif")

    def test_refused_click_skips_rest_of_entry_and_is_not_left(self):
        self.raw_file("b01", [self.item("v01-s0")])
        self.raw_file("b02", [self.item("v01-s1", click={"ok": True, "x": 1, "y": 2})])
        self.raw_file("b03", [{"visit_id": "v01-s2", "click_error": "Error: KB_CLICK_REFUSED:onclick"}])
        self.raw_file("b05", [self.item("v02")])
        rc, res, m = self.ingest()
        self.assertEqual((rc, res["left"]), (0, 0))                 # v01-s3 skipped, not left
        self.assertEqual(m["missing"]["M1-465"], {"reason": "click_refused", "detail": "onclick"})
        by = {x["visit_id"]: x for x in m["screens"]}
        self.assertEqual(by["v01-s2"]["reason"], "click_refused")
        self.assertNotIn("v01-s3", by)
        self.assertEqual(res["missing"]["M1-465"], "click_refused")

    def test_not_found_and_tenant_row(self):
        for err, code in (("KB_CLICK_NOTFOUND:View", "click_not_found"), ("KB_TENANT_ROW", "tenant_row")):
            self.raw_file("b01", [self.item("v01-s0")])
            self.raw_file("b02", [{"visit_id": "v01-s1", "click_error": err}])
            _, _, m = self.ingest()
            self.assertEqual(m["missing"]["M1-465"]["reason"], code)

    def test_unreached_click_is_left_and_resume_keeps_clicks(self):
        self.raw_file("b01", [self.item("v01-s0")])
        rc, res, _ = self.ingest()
        self.assertEqual(rc, 3)
        resume = json.loads((self.shots / "resume.json").read_text())
        e = resume["capture_plan"][0]
        self.assertEqual((e["surface"], e["clicks"][0], e["write_gated"]),
                         ("Sessions", {"row": True}, "confirm dialog after Save"))
        self.assertEqual(res["left_reasons"]["admin/Sessions#s1"], "not_reached")


class SpecReasons(unittest.TestCase):
    def test_texts(self):
        self.assertEqual(F.missing_reason_text("needs_write"),
                         "This step happens after you save; no screenshot")
        self.assertEqual(F.missing_reason_text("click_refused", "onclick"),
                         "the capture could not safely open this screen (onclick)")
        self.assertEqual(F.missing_reason_text("tenant_row"),
                         "the first row on this screen shows real tenant data")
        self.assertEqual(F.missing_reason_text("no_screen"), F.NO_SCREEN)
        self.assertEqual(F.missing_reason_text("something else"), "something else")
        self.assertEqual(SP._reason_text({"reason": "needs_write", "detail": "x"}),
                         F.MISSING_REASONS["needs_write"])
        self.assertIsNone(SP._reason_text(None))

    def test_build_spec_markers(self):
        prose = {"ticket": "T", "title": "t", "steps": [{"text": "Click **Add**.", "shot": "missing"}]}
        meta = {"ticket": "T"}
        with tempfile.TemporaryDirectory() as d:
            spec = SP.build_spec(prose, [], meta, F.MISSING_REASONS["needs_write"], d)
            self.assertEqual(spec["steps"][0]["missing_shot"], "This step happens after you save; no screenshot")
            spec = SP.build_spec(prose, [], meta, F.NO_SCREEN, d)
            self.assertNotIn("missing_shot", spec["steps"][0])
            self.assertEqual(spec["notes"], [F.NO_SCREEN_NOTE])


def run_hook(payload, raw=None):
    r = subprocess.run([sys.executable, str(HOOK)], input=raw if raw is not None else json.dumps(payload),
                       capture_output=True, text=True, timeout=30)
    return r.returncode, r.stdout.strip()


class Hook(unittest.TestCase):
    def call(self, tool, action=None, agent="medtrics-release-notes:kb-capturer"):
        d = {"tool_name": f"mcp__claude-in-chrome__{tool}", "tool_input": {"action": action} if action else {}}
        if agent is not None:
            d["agent_type"] = agent
            d["agent_id"] = "a1"
        return run_hook(d)

    def test_denies_for_the_capturer(self):
        for tool, action in (("computer", "type"), ("computer", "key"), ("computer", "left_click_drag"),
                             ("form_input", None)):
            rc, out = self.call(tool, action)
            self.assertEqual(rc, 0)
            o = json.loads(out)["hookSpecificOutput"]
            self.assertEqual((o["hookEventName"], o["permissionDecision"]), ("PreToolUse", "deny"))
            self.assertIn("KB_GUARD", o["permissionDecisionReason"])

    def test_allows_what_the_capturer_needs(self):
        for action in ("left_click", "hover", "wait", "screenshot", "scroll"):
            self.assertEqual(self.call("computer", action), (0, ""))

    def test_allows_everyone_else(self):
        for agent in (None, "", "general-purpose", "medtrics-release-notes:kb-writer"):
            for tool, action in (("computer", "type"), ("computer", "key"), ("form_input", None)):
                self.assertEqual(self.call(tool, action, agent), (0, ""), (agent, tool, action))

    def batch(self, actions, agent="medtrics-release-notes:kb-capturer"):
        return run_hook({"tool_name": "mcp__claude-in-chrome__browser_batch", "agent_type": agent,
                         "tool_input": {"actions": actions}})

    def test_batch_with_typing_is_denied_for_the_capturer(self):
        ok = [{"name": "navigate", "input": {"url": "x"}},
              {"name": "computer", "input": {"action": "hover", "coordinate": [1, 2]}}]
        self.assertEqual(self.batch(ok), (0, ""))
        for bad in ({"name": "computer", "input": {"action": "type", "text": "x"}},
                    {"name": "computer", "input": {"action": "key", "text": "Enter"}},
                    {"name": "form_input", "input": {"ref": "r1", "value": "x"}},
                    {"name": "mcp__claude-in-chrome__computer", "input": {"action": "type"}}):
            rc, out = self.batch(ok + [bad])
            self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny", bad)
        self.assertEqual(self.batch(ok + [{"name": "computer", "input": {"action": "type"}}], agent="general-purpose"),
                         (0, ""))

    def test_never_crashes(self):
        for raw in ("", "not json", "[]", "null", '{"tool_name": 5, "agent_type": ["kb-capturer"]}',
                    '{"agent_type": "kb-capturer", "tool_input": null, "tool_name": "x__computer"}'):
            rc, out = run_hook(None, raw)
            self.assertEqual((rc, out), (0, ""), raw)

    def test_hooks_json(self):
        h = json.loads(HOOKS_JSON.read_text())["hooks"]["PreToolUse"][0]
        self.assertEqual(h["matcher"], "mcp__claude-in-chrome__computer|mcp__claude-in-chrome__form_input"
                                       "|mcp__claude-in-chrome__browser_batch")
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/hooks/kb_guard.py", h["hooks"][0]["command"])

    def test_hook_writes_nothing(self):
        src = HOOK.read_text()
        for bad in ("open(", "write(", "makedirs", "mkdir"):
            self.assertNotIn(bad, src)


if __name__ == "__main__":
    unittest.main()
