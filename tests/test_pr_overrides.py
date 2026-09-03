"""Tests for scripts/pr_overrides.py — the deterministic half of the optional
Mem PR-override store. Stdlib-only, hermetic (no Mem, no network).

Pins: note parse/render round-trip; stray and malformed notes ignored (the
latter with a warning naming the note id); load_notes accepts object / list /
missing; newest `Resolved at` wins on duplicate notes, with a warning;
`pending` emits a create for a new ticket and an update carrying note_id +
version for a known one; bad ticket / bad PR exit 1; the PR URL comes from
--data when the gathered mrs carry the PR, else from GITHUB_REPO in .env.
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
        "pr_overrides", SCRIPTS_DIR / "pr_overrides.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


po = _load_module()

ORC232_NOTE = {
    "id": "11111111-1111-1111-1111-111111111111",
    "version": 3,
    "content": (
        "PR Override: ORC-232\n\n"
        "**Ticket:** ORC-232\n"
        "**PR:** 415\n"
        "**PR URL:** https://github.com/medtrics/medtrics-oracle/pull/415\n"
        "**Resolved by:** shubham@medtricslab.com\n"
        "**Resolved at:** 2026-09-02T12:00:00Z\n"
        "**Reason:** two open PRs; 416 is a spike branch\n"
    ),
}
STRAY_NOTE = {"id": "2222", "version": 1, "content": "Meeting notes\n\nnothing"}
MALFORMED_NOTE = {"id": "3333", "version": 1,
                  "content": "PR Override: ORC-9\n\n**Ticket:** ORC-9\n**PR:** soon\n"}


def _write(d, name, payload):
    p = Path(d) / name
    p.write_text(json.dumps(payload))
    return str(p)


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(po.sys, "argv", ["pr_overrides.py"] + argv), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = po.main()
    stdout = out.getvalue()
    block = stdout.split("---PR_OVERRIDES_RESULT---")[1].strip()
    return code, json.loads(block), err.getvalue(), stdout


class ParseRenderTests(unittest.TestCase):
    def test_round_trip(self):
        rec = po.parse_note(ORC232_NOTE)
        self.assertEqual(rec["ticket"], "ORC-232")
        self.assertEqual(rec["pr"], 415)
        self.assertEqual(rec["pr_url"],
                         "https://github.com/medtrics/medtrics-oracle/pull/415")
        self.assertEqual(rec["resolved_by"], "shubham@medtricslab.com")
        self.assertEqual(rec["resolved_at"], "2026-09-02T12:00:00Z")
        self.assertEqual(rec["reason"], "two open PRs; 416 is a spike branch")
        self.assertEqual(rec["note_id"], ORC232_NOTE["id"])
        self.assertEqual(rec["version"], 3)
        self.assertEqual(po.render_note(rec), ORC232_NOTE["content"])

    def test_title_alone_names_the_ticket_and_pr_tolerates_hash_and_url(self):
        rec = po.parse_note({"id": "x", "content":
                             "PR Override: orc-12\n**PR:** #77\n"})
        self.assertEqual((rec["ticket"], rec["pr"]), ("ORC-12", 77))
        rec = po.parse_note({"id": "x", "content":
                             "PR Override: ORC-12\n**PR:** "
                             "https://github.com/o/r/pull/78\n"})
        self.assertEqual(rec["pr"], 78)

    def test_stray_and_malformed_notes_are_ignored(self):
        self.assertIsNone(po.parse_note(STRAY_NOTE))
        self.assertIsNone(po.parse_note(MALFORMED_NOTE))           # PR not int
        self.assertIsNone(po.parse_note({"content": ""}))
        self.assertIsNone(po.parse_note(
            {"content": "PR Override: M1-5\n**PR:** 3\n"}))        # not ORC
        self.assertIsNone(po.parse_note(
            {"content": "PR Override: ORC-5\n**PR:** 0\n"}))       # not positive
        self.assertIsNone(po.parse_note(
            {"content": "PR Override: ORC-5\n**PR:** -3\n"}))
        self.assertTrue(po.is_override_note(MALFORMED_NOTE))
        self.assertFalse(po.is_override_note(STRAY_NOTE))

    def test_load_notes_accepts_object_list_or_missing(self):
        d = tempfile.mkdtemp()
        self.assertEqual(po.load_notes(""), [])
        self.assertEqual(po.load_notes(str(Path(d) / "missing.json")), [])
        empty = Path(d) / "empty.json"
        empty.write_text("")
        self.assertEqual(po.load_notes(str(empty)), [])
        p_obj = _write(d, "obj.json", {"notes": [ORC232_NOTE, STRAY_NOTE]})
        p_items = _write(d, "items.json", {"items": [ORC232_NOTE]})
        p_results = _write(d, "results.json", {"results": [ORC232_NOTE, 5]})
        p_list = _write(d, "list.json", [ORC232_NOTE])
        self.assertEqual(len(po.load_notes(p_obj)), 2)      # raw notes
        self.assertEqual(len(po.load_notes(p_items)), 1)
        self.assertEqual(len(po.load_notes(p_results)), 1)  # non-dict dropped
        self.assertEqual(len(po.load_notes(p_list)), 1)


class LoadOverridesTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_parsed_map_ignores_stray_and_warns_on_malformed(self):
        path = _write(self.d, "n.json",
                      {"notes": [ORC232_NOTE, STRAY_NOTE, MALFORMED_NOTE]})
        overrides, warnings = po.load_overrides(path)
        self.assertEqual(list(overrides), ["ORC-232"])
        self.assertEqual(overrides["ORC-232"]["pr"], 415)
        self.assertEqual(len(warnings), 1)
        self.assertIn("3333", warnings[0])        # names the note id
        self.assertIn("malformed", warnings[0])

    def test_missing_file_is_no_overrides_not_an_error(self):
        self.assertEqual(po.load_overrides(str(Path(self.d) / "nope.json")),
                         ({}, []))
        self.assertEqual(po.load_overrides(""), ({}, []))

    def test_unreadable_dump_is_no_overrides_plus_warning_never_raises(self):
        bad = Path(self.d) / "truncated.json"
        bad.write_text('{"notes": [{"id": "n1", "content": "PR Ove')
        overrides, warnings = po.load_overrides(str(bad))
        self.assertEqual(overrides, {})
        self.assertEqual(len(warnings), 1)
        self.assertIn("override dump unreadable", warnings[0])
        self.assertIn("truncated.json", warnings[0])
        # A directory in place of the file is an OSError -> same contract.
        overrides, warnings = po.load_overrides(self.d)
        self.assertEqual(overrides, {})
        self.assertEqual(len(warnings), 1)

    def test_entries_sharing_a_note_id_dedupe_to_highest_version_silently(self):
        # The command replaced/appended the updated note in the dump after an
        # update_note: same id, higher version, new PR. No duplicate warning.
        updated = dict(ORC232_NOTE, version=4,
                       content=ORC232_NOTE["content"]
                       .replace("**PR:** 415", "**PR:** 416")
                       .replace("2026-09-02T12:00:00Z", "2026-09-02T13:00:00Z"))
        path = _write(self.d, "n.json", {"notes": [updated, ORC232_NOTE]})
        overrides, warnings = po.load_overrides(path)
        self.assertEqual(warnings, [])
        self.assertEqual(overrides["ORC-232"]["pr"], 416)
        self.assertEqual(overrides["ORC-232"]["version"], 4)
        # Even if the newer version carries an OLDER Resolved at, the higher
        # version is the one Mem holds now.
        stale_date = dict(updated, content=updated["content"]
                          .replace("2026-09-02T13:00:00Z", "2026-01-01T00:00:00Z"))
        path = _write(self.d, "n2.json", {"notes": [ORC232_NOTE, stale_date]})
        overrides, warnings = po.load_overrides(path)
        self.assertEqual(warnings, [])
        self.assertEqual(overrides["ORC-232"]["pr"], 416)

    def test_hand_edited_shapes_are_tolerated(self):
        shapes = {
            "colon outside bold": "PR Override: ORC-5\n\n**PR**: 415\n",
            "leading bullet": "PR Override: ORC-5\n\n- **PR:** 415\n",
            "star bullet + colon outside": "PR Override: ORC-5\n\n* **PR**: 415\n",
            "PR word prefix": "PR Override: ORC-5\n\n**PR:** PR 415\n",
            "PR hash": "PR Override: ORC-5\n\n**PR:** PR#415\n",
            "url with fragment": ("PR Override: ORC-5\n\n**PR:** https://github.com/"
                                  "medtrics/medtrics-oracle/pull/415#issuecomment-1\n"),
            "url with query": ("PR Override: ORC-5\n\n**PR:** https://github.com/"
                               "o/r/pull/415?diff=split\n"),
            "url files tab": "PR Override: ORC-5\n\n**PR:** https://github.com/o/r/pull/415/files\n",
        }
        for label, content in shapes.items():
            rec = po.parse_note({"id": "x", "version": 1, "content": content})
            self.assertIsNotNone(rec, label)
            self.assertEqual((rec["ticket"], rec["pr"]), ("ORC-5", 415), label)
        # Bulleted / colon-outside shapes for the other fields parse too.
        rec = po.parse_note({"id": "x", "content":
                             "PR Override: ORC-5\n- **Ticket**: ORC-5\n- **PR**: 7\n"
                             "- **Resolved by**: a@b.c\n"})
        self.assertEqual(rec["resolved_by"], "a@b.c")

    def test_ticket_field_disagreeing_with_title_warns_and_ticket_wins(self):
        path = _write(self.d, "n.json", {"notes": [{
            "id": "mismatch-note", "version": 1,
            "content": "PR Override: ORC-232\n\n**Ticket:** ORC-233\n**PR:** 9\n"}]})
        overrides, warnings = po.load_overrides(path)
        self.assertEqual(list(overrides), ["ORC-233"])
        self.assertEqual(len(warnings), 1)
        self.assertIn("mismatch-note", warnings[0])
        self.assertIn("ORC-232", warnings[0])
        self.assertIn("ORC-233", warnings[0])
        # Agreeing (or absent) Ticket field: no warning.
        rec = po.parse_note(ORC232_NOTE)
        self.assertEqual(rec["parse_warnings"], [])

    def test_duplicate_notes_newest_resolved_at_wins_with_warning(self):
        older = dict(ORC232_NOTE, id="old-note", version=1,
                     content=ORC232_NOTE["content"]
                     .replace("**PR:** 415", "**PR:** 400")
                     .replace("2026-09-02T12:00:00Z", "2026-08-30T12:00:00Z"))
        undated = dict(ORC232_NOTE, id="undated-note", version=1,
                       content=ORC232_NOTE["content"]
                       .replace("**PR:** 415", "**PR:** 300")
                       .replace("2026-09-02T12:00:00Z", "—"))
        # Order in the dump must not matter.
        path = _write(self.d, "n.json", {"notes": [undated, older, ORC232_NOTE]})
        overrides, warnings = po.load_overrides(path)
        self.assertEqual(overrides["ORC-232"]["pr"], 415)
        self.assertEqual(overrides["ORC-232"]["note_id"], ORC232_NOTE["id"])
        self.assertEqual(len(warnings), 1)
        self.assertIn("3 override notes for ORC-232", warnings[0])
        self.assertIn("415", warnings[0])


class PendingTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.notes = _write(self.d, "notes.json", {"notes": [ORC232_NOTE]})
        self.env = Path(self.d) / ".env"
        self.env.write_text("GITHUB_REPO=https://github.com/medtrics/"
                            "medtrics-oracle.git\nGITHUB_TOKEN=x\n")

    def pending(self, ticket, pr, extra=None, notes=None):
        out = str(Path(self.d) / "pending.json")
        argv = ["pending", "--ticket", ticket, "--pr", pr,
                "--notes", notes if notes is not None else self.notes,
                "--out", out, "--resolved-by", "shubham@medtricslab.com",
                "--now", "2026-09-02T13:00:00Z",
                "--env-file", str(self.env)] + (extra or [])
        code, result, err, _ = _run(argv)
        payload = json.loads(Path(out).read_text()) if Path(out).exists() else None
        return code, result, payload, err

    def test_new_ticket_yields_create_with_url_from_env_repo(self):
        code, result, out, err = self.pending("ORC-240", "500",
                                              ["--reason", "sweep missed it"])
        self.assertEqual(code, 0)
        self.assertTrue(result["ok"])
        self.assertEqual(result["write_action"], "create")
        self.assertEqual(out["collection_title"], po.COLLECTION_TITLE)
        self.assertEqual(out["collection_description"], po.COLLECTION_DESCRIPTION)
        self.assertEqual(len(out["writes"]), 1)
        w = out["writes"][0]
        self.assertEqual(w["action"], "create")
        self.assertEqual(w["collection_title"], po.COLLECTION_TITLE)
        self.assertEqual((w["ticket"], w["pr"]), ("ORC-240", 500))
        self.assertNotIn("note_id", w)
        self.assertTrue(w["content"].startswith("PR Override: ORC-240\n"))
        self.assertIn("**Ticket:** ORC-240", w["content"])
        self.assertIn("**PR:** 500", w["content"])
        self.assertIn("**PR URL:** https://github.com/medtrics/medtrics-oracle/"
                      "pull/500", w["content"])
        self.assertIn("**Resolved by:** shubham@medtricslab.com", w["content"])
        self.assertIn("**Resolved at:** 2026-09-02T13:00:00Z", w["content"])
        self.assertIn("**Reason:** sweep missed it", w["content"])
        # The content round-trips through the parser.
        rec = po.parse_note({"id": "n", "version": 1, "content": w["content"]})
        self.assertEqual((rec["ticket"], rec["pr"]), ("ORC-240", 500))

    def test_known_ticket_yields_update_with_note_id_and_version(self):
        code, result, out, err = self.pending("orc-232", "416")
        self.assertEqual(code, 0)
        self.assertEqual(result["write_action"], "update")
        w = out["writes"][0]
        self.assertEqual(w["action"], "update")
        self.assertEqual(w["note_id"], ORC232_NOTE["id"])
        self.assertEqual(w["version"], 3)
        self.assertEqual(w["previous_pr"], 415)
        self.assertEqual(w["ticket"], "ORC-232")     # normalized upper-case
        self.assertIn("**PR:** 416", w["content"])
        self.assertNotIn("**PR:** 415", w["content"])
        self.assertIn("**Resolved at:** 2026-09-02T13:00:00Z", w["content"])
        self.assertIn("**Reason:** —", w["content"])

    def test_url_filled_from_data_when_mrs_carry_the_pr(self):
        data = _write(self.d, "data.json", {"issues": [
            {"identifier": "ORC-232", "mrs": [
                {"iid": 416, "web_url": "https://github.com/medtrics/"
                                        "medtrics-oracle/pull/416?x=1"}]}]})
        code, result, out, _ = self.pending("ORC-232", "416", ["--data", data])
        self.assertEqual(result["pr_url"],
                         "https://github.com/medtrics/medtrics-oracle/pull/416?x=1")
        self.assertIn("**PR URL:** https://github.com/medtrics/medtrics-oracle/"
                      "pull/416?x=1", out["writes"][0]["content"])

    def test_url_falls_back_to_env_repo_when_data_has_no_match(self):
        data = _write(self.d, "data.json", {"issues": [
            {"identifier": "ORC-232", "mrs": [{"iid": 415, "web_url": "u415"}]}]})
        code, result, out, _ = self.pending("ORC-232", "416", ["--data", data])
        self.assertEqual(result["pr_url"],
                         "https://github.com/medtrics/medtrics-oracle/pull/416")

    def test_no_url_source_renders_dash(self):
        self.env.unlink()
        code, result, out, _ = self.pending("ORC-1", "2")
        self.assertEqual(code, 0)
        self.assertEqual(result["pr_url"], "")
        self.assertIn("**PR URL:** —", out["writes"][0]["content"])

    def test_missing_notes_file_is_create(self):
        code, result, out, _ = self.pending(
            "ORC-232", "416", notes=str(Path(self.d) / "nope.json"))
        self.assertEqual(code, 0)
        self.assertEqual(result["write_action"], "create")

    def test_bad_ticket_exits_1(self):
        for bad in ("M1-583", "ORC", "232", "ORC-"):
            code, result, out, err = self.pending(bad, "415")
            self.assertEqual(code, 1, bad)
            self.assertFalse(result["ok"])
            self.assertIn("ORC-NNN", result["error"])
            self.assertIsNone(out)

    def test_bad_pr_exits_1(self):
        for bad in ("abc", "0", "-4", "4.5", ""):
            code, result, out, err = self.pending("ORC-232", bad)
            self.assertEqual(code, 1, repr(bad))
            self.assertFalse(result["ok"])
            self.assertIn("positive integer", result["error"])
            self.assertIsNone(out)

    def test_pr_accepts_hash_prefix(self):
        code, result, out, _ = self.pending("ORC-240", "#500")
        self.assertEqual(code, 0)
        self.assertEqual(result["pr"], 500)


class ShowTests(unittest.TestCase):
    def test_show_prints_table_and_result_block(self):
        d = tempfile.mkdtemp()
        notes = _write(d, "notes.json",
                       {"notes": [ORC232_NOTE, STRAY_NOTE, MALFORMED_NOTE]})
        code, result, err, stdout = _run(["show", "--notes", notes])
        self.assertEqual(code, 0)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["overrides"], {"ORC-232": 415})
        self.assertIn("ORC-232", stdout)
        self.assertIn("415", stdout)
        self.assertIn("shubham@medtricslab.com", stdout)
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("WARNING", err)

    def test_show_missing_dump(self):
        code, result, err, stdout = _run(["show", "--notes", "/nonexistent.json"])
        self.assertEqual(code, 0)
        self.assertEqual(result["count"], 0)
        self.assertIn("No PR overrides.", stdout)


if __name__ == "__main__":
    unittest.main()
