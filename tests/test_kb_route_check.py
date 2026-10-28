"""Tests for the 0.26.1 page check: route-check-js / route-fix (a start page that
is a 404 moves to /vadministration/... or is marked page_not_found), the probe's
`nf` flag and its ingest (reason page_not_found, no shot), and the ZZ DEMO
allow-strings. Hermetic: no browser, no network; node only for the JS checks.
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
from test_kb_chrome_capture import Base, run_cli, lab, probe_for, make_frame, DRIVER, ROUTES, ROUTES_PATH
from test_kb_gif_capture import make_gif

import kb_capture as C
import kb_format as F
import qa_gif_to_shots as Q

NODE = shutil.which("node")

SESS = "/administration/curriculum/sessions/"
VSESS = "/vadministration/curriculum/sessions/"
PLAN = {"run_id": "r1", "capture_plan": [
    {"persona": "admin", "surface": "Sessions", "url": SESS + "#top", "clicks": [{"row": True}],
     "tickets": ["M1-465"]},
    {"persona": "admin", "surface": "Users", "url": "/users/", "tickets": ["M1-1"]},
    {"persona": "admin", "surface": "Gone", "url": "/administration/gone/?a=1", "tickets": ["M1-7"]},
    {"persona": "admin", "surface": "Sessions again", "url": SESS, "tickets": ["M1-8"]},
]}


def res(url, status=200, nf=False, login=False, final=None):
    return {"url": url, "status": status, "final": final or url.split("?")[0], "login": login, "nf": nf}


RESULT = [res(SESS, 404, nf=True), res(VSESS), res("/users/"),
          res("/administration/gone/?a=1", 404, nf=True), res("/vadministration/gone/?a=1", 404, nf=True)]


class RouteCheckJs(Base):
    def test_urls_include_alternates_distinct_no_hash_capped(self):
        urls, dropped = C.route_check_urls(PLAN)
        self.assertEqual(urls, [SESS, VSESS, "/users/", "/administration/gone/?a=1",
                                "/vadministration/gone/?a=1"])
        self.assertEqual(dropped, 0)
        big = {"capture_plan": [{"url": f"/administration/p{i}/"} for i in range(30)]}
        urls, dropped = C.route_check_urls(big)
        self.assertEqual((len(urls), dropped), (20, 40))

    def test_snippet(self):
        (self.tmp / "plan.json").write_text(json.dumps(PLAN))
        import contextlib, io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = C.main(["route-check-js", "--plan", str(self.tmp / "plan.json"),
                         "--routes", str(ROUTES_PATH)])
        src = buf.getvalue().strip()
        self.assertEqual(rc, 0)
        self.assertNotIn("\n", src)
        self.assertIn(json.dumps(SESS), src)
        self.assertIn("credentials:'same-origin'", src)
        self.assertIn(json.dumps(ROUTES["demo"]["login_path"]), src)
        self.assertLess(len(src), 2000)
        if NODE:
            r = subprocess.run([NODE, "--check", "-"], input="(async()=>{await " + src + "})()",
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    @unittest.skipUnless(NODE, "node not installed")
    def test_snippet_runs_against_stub_fetch(self):
        drv = r"""
const src=require('fs').readFileSync(0,'utf8');
const pages={'/a/':[200,'<h1>Hi</h1>','/a/'],'/b/':[404,'<h2>x</h2>','/b/'],
 '/c/':[200,'<p>Error 404 page</p>','/c/'],'/d/':[200,'<input type="password">','/users/login/']};
global.fetch=async(u)=>{const p=pages[u];return {status:p[0],url:'https://x.test'+p[2],text:async()=>p[1]};};
global.DOMParser=function(){};
global.DOMParser.prototype.parseFromString=function(t){const pw=/password/.test(t);
 const m=t.match(/<h[12]>(.*?)<\/h[12]>/);
 return {title:'',body:{textContent:t.replace(/<[^>]+>/g,' ')},
  querySelectorAll:()=>[],querySelector:s=>s.indexOf('password')>=0?(pw?{}:null):(m?{textContent:m[1]}:null)};};
(0,eval)(src).then(r=>console.log(r));
"""
        urls = ["/a/", "/b/", "/c/", "/d/"]
        src = C.route_check_source(urls, "/users/login/")
        r = subprocess.run([NODE, "-e", drv.replace("require('fs').readFileSync(0,'utf8')", json.dumps(src))],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        got = {x["url"]: x for x in json.loads(r.stdout.strip())}
        self.assertEqual([got[u]["nf"] for u in urls], [False, True, True, False])
        self.assertEqual([got[u]["login"] for u in urls], [False, False, False, True])
        self.assertEqual(got["/b/"]["status"], 404)


class RouteFix(Base):
    def fix(self, result, plan=PLAN):
        (self.tmp / "plan.json").write_text(json.dumps(plan))
        (self.tmp / "res.json").write_text(json.dumps(result))
        return run_cli("route-fix", "--plan", self.tmp / "plan.json", "--result", self.tmp / "res.json",
                       "--out", self.tmp / "out.json")

    def test_moves_to_vadministration_and_skips_dead_pages(self):
        rc, res_, out = self.fix(RESULT)
        self.assertEqual(rc, 0)
        plan = json.loads((self.tmp / "out.json").read_text())["capture_plan"]
        s = plan[0]
        self.assertEqual((s["url"], s["route_fixed_from"]), (VSESS + "#top", SESS + "#top"))
        self.assertEqual(plan[1]["url"], "/users/")
        self.assertNotIn("skip_reason", plan[1])
        self.assertEqual(plan[2]["skip_reason"], "page_not_found")
        self.assertEqual(plan[3]["url"], VSESS)
        self.assertEqual(res_["page_not_found"], ["/administration/gone/?a=1"])
        self.assertEqual(len(res_["moved"]), 2)
        self.assertIn("2 start page(s) moved", out)
        self.assertIn("1 marked page_not_found", out)

    def test_reverse_alternate_and_unchecked_alternate(self):
        plan = {"capture_plan": [{"persona": "admin", "surface": "A", "url": "/vadministration/x/"}]}
        _, res_, _ = self.fix([res("/vadministration/x/", 404, nf=True), res("/administration/x/")], plan)
        self.assertEqual(json.loads((self.tmp / "out.json").read_text())["capture_plan"][0]["url"],
                         "/administration/x/")
        # an alternate that was not checked is never assumed ok
        _, res_, _ = self.fix([res("/vadministration/x/", 404, nf=True)], plan)
        self.assertEqual(json.loads((self.tmp / "out.json").read_text())["capture_plan"][0]["skip_reason"],
                         "page_not_found")

    def test_json_string_result_accepted(self):
        rc, _, _ = self.fix(json.dumps(RESULT))
        self.assertEqual(rc, 0)

    def test_login_is_expired_and_writes_nothing(self):
        rc, res_, out = self.fix([res(SESS, 200, login=True, final="/users/login/")])
        self.assertEqual((rc, res_["error"]), (3, "expired"))
        self.assertIn("KB_LOGIN", out)
        self.assertFalse((self.tmp / "out.json").exists())

    def test_skip_reason_flows_into_plan_calls_and_missing(self):
        self.fix(RESULT)
        plan = json.loads((self.tmp / "out.json").read_text())
        calls, res_ = self.plan_calls(plan)
        urls = [v["url"] for v in calls["visits"]]
        self.assertNotIn("/administration/gone/?a=1", urls)
        self.assertEqual(res_["skipped"], 1)
        sk = [s for s in calls["screens"] if s["reason"] == "page_not_found"]
        self.assertEqual([s["tickets"] for s in sk], [["M1-7"]])
        self.assertEqual(F.missing_reason_text("page_not_found"),
                         "the capture could not find this page in the app")


class ProbeNf(unittest.TestCase):
    def run_probe(self, body, allow=("Medtrics Medical Center",)):
        args = {"v": 1, "login": "/users/login/", "portal": None, "labels": [], "side": "nav",
                "allow": list(allow), "domain": "medtricslab.com"}
        sc = {"src": C.probe_source(), "args": args, "els": [], "body": body}
        r = subprocess.run([NODE, "-e", DRIVER], input=json.dumps(sc), capture_output=True,
                           text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    @unittest.skipUnless(NODE, "node not installed")
    def test_nf_flag(self):
        r = self.run_probe("Oh No! Looks like we are a little lost. Affected URL /x/ Error 404")
        self.assertTrue(r["nf"])
        self.assertFalse(self.run_probe("Sessions list")["nf"])
        self.assertTrue(self.run_probe("The Page not found here")["nf"])

    def test_python_check(self):
        self.assertTrue(C.is_not_found("Error 404"))
        self.assertFalse(C.is_not_found("Sessions"))

    @unittest.skipUnless(NODE, "node not installed")
    def test_zz_demo_course_is_clear_in_js_and_python(self):
        body = "ZZDEMO101 · ZZ DEMO - Intro to Clinical Skills"
        allow = ROUTES["demo"]["allow_strings"]
        py = Q.screen_text(body, "medtricslab.com", allow_strings=allow)
        self.assertNotIn("course_codes", [f["kind"] for f in py["findings"]])
        self.assertEqual(self.run_probe(body, allow)["tn"]["v"], py["verdict"])
        self.assertNotIn("course_codes", self.run_probe(body, allow)["tn"]["k"])
        other = self.run_probe("MCAP5010", allow)
        self.assertEqual(other["tn"]["k"], ["course_codes"])


@unittest.skipUnless(has_module("PIL"), "Pillow not installed")
class IngestNf(Base):
    def test_nf_screen_is_a_reason_not_label_misses(self):
        plan = {"run_id": "r1", "capture_plan": [
            {"persona": "admin", "surface": "Sessions", "url": SESS, "tickets": ["M1-465"],
             "clicks": [{"row": True}]},
            {"persona": "admin", "surface": "Users", "url": "/users/", "tickets": ["M1-1"]}]}
        calls, _ = self.plan_calls(plan)
        raw = self.tmp / "raw"
        raw.mkdir()
        dl = self.tmp / "dl"
        dl.mkdir()
        for v in calls["visits"]:
            make_gif(dl / Path(v["gif"]).name)
        pr = probe_for([lab("Groups")], nf=True)
        ok = probe_for([lab("Groups")])
        (raw / "b01.json").write_text(json.dumps({"batch_id": "b01", "status": "ok", "items": [
            {"visit_id": "v01-s0", "probe": pr, "gif": Path(calls["visits"][0]["gif"]).name},
            {"visit_id": "v02", "probe": ok, "gif": Path(calls["visits"][2]["gif"]).name}]}))
        rc, res_, _ = run_cli("ingest", "--calls", self.shots / "calls.json", "--raw-dir", raw,
                              "--out-dir", self.shots, "--no-ocr", "--downloads-dir", dl)
        m = json.loads((self.shots / "shots.json").read_text())
        self.assertEqual(m["missing"]["M1-465"], {"reason": "page_not_found", "detail": "/users/"})
        self.assertEqual([s["ticket"] for s in m["shots"]], ["M1-1"])   # none for the 404 page
        self.assertEqual([s["visit_id"] for s in m["screens"]], ["v01-s0", "v02"])
        self.assertNotIn("M1-1", m["missing"])


if __name__ == "__main__":
    unittest.main()
