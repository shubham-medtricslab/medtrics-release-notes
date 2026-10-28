"""Tests for the Chrome-extension capture path of scripts/kb/kb_capture.py:
`plan-calls` (browser_batch actions + capture.md), the generated probe (run
against a tiny DOM stub when node is on PATH) and `ingest` (raw files ->
shots.json). Hermetic: no browser, no network, Pillow-generated JPEG frames;
OCR is never used (--no-ocr / dom_box anchors).

Pins: per-visit action order start_recording -> navigate -> wait -> hover(cursor
park) -> probe stub -> stop_recording -> export (GIF capture, see
tests/test_kb_gif_capture.py); the persona's first batch starts with the portal entry;
batches hold --per-batch visits; labels reach the JS only through json.dumps;
visits without a URL are not planned; capture.md carries every batch and the
restore actions; the tenant-screen JS regexes are the Python patterns; ingest
maps CSS boxes to image px by width/innerWidth, treats out-of-view/occluded
as unverified, records the sidebar flag, accepts a JSON-string probe, sends a
blocked probe to `retry`, writes resume.json (exit 3) when anything is left,
is idempotent by visit_id, rejects aspect mismatches and BLOCK tenants.
"""

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kb_fixtures import has_module

import kb_capture as C
import qa_gif_to_shots as Q

ROUTES_PATH = Path(C.__file__).parent / "kb_routes.json"
ROUTES = json.loads(ROUTES_PATH.read_text())
NASTY = 'Say "hi" \\ it\'s </script> ${x} `t`'

PLAN = {"run_id": "r1", "capture_plan": [
    {"persona": "admin", "surface": "Users", "url": "/users/", "recipe": [],
     "tickets": ["M1-1", "M1-2"], "session": "valid"},
    {"persona": "admin", "surface": "Groups", "url": "/groups/", "recipe": [],
     "tickets": ["M1-3"], "session": "valid"},
    {"persona": "admin", "surface": "NoUrl", "url": "", "recipe": [], "tickets": ["M1-9"]},
    {"persona": "admin", "surface": "Roles", "url": "/roles/", "recipe": [], "tickets": ["M1-4"]},
    {"persona": "admin", "surface": "Rooms", "url": "/rooms/", "recipe": [], "tickets": ["M1-5"]},
    {"persona": "faculty", "surface": "Notes", "url": "/notes/", "recipe": [], "tickets": ["M1-6"]},
]}
INPUTS = {"M1-1": {"labels": ["Groups", NASTY]}, "M1-2": {"labels": ["Groups", "Roles"]},
          "M1-3": {"labels": ["Save"]}, "M1-4": {"labels": []}, "M1-5": {"labels": ["Add"]},
          "M1-6": {"labels": ["Notes"]}}


def run_cli(*argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = C.main([str(a) for a in argv])
    out = buf.getvalue()
    return rc, json.loads(out.split("---KB_CAPTURE_RESULT---")[1]), out


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def plan_calls(self, plan=PLAN, per_batch=3):
        (self.tmp / "plan.json").write_text(json.dumps(plan))
        (self.tmp / "inputs.json").write_text(json.dumps(INPUTS))
        self.shots = self.tmp / "shots"
        rc, res, _ = run_cli("plan-calls", "--plan", self.tmp / "plan.json",
                             "--inputs", self.tmp / "inputs.json", "--out-dir", self.shots,
                             "--per-batch", per_batch)
        self.assertEqual(rc, 0)
        return json.loads((self.shots / "calls.json").read_text()), res


class PlanCalls(Base):
    def test_actions_shape_and_order(self):
        calls, res = self.plan_calls()
        self.assertEqual((res["visits"], res["batches"], res["skipped"]), (5, 3, 1))
        b = calls["batches"][0]
        names = [(a["name"], a["input"].get("action")) for a in b["actions"]]
        entry = [("navigate", None), ("computer", "wait")]
        visit = [("gif_creator", "start_recording"), ("navigate", None), ("computer", "wait"),
                 ("computer", "hover"), ("javascript_tool", "javascript_exec"),
                 ("gif_creator", "stop_recording"), ("gif_creator", "export")]
        self.assertEqual(names, entry + visit * 3)
        for a in b["actions"]:
            self.assertEqual(a["input"]["tabId"], "$TAB")
        acts = b["actions"]
        self.assertTrue(acts[0]["input"]["url"].endswith("/?portal_change=administration"))
        self.assertEqual(acts[1]["input"]["duration"], 2)
        self.assertEqual(acts[5]["input"]["coordinate"], [1465, 740])
        self.assertEqual(acts[3]["input"]["url"], "https://medtrics.medtricslab.com/users/")
        self.assertNotIn("screenshot", [a["input"].get("action") for a in acts])

    def test_per_batch_and_persona_entry(self):
        calls, _ = self.plan_calls(per_batch=2)
        self.assertEqual([b["visit_ids"] for b in calls["batches"]],
                         [["v01", "v02"], ["v03", "v04"], ["v05"]])
        self.assertEqual([b["persona"] for b in calls["batches"]], ["admin", "admin", "faculty"])
        first = lambda b: next(a for a in b["actions"] if a["name"] == "navigate")["input"]["url"]
        self.assertIn("portal_change=administration", first(calls["batches"][0]))
        self.assertIn("/roles/", first(calls["batches"][1]))          # no entry mid-persona
        self.assertIn("portal_change=personal", first(calls["batches"][2]))
        self.assertEqual([v["visit_id"] for v in calls["visits"]], ["v01", "v02", "v03", "v04", "v05"])
        self.assertNotIn("NoUrl", [v["surface"] for v in calls["visits"]])

    def test_args_labels_and_stub(self):
        calls, _ = self.plan_calls()
        v = calls["visits"][0]
        self.assertEqual(v["labels"]["M1-1"], ["Groups", NASTY])
        a = v["args"]
        self.assertEqual(a["labels"], ["Groups", NASTY, "Roles"])      # de-duplicated union
        self.assertEqual((a["portal"], a["login"], a["domain"], a["v"]),
                         ("Admin", "/users/login/", "medtricslab.com", calls["probe_version"]))
        self.assertEqual(calls["visits"][4]["args"]["portal"], "Personal")
        stub = calls["batches"][0]["actions"][6]["input"]["text"]
        self.assertTrue(stub.endswith(")(" + json.dumps(a) + ")"))
        self.assertIn("KB_NOPROBE", stub)
        self.assertIn("(0,eval)(sessionStorage.getItem('kbp'))", stub)
        # the nasty label survives a JSON round trip and no raw quote breaks out
        args_text = stub[len(stub) - len(json.dumps(a)) - 1:-1]
        self.assertEqual(json.loads(args_text)["labels"][1], NASTY)

    def test_install_action(self):
        calls, _ = self.plan_calls()
        ins = calls["install"]
        self.assertEqual((ins["action"], ins["tabId"]), ("javascript_exec", "$TAB"))
        self.assertIn("sessionStorage.setItem('kbp',S)", ins["text"])
        self.assertTrue(ins["text"].endswith("'kbp:'+S.length"))
        self.assertTrue(ins["text"].startswith("var S=" + json.dumps(C.probe_source())))

    def test_restore_and_capture_md(self):
        calls, _ = self.plan_calls()
        self.assertTrue(calls["restore"][0]["input"]["url"].endswith("/?portal_change=administration"))
        md = (self.shots / "capture.md").read_text()
        for b in calls["batches"]:
            self.assertIn(json.dumps(b["actions"], indent=1), md)
            self.assertIn(b["batch_id"], md)
        self.assertIn(json.dumps(calls["install"], indent=1), md)
        self.assertIn(json.dumps(calls["restore"], indent=1), md)
        for word in ("$TAB", "raw/", "KB_LOGIN", "KB_PERSONA", "KB_NOPROBE", "bridge_down",
                     "NO_CHROME_TOOLS", "KB_CAPTURE_DONE"):
            self.assertIn(word, md)
        self.assertEqual(md.count("```json"), len(calls["batches"]) + 4)   # install, abort, raw example, restore

    def test_labels_capped(self):
        plan = {"run_id": "r", "capture_plan": [{"persona": "admin", "surface": "S", "url": "/s/",
                                                  "tickets": ["T"]}]}
        (self.tmp / "inputs.json").write_text(json.dumps({"T": {"labels": [f"L{i}" for i in range(20)]}}))
        (self.tmp / "plan.json").write_text(json.dumps(plan))
        run_cli("plan-calls", "--plan", self.tmp / "plan.json", "--inputs", self.tmp / "inputs.json",
                "--out-dir", self.tmp / "o")
        calls = json.loads((self.tmp / "o" / "calls.json").read_text())
        self.assertEqual(len(calls["visits"][0]["args"]["labels"]), C.MAX_LABELS)


class RoutesDemo(unittest.TestCase):
    def test_demo_keys(self):
        d = ROUTES["demo"]
        self.assertEqual(d["cursor_park"], [1465, 740])
        self.assertEqual(d["portal_entry"]["faculty"], "/?portal_change=personal")
        self.assertEqual(d["portal_label"], {"admin": "Admin", "faculty": "Personal"})
        self.assertEqual((d["home_portal"], d["domain"]), ("admin", "medtricslab.com"))
        self.assertIn("[class*=sidebar]", d["sidebar_selector"])


class ProbeSource(unittest.TestCase):
    def test_regex_sources_are_the_python_patterns(self):
        src = C.probe_source()
        t = C.tenant_regexes()
        self.assertEqual(t["inst"], [Q.INSTITUTION_RE.pattern, "i"])
        self.assertEqual(t["disc"], [Q.DISCIPLINE_RE.pattern, "i"])
        self.assertEqual(t["slug"], [Q.SLUG_RE.pattern, ""])
        self.assertEqual(t["email"], [Q.EMAIL_RE.pattern, ""])
        self.assertEqual(t["course"], [Q.COURSE_CODE_RE.pattern, ""])
        self.assertIn("var T=" + json.dumps(t) + ";", src)
        self.assertIn(f"nl>={Q.NONLATIN_MIN}", src)
        self.assertNotIn("__", src)
        self.assertTrue(src.startswith("(function(a){") and src.endswith("})"))

    def test_lengths_reported(self):
        # informational: the per-visit stub stays short, the install runs once
        a = {"v": 1, "login": "/users/login/", "portal": "Admin", "labels": ["A", "B", "C"],
             "side": ROUTES["demo"]["sidebar_selector"], "allow": ROUTES["demo"]["allow_strings"],
             "domain": "medtricslab.com"}
        self.assertLess(len(C.stub(a)), 600)


NODE = shutil.which("node")

DRIVER = r"""
const fs=require('fs');const sc=JSON.parse(fs.readFileSync(0,'utf8'));
class El{constructor(o){Object.assign(this,{tag:'div',className:'',innerText:'',children:[],parent:null,
 rect:{left:10,top:10,width:50,height:20,right:60,bottom:30},sb:false,bg:'rgba(0,0,0,0)'},o);
 this.textContent=this.textContent||this.innerText;}
 getBoundingClientRect(){return this.rect;}
 contains(o){for(let p=o;p;p=p.parent)if(p===this)return true;return false;}
 closest(){for(let p=this;p;p=p.parent)if(p.sb)return p;return null;}}
const els=sc.els.map(e=>new El(e));
els.forEach((e,i)=>{const d=sc.els[i];e.children=(d.kids||[]).map(k=>els[k]);e.children.forEach(c=>c.parent=e);});
const links=els.filter(e=>e.tag==='a');
const occl=sc.occlude!==undefined?els[sc.occlude]:null;
global.window=global;global.location={pathname:sc.path||'/users/',search:sc.search||''};
global.innerWidth=1470;global.innerHeight=802;global.devicePixelRatio=2;
global.getComputedStyle=e=>({backgroundColor:e.bg});
global.document={body:{innerText:sc.body,querySelectorAll:()=>els},
 querySelectorAll:s=>s.indexOf('portal_change')>=0?links:[],
 querySelector:s=>sc.password?{}:null,
 elementFromPoint:()=>occl||{contains:()=>false,__other:true}};
const f=(0,eval)(sc.src);
try{console.log(JSON.stringify(f(sc.args)));}catch(e){console.log(JSON.stringify({threw:e.message}));}
"""


@unittest.skipUnless(NODE, "node not installed")
class ProbeRuns(unittest.TestCase):
    def run_probe(self, els, body="Users list", portal=None, labels=(), **kw):
        args = {"v": 1, "login": "/users/login/", "portal": portal, "labels": list(labels),
                "side": "nav", "allow": ["Medtrics Medical Center"], "domain": "medtricslab.com"}
        sc = {"src": C.probe_source(), "args": args, "els": els, "body": body, **kw}
        r = subprocess.run([NODE, "-e", DRIVER], input=json.dumps(sc), capture_output=True,
                           text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_labels_sidebar_and_visibility(self):
        els = [{"innerText": "Users", "sb": True, "rect": {"left": 5, "top": 5, "width": 40, "height": 10,
                                                           "right": 45, "bottom": 15}},
               {"innerText": "Users", "rect": {"left": 300, "top": 100, "width": 60, "height": 20,
                                               "right": 360, "bottom": 120}},
               {"innerText": "Hidden", "rect": {"left": 0, "top": 0, "width": 0, "height": 0,
                                                "right": 0, "bottom": 0}},
               {"innerText": "Below", "rect": {"left": 10, "top": 790, "width": 50, "height": 40,
                                               "right": 60, "bottom": 830}}]
        # the stub's elementFromPoint returns something unrelated -> everything is occluded
        r = self.run_probe(els, labels=["Users", "Hidden", "Below", "Nope"], occlude=1)
        self.assertEqual((r["v"], r["u"], r["iw"], r["ih"], r["dpr"]), (1, "/users/", 1470, 802, 2))
        by = {x["l"]: x for x in r["labels"]}
        self.assertEqual(by["Users"]["b"], [300, 100, 60, 20])         # outside the sidebar
        self.assertEqual((by["Users"]["f"], by["Users"]["sb"], by["Users"]["oc"], by["Users"]["iv"]),
                         (True, False, False, True))
        self.assertFalse(by["Hidden"]["f"])
        self.assertFalse(by["Nope"]["f"])
        self.assertEqual((by["Below"]["f"], by["Below"]["iv"]), (True, False))
        self.assertEqual(r["tn"], {"v": "CLEAR", "k": []})

    def test_sidebar_only_and_occluded(self):
        els = [{"innerText": "Groups", "sb": True}]
        r = self.run_probe(els, labels=["Groups"])                     # elementFromPoint -> other
        x = r["labels"][0]
        self.assertEqual((x["f"], x["sb"], x["oc"]), (True, True, True))

    def test_login_and_persona(self):
        self.assertEqual(self.run_probe([], path="/users/login/")["threw"], "KB_LOGIN")
        self.assertEqual(self.run_probe([], password=True)["threw"], "KB_LOGIN")
        links = [{"tag": "a", "innerText": "Admin", "className": "btn btn-danger"},
                 {"tag": "a", "innerText": "Personal", "className": "btn"}]
        self.assertEqual(self.run_probe(links, portal="Personal")["threw"], "KB_PERSONA")
        self.assertNotIn("threw", self.run_probe(links, portal="Admin"))
        by_bg = [{"tag": "a", "innerText": "Admin", "bg": "rgb(10, 90, 200)"},
                 {"tag": "a", "innerText": "Personal", "bg": "rgb(255, 255, 255)"}]
        self.assertEqual(self.run_probe(by_bg, portal="Personal")["threw"], "KB_PERSONA")
        self.assertNotIn("threw", self.run_probe(by_bg, portal="Admin"))
        self.assertNotIn("threw", self.run_probe([], portal="Admin"))  # no portal links: no throw

    def test_tenant_screen(self):
        r = self.run_probe([], body="Contact jo@gmail.com for Medtrics Medical Center")
        self.assertEqual(r["tn"]["v"], "BLOCK")
        self.assertIn("foreign_email_domain", r["tn"]["k"])
        r = self.run_probe([], body="Mail a@medtricslab.com about MCAP5010")
        self.assertEqual((r["tn"]["v"], r["tn"]["k"]), ("REVIEW", ["course_codes"]))
        r = self.run_probe([], body="Welcome to Medtrics Medical Center")
        self.assertEqual(r["tn"]["v"], "CLEAR")                        # allow-listed
        for body in ("Mail a@medtricslab.com", "University of Somewhere", "x kfshmed y", "漢字漢字"):
            py = Q.screen_text(body, "medtricslab.com", allow_strings=["Medtrics Medical Center"])
            self.assertEqual(self.run_probe([], body=body)["tn"]["v"], py["verdict"], body)


def make_frame(path, w=1470, h=802):
    from PIL import Image
    Image.new("RGB", (w, h), (240, 240, 245)).save(path, "JPEG", quality=85)


def probe_for(labels=(), iw=1470, ih=802, tn="CLEAR", **kw):
    return {"v": 1, "u": "/users/", "iw": iw, "ih": ih, "dpr": 1, "labels": list(labels),
            "tn": {"v": tn, "k": []}, **kw}


def lab(l, box=(300, 100, 60, 20), f=True, t=None, iv=True, oc=False, sb=False):
    return {"l": l, "f": f, "t": l if t is None else t, "b": list(box), "iv": iv, "oc": oc, "sb": sb}


@unittest.skipUnless(has_module("PIL"), "Pillow not installed")
class Ingest(Base):
    def setUp(self):
        super().setUp()
        plan = {"run_id": "r1", "capture_plan": [
            {"persona": "admin", "surface": "Users", "url": "/users/", "tickets": ["M1-1"]},
            {"persona": "admin", "surface": "Groups", "url": "/groups/", "tickets": ["M1-3"]},
            {"persona": "faculty", "surface": "Notes", "url": "/notes/", "tickets": ["M1-6"]}]}
        self.calls, _ = self.plan_calls(plan)
        self.raw = self.tmp / "raw"
        self.raw.mkdir()
        self.png = {}
        for i in (1, 2, 3):
            p = self.tmp / f"in{i}.jpg"
            make_frame(p)
            self.png[i] = p

    def write_raw(self, batch_id, items, status="ok", error=None):
        (self.raw / f"{batch_id}.json").write_text(json.dumps(
            {"batch_id": batch_id, "status": status, "error": error, "items": items}))

    def item(self, vid, probe, n=1):
        return {"visit_id": vid, "probe": probe, "screenshot_path": str(self.png[n])}

    def ingest(self):
        rc, res, out = run_cli("ingest", "--calls", self.shots / "calls.json", "--raw-dir", self.raw,
                               "--out-dir", self.shots, "--no-ocr")
        return rc, res, json.loads((self.shots / "shots.json").read_text())["shots"]

    def test_exact_scale_one_dom_anchor(self):
        INPUTS_L = {"M1-1": ["Groups", NASTY], "M1-3": ["Save"], "M1-6": ["Notes"]}
        # plan-calls was run with the module INPUTS; labels for these tickets come from it
        v1 = next(v for v in self.calls["visits"] if v["surface"] == "Users")
        self.assertEqual(v1["labels"]["M1-1"], ["Groups", NASTY])
        probe = probe_for([lab("Groups", (300, 100.4, 60, 20.2)), lab(NASTY, f=False)], sb=None)
        self.write_raw("b01", [self.item("v01", probe)])
        rc, res, shots = self.ingest()
        self.assertEqual(len(shots), 1)
        s = shots[0]
        self.assertEqual((s["verified"], s["anchor"], s["label"], s["label_score"]),
                         (True, "dom_box", "Groups", 1.0))
        self.assertEqual(s["dom_box"], [300, 100, 60, 20])             # scale 1.0: exact image px
        self.assertEqual((s["tenant"], s["tenant_dom"], s["tenant_ocr"]), ("CLEAR", "CLEAR", None))
        self.assertEqual((s["visit_id"], s["ticket"], s["persona"], s["surface"]),
                         ("v01", "M1-1", "admin", "Users"))
        self.assertTrue(s["source"].startswith("chrome:medtrics.medtricslab.com/users/ "))
        self.assertTrue(Path(s["frame"]).name == "v01.jpg" and Path(s["frame"]).exists())
        self.assertIn("shot_id", s)
        self.assertFalse(s["sidebar"])
        self.assertIsNotNone(INPUTS_L)

    def test_scale_two(self):
        from PIL import Image
        Image.new("RGB", (2940, 1604), "white").save(self.png[1], "JPEG")
        self.write_raw("b01", [self.item("v01", probe_for([lab("Groups", (300, 100, 60, 20))]))])
        _, _, shots = self.ingest()
        self.assertEqual(shots[0]["dom_box"], [600, 200, 120, 40])

    def test_out_of_view_and_occluded_unverified_and_sidebar(self):
        probe = probe_for([lab("Groups", iv=False), lab(NASTY, oc=True, sb=True)])
        self.write_raw("b01", [self.item("v01", probe)])
        _, _, shots = self.ingest()
        self.assertEqual([s["verified"] for s in shots], [False, False])
        self.assertEqual([s["label"] for s in shots], ["Groups", NASTY])
        self.assertEqual([s["sidebar"] for s in shots], [False, True])
        self.assertNotIn("anchor", shots[0])

    def test_text_mismatch_unverified(self):
        self.write_raw("b01", [self.item("v01", probe_for([lab("Groups", t="Groups & more")]))])
        _, _, shots = self.ingest()
        self.assertFalse(shots[0]["verified"])

    def test_probe_as_json_string(self):
        self.write_raw("b01", [self.item("v01", json.dumps(probe_for([lab("Groups")]), indent=2))])
        _, _, shots = self.ingest()
        self.assertTrue(shots[0]["verified"])

    def test_blocked_probe_is_retry(self):
        self.write_raw("b01", [self.item("v01", "[BLOCKED: Base64 encoded data]"),
                               self.item("v02", probe_for([lab("Save")]), 2)])
        rc, res, shots = self.ingest()
        self.assertEqual(rc, 3)
        self.assertEqual(res["left_reasons"]["admin/Users"], "retry")
        self.assertEqual([s["visit_id"] for s in shots], ["v02"])
        resume = json.loads((self.shots / "resume.json").read_text())
        self.assertEqual([c["surface"] for c in resume["capture_plan"]], ["Users", "Notes"])
        self.assertEqual(resume["left_reasons"]["admin/Users"], "retry")
        garbled = json.dumps(probe_for([lab("Groups")]))[:-3]
        self.write_raw("b01", [self.item("v01", garbled)])
        self.assertEqual(self.ingest()[1]["left_reasons"]["admin/Users"], "retry")

    def test_expired_writes_resume_and_exits_3(self):
        self.write_raw("b01", [self.item("v01", probe_for([lab("Groups")]))], status="expired",
                       error="KB_LOGIN")
        rc, res, shots = self.ingest()
        self.assertEqual(rc, 3)
        self.assertFalse(res["ok"])
        self.assertEqual(res["left"], 2)
        self.assertEqual(res["left_reasons"],
                         {"admin/Groups": "expired", "faculty/Notes": "not_reached"})
        resume = json.loads((self.shots / "resume.json").read_text())
        self.assertEqual(resume["left_reasons"]["admin/Groups"], "expired")
        self.assertEqual([c["surface"] for c in resume["capture_plan"]], ["Groups", "Notes"])
        self.assertEqual(resume["run_id"], "r1")
        self.assertEqual(len(shots), 1)                                # what was taken is kept

    def test_unreached_batch_left(self):
        rc, res, _ = self.ingest()
        self.assertEqual(rc, 3)
        self.assertEqual(set(res["left_reasons"].values()), {"not_reached"})
        self.assertEqual(res["left"], 3)

    def test_complete_run_exits_0_and_clears_resume(self):
        self.write_raw("b01", [self.item("v01", probe_for([lab("Groups")])),
                               self.item("v02", probe_for([lab("Save")]), 2)])
        self.write_raw("b02", [self.item("v03", probe_for([lab("Notes")]), 3)])
        (self.shots / "resume.json").write_text("{}")
        rc, res, shots = self.ingest()
        self.assertEqual((rc, res["ok"], res["left"]), (0, True, 0))
        self.assertFalse((self.shots / "resume.json").exists())
        self.assertEqual(len(shots), 3)
        self.assertEqual(len({s["shot_id"] for s in shots}), 3)

    def test_reingest_replaces_by_visit_id(self):
        self.write_raw("b01", [self.item("v01", probe_for([lab("Groups")])),
                               self.item("v02", probe_for([lab("Save")]), 2)])
        self.ingest()
        _, _, again = self.ingest()
        self.assertEqual(len(again), 2)
        self.write_raw("b01", [self.item("v01", probe_for([lab("Groups", (10, 10, 20, 20))])),
                               self.item("v02", probe_for([lab("Save")]), 2)])
        _, _, third = self.ingest()
        self.assertEqual(len(third), 2)
        self.assertEqual(next(s for s in third if s["visit_id"] == "v01")["dom_box"], [10, 10, 20, 20])
        self.assertEqual(len({s["shot_id"] for s in third}), 2)

    def test_aspect_mismatch_unverified(self):
        probe = probe_for([lab("Groups")], ih=746)                      # innerHeight changed
        self.write_raw("b01", [self.item("v01", probe)])
        rc, _, shots = self.ingest()
        self.assertFalse(shots[0]["verified"])
        self.assertEqual(shots[0]["label"], "Groups")

    def test_low_resolution_unverified(self):
        make_frame(self.png[1], 1000, 545)
        self.write_raw("b01", [self.item("v01", probe_for([lab("Groups")], iw=1000, ih=545))])
        _, _, shots = self.ingest()
        self.assertFalse(shots[0]["verified"])

    def test_block_tenant(self):
        self.write_raw("b01", [self.item("v01", probe_for([lab("Groups")], tn="BLOCK"))])
        _, _, shots = self.ingest()
        self.assertEqual((shots[0]["verified"], shots[0]["tenant"], shots[0]["tenant_dom"]),
                         (True, "BLOCK", "BLOCK"))

    def test_missing_screenshot_is_retry(self):
        it = self.item("v01", probe_for([lab("Groups")]))
        it["screenshot_path"] = str(self.tmp / "nope.jpg")
        self.write_raw("b01", [it])
        _, res, shots = self.ingest()
        self.assertEqual(res["left_reasons"]["admin/Users"], "retry")
        self.assertEqual(shots, [])


class LegacyKept(unittest.TestCase):
    def test_legacy_subcommands_still_exist(self):
        for fn in ("cmd_login", "cmd_check", "cmd_run"):
            self.assertTrue(callable(getattr(C, fn)))


if __name__ == "__main__":
    unittest.main()
