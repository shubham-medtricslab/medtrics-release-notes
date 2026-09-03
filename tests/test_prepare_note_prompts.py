"""Tests for scripts/prepare_note_prompts.py.

Stdlib-only (unittest), hermetic. Focus: the dev-attribution contract that the
whole @-mention / client-tag feature depends on — `build_issue_header` must
emit `**Assignee Slack ID:**` and `**Clients:**` ONLY when those fields are
present on the issue object, which is exactly what the `/release-slack` and
`/oracle-release` "persist resolved fields into the data file" steps rely on.
A regression here silently drops developer mentions and client names from every
release post, with no error — so it is pinned explicitly.
"""

import importlib.util
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "prepare_note_prompts", SCRIPTS_DIR / "prepare_note_prompts.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prep = _load_module()


def _issue(**overrides):
    base = {
        "identifier": "M1-986",
        "title": "Graded Form Average Score Display",
        "url": "https://optimus.example.com/product-task/M1-986",
        "state": "QA1",
    }
    base.update(overrides)
    return base


class BuildIssueHeaderTests(unittest.TestCase):
    def test_core_fields_always_present(self):
        header = prep.build_issue_header(_issue())
        self.assertIn("# Issue: M1-986 — Graded Form Average Score Display",
                      header)
        self.assertIn("**URL:** https://optimus.example.com/product-task/M1-986",
                      header)
        self.assertIn("**State:** QA1", header)

    def test_slack_id_emitted_only_when_present(self):
        without = prep.build_issue_header(_issue())
        self.assertNotIn("Assignee Slack ID", without)

        with_id = prep.build_issue_header(_issue(assignee_slack_id="U02AB0913"))
        self.assertIn("**Assignee Slack ID:** U02AB0913", with_id)

    def test_clients_emitted_only_when_present(self):
        without = prep.build_issue_header(_issue())
        self.assertNotIn("**Clients:**", without)

        with_clients = prep.build_issue_header(_issue(clients="KFSHRC, MSUCVM"))
        self.assertIn("**Clients:** KFSHRC, MSUCVM", with_clients)

    def test_empty_string_fields_are_not_emitted(self):
        # The write-back steps only set these when non-empty; a falsy value
        # must not produce a dangling label.
        header = prep.build_issue_header(
            _issue(assignee_slack_id="", clients="", pr_links="", mr_links=""))
        self.assertNotIn("Assignee Slack ID", header)
        self.assertNotIn("**Clients:**", header)
        self.assertNotIn("**PR Links:**", header)
        self.assertNotIn("**MR Links:**", header)

    def test_pr_links_emitted_only_when_present(self):
        # /oracle-release persists pr_links (joined GitHub PR URLs) into the
        # JSON so the analyzer can copy them verbatim.
        without = prep.build_issue_header(_issue())
        self.assertNotIn("**PR Links:**", without)

        with_prs = prep.build_issue_header(_issue(
            pr_links="https://github.com/medtrics/medtrics-oracle/pull/42"))
        self.assertIn(
            "**PR Links:** https://github.com/medtrics/medtrics-oracle/pull/42",
            with_prs)

    def test_mr_links_emitted_only_when_present(self):
        # /release-slack persists mr_links (joined GitLab MR URLs) the same way.
        without = prep.build_issue_header(_issue())
        self.assertNotIn("**MR Links:**", without)

        with_mrs = prep.build_issue_header(_issue(
            mr_links="https://gitlab.com/medtrics/app/-/merge_requests/6567"))
        self.assertIn(
            "**MR Links:** https://gitlab.com/medtrics/app/-/merge_requests/6567",
            with_mrs)

    def test_assignee_and_email_and_mr_author(self):
        header = prep.build_issue_header(_issue(
            assignee_name="Chris Tokodi",
            assignee_email="chris@example.com",
            mrs=[{"author_name": "Chris Tokodi"},
                 {"author_username": "ctokodi"}],
        ))
        self.assertIn("**Assignee:** Chris Tokodi", header)
        self.assertIn("**Assignee Email:** chris@example.com", header)
        self.assertIn("**MR Author(s):** Chris Tokodi", header)

    def test_description_appended_when_present(self):
        header = prep.build_issue_header(_issue(description="Adds an average."))
        self.assertIn("## Description", header)
        self.assertIn("Adds an average.", header)


if __name__ == "__main__":
    unittest.main()
