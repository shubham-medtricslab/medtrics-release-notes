"""Tests for the 0.26.0 click capture (0.27.0: clickable table rows, write mode):
the kbclick refusal rules (Python mirror click_refusal() / row_target() and the JS
built from the same constants, run in node against a fake DOM), click visits in plan-calls / capture.md, ingest of click results
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
from test_kb_write_guard import run_dom

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


# 0.27.0: more write words, word start with an un / re prefix. (name, element, refusal on a plain page,
# refusal on a Vue page)
NEW_WORD_CASES = [
    ("Vue: Resend invitation", el("button", "Resend invitation"), "write_verb"),
    ("Vue: Notify attendees", el("button", "Notify attendees"), "write_verb"),
    ("Vue: Invite", el("button", "Invite"), "write_verb"),
    ("Vue: Copy session", el("button", "Copy session"), "write_verb"),
    ("Vue: Duplicate", el("button", "Duplicate"), "write_verb"),
    ("Vue: Remind", el("button", "Remind everyone"), "write_verb"),
    ("Vue: Clone", el("button", "Clone"), "write_verb"),
    ("Vue: Reset password", el("button", "Reset password"), "write_verb"),
    ("Vue: Unpublish", el("button", "Unpublish"), "write_verb"),
    ("Vue: Reactivate", el("button", "Reactivate"), "write_verb"),
    ("Vue: Enroll", el("button", "Enroll learners"), "write_verb"),
    ("Vue: Unenroll", el("button", "Unenroll"), "write_verb"),
    ("Vue: Resend lowercase mid-text", el("button", "Click to resend"), "write_verb"),
    ("href copy", el(href="/sessions/5/copy"), "danger_attr"),
    ("href resend", el(href="/sessions/5/resend"), "danger_attr"),
    ("href unpublish", el(href="/sessions/5/unpublish"), "danger_attr"),
    ("href duplicate", el(href="/sessions/5/duplicate/"), "danger_attr"),
    ("href reactivate", el(href="/users/5/reactivate/"), "danger_attr"),
    ("href notify", el(href="/sessions/5/notify/"), "danger_attr"),
    ("href invite", el(href="/users/invite/"), "danger_attr"),
    ("href remind_all", el(href="/x/remind_all/"), "danger_attr"),
    ("href unenroll", el(href="/x/5/unenroll/"), "danger_attr"),
    ("data attr resend", el("button", "Open", **{"data-bs-toggle": "modal", "data-url": "/x/resend/"}), "danger_attr"),
    ("link Assignments stays", el("a", "Assignments", href="/administration/assignments/"), ""),
    ("button Assignments stays", el("button", "Assignments"), ""),
    ("Assign stays", el("button", "Assign"), ""),
    ("Designation stays", el("button", "Designation"), ""),
    ("Reservations stays", el("button", "Reservations"), ""),
    ("Preset stays", el("button", "Presets"), ""),
]


class NewWriteWords(unittest.TestCase):
    def test_mirror(self):
        for name, e, refusal in NEW_WORD_CASES:
            with self.subTest(name, vue=True):
                want = refusal if refusal in ("write_verb", "danger_attr") else ""
                self.assertEqual(C.click_refusal(e, vue=True), want)
            if e["tag"] == "a":                                   # links need no Vue exemption
                with self.subTest(name, vue=False):
                    self.assertEqual(C.click_refusal(e), refusal)

    def test_word_boundary(self):
        for t in ("Resend", "resend the email", "Unpublish", "reactivate", "Reset", "Copy", "Notify", "Invite"):
            self.assertRegex(t, C._CLICK_RE["write"])
        for t in ("Assign", "Assignments", "Design", "Reservations", "Presets", "Consign"):
            self.assertNotRegex(t, C._CLICK_RE["write"])
        for t in ("/x/resend/", "/x/unpublish", "x_copy", "/a/reactivate/", "/b/copy"):
            self.assertRegex(t, C._CLICK_RE["attr"])
        for t in ("/x/assignments/", "/x/design/", "/x/reservations/"):
            self.assertNotRegex(t, C._CLICK_RE["attr"])

    @unittest.skipUnless(NODE, "node not installed")
    def test_js_agrees_with_the_mirror(self):
        cases = [{"els": [e], "args": {"text": e["text"], "side": "nav"}, "occlude": e["occluded"], "vue": True}
                 for _, e, _ in NEW_WORD_CASES]
        for (name, e, refusal), g in zip(NEW_WORD_CASES, run_js(self, cases)):
            want = refusal if refusal in ("write_verb", "danger_attr") else ""
            with self.subTest(name):
                self.assertEqual(g.get("threw", ""), "KB_CLICK_REFUSED:" + want if want else "")


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
        self.assertLess(len(js), 16000)      # 0.28.0: + one-shot allowance, choose mode, group rows (was 4000 before 0.27.0)


DRIVER = r"""
const fs=require('fs');const sc=JSON.parse(fs.readFileSync(0,'utf8'));
class El{constructor(o){this.o=o;this.tagName=o.tag.toUpperCase();this.innerText=o.text;this.textContent=o.text;
 this.attributes=Object.keys(o.attrs).map(k=>({name:k,value:String(o.attrs[k])}));
 this.className=o.attrs['class']||'';this.value=o.attrs.value;}
 getAttribute(n){return n in this.o.attrs?String(this.o.attrs[n]):null;}
 hasAttribute(n){return n in this.o.attrs;}
 getBoundingClientRect(){return this.o.visible?{left:100,top:50,width:40,height:20,right:140,bottom:70}:{left:0,top:0,width:0,height:0,right:0,bottom:0};}
 closest(s){if(s==='form')return this.o.in_post_form?{getAttribute:()=>'post'}:null;
  if(s.indexOf('button')>=0)return this.o.in_control?this:null;return this.o.in_sidebar?this:null;}
 querySelector(){return this.o.has_control?{}:null;}
 contains(x){return x===this;}
 scrollIntoView(){global.cur=this;}}
const run=(c)=>{
 const els=c.els.map(e=>new El(e));
 const specs=!c.table?[]:typeof c.table.row==='number'?[{link:c.table.row}]:c.table.rows||[];
 const rows=specs.map(r=>{if(r.cells&&r.cells.length)els[r.cells[0]].cursor=r.cell_cursor||'';
  return {className:r.cls||'',cursor:r.cursor||'',
  querySelector:s=>s==='a[href]'?(r.link==null?null:els[r.link]):s==='td,th'?(r.cells&&r.cells.length?els[r.cells[0]]:null):s.indexOf('expand')>=0?(r.group?{}:null):null,
  querySelectorAll:s=>s==='span'?(r.spans||[]).map(i=>els[i]):s==='td,th'?(r.cells||[]).map(i=>els[i]):[]};});
 const tbl=c.table?{getBoundingClientRect:()=>({left:0,top:0,width:900,height:300}),
   querySelectorAll:s=>s==='tbody tr'?rows:[]}:null;
 const dlg=c.dialog?{getBoundingClientRect:()=>({left:0,top:0,width:500,height:400}),contains:e=>c.dialog.inside.some(i=>els[i]===e)}:null;
 const opts=(c.options||[]).map(i=>els[i]);
 global.getComputedStyle=e=>({cursor:e.cursor||''});
 global.window=global;global.__VUE__=undefined;
 global.document={querySelectorAll:s=>s==='table'?(tbl?[tbl]:[]):s.indexOf('modal.show')>=0?(dlg?[dlg]:[]):s.indexOf('role=option')>=0?opts:els,
   querySelector:s=>s==='[data-v-app]'&&c.vue?{}:null,
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


def run_js(testcase, cases):
    r = subprocess.run([NODE, "-e", DRIVER], input=json.dumps({"src": C.click_source(), "cases": cases}),
                       capture_output=True, text=True, timeout=60)
    testcase.assertEqual(r.returncode, 0, r.stderr)
    return json.loads(r.stdout)


# (name, element, allowed write texts, expected refusal) — 0.27.0 write mode
SAVE_TOGGLE = {"data-toggle": "modal"}
WRITE_CASES = [
    ("save button", el("button", "Save"), ["Save"], ""),
    ("save submit input", el("input", "Save", type="submit"), ["Save"], ""),
    ("save in post form", el("button", "Save", in_post_form=True), ["Save"], ""),
    ("save submit button in post form", el("button", "Save", type="submit", in_post_form=True), ["Save"], ""),
    ("case and spaces", el("button", "Save"), ["  sAVE "], ""),
    ("not listed", el("button", "Save"), ["Create"], "write_verb"),
    ("empty list", el("button", "Save"), [], "write_verb"),
    ("exact only", el("button", "Save and close"), ["Save"], "write_verb"),
    ("no-button", el("button", "No, don't send notifications"), ["No, don't send notifications"], ""),
    ("no-button not listed", el("button", "No, don't send notifications"), ["Save"], "write_verb"),
    ("no-button in post form", el("button", "No, don't send notifications", in_post_form=True),
     ["No, don't send notifications"], ""),
    ("send", el("button", "Send"), ["Send"], "write_danger"),
    ("notify", el("button", "Notify participants"), ["Notify participants"], "write_danger"),
    ("yes send", el("button", "Yes, send notifications"), ["Yes, send notifications"], "write_danger"),
    ("delete", el("button", "Delete"), ["Delete"], "write_danger"),
    ("approve", el("button", "Approve"), ["Approve"], "write_danger"),
    ("publish", el("button", "Publish"), ["Publish"], "write_danger"),
    ("invite", el("button", "Invite"), ["Invite"], "write_danger"),
    ("archive title", el("button", "Save", title="Archive it"), ["Save"], "write_danger"),
    ("nothing is not No", el("button", "Notify"), ["Notify"], "write_danger"),
    ("No, send is not negated", el("button", "No, send notifications"), ["No, send notifications"], "write_danger"),
    ("No notify is not negated", el("button", "No notify"), ["No notify"], "write_danger"),
    ("No, invite", el("button", "No, invite everyone"), ["No, invite everyone"], "write_danger"),
    ("No, delete", el("button", "No, delete it"), ["No, delete it"], "write_danger"),
    ("Don't notify", el("button", "Don't notify"), ["Don't notify"], ""),
    ("do not send", el("button", "Do not send anything"), ["Do not send anything"], ""),
    ("curly don't", el("button", "No, don\u2019t send notifications"), ["No, don\u2019t send notifications"], ""),
    ("not send", el("button", "Not send"), ["Not send"], ""),
    ("don't then delete", el("button", "Don't send and delete"), ["Don't send and delete"], "write_danger"),
    ("don't send or notify", el("button", "Don't send or notify"), ["Don't send or notify"], "write_danger"),
    ("Cancel", el("button", "Cancel"), ["Cancel"], ""),
    ("Close", el("button", "Close"), ["Close"], ""),
    ("bare No", el("button", "No"), ["No"], ""),
    ("danger class", el("button", "Save", **{"class": "btn btn-danger"}), ["Save"], "danger_class"),
    ("danger href", el("a", "Save", href="/x/delete/3/"), ["Save"], "danger_attr"),
    ("danger data attr", el("button", "Save", **{"data-action": "remove"}), ["Save"], "danger_attr"),
    ("onclick", el("button", "Save", onclick="go()"), ["Save"], "onclick"),
    ("data-href", el("button", "Save", **{"data-href": "/x/"}), ["Save"], "data_href"),
    ("occluded", el("button", "Save", occluded=True), ["Save"], "occluded"),
    ("list does not lift other buttons", el("button", "Add Schedule"), ["Save"], "button_no_toggle"),
    ("plain link unchanged", el("a", "View"), ["Save"], ""),
]


class WriteMode(unittest.TestCase):
    def test_mirror(self):
        for name, e, write, want in WRITE_CASES:
            with self.subTest(name):
                self.assertEqual(C.click_refusal(e, write=write), want)

    def test_without_write_nothing_changes(self):
        for e in (el("button", "Save"), el("input", "Save", type="submit"),
                  el("button", "No, don't send notifications")):
            self.assertIn(C.click_refusal(e), ("write_verb", "submit"))

    def test_row_clicks_never_use_write(self):
        self.assertEqual(C.click_refusal(el(text="Save"), text_click=False, write=["Save"]), "write_verb")

    @unittest.skipUnless(NODE, "node not installed")
    def test_js_agrees_with_the_mirror(self):
        cases = [{"els": [e], "args": {"text": e["text"], "side": "nav", "write": w}, "occlude": e["occluded"]}
                 for _, e, w, _ in WRITE_CASES]
        for (name, e, w, want), got in zip(WRITE_CASES, run_js(self, cases)):
            with self.subTest(name):
                self.assertEqual(got.get("threw", ""), "KB_CLICK_REFUSED:" + want if want else "", name)
                if not want:
                    self.assertTrue(got["ok"])

    @unittest.skipUnless(NODE, "node not installed")
    def test_js_lifts_only_the_exact_element(self):
        save, other = el("button", "Save"), el("button", "Save as draft")
        got = run_js(self, [
            {"els": [other, save], "args": {"text": "Save", "side": "nav", "write": ["Save"]}},
            {"els": [other], "args": {"text": "Save as draft", "side": "nav", "write": ["Save"]}},
        ])
        self.assertTrue(got[0]["ok"])
        self.assertEqual(got[1]["threw"], "KB_CLICK_REFUSED:write_verb")

    def test_constants_in_the_js(self):
        js = C.click_source()
        self.assertIn("a.write", js)
        self.assertIn("input[type=submit]", js)
        self.assertIn(json.dumps(C.CLICK_RX["wd"])[1:-1], js)


def spn(text, cls="", **kw):
    return el("span", text, **({"class": cls} if cls else {}), **kw)


NAME = "ZZ DEMO - Clinical Founda\u2026"
TRIANGLE = spn("", "triangle expand")
# The live session list: <tr class="clickable"><td><span>name</span><span class="triangle expand"></span></td>…
ROW_CASES = [
    ("live session row", [spn("Course Code"), spn(NAME), TRIANGLE], {"cls": "clickable", "spans": [0, 1, 2]}, 0),
    ("class clickable, name span first", [spn(NAME), TRIANGLE], {"cls": "clickable", "spans": [0, 1]}, 0),
    ("cursor on the row", [spn(NAME)], {"cursor": "pointer", "spans": [0]}, 0),
    ("cursor on the first cell", [spn(NAME), el("td", "cell")], {"cell_cursor": "pointer", "spans": [0], "cells": [1]}, 0),
    ("triangle skipped", [TRIANGLE, spn(NAME)], {"cls": "clickable", "spans": [0, 1]}, 1),
    ("a span holding a button is skipped", [{**spn("Edit"), "has_control": True}, spn(NAME)],
     {"cls": "clickable", "spans": [0, 1]}, 1),
    ("a span inside a button is skipped", [{**spn("Edit"), "in_control": True}, spn(NAME)],
     {"cls": "clickable", "spans": [0, 1]}, 1),
    ("refused span is skipped", [spn("Save session"), spn(NAME)], {"cls": "clickable", "spans": [0, 1]}, 1),
    ("cells when there is no span", [TRIANGLE, el("td", NAME)], {"cls": "clickable", "spans": [0], "cells": [1]}, 1),
    ("not clickable", [spn(NAME)], {"spans": [0]}, None),
    ("clickable but nothing to say", [TRIANGLE], {"cls": "clickable", "spans": [0]}, None),
    ("an a[href] row keeps the old rule", [el("a", "Intro Course"), spn(NAME)],
     {"link": 0, "cls": "clickable", "spans": [1]}, 0),
]


class ClickableRows(unittest.TestCase):
    @staticmethod
    def mirror_row(els, r):
        return {"link": els[r["link"]] if r.get("link") is not None else None, "class": r.get("cls", ""),
                "cursor": r.get("cursor", ""), "cell_cursor": r.get("cell_cursor", ""),
                "spans": [els[i] for i in r.get("spans", [])], "cells": [els[i] for i in r.get("cells", [])]}

    def test_mirror(self):
        for name, els, r, want in ROW_CASES:
            with self.subTest(name):
                got = C.row_target(self.mirror_row(els, r))
                self.assertIs(got, None if want is None else els[want])

    def test_mirror_all_refused_returns_the_first_so_it_is_refused(self):
        els = [spn("Save session"), spn("Delete me")]
        got = C.row_target(self.mirror_row(els, {"cls": "clickable", "spans": [0, 1]}))
        self.assertIs(got, els[0])
        self.assertEqual(C.click_refusal(got, text_click=False), "write_verb")

    @unittest.skipUnless(NODE, "node not installed")
    def test_js_agrees_with_the_mirror(self):
        args = {"row": True, "side": "nav", "pa": {"labels": []}}
        cases = [{"els": els, "args": args, "table": {"rows": [r]}} for _, els, r, _ in ROW_CASES]
        for (name, els, r, want), got in zip(ROW_CASES, run_js(self, cases)):
            with self.subTest(name):
                if want is None:
                    self.assertEqual(got, {"threw": "KB_CLICK_NOTFOUND:row"})
                else:
                    self.assertEqual(got, {"ok": True, "x": 120, "y": 60, "label": els[want]["text"]})

    @unittest.skipUnless(NODE, "node not installed")
    def test_js_first_candidate_row_wins_and_gates_hold(self):
        args = {"row": True, "side": "nav", "pa": {"labels": []}}
        els = [spn("plain"), spn(NAME), spn("later")]
        rows = [{"spans": [0]}, {"cls": "clickable", "spans": [1]}, {"cls": "clickable", "spans": [2]}]
        got = run_js(self, [
            {"els": els, "args": args, "table": {"rows": rows}},
            {"els": els, "args": args, "table": {"rows": rows}, "tn": "BLOCK"},
            {"els": els, "args": args, "table": {"rows": rows}, "tn": "REVIEW"},
            {"els": [spn("Save session")], "args": args, "table": {"rows": [{"cls": "clickable", "spans": [0]}]}},
            {"els": [spn(NAME)], "args": args, "table": {"rows": [{"cls": "clickable", "spans": [0]}]},
             "occlude": True},
            {"els": els, "args": args, "table": {"rows": []}},
        ])
        self.assertEqual(got[0]["label"], NAME)
        self.assertEqual(got[1], {"threw": "KB_TENANT_ROW"})
        self.assertEqual(got[2]["label"], NAME)
        self.assertEqual(got[3], {"threw": "KB_CLICK_REFUSED:write_verb"})
        self.assertEqual(got[4], {"threw": "KB_CLICK_REFUSED:occluded"})
        self.assertEqual(got[5], {"threw": "KB_CLICK_NOTFOUND:row"})


VUE_CASES = [
    ("Add schedule button", el("button", "Add schedule"), "button_no_toggle", ""),
    ("button role", el("div", "Add schedule", role="button"), "button_no_toggle", ""),
    ("typed button", el("button", "Add schedule", type="button"), "button_no_toggle", ""),
    ("anchor #", el("a", "More", href="#"), "anchor_no_target", ""),
    ("anchor js", el("a", "More", href="javascript:void(0)"), "anchor_no_target", ""),
    ("anchor no href", {**el("a", "More"), "attrs": {}}, "anchor_no_target", ""),
    # every other refusal stays on a Vue page
    ("submit", el("button", "Go", type="submit"), "submit", "submit"),
    ("post form", el("button", "Add", in_post_form=True), "post_form", "post_form"),
    ("onclick", el("button", "Add", onclick="go()"), "onclick", "onclick"),
    ("data-href", el("button", "Add", **{"data-href": "/x/"}), "data_href", "data_href"),
    ("btn-danger", el("button", "Add", **{"class": "btn btn-danger"}), "danger_class", "danger_class"),
    ("delete href", el(href="/x/delete/3/"), "danger_attr", "danger_attr"),
    ("danger data attr", el("button", "Open", **{"data-action": "archive"}), "danger_attr", "danger_attr"),
    ("ajax link", el(**{"class": "js-open"}), "ajax_link", "ajax_link"),
    ("write verb", el("button", "Save changes"), "write_verb", "write_verb"),
    ("write verb title", el("button", "Open", title="Delete it"), "write_verb", "write_verb"),
    ("occluded", el("button", "Add schedule", occluded=True), "button_no_toggle", "occluded"),
]


class VueRule(unittest.TestCase):
    """Vue pages (the write guard makes the two structural refusals unnecessary)."""
    def test_detection_rule(self):
        self.assertFalse(C.is_vue_page())
        for kw in ({"has_data_v_app": True}, {"has_vue_app_prop": True}, {"has_global": True}):
            self.assertTrue(C.is_vue_page(**kw))

    def test_mirror(self):
        for name, e, plain, vue in VUE_CASES:
            with self.subTest(name):
                self.assertEqual(C.click_refusal(e), plain)
                self.assertEqual(C.click_refusal(e, vue=True), vue)

    def test_add_schedule_is_allowed_only_on_a_vue_page(self):
        b = el("button", "Add schedule")
        self.assertEqual((C.click_refusal(b), C.click_refusal(b, vue=True)), ("button_no_toggle", ""))

    @unittest.skipUnless(NODE, "node not installed")
    def test_js_agrees_with_the_mirror(self):
        cases = [{"els": [e], "args": {"text": e["text"], "side": "nav"}, "occlude": e["occluded"], "vue": v}
                 for _, e, _, _ in VUE_CASES for v in (False, True)]
        got = iter(run_js(self, cases))
        for name, e, plain, vue in VUE_CASES:
            for want in (plain, vue):
                g = next(got)
                with self.subTest(name, vue=want == vue):
                    self.assertEqual(g.get("threw", ""), "KB_CLICK_REFUSED:" + want if want else "")

    @unittest.skipUnless(NODE, "node not installed")
    def test_js_vue_detection_sources(self):
        src = C.click_source()
        for needle in ("[data-v-app]", "__VUE__", "__vue_app__"):
            self.assertIn(needle, src)
        [off, on] = run_js(self, [{"els": [el("button", "Add schedule")], "args": {"text": "Add schedule", "side": "nav"}},
                                  {"els": [el("button", "Add schedule")], "args": {"text": "Add schedule", "side": "nav"},
                                   "vue": True}])
        self.assertEqual(off["threw"], "KB_CLICK_REFUSED:button_no_toggle")
        self.assertTrue(on["ok"])


GROUP_ROWS = [
    # (name, elements, rows, expected (element index, group))
    ("only group rows: first group row", [spn("Course A"), spn("Course B")],
     [{"cls": "clickable", "spans": [0], "group": True}, {"cls": "clickable", "spans": [1], "group": True}], (0, True)),
    ("a leaf after groups wins", [spn("Course A"), spn("Session 1")],
     [{"cls": "clickable", "spans": [0], "group": True}, {"cls": "clickable", "spans": [1]}], (1, False)),
    ("first leaf wins", [spn("Session 1"), spn("Session 2")],
     [{"cls": "clickable", "spans": [0]}, {"cls": "clickable", "spans": [1]}], (0, False)),
    ("group by class", [spn("Course A")], [{"cls": "clickable group-header", "spans": [0]}], (0, True)),
    ("a class that merely starts with group is not a group", [spn("Session 1")], [{"cls": "clickable grouped", "spans": [0]}], (0, False)),
    ("a link row is a leaf even with a triangle", [el("a", "Intro"), spn("x")],
     [{"link": 0, "cls": "clickable", "spans": [1], "group": True}], (0, False)),
    ("no candidates", [spn("x")], [{"spans": [0]}], (None, False)),
]


class GroupRows(unittest.TestCase):
    @staticmethod
    def mirror(els, r):
        return {"link": els[r["link"]] if r.get("link") is not None else None, "class": r.get("cls", ""),
                "spans": [els[i] for i in r.get("spans", [])], "group": r.get("group", False)}

    def test_mirror(self):
        for name, els, rows, (idx, grp) in GROUP_ROWS:
            with self.subTest(name):
                e, g = C.row_pick([self.mirror(els, r) for r in rows])
                self.assertIs(e, None if idx is None else els[idx])
                self.assertEqual(g, grp)

    @unittest.skipUnless(NODE, "node not installed")
    def test_js_agrees_with_the_mirror(self):
        args = {"row": True, "side": "nav", "pa": {"labels": []}}
        cases = [{"els": els, "args": args, "table": {"rows": rows}} for _, els, rows, _ in GROUP_ROWS]
        for (name, els, rows, (idx, grp)), got in zip(GROUP_ROWS, run_js(self, cases)):
            with self.subTest(name):
                if idx is None:
                    self.assertEqual(got, {"threw": "KB_CLICK_NOTFOUND:row"})
                    continue
                want = {"ok": True, "x": 120, "y": 60, "label": els[idx]["text"]}
                self.assertEqual(got, {**want, "group": True} if grp else want)

    def test_group_selector_is_in_the_js(self):
        self.assertIn(C.GROUP_SEL, C.click_source())
        for part in (".expand", ".triangle", "[aria-expanded]"):
            self.assertIn(part, C.GROUP_SEL)

    @unittest.skipUnless(NODE, "node not installed")
    def test_group_click_is_still_guarded(self):
        args = {"row": True, "side": "nav", "pa": {"labels": []}}
        rows = [{"cls": "clickable", "spans": [0], "group": True}]
        got = run_js(self, [
            {"els": [spn("Course A")], "args": args, "table": {"rows": rows}, "tn": "BLOCK"},
            {"els": [spn("Delete course")], "args": args, "table": {"rows": rows}},
            {"els": [spn("Course A")], "args": args, "table": {"rows": rows}, "occlude": True}])
        self.assertEqual([g["threw"] for g in got],
                         ["KB_TENANT_ROW", "KB_CLICK_REFUSED:write_verb", "KB_CLICK_REFUSED:occluded"])


class WriteScope(unittest.TestCase):
    """args.write: only the top-most open dialog is searched; two matches are ambiguous."""
    @unittest.skipUnless(NODE, "node not installed")
    def test_dialog_scope_and_ambiguity(self):
        page_save, dlg_save = el("button", "Save"), el("button", "Save")
        wa = {"text": "Save", "side": "nav", "write": ["Save"]}
        got = run_js(self, [
            {"els": [page_save, dlg_save], "args": wa, "dialog": {"inside": [1]}},       # only the dialog's Save
            {"els": [page_save, dlg_save], "args": wa},                                  # no dialog: two matches
            {"els": [page_save, dlg_save], "args": wa, "dialog": {"inside": [0, 1]}},    # both inside
            {"els": [page_save], "args": wa},                                            # one match, no dialog
            {"els": [page_save], "args": wa, "dialog": {"inside": []}},                  # dialog, Save outside it
            {"els": [page_save, dlg_save], "args": {"text": "Save", "side": "nav"}},    # not write mode
        ])
        self.assertTrue(got[0]["ok"])
        self.assertEqual(got[1], {"threw": "KB_CLICK_REFUSED:ambiguous"})
        self.assertEqual(got[2], {"threw": "KB_CLICK_REFUSED:ambiguous"})
        self.assertTrue(got[3]["ok"])
        self.assertEqual(got[4], {"threw": "KB_CLICK_NOTFOUND:Save"})
        self.assertEqual(got[5], {"threw": "KB_CLICK_REFUSED:write_verb"})                # unchanged

    def test_selector_constants(self):
        for part in (".modal.show", "[role=dialog]", ".swal2-popup"):
            self.assertIn(part, C.DIALOG_SEL)
            self.assertIn(part, C.click_source())


OPT_SELECTORS = ["[role=listbox] [role=option]", ".multiselect__content li",
                 ".dropdown-menu .dropdown-item", ".vs__dropdown-option", "ul[role=listbox] li"]


class Kbopt(unittest.TestCase):
    def run_opt(self, cases):
        drv = DRIVER.replace("sc.src", "sc.src")        # same fake DOM; the source is the option picker
        r = subprocess.run([NODE, "-e", drv], input=json.dumps({"src": C.option_source(), "cases": cases}),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_selectors_and_install(self):
        for sel in OPT_SELECTORS:
            self.assertIn(sel, C.OPTION_SEL)
        self.assertIn(C.OPTION_SEL, C.option_source())
        text = C.install_action()["text"]
        self.assertIn("sessionStorage.setItem('kbo',O)", text)
        self.assertIn(json.dumps(C.option_source()), text)
        self.assertTrue(C.stub({"option": "x"}, C.KBO_KEY).startswith("/*KB*/(sessionStorage.getItem('kbo')?"))

    def test_mirror(self):
        for t, want in (("ZZ DEMO Venue", ""), ("Cancel", ""), ("No, don't send notifications", ""),
                        ("No, send notifications", "write_danger"), ("No notify", "write_danger"),
                        ("Don't send", ""), ("Do not notify", ""), ("No, invite everyone", "write_danger"),
                        ("Send notifications", "write_danger"), ("Invite everyone", "write_danger"),
                        ("Delete", "write_danger"), ("Publish", "write_danger"), ("Approve", "write_danger"),
                        ("Notify participants", "write_danger"), ("Archive", "write_danger")):
            self.assertEqual(C.option_refusal(t), want, t)

    @unittest.skipUnless(NODE, "node not installed")
    def test_js(self):
        a, b = el("li", "ZZ DEMO Venue A"), el("li", "ZZ DEMO Venue B")
        hid = el("li", "ZZ DEMO Venue A", visible=False)
        a2 = el("li", "ZZ DEMO Venue A")
        send = el("li", "Send notifications")
        no = el("li", "No, don't send notifications")
        o = lambda opt, els, **k: {"els": els, "args": {"option": opt}, "options": list(range(len(els))), **k}
        got = self.run_opt([
            o("ZZ DEMO Venue B", [a, b]),
            o("zz demo  venue b", [a, b]),                 # trimmed / case-insensitive, still exact
            o("ZZ DEMO Venue", [a, b]),                    # a prefix is not a match
            o("ZZ DEMO Venue A", [hid]),                   # invisible
            o("ZZ DEMO Venue A", [a, a2]),                 # two matches
            o("Send notifications", [send]),
            o("No, don't send notifications", [no]),
            o("ZZ DEMO Venue A", [a], occlude=True),
            o("ZZ DEMO Venue A", [a], options=[]),         # not inside an open listbox
            o("", [a]),
        ])
        self.assertEqual(got[0], {"ok": True, "x": 120, "y": 60, "label": "ZZ DEMO Venue B"})
        self.assertEqual(got[1], got[0])
        self.assertEqual([g.get("threw") for g in got[2:]],
                         ["KB_CLICK_NOTFOUND:ZZ DEMO Venue", "KB_CLICK_NOTFOUND:ZZ DEMO Venue A",
                          "KB_CLICK_REFUSED:ambiguous", "KB_CLICK_REFUSED:write_danger", None,
                          "KB_CLICK_REFUSED:occluded", "KB_CLICK_NOTFOUND:ZZ DEMO Venue A",
                          "KB_CLICK_NOTFOUND:option"])
        self.assertTrue(got[6]["ok"])                      # "No, ..." is allowed

    def test_error_codes_map(self):
        self.assertEqual(C.click_reason("Error: KB_CLICK_REFUSED:write_danger"), ("click_refused", "write_danger"))


CLICK_PLAN = {"run_id": "r1", "capture_plan": [
    {"persona": "admin", "surface": "Sessions", "url": "/administration/curriculum/sessions/",
     "clicks": [{"row": True}, {"text": "View"}, {"text": "Add Schedule"}],
     "write_gated": "confirm dialog after Save", "tickets": ["M1-465"], "route_source": "url_page"},
    {"persona": "admin", "surface": "Users", "url": "/users/", "clicks": [], "tickets": ["M1-1"]},
]}


CHOOSE_LISTS = [
    (["Yes, send notifications", "No, don't send notifications", "Cancel"], "No, don't send notifications"),
    (["Cancel", "Yes", "No, don't send notifications"], "No, don't send notifications"),
    (["OK", "Cancel"], "Cancel"),
    (["Close", "Continue"], "Close"),
    (["Yes", "No"], "No"),
    (["Cancel", "No"], "No"),                       # a bare "No" ranks above Cancel (it sends the request)
    (["Skip", "Do not send"], "Do not send"),
    (["Yes, send notifications", "Save"], None),
    (["OK", "Continue", "Confirm", "Proceed"], None),
    (["Send now", "Notify participants"], None),
    (["No, send notifications", "Yes"], None),     # "No," in front of send is not a negation
    (["Invite everyone", "Delete it"], None),
    ([], None),
]


@unittest.skipUnless(NODE, "node not installed")
class ChooseMode(unittest.TestCase):
    """0.28.0 dry run: kbclick {"choose": true} picks the safe answer inside the open dialog by
    rule (negated answer, bare No, Cancel / Close / Skip) so the data maker never decides it."""

    def pick(self, buttons, outside=("Save",)):
        dom = {"dialog": {"text": "Notify Participants? " + " ".join(buttons), "buttons": list(buttons)},
               "outside": list(outside)}
        r = run_dom("const k=(0,eval)(CLICK);try{return k({side:'nav',choose:true,write:['Save']});}"
                    "catch(e){return {err:e.message};}", dom)
        return r["out"]

    def test_the_page_picks_by_rule_and_the_python_mirror_agrees(self):
        for buttons, want in CHOOSE_LISTS:
            with self.subTest(buttons):
                out = self.pick(buttons)
                if want is None:
                    self.assertIn("err", out)
                    self.assertTrue(out["err"].startswith("KB_CLICK_NOTFOUND:"), out)
                else:
                    self.assertEqual((out.get("ok"), out.get("label")), (True, want))
                i = C.choose_pick(buttons)
                self.assertEqual(None if i is None else buttons[i], want)

    def test_no_dialog_means_nothing_to_choose(self):
        r = run_dom("const k=(0,eval)(CLICK);try{return k({side:'nav',choose:true});}catch(e){return {err:e.message};}",
                    {"outside": ["Cancel"]})
        self.assertEqual(r["out"], {"err": "KB_CLICK_NOTFOUND:dialog"})

    def test_it_never_picks_outside_the_dialog_or_arms_anything(self):
        out = self.pick(["Yes", "OK"], outside=("Cancel", "No, don't send notifications"))
        self.assertEqual(out, {"err": "KB_CLICK_NOTFOUND:choose"})
        r = run_dom("const k=(0,eval)(CLICK);const o=k({side:'nav',choose:true,write:['Save']});"
                    "try{await fetch('/api/x/',{method:'POST'});return 'sent';}catch(e){return [o.ok,e.message,window.__kbA];}")
        self.assertEqual(r["out"], [True, "KB_WRITE_BLOCKED", None])            # choosing alone never lifts the guard

    def test_the_answer_texts_agree_with_the_data_planners_rule(self):
        import kb_data as D
        for text in ("Cancel", "Close", "No", "No, don't send notifications", "Don't send", "Skip", "Do not notify"):
            self.assertIsNotNone(C.choose_rank(text), text)
            self.assertEqual(D.choose_problem(text), "", text)
        for text in ("Yes", "OK", "Continue", "Confirm", "Send", "Save", "Cancel and confirm", "No, send notifications"):
            self.assertIsNone(C.choose_rank(text), text)
            self.assertNotEqual(D.choose_problem(text), "", text)


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
        ok = [{"name": "navigate", "input": {"url": "/x/"}},
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

    def maker(self, tool, action=None, text=None, agent="medtrics-release-notes:kb-data-maker"):
        inp = {}
        if action:
            inp["action"] = action
        if text is not None:
            inp["text"] = text
        return run_hook({"tool_name": f"mcp__claude-in-chrome__{tool}", "agent_type": agent,
                         "tool_input": inp})

    def denied(self, result):
        rc, out = result
        self.assertEqual(rc, 0)
        o = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(o["permissionDecision"], "deny")
        self.assertIn("KB_GUARD", o["permissionDecisionReason"])

    def test_data_maker_may_type_fill_and_tab(self):
        self.assertEqual(self.maker("computer", "type", "ZZ KB DEMO"), (0, ""))
        self.assertEqual(self.maker("form_input"), (0, ""))
        self.assertEqual(self.maker("computer", "key", "Tab"), (0, ""))
        self.assertEqual(self.maker("computer", "key", "Escape"), (0, ""))
        for action in ("left_click", "hover", "wait", "scroll"):
            self.assertEqual(self.maker("computer", action), (0, ""))

    def test_data_maker_cannot_press_other_keys_or_drag(self):
        for text in ("Enter", "Return", "Tab Enter", "Escape Enter", "tab", "escape", "Esc", "ctrl+a", "Backspace",
                     "Delete", "", None):
            self.denied(self.maker("computer", "key", text))
        self.denied(self.maker("computer", "left_click_drag"))

    def test_data_maker_batch(self):
        ok = [{"name": "computer", "input": {"action": "type", "text": "x"}},
              {"name": "form_input", "input": {"ref": "r", "value": "x"}},
              {"name": "computer", "input": {"action": "key", "text": "Tab"}},
              {"name": "computer", "input": {"action": "key", "text": "Escape"}}]
        agent = "medtrics-release-notes:kb-data-maker"
        self.assertEqual(self.batch(ok, agent=agent), (0, ""))
        for bad in ({"name": "computer", "input": {"action": "key", "text": "Enter"}},
                    {"name": "computer", "input": {"action": "left_click_drag"}}):
            self.denied(self.batch(ok + [bad], agent=agent))
        # the capturer rules did not loosen
        self.denied(self.batch(ok))

    AGENTS = ("medtrics-release-notes:kb-capturer", "medtrics-release-notes:kb-data-maker")

    def tool(self, name, inp, agent):
        return run_hook({"tool_name": f"mcp__claude-in-chrome__{name}", "agent_type": agent, "tool_input": inp})

    def test_typed_text_with_newline_return_or_tab_is_denied(self):
        for agent in self.AGENTS:
            for text in ("a\nb", "a\rb", "a\tb", "\n", "x\r\n"):
                self.denied(self.tool("computer", {"action": "type", "text": text}, agent))
                self.denied(self.batch([{"name": "computer", "input": {"action": "type", "text": text}}], agent=agent))
        maker = self.AGENTS[1]
        for text in ("ZZ DEMO", "09:00", "2026-12-31", "two words", ""):
            self.assertEqual(self.tool("computer", {"action": "type", "text": text}, maker), (0, ""), text)
        self.assertEqual(self.tool("computer", {"action": "type", "text": "a\nb"}, "general-purpose"), (0, ""))

    def test_javascript_needs_the_marker(self):
        good = {"action": "javascript_exec", "text": C.stub({"x": 1})}
        for agent in self.AGENTS:
            self.assertEqual(self.tool("javascript_tool", good, agent), (0, ""))
            self.assertEqual(self.tool("javascript_tool", {"text": C.install_action()["text"]}, agent), (0, ""))
            self.assertEqual(self.tool("javascript_tool",
                                       {"text": C.route_check_source(["/a/"], "/users/login/")}, agent), (0, ""))
            for bad in ("document.cookie", " /*KB*/x", "x /*KB*/", "/* KB */x", "//*KB*/\nx", "", None):
                self.denied(self.tool("javascript_tool", {"action": "javascript_exec", "text": bad}, agent))
            self.denied(self.tool("javascript_tool", {"action": "javascript_exec"}, agent))
            self.denied(self.batch([{"name": "javascript_tool", "input": {"text": "fetch('/x',{method:'POST'})"}}],
                                   agent=agent))
            self.assertEqual(self.batch([{"name": "javascript_tool", "input": good}], agent=agent), (0, ""))
        self.assertEqual(self.tool("javascript_tool", {"text": "document.cookie"}, "general-purpose"), (0, ""))
        self.assertEqual(self.tool("javascript_tool", {"text": "document.cookie"}, None), (0, ""))

    def test_generated_scripts_all_carry_the_marker(self):
        import kb_data as D
        self.assertTrue(C.install_action()["text"].startswith(C.JS_MARKER))
        self.assertEqual(C.JS_MARKER, "/*KB*/")
        for key in (C.KEY, C.KBC_KEY, C.KBO_KEY):
            self.assertTrue(C.stub({}, key).startswith("/*KB*/"))
        self.assertTrue(C.route_check_source(["/a/"], "/l/").startswith("/*KB*/await "))
        self.assertTrue(D.field_js("A", "b").startswith("/*KB*/"))
        self.assertTrue(D.field_js("A", mode="read").startswith("/*KB*/"))

    def test_navigate_stays_on_the_demo_site(self):
        routes = json.loads((HOOK.parent.parent / "scripts" / "kb" / "kb_routes.json").read_text())["demo"]
        host = routes["base_url"]
        entries = list(routes["portal_entry"].values())
        ok = ["/", "/administration/", "/vadministration/curriculum/sessions/", host + "/administration/",
              host + "/x/?a=1#frag", "http://medtrics.medtricslab.com/x/", "medtrics.medtricslab.com/x/"] + \
             entries + [host + e for e in entries]
        bad = ["https://evil.example/", "http://evil.example/administration/", "//evil.example/x", "/\\evil.example",
               "https://medtrics.medtricslab.com.evil.example/", "https://medtrics.medtricslab.com@evil.example/",
               "evil.example/x", "x", "", "javascript:alert(1)", "data:text/html,x", "file:///etc/passwd",
               "ftp://medtrics.medtricslab.com/", "/x/\n", host + "/x/\n"]
        for agent in self.AGENTS:
            for u in ok:
                self.assertEqual(self.tool("navigate", {"url": u}, agent), (0, ""), (agent, u))
            for u in bad:
                self.denied(self.tool("navigate", {"url": u}, agent))
                self.denied(self.batch([{"name": "navigate", "input": {"url": u}}], agent=agent))
            self.denied(self.tool("navigate", {}, agent))
        self.assertEqual(self.tool("navigate", {"url": "https://evil.example/"}, "general-purpose"), (0, ""))

    def test_navigate_refuses_danger_paths_except_the_portal_entries(self):
        routes = json.loads((HOOK.parent.parent / "scripts" / "kb" / "kb_routes.json").read_text())["demo"]
        host = routes["base_url"]
        bad = ["/users/logout/", "/users/signout", "/x/delete/3/", "/x/remove/3/", "/x/%64elete/3/", "/x/LOGOUT/",
               "/?portal_change=other", "/?portal_change=administration&x=1", "/?portal_change=personal#x",
               "/administration/?portal_change=administration", "/x/?next=/users/logout/",
               host + "/users/logout/", host + "/?portal_change=evil", "/?portal_change=ADMINISTRATION"]
        for agent in self.AGENTS:
            for u in bad:
                if u.endswith("#x"):          # the fragment never reaches the server: the entry itself
                    continue
                self.denied(self.tool("navigate", {"url": u}, agent))
            for u in (routes["portal_entry"]["admin"], routes["portal_entry"]["personal"] if "personal" in routes["portal_entry"]
                      else routes["portal_entry"]["faculty"], host + routes["portal_entry"]["admin"],
                      routes["portal_entry"]["admin"] + "#top"):
                self.assertEqual(self.tool("navigate", {"url": u}, agent), (0, ""), u)

    def test_never_crashes(self):
        for raw in ("", "not json", "[]", "null", '{"tool_name": 5, "agent_type": ["kb-capturer"]}',
                    '{"agent_type": "kb-capturer", "tool_input": null, "tool_name": "x__computer"}'):
            rc, out = run_hook(None, raw)
            self.assertEqual((rc, out), (0, ""), raw)

    def test_hooks_json(self):
        h = json.loads(HOOKS_JSON.read_text())["hooks"]["PreToolUse"][0]
        self.assertEqual(h["matcher"], "mcp__claude-in-chrome__computer|mcp__claude-in-chrome__form_input"
                                       "|mcp__claude-in-chrome__browser_batch|mcp__claude-in-chrome__javascript_tool"
                                       "|mcp__claude-in-chrome__navigate")
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/hooks/kb_guard.py", h["hooks"][0]["command"])

    def test_hook_writes_nothing(self):
        src = HOOK.read_text()
        for bad in ("open(", "write(", "makedirs", "mkdir"):
            self.assertNotIn(bad, src)


if __name__ == "__main__":
    unittest.main()
