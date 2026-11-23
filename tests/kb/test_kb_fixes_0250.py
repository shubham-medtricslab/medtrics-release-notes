"""0.25.0 KB fixes, from run kb-20261123-1952 (M1-1459, M1-1245, M1-1446, M1-1413, M1-1166):

1. A partial re-assemble (`assemble --tickets M1-1166`) kept only that ticket in
   assemble.json, so packaging showed the other four as "not reached".
2. M1-1413's ticket names its labels in **bold**: no labels were sent to capture.
3. The tenant check counted typography (en dash, ellipsis, curly quotes) as
   non-Latin script and blocked the Time Off Requests page.
4. A 1x1 hidden "Delete" counted as a found label: no heading fallback, no reason.
   The capturer also shortened b01's visit id "v01-s0" to "v01" (the capture.md
   example said "v01"), so the start screen read as "not reached".
5. The Case Logs list loads by POST; the write guard blocked it, so "View" was never
   on the page and the report said "button not found".
Hermetic: no browser, no network (node only for the guard JS, skipped without it).
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kb_fixtures  # noqa: F401  (puts scripts/kb on sys.path)

import kb_capture as C
import kb_format as F
import kb_inputs as I
import kb_spec as SP
import qa_gif_to_shots as Q

NODE = shutil.which("node")
TICKETS = ["M1-1245", "M1-1446", "M1-1459", "M1-1166", "M1-1413"]
LIST_POST = "/api/rotations/procedure_logs/logs_filter_list/"


# ── 1. partial re-assemble ───────────────────────────────────────────────────
class PartialAssemble(unittest.TestCase):
    def test_merge_rows_swaps_in_place_and_keeps_the_rest(self):
        old = [{"ticket": t, "status": "partial"} for t in TICKETS]
        out = SP.merge_rows(old, [{"ticket": "M1-1166", "status": "drafted"}])
        self.assertEqual([r["ticket"] for r in out], TICKETS)
        self.assertEqual(out[3]["status"], "drafted")
        self.assertEqual(out[0]["status"], "partial")

    def test_a_retried_ticket_that_passes_leaves_failures(self):
        old = [{"ticket": "M1-1166", "fails": ["G3"]}, {"ticket": "M1-1413", "fails": ["G1"]}]
        out = SP.merge_rows(old, [], redone=["M1-1166"])
        self.assertEqual([r["ticket"] for r in out], ["M1-1413"])

    def test_assemble_with_tickets_keeps_every_row(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            groups = {"writer_groups": [{"group_id": "admin-01", "persona": "admin",
                                         "portal": "Administration portal",
                                         "audience": "Program Administrator", "tickets": TICKETS}]}
            (d / "groups.json").write_text(json.dumps(groups))
            (d / "prose").mkdir()
            a = SimpleNamespace(groups=str(d / "groups.json"), routes=None, shots=None, priors=None,
                                prose_dir=str(d / "prose"), out_dir=str(d / "specs"), tickets=None,
                                no_annotate=True)
            SP.cmd_assemble(a)
            a.tickets = "M1-1166"
            SP.cmd_assemble(a)
            rows = json.loads((d / "specs" / "assemble.json").read_text())
            self.assertEqual([r["ticket"] for r in rows], TICKETS)
            fails = json.loads((d / "specs" / "failures.json").read_text())
            self.assertIsInstance(fails, list)


# ── 2. bold labels ───────────────────────────────────────────────────────────
M1_1413_BODY = """On Step 1 of evaluation setup (Submission Options > Alerts), the options are radio buttons.

## Functional Specs:
### Submission Alert — choose one

- Display **Automatic Alerts**, **Manual Alerts**, and **No submission alert** as radio buttons.
- Default new evaluations to **No submission alert**.
- Each alert must have its own **Who should be notified?** field.
**Admin list:** Approve primary
**Approve modal**: collapsed preview
## **Summary**
"""


class BoldLabels(unittest.TestCase):
    def test_bold_terms_become_labels(self):
        self.assertEqual(I.bolded(M1_1413_BODY),
                         ["Automatic Alerts", "Manual Alerts", "No submission alert"])

    def test_build_input_has_labels_for_a_bold_only_ticket(self):
        rec = I.build_input({"identifier": "M1-1413", "title": "Alerts", "description": M1_1413_BODY})
        for want in ("Automatic Alerts", "Manual Alerts", "No submission alert"):
            self.assertIn(want, rec["labels"])
        self.assertNotIn("Summary", rec["labels"])
        self.assertNotIn("Admin list", rec["labels"])


# ── 3. non-Latin = letters only ──────────────────────────────────────────────
TYPOGRAPHY = "Nov 3 – Nov 5 … “Approved” • →  ‘x’"


class NonLatin(unittest.TestCase):
    def test_typography_never_counts(self):
        self.assertEqual(sum(Q.is_nonlatin_letter(c) for c in TYPOGRAPHY * 5), 0)

    def test_real_scripts_count(self):
        for c in "БЖ中ا":
            self.assertTrue(Q.is_nonlatin_letter(c), c)
        for c in "éΩñ":
            self.assertFalse(Q.is_nonlatin_letter(c), c)

    @unittest.skipUnless(NODE, "node not installed")
    def test_probe_regex_matches_the_python_rule(self):
        src = C.probe_source()
        i = src.index("new RegExp(", src.index("NLR=null"))
        expr = src[i:src.index(";", i)]
        sample = list("–…’•→БЖ中اéΩ")
        r = subprocess.run([NODE, "-e", f"var R={expr};console.log(JSON.stringify("
                            f"{json.dumps(sample)}.map(function(c){{return R.test(c);}})))"],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), [Q.is_nonlatin_letter(c) for c in sample])


# ── 4. hidden labels, missing reasons, shortened visit ids ───────────────────
HIDDEN_DELETE = {"b": [1490.7, 759.6, 1, 1], "f": True, "iv": False, "l": "Delete", "t": "Delete"}


def visit(vid, tickets, labels, kind="start", step=0, entry=None):
    return {"visit_id": vid, "kind": kind, "step": step, "entry": entry or vid.split("-")[0],
            "tickets": tickets, "labels": {t: list(labels) for t in tickets}}


class HiddenAndReasons(unittest.TestCase):
    def test_a_1x1_label_is_hidden(self):
        self.assertTrue(C.label_hidden(HIDDEN_DELETE))
        self.assertFalse(C.label_hidden({"b": [304, 151, 104, 35]}))

    def test_screen_form_drops_hidden_and_records_block(self):
        sf = C.screen_form({"labels": [HIDDEN_DELETE], "tn": {"v": "BLOCK", "k": ["non_latin_script"]},
                            "wb": 1, "wl": "POST " + LIST_POST})
        self.assertNotIn("found", sf)
        self.assertEqual(sf["tenant"], {"v": "BLOCK", "k": ["non_latin_script"]})
        self.assertEqual(sf["blocked"], "POST " + LIST_POST)

    def test_no_labels_reason(self):
        calls = {"visits": [visit("v05", ["M1-1413"], [])]}
        screens = [{"visit_id": "v05", "tickets": ["M1-1413"], "reason": None}]
        self.assertEqual(C._screens_missing(calls, screens, [])["M1-1413"]["reason"], "no_labels")

    def test_tenant_block_reason_and_unverified_shot_is_no_shot(self):
        calls = {"visits": [visit("v01-s1", ["M1-1245"], ["Delete"], "click", 1, "v01")]}
        screens = [{"visit_id": "v01-s1", "tickets": ["M1-1245"], "reason": None,
                    "tenant": {"v": "BLOCK", "k": ["non_latin_script"]}}]
        shots = [{"ticket": "M1-1245", "verified": False, "label": "Delete"}]
        m = C._screens_missing(calls, screens, shots)["M1-1245"]
        self.assertEqual(m, {"reason": "tenant_block", "detail": "non_latin_script"})
        self.assertIn("non-Latin script", F.missing_reason_text(m["reason"], m["detail"]))

    def test_label_not_found_still_used(self):
        calls = {"visits": [visit("v05", ["M1-1413"], ["Automatic Alerts"])]}
        screens = [{"visit_id": "v05", "tickets": ["M1-1413"], "reason": None}]
        self.assertEqual(C._screens_missing(calls, screens, [])["M1-1413"]["reason"], "label_not_found")

    def test_shortened_visit_id_is_matched(self):
        self.assertEqual(C.item_visit_id("v01", ["v01-s0"]), "v01-s0")
        self.assertEqual(C.item_visit_id("v05", ["v05"]), "v05")
        self.assertEqual(C.item_visit_id("v01", ["v01-s0", "v01-s1"]), "v01")   # ambiguous: unchanged
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "b01.json").write_text(json.dumps(
                {"batch_id": "b01", "status": "ok", "items": [{"visit_id": "v01", "probe": {}}]}))
            items, _ = C._load_raw(d, {"b01": ["v01-s0"]})
            self.assertEqual(list(items), ["v01-s0"])
            self.assertEqual(items["v01-s0"]["visit_id"], "v01-s0")

    def test_capture_md_example_uses_the_real_first_visit(self):
        calls = {"batches": [{"batch_id": "b01", "persona": "admin", "kind": "visits",
                              "visit_ids": ["v01-s0"], "actions": []}],
                 "visits": [], "accounts": [], "restore": [], "install": [], "abort": [],
                 "canary": {"gif": "x.gif", "actions": []}, "run_id": "r", "entries": {},
                 "screens": [], "write_visits": [], "base_url": "https://x", "gif_dir": "g"}
        md = C.capture_md(calls)
        self.assertIn('"visit_id": "v01-s0"', md)
        self.assertIn("never shortened", md)
        self.assertNotIn('"visit_id": "v01"\n', md)


# ── 5. route-listed read-only POSTs ──────────────────────────────────────────
class ReadPosts(unittest.TestCase):
    ROUTES = {"surfaces": [{"surface": "Cases > Case logs", "persona": "admin",
                            "read_posts": [LIST_POST, "/api/logs/save/", "/api/x?y=1"]},
                           {"surface": "Other", "persona": "admin", "read_posts": ["/api/other/"]}]}
    PLAN = {"capture_plan": [{"persona": "admin", "surface": "Cases > Case logs"}]}

    def test_only_valid_paths_of_visited_routes(self):
        self.assertEqual(C.read_posts_for(self.PLAN, self.ROUTES), [LIST_POST])

    def test_write_verbs_and_queries_refused(self):
        for bad in ("/api/logs/save/", "/api/case/delete/", "/api/x?y=1", "api/x/", "",
                    "/api/reviewer/reassign/", "/api/log/approve/"):
            self.assertFalse(C.read_post_ok(bad), bad)
        self.assertTrue(C.read_post_ok(LIST_POST))

    def test_python_mirror(self):
        reads = [LIST_POST]
        self.assertFalse(C.request_blocked("POST", path=LIST_POST, reads=reads))
        self.assertTrue(C.request_blocked("PUT", path=LIST_POST, reads=reads))
        self.assertTrue(C.request_blocked("POST", path="/api/other/", reads=reads))
        self.assertTrue(C.request_blocked("POST", path=LIST_POST))           # not listed this run
        self.assertTrue(C.request_blocked("POST"))

    def test_the_real_route_lists_the_case_log_list(self):
        routes = json.loads(C.DEFAULT_ROUTES.read_text())
        self.assertEqual(C.read_posts_for(self.PLAN, routes), [LIST_POST])

    @unittest.skipUnless(NODE, "node not installed")
    def test_guard_lets_only_the_listed_post_through(self):
        harness = r"""
const vm=require('vm');const sc=JSON.parse(require('fs').readFileSync(0,'utf8'));const log=[];
class XHR{open(m,u){}send(){log.push('xhr.send');}}
const ctx={URL,log,XHR};ctx.window=ctx;
ctx.location={href:'https://demo.example/cases/',origin:'https://demo.example',pathname:'/cases/',search:''};
ctx.fetch=function(){log.push('fetch');return Promise.resolve('real');};ctx.XMLHttpRequest=XHR;
ctx.navigator={sendBeacon(){return true;}};
ctx.document={baseURI:'https://demo.example/cases/',addEventListener(){},querySelectorAll:()=>[]};
vm.createContext(ctx);vm.runInContext(sc.guard,ctx);
(async()=>{const out=[];
 out.push(await vm.runInContext("fetch('"+sc.path+"',{method:'POST'}).then(()=>'ok',()=>'blocked')",ctx));
 out.push(await vm.runInContext("fetch('"+sc.path+"save/',{method:'POST'}).then(()=>'ok',()=>'blocked')",ctx));
 out.push(await vm.runInContext("fetch('https://evil.example"+sc.path+"',{method:'POST'}).then(()=>'ok',()=>'blocked')",ctx));
 out.push(await vm.runInContext("fetch('"+sc.path+"',{method:'PUT'}).then(()=>'ok',()=>'blocked')",ctx));
 out.push(vm.runInContext("(function(){var x=new XMLHttpRequest();x.open('POST','"+sc.path+"');try{x.send();return 'ok';}catch(e){return 'blocked';}})()",ctx));
 out.push(vm.runInContext("(function(){var x=new XMLHttpRequest();x.open('POST','/api/other/');try{x.send();return 'ok';}catch(e){return 'blocked';}})()",ctx));
 console.log(JSON.stringify({out,reads:ctx.__kbReads,blocked:ctx.__kbBlocked}));})();
"""
        r = subprocess.run([NODE, "-e", harness], capture_output=True, text=True, timeout=60,
                           input=json.dumps({"guard": C.guard_source([LIST_POST]), "path": LIST_POST}))
        self.assertEqual(r.returncode, 0, r.stderr)
        res = json.loads(r.stdout)
        self.assertEqual(res["out"], ["ok", "blocked", "blocked", "blocked", "ok", "blocked"])
        self.assertEqual(res["blocked"], 4)
        self.assertEqual(res["reads"], ["POST " + LIST_POST] * 2)

    def test_install_and_calls_carry_the_list(self):
        inst = C.install_action([LIST_POST])["text"]
        self.assertIn("logs_filter_list", inst)
        self.assertNotIn("logs_filter_list", C.install_action()["text"])

    def test_click_not_found_after_a_blocked_list_is_request_blocked(self):
        visits = [visit("v03-s2", ["M1-1459"], ["View"], "click", 2, "v03"),
                  visit("v03-s3", ["M1-1459"], ["Reassign"], "click", 3, "v03")]
        items = {"v03-s2": {"probe": {"v": 1, "u": "/x", "iw": 1, "ih": 1, "labels": [],
                                      "wb": 1, "wl": "POST " + LIST_POST}}}
        self.assertEqual(C.blocked_before(visits, visits[1], items), "POST " + LIST_POST)
        self.assertEqual(C.blocked_before(visits, visits[0], {}), "")
        self.assertIn(LIST_POST, F.missing_reason_text("request_blocked", "POST " + LIST_POST))


if __name__ == "__main__":
    unittest.main()
