"""Tests for scripts/slack_resolve_users.py.

Stdlib-only (unittest), hermetic — the Slack HTTP layer is mocked. Pins the
resolution contract the release commands depend on: email match writes the id,
`users_not_found` falls through to a UNIQUE full-name match, an ambiguous or
missing name leaves the id empty (plain-name fallback), the Oracle shape
(no assignee — name from mrs[].author_name) resolves by name, and a 429 is
retried. Deactivated members and bots are never matched.
"""

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "slack_resolve_users", SCRIPTS_DIR / "slack_resolve_users.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


resolver = _load_module()


class _Resp:
    """Minimal urlopen context-manager stand-in."""

    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# Directory used by the users.list mock.
MEMBERS = [
    {"id": "U0CHRIS", "real_name": "Chris Tokodi",
     "profile": {"display_name": "chris", "real_name": "Chris Tokodi"}},
    {"id": "U0JANE", "real_name": "Jane Dev",
     "profile": {"display_name": "jane", "real_name": "Jane Dev"}},
    {"id": "U0DUPE1", "real_name": "Sam Common", "profile": {}},
    {"id": "U0DUPE2", "real_name": "Sam Common", "profile": {}},   # ambiguous
    {"id": "U0CHAR", "real_name": "Charles Norris", "profile": {}},  # fuzzy target
    {"id": "U0PAT1", "real_name": "Pat Lee", "profile": {}},         # near-tie A
    {"id": "U0PAT2", "real_name": "Pat Loe", "profile": {}},         # near-tie B
    {"id": "U0GONE", "deleted": True, "real_name": "Ghost User",
     "profile": {}},
    {"id": "B0BOT", "is_bot": True, "real_name": "Release Bot", "profile": {}},
]

# email -> Slack id for the lookupByEmail mock; missing => users_not_found.
EMAIL_DIRECTORY = {"chris@medtricslab.com": "U0CHRIS"}


def fake_urlopen_factory(email_dir=None, members=None, fail_first_429=False):
    email_dir = EMAIL_DIRECTORY if email_dir is None else email_dir
    members = MEMBERS if members is None else members
    state = {"n_calls": 0}

    def fake_urlopen(req, timeout=None):
        url = req.full_url
        path, _, query = url.partition("?")
        params = dict(urllib.parse.parse_qsl(query))
        state["n_calls"] += 1

        if fail_first_429 and state["n_calls"] == 1:
            raise urllib.error.HTTPError(url, 429, "Too Many Requests",
                                         {"Retry-After": "0"}, io.BytesIO(b"{}"))

        if path.endswith("/users.lookupByEmail"):
            uid = email_dir.get(params.get("email"))
            if uid:
                return _Resp({"ok": True, "user": {"id": uid}})
            return _Resp({"ok": False, "error": "users_not_found"})
        if path.endswith("/users.list"):
            return _Resp({"ok": True, "members": members,
                          "response_metadata": {"next_cursor": ""}})
        raise AssertionError(f"unexpected Slack call: {url}")

    return fake_urlopen


class ResolveHarness(unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(
            "os.environ", {"SLACK_BOT_TOKEN": "xoxb-test"})
        self._env.start()
        self.addCleanup(self._env.stop)

    def run_resolver(self, issues, extra_argv=None, **factory_kwargs):
        d = tempfile.mkdtemp()
        data_path = str(Path(d) / "data.json")
        Path(data_path).write_text(json.dumps({"issues": issues}))
        argv = ["slack_resolve_users.py", "--data", data_path,
                "--env-path", str(Path(d) / "no.env")]
        if extra_argv:
            argv += extra_argv
        stderr = io.StringIO()
        with mock.patch.object(resolver.urllib.request, "urlopen",
                               fake_urlopen_factory(**factory_kwargs)), \
                mock.patch.object(resolver.sys, "argv", argv), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(stderr):
            code = resolver.main()
        result = json.loads(Path(data_path).read_text())
        return code, result["issues"], stderr.getvalue()


class EmailMatchTests(ResolveHarness):
    def test_email_hit_writes_id(self):
        code, issues, _ = self.run_resolver(
            [{"identifier": "M1-1", "assignee_email": "chris@medtricslab.com",
              "assignee_name": "Chris Tokodi"}])
        self.assertEqual(code, 0)
        self.assertEqual(issues[0]["assignee_slack_id"], "U0CHRIS")

    def test_email_miss_falls_through_to_name(self):
        # Email not in the directory → users_not_found → unique name match.
        code, issues, _ = self.run_resolver(
            [{"identifier": "M1-2", "assignee_email": "nobody@medtricslab.com",
              "assignee_name": "Jane Dev"}])
        self.assertEqual(issues[0]["assignee_slack_id"], "U0JANE")

    def test_no_name_fallback_flag_stops_at_email(self):
        code, issues, _ = self.run_resolver(
            [{"identifier": "M1-3", "assignee_email": "nobody@medtricslab.com",
              "assignee_name": "Jane Dev"}],
            extra_argv=["--no-name-fallback"])
        self.assertEqual(issues[0]["assignee_slack_id"], "")


class NameMatchTests(ResolveHarness):
    def test_oracle_shape_resolves_by_pr_author_name(self):
        # No assignee_email / assignee_name; developer is the PR author.
        code, issues, _ = self.run_resolver(
            [{"identifier": "ORC-9", "mrs": [{"author_name": "Chris Tokodi"}]}])
        self.assertEqual(issues[0]["assignee_slack_id"], "U0CHRIS")

    def test_ambiguous_name_is_not_tagged(self):
        code, issues, _ = self.run_resolver(
            [{"identifier": "ORC-10", "mrs": [{"author_name": "Sam Common"}]}])
        self.assertEqual(issues[0]["assignee_slack_id"], "")

    def test_unknown_name_left_empty(self):
        code, issues, _ = self.run_resolver(
            [{"identifier": "ORC-11", "mrs": [{"author_name": "Nobody Here"}]}])
        self.assertEqual(issues[0]["assignee_slack_id"], "")

    def test_single_token_name_never_matches(self):
        # "chris" would hit U0CHRIS's display_name, but a one-word name (e.g. a
        # GitHub login fallback) must never tag — only full multi-token names do.
        code, issues, _ = self.run_resolver(
            [{"identifier": "ORC-12", "mrs": [{"author_name": "chris"}]}])
        self.assertEqual(issues[0]["assignee_slack_id"], "")

    def test_deactivated_and_bot_never_match(self):
        code, issues, _ = self.run_resolver([
            {"identifier": "X-1", "mrs": [{"author_name": "Ghost User"}]},
            {"identifier": "X-2", "mrs": [{"author_name": "Release Bot"}]},
        ])
        self.assertEqual(issues[0]["assignee_slack_id"], "")
        self.assertEqual(issues[1]["assignee_slack_id"], "")


class GithubEmailTierTests(ResolveHarness):
    def test_oracle_pr_author_email_resolves_by_email(self):
        # The Oracle gather writes the PR author's real email onto mrs[].
        # author_email; the email tier should use it (no name match needed).
        code, issues, _ = self.run_resolver(
            [{"identifier": "ORC-20",
              "mrs": [{"author_name": "Charles Norris",
                       "author_email": "chris@medtricslab.com"}]}])
        self.assertEqual(issues[0]["assignee_slack_id"], "U0CHRIS")


class FuzzyMatchTests(ResolveHarness):
    def test_fuzzy_typo_matches(self):
        # "Charles Noris" (missing r) fuzzy-matches "Charles Norris".
        code, issues, _ = self.run_resolver(
            [{"identifier": "ORC-21", "mrs": [{"author_name": "Charles Noris"}]}])
        self.assertEqual(issues[0]["assignee_slack_id"], "U0CHAR")

    def test_fuzzy_near_tie_left_unresolved(self):
        # "Pat Lue" is ~equally close to "Pat Lee" and "Pat Loe" → no tag.
        code, issues, _ = self.run_resolver(
            [{"identifier": "ORC-22", "mrs": [{"author_name": "Pat Lue"}]}])
        self.assertEqual(issues[0]["assignee_slack_id"], "")

    def test_no_fuzzy_flag_disables_tier(self):
        code, issues, _ = self.run_resolver(
            [{"identifier": "ORC-23", "mrs": [{"author_name": "Charles Noris"}]}],
            extra_argv=["--no-fuzzy"])
        self.assertEqual(issues[0]["assignee_slack_id"], "")


class PreresolvedAndProvenanceTests(ResolveHarness):
    def test_preresolved_issue_is_skipped(self):
        # A Slack id already on the issue (e.g. from the Mem developer
        # directory) must survive untouched — even though the email would
        # resolve to a different id.
        code, issues, err = self.run_resolver(
            [{"identifier": "M1-30", "assignee_email": "chris@medtricslab.com",
              "assignee_name": "Chris Tokodi",
              "assignee_slack_id": "U0FROMMEM",
              "assignee_slack_resolved_via": "mem"}])
        self.assertEqual(code, 0)
        self.assertEqual(issues[0]["assignee_slack_id"], "U0FROMMEM")
        self.assertEqual(issues[0]["assignee_slack_resolved_via"], "mem")
        self.assertIn("1 pre-resolved", err)

    def test_force_re_resolves_preresolved_issue(self):
        code, issues, _ = self.run_resolver(
            [{"identifier": "M1-31", "assignee_email": "chris@medtricslab.com",
              "assignee_slack_id": "U0FROMMEM"}],
            extra_argv=["--force"])
        self.assertEqual(issues[0]["assignee_slack_id"], "U0CHRIS")
        self.assertEqual(issues[0]["assignee_slack_resolved_via"], "email")

    def test_resolved_via_is_stamped_per_tier(self):
        code, issues, _ = self.run_resolver([
            {"identifier": "T-email", "assignee_email": "chris@medtricslab.com"},
            {"identifier": "T-name", "mrs": [{"author_name": "Jane Dev"}]},
            {"identifier": "T-fuzzy", "mrs": [{"author_name": "Charles Noris"}]},
            {"identifier": "T-none", "mrs": [{"author_name": "Nobody Here"}]},
        ])
        via = {i["identifier"]: i["assignee_slack_resolved_via"] for i in issues}
        self.assertEqual(via, {"T-email": "email", "T-name": "name",
                               "T-fuzzy": "fuzzy", "T-none": ""})

    def test_result_block_lists_resolutions(self):
        d = tempfile.mkdtemp()
        data_path = str(Path(d) / "data.json")
        Path(data_path).write_text(json.dumps({"issues": [
            {"identifier": "M1-40", "assignee_email": "chris@medtricslab.com"},
            {"identifier": "M1-41", "assignee_slack_id": "U0FROMMEM"},
        ]}))
        argv = ["slack_resolve_users.py", "--data", data_path,
                "--env-path", str(Path(d) / "no.env")]
        stdout = io.StringIO()
        with mock.patch.object(resolver.urllib.request, "urlopen",
                               fake_urlopen_factory()), \
                mock.patch.object(resolver.sys, "argv", argv), \
                contextlib.redirect_stdout(stdout), \
                contextlib.redirect_stderr(io.StringIO()):
            resolver.main()
        block = stdout.getvalue().split("---SLACK_RESOLVE_RESULT---")[1].strip()
        result = json.loads(block)
        self.assertEqual(result["preresolved"], 1)
        self.assertEqual(result["resolutions"],
                         [{"identifier": "M1-40", "slack_id": "U0CHRIS",
                           "via": "email"}])


class RetryAndConfigTests(ResolveHarness):
    def test_429_is_retried_then_succeeds(self):
        code, issues, _ = self.run_resolver(
            [{"identifier": "M1-4", "assignee_email": "chris@medtricslab.com"}],
            fail_first_429=True)
        self.assertEqual(code, 0)
        self.assertEqual(issues[0]["assignee_slack_id"], "U0CHRIS")

    def test_missing_token_is_config_error(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            d = tempfile.mkdtemp()
            data_path = str(Path(d) / "data.json")
            Path(data_path).write_text(json.dumps({"issues": []}))
            argv = ["slack_resolve_users.py", "--data", data_path,
                    "--env-path", str(Path(d) / "no.env")]
            with mock.patch.object(resolver.sys, "argv", argv), \
                    contextlib.redirect_stderr(io.StringIO()), \
                    contextlib.redirect_stdout(io.StringIO()):
                code = resolver.main()
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
