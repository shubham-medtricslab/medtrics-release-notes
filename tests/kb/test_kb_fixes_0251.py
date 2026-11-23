"""0.25.1 KB fixes, from run kb-20261123-2158 (the 0.25.0 retry of M1-1245, M1-1459, M1-1413):

1. Nothing scrolled a label into view. M1-1413's alert radios were found at y=1147 in
   a 639 px viewport (iv=false), so the frame never showed them; "Submit" and "View" on
   the Case Logs list were the same. A new stored step, kbs, runs before the hover and
   centres the first found label (or scrolls to the top when none is found).
2. After kbclick scrolled the page, the heading fallback recorded "Case Logs" at y=-450
   and y=-926 as VERIFIED; assembly then crashed in Pillow drawing the ring (y1 < y0).
   The heading fallback now needs the box on the frame, and assembly treats an
   off-frame box as a text-only step instead of crashing.
3. "Reassign Case Log Reviewer" was not found: the element's text is "Reassign Case Log
   Reviewer Philip Hayes". The probe now falls back to a word-boundary prefix match for
   labels of 2+ words and 8+ chars (pm: true), never for "No" / "View".
4. M1-1245's Requests list was captured with its loading spinner. The probe reports a
   visible loader (ld); ingest drops that frame's shots as still_loading.
Hermetic: no browser, no network (node only for the JS, skipped without it).
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

import kb_capture as C
import kb_format as F

NODE = shutil.which("node")
HAVE_PIL = has_module("PIL")
IW, IH = 1536, 639


def probe(labels, h=None, ld=""):
    return {"v": 1, "u": "/cases/", "iw": IW, "ih": IH, "dpr": 1.25, "nf": False,
            "labels": labels, "h": h, "tn": {"v": "CLEAR", "k": []}, "tw": {}, "ld": ld}


def lab(l, b, iv=True, pm=False):
    o = {"l": l, "f": True, "t": l, "b": list(b), "iv": iv, "oc": False, "sb": False}
    if pm:
        o["pm"] = True
    return o


@unittest.skipUnless(HAVE_PIL, "Pillow not installed")
class Ingest(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, True)
        self.png = self.d / "frame.png"
        Image.new("RGB", (IW, IH), (250, 250, 250)).save(self.png)
        self.calls = {"base_url": "https://medtrics.medtricslab.com"}

    def ingest(self, pr, labels, kind="click"):
        v = {"visit_id": "v02-s3", "kind": kind, "step": 3, "entry": "v02", "url": "/cases/",
             "persona": "admin", "surface": "Cases > Case logs", "tickets": ["M1-1459"],
             "labels": {"M1-1459": labels}}
        it = {"visit_id": "v02-s3", "probe": pr, "screenshot_path": str(self.png)}
        entries, reason = C.ingest_visit(v, it, self.calls, {"domain": "medtricslab.com",
                                                              "allow_strings": []},
                                         self.d / "shots", False, "now")
        self.assertIsNone(reason)
        return entries

    def test_offscreen_heading_is_no_shot(self):
        # the real b06 probe: no label found, heading scrolled to y=-926
        pr = probe([{"l": "Case log not available", "f": False, "t": "", "b": None,
                     "iv": False, "oc": False, "sb": False}],
                   h={"t": "Case Logs", "b": [195.5, -926.4, 1315, 61.6]})
        self.assertEqual(self.ingest(pr, ["Case log not available"]), [])

    def test_onscreen_heading_still_falls_back(self):
        pr = probe([], h={"t": "Case Logs", "b": [195.5, 89.6, 1314.8, 61.6]})
        e = self.ingest(pr, ["Case log not available"])
        self.assertEqual([(x["fallback"], x["verified"]) for x in e], [("heading", True)])

    def test_prefix_match_is_recorded(self):
        # the live probe result: the element's text carries the reviewer's name
        l = lab("Reassign Case Log Reviewer", (526.5, 337, 468, 42), pm=True)
        l["t"] = "Reassign Case Log Reviewer Philip Hayes"
        e = self.ingest(probe([l]), ["Reassign Case Log Reviewer"])
        self.assertEqual((e[0]["verified"], e[0]["label_match"], e[0]["label"]),
                         (True, "prefix", "Reassign Case Log Reviewer"))
        l2 = dict(l, t="Something else entirely")          # pm without the prefix is no evidence
        self.assertFalse(self.ingest(probe([l2]), ["Reassign Case Log Reviewer"])[0]["verified"])

    def test_loading_frame_ships_nothing(self):
        pr = probe([lab("Requests", (304, 151, 104, 35))],
                   h={"t": "Time Off Benefits", "b": [195.5, 89.6, 1314.8, 61.6]}, ld="spinner-border")
        e = self.ingest(pr, ["Requests"])
        self.assertEqual([(x["verified"], x.get("reason")) for x in e], [(False, "still_loading")])
        self.assertEqual(C.screen_form(pr)["loading"], "spinner-border")


class Missing(unittest.TestCase):
    def test_still_loading_reason(self):
        calls = {"visits": [{"visit_id": "v01-s1", "kind": "click", "step": 1, "entry": "v01",
                             "tickets": ["M1-1245"], "labels": {"M1-1245": ["Requests"]}}]}
        screens = [{"visit_id": "v01-s1", "tickets": ["M1-1245"], "reason": None,
                    "loading": "spinner-border"}]
        m = C._screens_missing(calls, screens, [{"ticket": "M1-1245", "verified": False}])["M1-1245"]
        self.assertEqual(m["reason"], "still_loading")
        self.assertIn("still loading", F.missing_reason_text(m["reason"], m["detail"]))


@unittest.skipUnless(HAVE_PIL, "Pillow not installed")
class Assembly(unittest.TestCase):
    def test_offframe_box_is_text_only_not_a_crash(self):
        from PIL import Image
        import kb_spec as S
        import qa_annotate as A
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        frame = d / "v02-s2.png"
        Image.new("RGB", (IW, IH), "white").save(frame)
        with Image.open(frame) as im, self.assertRaises(A.OffFrame):
            A.annotate(im, A.anchor_from_box([196, -926, 1315, 62]), 1)
        # the shots.orig.json entry that crashed the run, as ingest 0.25.0 wrote it
        shot = {"shot_id": "s7", "ticket": "M1-1459", "verified": True, "frame": str(frame),
                "anchor": "dom_box", "label": "Case Logs", "dom_box": [196, -450, 1315, 62],
                "tenant": "REVIEW", "source": "chrome:x"}
        prose = {"ticket": "M1-1459", "title": "T", "steps": [
            {"text": "Open **Cases**, then **Case Logs**.", "shot": "s7"}]}
        spec = S.build_spec(prose, [shot], {}, None, d / "out")
        st = spec["steps"][0]
        self.assertNotIn("image", st)
        self.assertEqual(st["missing_shot"], "screenshot s7 showed its item off-screen")

    def test_onframe_box_still_rings(self):
        from PIL import Image
        import kb_spec as S
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        frame = d / "f.png"
        Image.new("RGB", (IW, IH), "white").save(frame)
        self.assertTrue(S._annotate({"frame": str(frame), "dom_box": [304, 151, 104, 35]}, 1, d / "o.jpg"))
        self.assertTrue((d / "o.jpg").is_file())


# ── JS: kbFind (probe + kbs) and kbs on a tiny fake DOM ──────────────────────
FAKE_DOM = r"""
var mk=function(tag,text,box,kids,cls){var e={tagName:tag,className:cls||'',kids:kids||[],parent:null,
 _box:box,_text:text};e.kids.forEach(function(k){k.parent=e;});
 e.getBoundingClientRect=function(){var b=e._box,sy=W.scrollY;return {left:b[0],top:b[1]-sy,width:b[2],height:b[3],
  right:b[0]+b[2],bottom:b[1]-sy+b[3]};};
 e.contains=function(o){while(o){if(o===e)return true;o=o.parent;}return false;};
 e.closest=function(){return null;};
 e.scrollIntoView=function(){W.scrollY=Math.max(0,e._box[1]+e._box[3]/2-W.innerHeight/2);W.log.push('into:'+e._text);};
 Object.defineProperty(e,'innerText',{get:function(){return e._text!==null?e._text:e.kids.map(function(k){return k.innerText;}).join(' ');}});
 Object.defineProperty(e,'textContent',{get:function(){return e.innerText;}});
 return e;};
var W={innerWidth:1536,innerHeight:639,scrollY:0,log:[],scrollTo:function(x,y){W.scrollY=y;W.log.push('top');}};
var flat=function(e){return [e].concat.apply([e],e.kids.map(flat)).slice(1);};
var mkdoc=function(els){var body=mk('BODY',null,[0,0,1536,3000],els);
 return {body:{querySelectorAll:function(){var out=[];var walk=function(e){e.kids.forEach(function(k){out.push(k);walk(k);});};walk(body);return out;}}};};
"""


def run_js(body):
    r = subprocess.run([NODE, "-e", body], capture_output=True, text=True, timeout=30)
    if r.returncode:
        raise AssertionError(r.stderr)
    return json.loads(r.stdout)


@unittest.skipUnless(NODE, "node not installed")
class Js(unittest.TestCase):
    def find(self, label, els_js):
        src = C.find_source()
        return run_js(FAKE_DOM + src + f"""
var els={els_js};var D=mkdoc(els);var all=D.body.querySelectorAll('*');
var norm=function(s){{return String(s||'').replace(/\\s+/g,' ').trim();}};
var vis=function(e){{var r=e.getBoundingClientRect();return r.width>0&&r.height>0;}};
var r=kbFind({json.dumps(label)},all,vis,function(){{return false;}},norm);
console.log(JSON.stringify(r?{{t:r.e.innerText,pm:r.pm}}:null));""")

    def test_exact_wins(self):
        self.assertEqual(self.find("Manual Alerts", "[mk('LABEL','Manual Alerts',[246,1196,88,17])]"),
                         {"t": "Manual Alerts", "pm": False})

    def test_prefix_on_the_reviewer_toggle(self):
        self.assertEqual(self.find("Reassign Case Log Reviewer",
                                   "[mk('DIV',null,[500,200,600,300],[mk('BUTTON','Reassign Case Log Reviewer Philip Hayes',[520,260,400,40])])]"),
                         {"t": "Reassign Case Log Reviewer Philip Hayes", "pm": True})

    def test_short_labels_never_prefix_match(self):
        self.assertIsNone(self.find("No", "[mk('LABEL','No submission alert',[246,1245,123,17])]"))
        self.assertIsNone(self.find("View", "[mk('A','View details',[1346,780,65,30])]"))

    def test_prefix_needs_a_word_boundary(self):
        self.assertIsNone(self.find("Case Log Review", "[mk('SPAN','Case Log Reviewers list',[1,1,90,20])]"))

    def kbs(self, labels, els_js, scroll_y=0):
        return run_js(FAKE_DOM + f"""
var els={els_js};var document=mkdoc(els);var window=W;W.scrollY={scroll_y};
var f={C.scroll_source()};
var r=f({{labels:{json.dumps(labels)},side:'nav'}});
console.log(JSON.stringify({{r:r,log:W.log,y:W.scrollY}}));""")

    def test_kbs_scrolls_the_m1_1413_radio_into_view(self):
        out = self.kbs(["Automatic Alerts", "Manual Alerts"],
                       "[mk('H1','Evaluation Form',[195,89,1315,62]),mk('LABEL','Automatic Alerts',[246,1147,107,17])]")
        self.assertEqual(out["r"]["s"], "scrolled")
        self.assertEqual(out["log"], ["into:Automatic Alerts"])
        self.assertTrue(0 <= 1147 - out["y"] <= 639 - 17)

    def test_kbs_leaves_an_in_view_label_alone(self):
        out = self.kbs(["Requests"], "[mk('A','Requests',[304,151,104,35])]")
        self.assertEqual((out["r"]["s"], out["log"]), ("in_view", []))

    def test_kbs_never_moves_when_a_wanted_label_is_in_view(self):
        # live Case Logs start page: sidebar "Logs" in view, "Submit" below the fold -> stay
        out = self.kbs(["Logs", "Submit"],
                       "[mk('A','Logs',[312,62,28,17]),mk('BUTTON','Submit',[1429,1019,66,33])]")
        self.assertEqual((out["r"], out["log"]), ({"s": "in_view", "l": "Logs"}, []))
        # live Time Off page: first label lower down, the "Requests" tab already in view -> stay
        out = self.kbs(["Time Off", "Requests"],
                       "[mk('DIV','Time Off',[257,597,211,61]),mk('A','Requests',[304,151,104,36])]")
        self.assertEqual((out["r"], out["log"]), ({"s": "in_view", "l": "Requests"}, []))
        # a hidden 1x1 match never counts
        out = self.kbs(["Delete", "View"],
                       "[mk('A','Delete',[1490,300,1,1]),mk('A','View',[1346,1400,65,30])]")
        self.assertEqual(out["log"], ["into:View"])

    def test_kbs_scrolls_to_top_when_nothing_found(self):
        out = self.kbs(["Case log not available"], "[mk('H1','Case Logs',[195,89,1315,62])]", scroll_y=1016)
        self.assertEqual((out["r"]["s"], out["log"], out["y"]), ("top", ["top"], 0))

    def test_loader_selector_covers_the_apps_own_loader(self):
        # live: <h3 class="loader-txt font-italic ms-4">Please wait…</h3> on Time Off Benefits
        sel = C.LOADER_SEL
        for part in ("[class^=loader]", "[class*=' loader']", "[class^=spinner]", "[aria-busy=true]"):
            self.assertIn(part, sel.split(","))
        self.assertNotIn("[class*=loader]", sel)          # would match "uploader"
        self.assertIn(json.dumps(sel), C.probe_source())

    def test_install_stores_kbs_and_stub_calls_it(self):
        text = C.install_action()["text"]
        self.assertIn("sessionStorage.setItem('kbs'", text)
        self.assertIn("(0,eval)(sessionStorage.getItem('kbs'))",
                      C._scroll({"args": {"labels": ["X"], "side": "nav"}})["input"]["text"])
        r = subprocess.run([NODE, "--check", "-"], input="var f=" + C.probe_source() + ";",
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
