"""Tests for the 0.22.1 checkbox click and surface click paths: kbclick {"check": label}
(Python mirror check_refusal() and the JS, run in node against a fake DOM), a surface's
own "clicks" in kb_routes.json (M1-1520's Migrate tab path), plan-calls / kb_data with a
check click, and the Windows fixes (UTF-8 relaunch, LibreOffice / tesseract lookup).
Hermetic: no browser, no network.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "kb"))
sys.path.insert(0, str(ROOT / "scripts"))

import kb_capture as C
import kb_data as KD
import kb_doctor as DOC
import kb_pdf
import kb_select as S
import utf8_mode

NODE = shutil.which("node")
ROUTES = json.loads((ROOT / "scripts" / "kb" / "kb_routes.json").read_text(encoding="utf-8"))


def box(**kw):
    b = {"attrs": {"type": "checkbox"}, "parent_class": "", "in_dialog": False, "same_form": True,
         "disabled": False, "checked": False}
    b.update(kw)
    return b


MIRROR = [
    ("plain box", "Sessions", box(), None, ""),
    ("unpublished is a box, not a verb", "Set migrated sessions to Unpublished", box(), None, ""),
    ("delete words", "Delete selected sessions", box(), None, "write_danger"),
    ("notify words", "Notify learners", box(), None, "write_danger"),
    ("negated notify", "Do not notify learners", box(), None, ""),
    ("onchange on box", "Sessions", box(attrs={"type": "checkbox", "onchange": "save()"}), None, "onclick"),
    ("onclick on label", "Sessions", box(), {"attrs": {"onclick": "x()"}}, "onclick"),
    ("switch class", "Active", box(attrs={"type": "checkbox", "class": "form-check-input switch"}), None, "switch"),
    ("switch parent", "Active", box(parent_class="form-check form-switch"), None, "switch"),
    ("role switch", "Active", box(attrs={"type": "checkbox", "role": "switch"}), None, "switch"),
    ("toggle label", "Active", box(), {"attrs": {"class": "toggle-label"}}, "switch"),
    ("danger class", "Sessions", box(attrs={"type": "checkbox", "class": "js-delete"}), None, "danger_class"),
    ("in dialog", "Sessions", box(in_dialog=True), None, "in_dialog"),
    ("other form", "Sessions", box(same_form=False), None, "other_form"),
    ("disabled", "Session Schedules", box(disabled=True), None, "disabled"),
    ("already ticked", "Sessions", box(checked=True), None, "already_checked"),
]


class CheckMirror(unittest.TestCase):
    def test_rules(self):
        for name, text, b, lab, want in MIRROR:
            with self.subTest(name):
                self.assertEqual(C.check_refusal(text, b, lab), want)


# A fake DOM with only what kbclick's check branch touches: <label> elements, each with
# one checkbox control.
DRIVER = r"""
const fs=require('fs');const sc=JSON.parse(fs.readFileSync(0,'utf8'));
const attrs=o=>Object.keys(o||{}).map(k=>({name:k,value:String(o[k])}));
const rect=v=>v?{left:100,top:50,width:40,height:20,right:140,bottom:70}:{left:0,top:0,width:0,height:0,right:0,bottom:0};
const run=(c)=>{
 const form={};const other={};
 const labels=c.labels.map(l=>{
  const b=l.box;const bx={tagName:'INPUT',type:'checkbox',checked:!!b.checked,disabled:!!b.disabled,
   form:b.same_form===false?other:(l.in_form===false?null:form),labels:{length:b.labels||1},
   parentElement:{className:b.parent_class||''},attributes:attrs(b.attrs),
   getAttribute:n=>n in (b.attrs||{})?String(b.attrs[n]):null,
   closest:s=>s.indexOf('modal.show')>=0&&b.in_dialog?{}:null,
   getBoundingClientRect:()=>rect(b.visible!==false),contains:x=>x===bx,
   scrollIntoView(){global.cur=this;}};
  const lab={tagName:'LABEL',textContent:' '+l.text+' ',innerText:l.text,control:l.no_control?null:bx,
   attributes:attrs(l.attrs),getAttribute:n=>n in (l.attrs||{})?String(l.attrs[n]):null,
   cloneNode:()=>({querySelectorAll:()=>[],textContent:' '+l.text+' '}),
   closest:s=>s==='form'?(l.in_form===false?null:form):null,
   getBoundingClientRect:()=>rect(l.visible!==false),contains:x=>x===lab||x===bx,
   scrollIntoView(){global.cur=this;}};
  return lab;});
 global.getComputedStyle=()=>({cursor:''});
 global.window=global;global.__VUE__=undefined;
 global.document={querySelectorAll:s=>s==='label'?labels:[],querySelector:()=>null,
  elementFromPoint:()=>c.occlude==='label'?labels[0]:c.occlude?{contains:()=>false}:global.cur};
 global.sessionStorage={getItem:()=>null};
 const f=(0,eval)(sc.src);
 try{return f(c.args);}catch(e){return {threw:e.message};}
};
console.log(JSON.stringify(sc.cases.map(run)));
"""


@unittest.skipUnless(NODE, "node not installed")
class CheckJs(unittest.TestCase):
    def run_cases(self, cases):
        r = subprocess.run([NODE, "-e", DRIVER], input=json.dumps({"src": C.click_source(), "cases": cases}),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_js_agrees_with_the_mirror(self):
        cases = [{"labels": [{"text": text, "box": b, "attrs": (lab or {}).get("attrs") or {}}],
                  "args": {"side": "nav", "check": text}} for _, text, b, lab, _ in MIRROR]
        for (name, *_, want), got in zip(MIRROR, self.run_cases(cases)):
            with self.subTest(name):
                self.assertEqual(got.get("threw", ""), "KB_CLICK_REFUSED:" + want if want else "")
                if not want:
                    self.assertTrue(got["ok"])

    def test_js_finds_the_label_and_returns_its_words(self):
        [got] = self.run_cases([{"labels": [{"text": "Courses", "box": box()},
                                            {"text": "Sessions", "box": box()}],
                                 "args": {"side": "nav", "check": "sessions"}}])
        self.assertEqual((got["ok"], got["label"]), (True, "sessions"))

    def test_js_not_found_ambiguous_hidden_and_shared_label(self):
        got = self.run_cases([
            {"labels": [{"text": "Courses", "box": box()}], "args": {"side": "nav", "check": "Sessions"}},
            {"labels": [{"text": "Sessions", "box": box()}, {"text": "Sessions", "box": box()}],
             "args": {"side": "nav", "check": "Sessions"}},
            {"labels": [{"text": "Sessions", "box": box(), "visible": False}],
             "args": {"side": "nav", "check": "Sessions"}},
            {"labels": [{"text": "Sessions", "box": {**box(), "labels": 2}}],
             "args": {"side": "nav", "check": "Sessions"}},
            {"labels": [{"text": "Sessions", "box": box(), "no_control": True}],
             "args": {"side": "nav", "check": "Sessions"}},
        ])
        self.assertEqual(got[0]["threw"], "KB_CLICK_NOTFOUND:Sessions")
        self.assertEqual(got[1]["threw"], "KB_CLICK_REFUSED:ambiguous")
        self.assertEqual(got[2]["threw"], "KB_CLICK_NOTFOUND:Sessions")     # a label on a hidden tab
        self.assertEqual(got[3]["threw"], "KB_CLICK_NOTFOUND:Sessions")     # a box with two labels
        self.assertEqual(got[4]["threw"], "KB_CLICK_NOTFOUND:Sessions")

    def test_js_post_form_does_not_refuse_a_box_but_a_hidden_box_clicks_its_label(self):
        got = self.run_cases([
            {"labels": [{"text": "Sessions", "box": {**box(), "visible": False}}],
             "args": {"side": "nav", "check": "Sessions"}},
            {"labels": [{"text": "Sessions", "box": box()}], "occlude": "label",
             "args": {"side": "nav", "check": "Sessions"}},
            {"labels": [{"text": "Sessions", "box": box()}], "occlude": True,
             "args": {"side": "nav", "check": "Sessions"}},
        ])
        self.assertTrue(got[0]["ok"])
        self.assertTrue(got[1]["ok"])                       # the point lands on the box's label: fine
        self.assertEqual(got[2]["threw"], "KB_CLICK_REFUSED:occluded")

    def test_js_text_click_still_refuses_the_same_words(self):
        """A {"text"} click on "Set migrated sessions to Unpublished" stays a write verb."""
        [got] = self.run_cases([{"labels": [], "args": {"side": "nav", "text": "Set migrated sessions to Unpublished"}}])
        self.assertEqual(got["threw"], "KB_CLICK_REFUSED:write_verb")


class SurfaceClicks(unittest.TestCase):
    MIGRATE = [{"text": "Migrate Curriculum"}, {"check": "Courses"}, {"check": "Sessions"},
               {"check": "Session Schedules"}, {"check": "Set migrated sessions to Unpublished"}]

    def test_m1_1520_gets_the_migrate_tab_path(self):
        s, how = S.place_issue({"identifier": "M1-1520", "title": "t", "description": ""}, ROUTES)
        p = S._placement({"identifier": "M1-1520"}, s, how, ROUTES)
        self.assertEqual(p["route"], {"start": "/administration/curriculum/", "clicks": self.MIGRATE,
                                      "write_gated": "", "source": "surface"})
        plan = S.group([p], ROUTES)
        entry = next(c for c in (plan.get("capture_plan") or plan.get("capture") or [])
                     if "M1-1520" in c["tickets"]) if isinstance(plan, dict) else \
            next(c for c in plan[0] if "M1-1520" in c["tickets"])
        self.assertEqual(entry["clicks"], self.MIGRATE)

    def test_a_ticket_route_without_clicks_on_the_page_takes_the_surface_path(self):
        route = {"start": "/administration/curriculum/", "clicks": [], "write_gated": "", "source": "url_page"}
        self.assertEqual(S._route_surface(route, ROUTES)["route"]["clicks"], self.MIGRATE)
        own = {**route, "clicks": [{"text": "Other"}]}
        self.assertEqual(S._route_surface(own, ROUTES)["route"]["clicks"], [{"text": "Other"}])
        deeper = {**route, "start": "/administration/curriculum/sessions/"}
        self.assertEqual(S._route_surface(deeper, ROUTES)["route"]["clicks"], [])

    def test_every_surface_click_in_the_table_is_safe(self):
        for s in ROUTES["surfaces"]:
            for k in s.get("clicks") or []:
                with self.subTest(surface=s["surface"], click=k):
                    self.assertEqual(len(k), 1)
                    self.assertTrue(set(k) <= {"text", "check", "row"})
                    if "text" in k:
                        self.assertEqual(C.click_refusal({"tag": "a", "text": k["text"],
                                                          "attrs": {"href": "#t", "data-bs-toggle": "tab"}}), "")
                    if "check" in k:
                        self.assertEqual(C.check_refusal(k["check"], box()), "")


class PlanWithChecks(unittest.TestCase):
    def test_click_spec_labels_and_args(self):
        self.assertEqual(C._click_spec({"check": "Sessions"}), {"check": "Sessions"})
        clicks = [{"text": "Migrate Curriculum"}, {"check": "Courses"}]
        self.assertEqual(C.step_labels(clicks, 0, "Curriculum", ["x"]), ["Curriculum", "Migrate Curriculum"])
        self.assertEqual(C.step_labels(clicks, 1, "Curriculum", ["x"]), ["Courses"])
        self.assertEqual(C.step_labels(clicks, 2, "Curriculum", ["x"]), ["x"])

    def test_kb_data_keeps_ticks_and_checks_their_words(self):
        self.assertEqual(KD._click_specs([{"text": "Migrate Curriculum"}, {"check": "Sessions"}, {"row": True}]),
                         [{"text": "Migrate Curriculum"}, {"check": "Sessions"}, {"row": True}])
        self.assertEqual(KD.click_json("nav", check="Sessions"), {"side": "nav", "check": "Sessions"})
        self.assertTrue(KD.forbidden_in("Delete all sessions"))
        line = KD.preview_line("M1-1520", {"start": "/a/", "path": [{"text": "Tab"}, {"check": "Sessions"}, {"row": True}],
                                           "fields": [], "mode": KD.DRY, "id": "r1", "what": "x", "persona": "admin", "save": "Save"})
        self.assertIn('/a/ → "Tab" → tick "Sessions" → first row', line)


class Utf8Mode(unittest.TestCase):
    def test_reruns_on_windows_without_utf8_mode(self):
        fake = mock.Mock(flags=mock.Mock(utf8_mode=0), executable="py.exe", argv=["kb_select.py", "place"])
        fake.exit.side_effect = SystemExit
        with mock.patch.object(utf8_mode, "sys", fake), mock.patch.object(utf8_mode.os, "name", "nt"), \
                mock.patch.object(utf8_mode.subprocess, "call", return_value=3) as call:
            with self.assertRaises(SystemExit):
                utf8_mode.ensure()
        args, kw = call.call_args
        self.assertEqual(args[0], ["py.exe", "-X", "utf8", "kb_select.py", "place"])
        self.assertEqual(kw["env"]["PYTHONUTF8"], "1")
        fake.exit.assert_called_once_with(3)

    def test_does_nothing_elsewhere_or_when_on(self):
        with mock.patch.object(utf8_mode.subprocess, "call") as call:
            with mock.patch.object(utf8_mode.os, "name", "posix"):
                utf8_mode.ensure()
            fake = mock.Mock(flags=mock.Mock(utf8_mode=1))
            with mock.patch.object(utf8_mode, "sys", fake), mock.patch.object(utf8_mode.os, "name", "nt"):
                utf8_mode.ensure()
        call.assert_not_called()

    def test_every_entry_script_calls_it_first(self):
        found = [p for d in ("scripts/release", "scripts/kb", "tools") for p in sorted((ROOT / d).glob("*.py"))]
        self.assertGreater(len(found), 30)
        for p in found:
            src = p.read_text(encoding="utf-8")
            if 'if __name__ == "__main__":' not in src:
                continue
            with self.subTest(p.name):
                block = src.split('if __name__ == "__main__":', 1)[1]
                self.assertLess(block.index("utf8_mode.ensure()"), block.index("main("))


class WindowsTools(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def exe(self, *parts):
        p = self.tmp.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("")
        return str(p)

    def test_soffice_from_kb_soffice_then_program_files(self):
        own = self.exe("x", "soffice.exe")
        pf = self.exe("PF", "LibreOffice", "program", "soffice.exe")
        with mock.patch.object(kb_pdf.shutil, "which", return_value=None), \
                mock.patch.dict(os.environ, {"KB_SOFFICE": f'"{own}"', "ProgramFiles": str(self.tmp / "PF")}):
            self.assertEqual(kb_pdf.soffice(), own)
        real = os.path.isfile
        with mock.patch.object(kb_pdf.shutil, "which", return_value=None), \
                mock.patch.object(kb_pdf.os.path, "isfile", lambda p: real(p) and not p.startswith("/Applications")), \
                mock.patch.dict(os.environ, {"KB_SOFFICE": "", "KB_HOME": str(self.tmp),
                                             "ProgramFiles": str(self.tmp / "PF")}):
            self.assertEqual(kb_pdf.soffice(), pf)       # (a Mac with LibreOffice installed would win otherwise)
            (self.tmp / ".env").write_text(f'KB_SOFFICE="{own}"\n')
            self.assertEqual(kb_pdf.soffice(), own)      # the folder's .env counts too

    def test_tesseract_off_path_is_added_to_path(self):
        t = self.exe("LA", "Programs", "Tesseract-OCR", "tesseract.exe")
        env = {k: v for k, v in os.environ.items() if k not in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)")}
        env.update({"LOCALAPPDATA": str(self.tmp / "LA"), "PATH": "/usr/bin"})
        with mock.patch.object(kb_pdf.shutil, "which", return_value=None), mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(kb_pdf.tesseract(), t)
            self.assertTrue(os.environ["PATH"].startswith(os.path.dirname(t) + os.pathsep))

    def test_doctor_names_the_winget_package_on_windows(self):
        with mock.patch.object(DOC.shutil, "which", return_value=None), \
                mock.patch.object(DOC, "_privileged", return_value=None), mock.patch.object(DOC.os, "name", "nt"):
            out = DOC.install_system(["LibreOffice", "tesseract"])
        self.assertIn("winget install --id TheDocumentFoundation.LibreOffice -e", out["LibreOffice"])
        self.assertIn("winget install --id UB-Mannheim.TesseractOCR -e", out["tesseract"])


if __name__ == "__main__":
    unittest.main()
