"""0.28.1: tenant-slug stems, orchestrator-sent install, stale needs_write, `confirm --ok none`,
a sweep that skips what it may not move, and re-checking a run from its own gifs/."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kb_fixtures import has_module
from test_kb_chrome_capture import Base, DRIVER, NODE, ROUTES, lab, probe_for, run_cli

import kb_capture as C
import kb_data as D
import qa_gif_to_shots as Q

LONG_ACTION = 1500


class SlugStems(unittest.TestCase):
    def slugs(self, text):
        return [x for f in Q.screen_text(text)["findings"] if f["kind"] == "tenant_slug" for x in f["value"]]

    def test_schedul_stem_is_never_a_slug(self):
        for w in ("schedulers", "Schedulers", "rescheduling", "unscheduled", "scheduler", "Rescheduled sessions"):
            self.assertEqual(self.slugs(w), [], w)

    def test_other_english_words(self):
        for w in ("procedures", "procedural", "contested", "protesters", "attestation", "pretest",
                  "posttest", "retesting", "untested", "latest", "greatest", "immediately",
                  "intermediate", "someday", "uniformed", "education", "coeducation", "named",
                  "renamed", "formed", "informed", "confirmed", "claimed", "unarmed", "velvet",
                  "interuniversity"):
            self.assertEqual(self.slugs(w), [], w)

    def test_prefixed_slugs_stay_flagged_and_word_starts_are_ignored(self):
        for w in ("uwmedicine", "ucmedical", "stateuniversity"):
            self.assertEqual(self.slugs(f"school {w} here"), [w], w)
        for w in ("medicine", "immediate", "education", "reduce", "universal"):
            self.assertEqual(self.slugs(w), [], w)

    def test_schedul_and_procedur_anywhere_are_words(self):
        for w in ("noschedule", "noprocedure", "No schedule", "No procedures"):
            self.assertEqual(self.slugs(w), [], w)
        self.assertEqual(self.slugs("uwmedicine"), ["uwmedicine"])

    def test_real_slugs_still_match(self):
        for w in ("ncsuvet", "ucsfmed", "qatest", "xymed", "kfshmed"):
            self.assertEqual(self.slugs(f"school {w} here"), [w], w)
        self.assertEqual(self.slugs("schedulers ncsuvet"), ["ncsuvet"])

    @unittest.skipUnless(NODE, "node not installed")
    def test_js_probe_uses_the_same_constants(self):
        self.assertIn(json.dumps(Q.slug_stop_spec()), C.probe_source())
        for body in ("Schedulers", "rescheduling", "ncsuvet", "Procedures and the latest", "xymed", "untested",
                     "uwmedicine", "ucmedical", "stateuniversity", "medicine", "immediate", "education",
                     "reduce", "universal", "noschedule", "noprocedure", "school uwmedicine here"):
            args = {"v": 1, "login": "/users/login/", "portal": None, "labels": [], "side": "nav",
                    "allow": [], "domain": "medtricslab.com"}
            sc = {"src": C.probe_source(), "args": args, "els": [], "body": body}
            r = subprocess.run([NODE, "-e", DRIVER], input=json.dumps(sc), capture_output=True,
                               text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(r.stdout)["tn"]["v"], Q.screen_text(body, "medtricslab.com")["verdict"], body)


class InstallIsTheOrchestrators(Base):
    PLAN = {"run_id": "r1", "capture_plan": [
        {"persona": "admin", "surface": "Sessions", "url": "/vadministration/curriculum/sessions/",
         "clicks": [{"text": "Add schedule"}, {"row": True}], "tickets": ["M1-465"]},
        {"persona": "faculty", "surface": "Notes", "url": "/notes/", "tickets": ["M1-6"]}]}

    def test_no_capturer_action_is_long_and_install_is_a_list(self):
        calls, _ = self.plan_calls(self.PLAN)
        self.assertEqual([a["name"] for a in calls["install"]], ["navigate", "computer", "javascript_tool"])
        self.assertGreater(len(calls["install"][2]["input"]["text"]), 10000)

        def texts(x):
            if isinstance(x, dict):
                for v in x.values():
                    yield from texts(v)
            elif isinstance(x, list):
                for v in x:
                    yield from texts(v)
            elif isinstance(x, str):
                yield x
        for b in calls["batches"]:
            for key in ("actions", "pick", "act"):
                for t in texts(b.get(key) or []):
                    self.assertLess(len(t), LONG_ACTION, (b["batch_id"], key))
        md = (self.shots / "capture.md").read_text()
        self.assertLess(max(len(l) for l in md.splitlines()), LONG_ACTION)
        for word in ("needs_install", "stop=needs_install", "--from-batch"):
            self.assertIn(word, md)

    def test_stubs_throw_noprobe_when_scripts_are_missing(self):
        calls, _ = self.plan_calls(self.PLAN)
        stub = calls["batches"][0]["actions"][-3]["input"]["text"]
        self.assertIn("KB_NOPROBE", stub)
        self.assertIn("needs_install", C.STATUSES)

    def test_agent_and_skill_text(self):
        root = Path(C.__file__).resolve().parents[2]
        cap = (root / "agents" / "kb-capturer.md").read_text()
        self.assertIn("needs_install", cap)
        self.assertNotIn("send the install action from `capture.md`", cap)
        self.assertIn("needs_install", (root / "agents" / "kb-data-maker.md").read_text())
        self.assertIn("data_install.json", (root / "skills" / "kb-capture" / "SKILL.md").read_text())

    def test_data_brief_leaves_the_install_to_the_orchestrator(self):
        text = D.HEAD
        self.assertIn("needs_install", text)
        self.assertNotIn("{install}", text)
        self.assertNotIn("{install_fields}", text)
        self.assertEqual(D.field_install_action()["action"], "javascript_exec")


class RescreenStoredProbe(unittest.TestCase):
    def probe(self, words, **kw):
        return {"iw": 1470, "labels": [], "tn": {"v": "BLOCK", "k": ["tenant_slug", "course_codes"]},
                "tw": {"tenant_slug": words}, **kw}

    def test_old_raw_probe_with_only_ordinary_words_is_cleared(self):
        p = C.parse_probe(self.probe(["schedulers"]))
        self.assertEqual(p["tn"], {"v": "REVIEW", "k": ["course_codes"]})
        self.assertNotIn("tenant_slug", p["tw"])

    def test_a_real_slug_or_a_full_word_list_stays_blocked(self):
        self.assertEqual(C.parse_probe(self.probe(["schedulers", "ncsuvet"]))["tn"]["v"], "BLOCK")
        self.assertEqual(C.parse_probe(self.probe(["schedulers"] * 5))["tn"]["v"], "BLOCK")

    def test_other_hard_kinds_keep_the_block(self):
        p = self.probe(["schedulers"])
        p["tn"]["k"].append("foreign_email_domain")
        self.assertEqual(C.parse_probe(p)["tn"]["v"], "BLOCK")


class ConfirmNone(unittest.TestCase):
    def test_none_parses_to_no_confirmations(self):
        import kb_package as PK
        items = [{"n": 1, "tenant": "CLEAR"}, {"n": 2, "tenant": "BLOCK"}]
        self.assertEqual(PK.parse_ok("none", items), [])
        self.assertEqual(PK.parse_ok(" None ", items), [])


@unittest.skipUnless(has_module("PIL"), "Pillow not installed")
class IngestFromRunGifs(Base):
    def setUp(self):
        super().setUp()
        plan = {"run_id": "r1", "capture_plan": [
            {"persona": "admin", "surface": "Sessions", "url": "/s/", "tickets": ["M1-465"],
             "write_gated": "New Session Created - notify participants"}]}
        self.calls, _ = self.plan_calls(plan)
        # the 1755 shape: the data plan came after plan-calls, so calls.screens still has needs_write
        self.assertEqual([x["reason"] for x in self.calls["screens"]], ["needs_write"])
        v = self.calls["visits"][0]
        v["labels"] = {"M1-465": ["Sessions"]}
        self.calls["write_visits"] = [{
            **{k: v[k] for k in ("persona", "surface", "url", "session", "args")},
            "visit_id": "v01-w1", "kind": "write", "entry": "v01", "ticket": "M1-465",
            "tickets": ["M1-465"], "labels": ["Save"], "gif": "kb-capture/r1/v01-w1.gif"}]
        (self.shots / "calls.json").write_text(json.dumps(self.calls))
        self.raw = self.shots / "raw"
        self.raw.mkdir()
        gifs = self.shots / "gifs"
        gifs.mkdir()
        from PIL import Image
        for vid in ("v01", "v01-w1"):
            Image.new("RGB", (1470, 802), (240, 240, 245)).save(gifs / f"{vid}.gif", "GIF")
        (self.raw / "b01.json").write_text(json.dumps({"batch_id": "b01", "status": "ok", "items": [
            {"visit_id": "v01", "probe": probe_for([lab("Sessions")], tn="BLOCK"),
             "gif": "kb-capture/r1/v01.gif"}]}))
        (self.raw / "data-M1-465.json").write_text(json.dumps({"items": [
            {"visit_id": "v01-w1", "probe": probe_for([lab("Save")]), "gif": "kb-capture/r1/v01-w1.gif",
             "created": {"status": "saved"}}]}))

    def ingest(self, *extra):
        rc, res, out = run_cli("ingest", "--calls", self.shots / "calls.json", "--raw-dir", self.raw,
                               "--out-dir", self.shots, "--no-ocr", *extra)
        return rc, res, json.loads((self.shots / "shots.json").read_text())

    def test_reingest_from_run_gifs_needs_no_downloads(self):
        rc, res, m = self.ingest("--from-run-gifs")
        self.assertEqual(rc, 0, res)
        self.assertEqual(res["visits"], 2)
        self.assertEqual({s["visit_id"] for s in m["shots"]}, {"v01", "v01-w1"})
        for s in m["shots"]:
            self.assertEqual(Path(s["frame"]).suffix, ".png")
        # the screen is re-run with the current rules: this probe's own tn is what is re-read
        self.assertEqual(next(s for s in m["shots"] if s["visit_id"] == "v01")["tenant_dom"], "BLOCK")

    def test_missing_run_gifs_are_download_missing(self):
        shutil.rmtree(self.shots / "gifs")
        rc, res, m = self.ingest("--from-run-gifs")
        self.assertEqual(rc, 3)                      # copies gone: download_missing, nothing guessed
        self.assertIn("download_missing", res["left_reasons"].values())

    def test_a_captured_write_visit_drops_the_stale_needs_write(self):
        rc, res, m = self.ingest("--from-run-gifs")
        self.assertEqual(m["missing"], {})
        self.assertNotIn("needs_write", [s.get("reason") for s in m["screens"]])
        w = next(s for s in m["screens"] if s["visit_id"] == "v01-w1")
        self.assertEqual(w["note"], "saved")

    def test_a_write_visit_without_a_shot_keeps_needs_write(self):
        (self.raw / "data-M1-465.json").write_text(json.dumps({"items": [
            {"visit_id": "v01-w1", "write_error": "KB_FIELD_NOSTICK"}]}))
        rc, res, m = self.ingest("--from-run-gifs")
        self.assertEqual(m["missing"]["M1-465"]["reason"], "write_failed")

    def test_a_dry_run_keeps_needs_write(self):
        (self.raw / "data-M1-465.json").write_text(json.dumps({"items": [
            {"visit_id": "v01-w1", "mode": "dry_run", "probe": probe_for([lab("Save")])}]}))
        rc, res, m = self.ingest("--from-run-gifs")
        self.assertEqual(m["missing"]["M1-465"]["reason"], "needs_write")


class SweepSkips(unittest.TestCase):
    def test_an_unmovable_item_is_skipped_not_fatal(self):
        if os.geteuid() == 0:
            self.skipTest("root ignores permissions")
        with tempfile.TemporaryDirectory() as d:
            dl = Path(d) / "dl"
            root = dl / "kb-capture"
            (root / "r1").mkdir(parents=True)
            (root / "r1" / "v01.gif").write_bytes(b"x")
            locked = root / "locked"
            locked.mkdir()
            (locked / "canary.gif").write_bytes(b"y")
            os.chmod(locked, 0o555)
            into = Path(d) / "stale"
            try:
                rc, res, out = run_cli("sweep", "--downloads-dir", dl, "--into", into)
            finally:
                os.chmod(locked, 0o755)
            self.assertEqual((rc, res["ok"]), (0, True))
            self.assertEqual([Path(m["to"]).name for m in res["moved"]], ["r1"])
            self.assertEqual([Path(x["path"]).name for x in res["skipped"]], ["locked"])
            self.assertTrue(res["skipped"][0]["error"])
            self.assertIn("skipped 1 item(s) that could not be moved: locked", out)
            self.assertTrue((locked / "canary.gif").exists())

    def test_failed_move_keeps_the_copy_and_reports_it(self):
        if os.geteuid() == 0:
            self.skipTest("root ignores permissions")
        with tempfile.TemporaryDirectory() as d:
            dl = Path(d) / "dl"
            root = dl / "kb-capture"
            src = root / "r1"
            (src / "sub").mkdir(parents=True)
            (src / "sub" / "a.gif").write_bytes(b"x")
            into = Path(d) / "stale"
            (into / "r1").mkdir(parents=True)     # a pre-existing item at the destination
            (into / "r1" / "old.gif").write_bytes(b"old")
            import errno
            from unittest import mock

            def nope(*_a, **_k):
                raise PermissionError(errno.EACCES, "cannot remove source")
            try:     # rename fails (cross-device) so shutil.move copies, then removing the source fails
                with mock.patch("os.rename", side_effect=OSError(errno.EXDEV, "xdev")), \
                        mock.patch("shutil.rmtree", side_effect=nope):
                    rc, res, out = run_cli("sweep", "--downloads-dir", dl, "--into", into)
                self.assertEqual(res["moved"], [])
                self.assertEqual(len(res["skipped"]), 1)
                sk = res["skipped"][0]
                self.assertIn("copied, source not removed", sk["error"])
                self.assertTrue((src / "sub" / "a.gif").exists())
                self.assertTrue((into / "r1" / "old.gif").exists())          # never dropped
                self.assertEqual((Path(sk["copied_to"]) / "sub" / "a.gif").read_bytes(), b"x")
                self.assertNotEqual(Path(sk["copied_to"]), into / "r1")
            finally:
                pass


if __name__ == "__main__":
    unittest.main()
