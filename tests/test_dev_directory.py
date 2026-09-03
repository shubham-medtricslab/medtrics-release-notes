"""Tests for scripts/dev_directory.py — the deterministic half of the optional
Mem developer directory. Stdlib-only, hermetic (no Mem, no network).

Pins: note parse/render round-trip; prefill matching precedence (email →
login → unique name) on both the classic (assignee) and Oracle (PR author)
issue shapes; prefill never overwrites an existing id; pending emits a create
for a new developer, an update carrying the note version for a known one,
nothing for a Mem-served issue, and never overwrites a conflicting mapping;
a missing notes file is an empty directory, not an error.
"""

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "dev_directory", SCRIPTS_DIR / "dev_directory.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dd = _load_module()

CHRIS_NOTE = {
    "id": "11111111-1111-1111-1111-111111111111",
    "version": 3,
    "content": (
        "Developer: Chris Tokodi\n\n"
        "**Slack ID:** U0CHRIS\n"
        "**Emails:** chris@medtricslab.com\n"
        "**GitHub login:** ctokodi\n"
        "**GitLab username:** —\n"
        "**Optimus assignee id:** 42\n"
        "**Resolved via:** email\n"
        "**First seen:** 2026-09-01\n"
        "**Last used:** 2026-09-01\n"
        "**Source:** medtrics-release-notes /release-slack\n"
    ),
}
STRAY_NOTE = {"id": "2222", "version": 1, "content": "Meeting notes\n\nnothing"}


def _write(d, name, payload):
    p = Path(d) / name
    p.write_text(json.dumps(payload))
    return str(p)


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(dd.sys, "argv", ["dev_directory.py"] + argv), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = dd.main()
    block = out.getvalue().split("---DEV_DIRECTORY_RESULT---")[1].strip()
    return code, json.loads(block), err.getvalue()


class ParseRenderTests(unittest.TestCase):
    def test_round_trip(self):
        rec = dd.parse_note(CHRIS_NOTE)
        self.assertEqual(rec["slack_id"], "U0CHRIS")
        self.assertEqual(rec["emails"], ["chris@medtricslab.com"])
        self.assertEqual(rec["github_login"], "ctokodi")
        self.assertEqual(rec["version"], 3)
        self.assertEqual(dd.render_note(rec), CHRIS_NOTE["content"])

    def test_stray_note_is_ignored(self):
        self.assertIsNone(dd.parse_note(STRAY_NOTE))
        self.assertIsNone(dd.parse_note(
            {"content": "Developer: No Id\n\n**Emails:** x@y.com\n"}))

    def test_load_notes_accepts_object_list_or_missing(self):
        d = tempfile.mkdtemp()
        self.assertEqual(dd.load_notes(""), [])
        self.assertEqual(dd.load_notes(str(Path(d) / "missing.json")), [])
        p_obj = _write(d, "obj.json", {"notes": [CHRIS_NOTE, STRAY_NOTE]})
        p_list = _write(d, "list.json", [CHRIS_NOTE])
        self.assertEqual(len(dd.load_notes(p_obj)), 1)
        self.assertEqual(len(dd.load_notes(p_list)), 1)


class PrefillTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.notes = _write(self.d, "notes.json", {"notes": [CHRIS_NOTE]})

    def prefill(self, issues, extra=None):
        data = _write(self.d, "data.json", {"issues": issues})
        code, result, err = _run(["prefill", "--data", data, "--notes",
                                  self.notes] + (extra or []))
        return code, result, json.loads(Path(data).read_text())["issues"]

    def test_classic_email_match(self):
        code, result, issues = self.prefill(
            [{"identifier": "M1-1", "assignee_email": "Chris@MedtricsLab.com",
              "assignee_name": "Someone Else"}])
        self.assertEqual(code, 0)
        self.assertEqual(issues[0]["assignee_slack_id"], "U0CHRIS")
        self.assertEqual(issues[0]["assignee_slack_resolved_via"], "mem")
        self.assertEqual(result["by_email"], 1)

    def test_oracle_login_match(self):
        code, result, issues = self.prefill(
            [{"identifier": "ORC-1", "mrs": [{"author_name": "ctokodi",
                                              "author_username": "ctokodi"}]}])
        self.assertEqual(issues[0]["assignee_slack_id"], "U0CHRIS")
        self.assertEqual(result["by_login"], 1)
        # Oracle shape has no assignee_name — the directory name is surfaced.
        self.assertEqual(issues[0]["assignee_name_from_directory"], "Chris Tokodi")

    def test_unique_name_match(self):
        code, result, issues = self.prefill(
            [{"identifier": "ORC-2", "mrs": [{"author_name": "chris tokodi"}]}])
        self.assertEqual(issues[0]["assignee_slack_id"], "U0CHRIS")
        self.assertEqual(result["by_name"], 1)

    def test_single_token_name_never_matches(self):
        code, result, issues = self.prefill(
            [{"identifier": "ORC-3", "mrs": [{"author_name": "chris"}]}])
        self.assertNotIn("assignee_slack_id", issues[0])
        self.assertEqual(result["unmatched"], ["ORC-3"])

    def test_existing_id_is_not_overwritten(self):
        code, result, issues = self.prefill(
            [{"identifier": "M1-4", "assignee_email": "chris@medtricslab.com",
              "assignee_slack_id": "U0OTHER"}])
        self.assertEqual(issues[0]["assignee_slack_id"], "U0OTHER")
        self.assertEqual(result["prefilled"], 0)

    def test_missing_notes_file_is_empty_directory(self):
        data = _write(self.d, "data.json", {"issues": [
            {"identifier": "M1-5", "assignee_email": "chris@medtricslab.com"}]})
        code, result, _ = _run(["prefill", "--data", data, "--notes",
                                str(Path(self.d) / "nope.json")])
        self.assertEqual(code, 0)
        self.assertEqual(result["notes_loaded"], 0)
        self.assertEqual(result["prefilled"], 0)

    def test_missing_data_file_is_config_error(self):
        code, result, _ = _run(["prefill", "--data",
                                str(Path(self.d) / "nope.json")])
        self.assertEqual(code, 1)
        self.assertFalse(result["ok"])


class PendingTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.notes = _write(self.d, "notes.json", {"notes": [CHRIS_NOTE]})

    def pending(self, issues):
        data = _write(self.d, "data.json", {"issues": issues})
        out = str(Path(self.d) / "pending.json")
        code, result, err = _run(["pending", "--data", data, "--notes", self.notes,
                                  "--out", out, "--source", "/oracle-release",
                                  "--today", "2026-09-02"])
        return code, result, json.loads(Path(out).read_text())

    def test_new_developer_yields_create(self):
        code, result, out = self.pending([
            {"identifier": "ORC-10", "assignee_slack_id": "U0JANE",
             "assignee_slack_resolved_via": "name",
             "mrs": [{"author_name": "Jane Dev", "author_username": "janedev",
                      "author_email": "jane@medtricslab.com"}]},
            # Same developer on a second ticket → still ONE create.
            {"identifier": "ORC-11", "assignee_slack_id": "U0JANE",
             "assignee_slack_resolved_via": "name",
             "mrs": [{"author_name": "Jane Dev", "author_username": "janedev"}]},
        ])
        self.assertEqual(code, 0)
        self.assertEqual(result["creates"], 1)
        self.assertEqual(out["collection_title"], dd.COLLECTION_TITLE)
        w = out["writes"][0]
        self.assertEqual(w["action"], "create")
        self.assertEqual(w["collection_title"], dd.COLLECTION_TITLE)
        self.assertTrue(w["content"].startswith("Developer: Jane Dev\n"))
        self.assertIn("**Slack ID:** U0JANE", w["content"])
        self.assertIn("**Emails:** jane@medtricslab.com", w["content"])
        self.assertIn("**GitHub login:** janedev", w["content"])
        self.assertIn("**Resolved via:** name", w["content"])
        self.assertIn("**First seen:** 2026-09-02", w["content"])
        self.assertIn("**Source:** medtrics-release-notes /oracle-release",
                      w["content"])

    def test_known_developer_new_email_yields_update_with_version(self):
        code, result, out = self.pending([
            {"identifier": "M1-12", "assignee_slack_id": "U0CHRIS",
             "assignee_slack_resolved_via": "email",
             "assignee_email": "chris.t@medtricslab.com",
             "assignee_name": "Chris Tokodi"}])
        self.assertEqual(result["updates"], 1)
        w = out["writes"][0]
        self.assertEqual(w["action"], "update")
        self.assertEqual(w["note_id"], CHRIS_NOTE["id"])
        self.assertEqual(w["version"], 3)
        self.assertIn("**Emails:** chris@medtricslab.com, chris.t@medtricslab.com",
                      w["content"])
        self.assertIn("**Last used:** 2026-09-02", w["content"])
        # The original first-seen date and tier are preserved.
        self.assertIn("**First seen:** 2026-09-01", w["content"])
        self.assertIn("**Resolved via:** email", w["content"])

    def test_known_developer_nothing_new_yields_no_write(self):
        code, result, out = self.pending([
            {"identifier": "M1-13", "assignee_slack_id": "U0CHRIS",
             "assignee_slack_resolved_via": "email",
             "assignee_email": "chris@medtricslab.com",
             "assignee_id": 42, "assignee_name": "Chris Tokodi"}])
        self.assertEqual(out["writes"], [])

    def test_mem_served_and_unresolved_issues_are_skipped(self):
        code, result, out = self.pending([
            {"identifier": "M1-14", "assignee_slack_id": "U0CHRIS",
             "assignee_slack_resolved_via": "mem",
             "assignee_email": "chris@medtricslab.com"},
            {"identifier": "M1-15", "assignee_slack_id": "",
             "assignee_slack_resolved_via": "", "assignee_name": "Nobody"},
        ])
        self.assertEqual(out["writes"], [])
        self.assertEqual(result["served_from_mem"], 1)

    def test_conflicting_mapping_is_not_overwritten(self):
        # Directory says chris@ → U0CHRIS; resolver found U0DIFF for the same
        # email. Never rewrite the existing note — emit a separate create so a
        # human sees both in Mem.
        code, result, out = self.pending([
            {"identifier": "M1-16", "assignee_slack_id": "U0DIFF",
             "assignee_slack_resolved_via": "fuzzy",
             "assignee_email": "chris@medtricslab.com",
             "assignee_name": "Chris Tokodi"}])
        self.assertEqual(result["updates"], 0)
        self.assertEqual(result["creates"], 1)
        self.assertEqual(out["writes"][0]["slack_id"], "U0DIFF")


if __name__ == "__main__":
    unittest.main()
