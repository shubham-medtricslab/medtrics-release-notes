"""Tests for scripts/kb/kb_checklist.py — attach each ticket's manual QA
checklist from the GitLab MR attachment (mocked, no network)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kb_fixtures import KB_DIR  # noqa: F401  (puts scripts/kb on sys.path)

import kb_checklist as C

ENV = {"GITLAB_URL": "https://gitlab.example", "GITLAB_PROJECT_ID": "1", "GITLAB_TOKEN": "t"}
BRANCH = "m1-465-sessions-a-session-schedule-change-email-notifications"
EXACT = BRANCH + "-manual-qa-checklist.txt"
S1, S2, S3 = "a" * 32, "b" * 32, "c" * 32


def note(name, secret, at, nid=1):
    return {"id": nid, "created_at": at, "body": f"QA attached [{name}](/uploads/{secret}/{name})"}


class Find(unittest.TestCase):
    def test_slug(self):
        self.assertEqual(C.branch_slug("feat/M1-1596/Case-Log"), "feat-m1-1596-case-log")

    def test_priority_exact_then_suffix_then_ticket(self):
        notes = [note("other-manual-qa-checklist.txt", S1, "2026-06-03T10:00:00Z"),
                 note(EXACT, S2, "2026-06-01T10:00:00Z"),
                 note("m1-465-notes.txt", S3, "2026-06-04T10:00:00Z")]
        best = C.pick_best(C.find_candidates(notes, BRANCH, "M1-465"))
        self.assertEqual((best["kind"], best["secret"]), ("exact_branch_slug", S2))
        best = C.pick_best(C.find_candidates(notes[:1] + notes[2:], BRANCH, "M1-465"))
        self.assertEqual(best["kind"], "suffix")
        best = C.pick_best(C.find_candidates(notes[2:], BRANCH, "M1-465"))
        self.assertEqual(best["kind"], "ticket_id")
        self.assertIsNone(C.pick_best(C.find_candidates([note("shot.png", S1, "x")], BRANCH, "M1-465")))

    def test_newest_wins_a_tie(self):
        notes = [note(EXACT, S1, "2026-06-01T10:00:00Z"), note(EXACT, S2, "2026-06-05T10:00:00Z")]
        self.assertEqual(C.pick_best(C.find_candidates(notes, BRANCH, "M1-465"))["secret"], S2)


def data(**over):
    mr = {"iid": 7, "web_url": "https://gitlab.example/mr/7"}
    mr.update(over)
    return {"issues": [{"identifier": "M1-465", "description": "x", "mrs": [mr]}]}


class Attach(unittest.TestCase):
    def run_attach(self, d, notes, text="CHECKLIST TEXT", branch=BRANCH):
        def api(env, path, params=None):
            if path.endswith("/notes"):
                return (notes if (params or {}).get("page", 1) == 1 else []), {}
            return {"source_branch": branch}, {}
        with mock.patch.object(C.G, "gitlab_api", side_effect=api) as m, \
                mock.patch.object(C, "fetch_upload", return_value=text) as f:
            return C.attach(d, ENV), m, f

    def test_attaches_mr_attachment_fetching_branch(self):
        d = data()
        (n, missing, errors), m, f = self.run_attach(d, [note(EXACT, S1, "2026-06-01T10:00:00Z")])
        self.assertEqual((n, missing, errors), (1, [], []))
        self.assertEqual(d["issues"][0]["qa_checklist"],
                         {"text": "CHECKLIST TEXT", "name": EXACT, "mr_iid": 7, "source": "mr_attachment"})
        f.assert_called_once_with(ENV, S1, EXACT)
        self.assertTrue(any(c.args[1] == "merge_requests/7" for c in m.call_args_list))

    def test_source_branch_present_skips_mr_fetch(self):
        d = data(source_branch=BRANCH)
        _, m, _ = self.run_attach(d, [note(EXACT, S1, "x")])
        self.assertFalse(any(c.args[1] == "merge_requests/7" for c in m.call_args_list))

    def test_optimus_fallback_and_missing(self):
        d = data()
        d["issues"][0]["description"] = "Intro\n\n## QA Checklist\n\n1. Navigate to /a/b/\n\n## Notes\nz"
        (n, missing, _), _, _ = self.run_attach(d, [])
        qa = d["issues"][0]["qa_checklist"]
        self.assertEqual((n, qa["source"], qa["mr_iid"]), (1, "optimus", None))
        self.assertIn("Navigate to /a/b/", qa["text"])
        self.assertNotIn("Notes", qa["text"])
        d = data()
        (n, missing, _), _, _ = self.run_attach(d, [])
        self.assertEqual((n, missing), (0, ["M1-465"]))
        self.assertNotIn("qa_checklist", d["issues"][0])

    def test_network_error_never_fails(self):
        d = data()
        with mock.patch.object(C.G, "gitlab_api", return_value=(None, {})):
            n, missing, errors = C.attach(d, ENV)
        self.assertEqual((n, missing), (0, ["M1-465"]))
        self.assertEqual(len(errors), 1)

    def test_cli_writes_out_and_exits_zero(self):
        with tempfile.TemporaryDirectory() as t:
            src, out = Path(t) / "d.json", Path(t) / "o.json"
            src.write_text(json.dumps(data(source_branch=BRANCH)))
            with mock.patch.object(C.G, "load_env", return_value=ENV), \
                    mock.patch.object(C.G, "gitlab_api", return_value=([note(EXACT, S1, "x")], {})), \
                    mock.patch.object(C, "fetch_upload", return_value="T"):
                self.assertEqual(C.main(["attach", "--data", str(src), "--out", str(out)]), 0)
            self.assertEqual(json.loads(out.read_text())["issues"][0]["qa_checklist"]["text"], "T")
            self.assertNotIn("qa_checklist", json.loads(src.read_text())["issues"][0])
            with mock.patch.object(C.G, "load_env", side_effect=SystemExit(1)):
                self.assertEqual(C.main(["attach", "--data", str(src), "--out", str(out)]), 0)


if __name__ == "__main__":
    unittest.main()
