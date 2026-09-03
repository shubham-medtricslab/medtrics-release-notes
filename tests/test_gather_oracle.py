"""Tests for scripts/gather_notes_data_oracle.py.

Stdlib-only (unittest + unittest.mock), hermetic — no network. Covers:
  - github_api retry classification (the load-bearing rule: retryable iff
    429 OR x-ratelimit-remaining == "0" OR a Retry-After header is present —
    GitHub secondary limits send 403 + Retry-After with NONZERO remaining)
  - proactive throttle, wait capping at 120s, 5-attempt exhaustion
  - Link-header pagination + the absent-Link full-page fallback
  - fetch_pr_diffs normalization (previous_filename -> old_path, missing
    patch -> "")
  - the one-sweep-per-run PR discovery, local matching (ORC-59 vs ORC-597),
    dedupe, the search secondary for unmatched tickets, and the loud warning
    that keeps a ticket with empty mrs
  - load_env GITHUB_REPO normalization + validation
  - the startup canary hard-exiting on failure

NOTE: the oracle script inserts its own directory into sys.path and does
`import gather_notes_data_optimus as base` at module top — two module
objects may exist under this loader, so mocks target `oracle.base.<fn>`
(the SAME module object the oracle script uses), never a separately-loaded
copy.
"""

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


def _load_module():
    # Ensure the script's own `import gather_notes_data_optimus` resolves
    # even before the script's self-insert runs.
    scripts_dir = str(SCRIPTS_DIR)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location(
        "gather_notes_data_oracle", SCRIPTS_DIR / "gather_notes_data_oracle.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


oracle = _load_module()

ENV = {
    "OPTIMUS_API_KEY": "test-optimus-key",
    "GITHUB_TOKEN": "test-github-token",
    "GITHUB_REPO": "medtrics/medtrics-oracle",
    "GITHUB_API_URL": "https://api.github.com",
}

NOW = 1_760_000_000  # fixed clock for deterministic wait computation


def _http_error(code, headers=None):
    msg = Message()
    for k, v in (headers or {}).items():
        msg[k] = str(v)
    return urllib.error.HTTPError(
        "https://api.github.com/test", code, "err", msg,
        io.BytesIO(b"upstream error"))


class _FakeResponse:
    def __init__(self, payload, headers=None):
        self._payload = json.dumps(payload).encode("utf-8")
        self._headers = headers or {}

    def read(self):
        return self._payload

    def getheaders(self):
        return list(self._headers.items())

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _run_github_api(responses, path="/repos/medtrics/medtrics-oracle/pulls",
                    params=None, now=NOW):
    """Drive oracle.github_api against a scripted list of responses.

    Returns (result, headers, sleep_mock, urlopen_call_count).
    """
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req)
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    with mock.patch.object(oracle.urllib.request, "urlopen",
                           side_effect=fake_urlopen), \
            mock.patch.object(oracle.time, "sleep") as fake_sleep, \
            mock.patch.object(oracle.time, "time", return_value=now), \
            mock.patch.object(oracle, "log"):
        data, headers = oracle.github_api(ENV, path, params)
    return data, headers, fake_sleep, len(calls)


class GithubApiTests(unittest.TestCase):
    def test_success_returns_data_and_lowercased_headers(self):
        payload = [{"number": 1}]
        resp = _FakeResponse(payload, headers={
            "X-RateLimit-Remaining": "4999",
            "Link": '<https://api.github.com/x?page=2>; rel="next"',
        })
        data, headers, fake_sleep, n = _run_github_api([resp])
        self.assertEqual(data, payload)
        self.assertEqual(headers["x-ratelimit-remaining"], "4999")
        self.assertIn("link", headers)
        self.assertNotIn("Link", headers)
        fake_sleep.assert_not_called()
        self.assertEqual(n, 1)

    def test_429_with_retry_after_sleeps_that_value_then_succeeds(self):
        responses = [_http_error(429, {"Retry-After": "7"}),
                     _FakeResponse({"ok": True})]
        data, _, fake_sleep, n = _run_github_api(responses)
        self.assertEqual(data, {"ok": True})
        fake_sleep.assert_called_once_with(7)
        self.assertEqual(n, 2)

    def test_403_remaining_zero_retries_with_reset_wait_capped_at_120(self):
        responses = [
            _http_error(403, {"x-ratelimit-remaining": "0",
                              "x-ratelimit-reset": str(NOW + 500)}),
            _FakeResponse({"ok": True}),
        ]
        data, _, fake_sleep, n = _run_github_api(responses)
        self.assertEqual(data, {"ok": True})
        fake_sleep.assert_called_once_with(120)  # 501 capped at 120
        self.assertEqual(n, 2)

    def test_403_with_retry_after_and_nonzero_remaining_retries(self):
        # GitHub secondary rate limits: 403 + Retry-After with NONZERO
        # remaining. Misclassifying this as non-retryable is the bug the
        # classification rule exists to prevent — assert it DOES retry.
        responses = [
            _http_error(403, {"Retry-After": "3",
                              "x-ratelimit-remaining": "42"}),
            _FakeResponse({"ok": True}),
        ]
        data, _, fake_sleep, n = _run_github_api(responses)
        self.assertEqual(data, {"ok": True})
        fake_sleep.assert_called_once_with(3)
        self.assertEqual(n, 2)

    def test_plain_403_does_not_retry(self):
        # No rate-limit exhaustion, no Retry-After: a real permission error.
        responses = [_http_error(403)]
        data, headers, fake_sleep, n = _run_github_api(responses)
        self.assertIsNone(data)
        self.assertEqual(headers, {})
        fake_sleep.assert_not_called()
        self.assertEqual(n, 1)

    def test_persistent_500_exhausts_five_attempts_never_raises(self):
        responses = [_http_error(500) for _ in range(5)]
        try:
            data, headers, fake_sleep, n = _run_github_api(responses)
        except SystemExit:
            self.fail("github_api called sys.exit on persistent 500")
        self.assertIsNone(data)
        self.assertEqual(headers, {})
        self.assertEqual(n, 5)
        self.assertEqual(fake_sleep.call_count, 4)  # sleeps between attempts

    def test_url_error_retried_then_success(self):
        responses = [urllib.error.URLError("connection refused"),
                     TimeoutError("timed out"),
                     _FakeResponse({"ok": True})]
        data, _, fake_sleep, n = _run_github_api(responses)
        self.assertEqual(data, {"ok": True})
        self.assertEqual(n, 3)
        self.assertEqual(fake_sleep.call_count, 2)

    def test_proactive_throttle_sleeps_when_remaining_low(self):
        resp = _FakeResponse({"ok": True}, headers={
            "x-ratelimit-remaining": "3",
            "x-ratelimit-reset": str(NOW + 10),
        })
        data, _, fake_sleep, _ = _run_github_api([resp])
        self.assertEqual(data, {"ok": True})
        fake_sleep.assert_called_once_with(11)  # reset - now + 1


class LinkPaginationTests(unittest.TestCase):
    def test_next_page_from_link_parses_full_next_url(self):
        headers = {"link": ('<https://api.github.com/repositories/1/pulls'
                            '?page=2&per_page=100>; rel="next", '
                            '<https://api.github.com/repositories/1/pulls'
                            '?page=5&per_page=100>; rel="last"')}
        self.assertEqual(
            oracle._next_page_from_link(headers),
            "https://api.github.com/repositories/1/pulls?page=2&per_page=100")

    def test_next_page_from_link_returns_none_when_absent(self):
        self.assertIsNone(oracle._next_page_from_link({}))
        self.assertIsNone(oracle._next_page_from_link(
            {"link": '<https://x>; rel="last"'}))

    def test_pagination_fallback_full_page_no_link_then_short_page_stops(self):
        calls = []

        def fake_api(env, path, params=None):
            calls.append((path, dict(params or {})))
            if params["page"] == "1":
                return [{"number": i} for i in range(100)], {}  # full, no Link
            return [{"number": 100}], {}  # short page -> stop

        with mock.patch.object(oracle, "github_api", side_effect=fake_api):
            items = oracle.github_paginate(
                ENV, "/repos/medtrics/medtrics-oracle/pulls",
                {"state": "all"}, per_page=100)
        self.assertEqual(len(items), 101)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1]["page"], "1")
        self.assertEqual(calls[1][1]["page"], "2")


class FetchPrDiffsTests(unittest.TestCase):
    def test_paginates_via_link_and_normalizes_schema(self):
        page1 = [{"filename": f"src/f{i}.tsx", "patch": f"@@ -{i}"}
                 for i in range(100)]
        page2 = [
            {"filename": "src/new_name.ts",
             "previous_filename": "src/old_name.ts",
             "patch": "@@ rename"},
            {"filename": "assets/logo.png"},  # no patch (binary)
        ]
        next_url = "https://api.github.com/repos/x/y/pulls/12/files?page=2"
        calls = []

        def fake_api(env, path, params=None):
            calls.append(path)
            if path.startswith("https://"):
                return page2, {}
            return page1, {"link": f'<{next_url}>; rel="next"'}

        with mock.patch.object(oracle, "github_api", side_effect=fake_api):
            diffs = oracle.fetch_pr_diffs(ENV, "medtrics/medtrics-oracle", 12)

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], "/repos/medtrics/medtrics-oracle/pulls/12/files")
        self.assertEqual(calls[1], next_url)
        self.assertEqual(len(diffs), 102)
        # Plain file: old_path falls back to filename.
        self.assertEqual(diffs[0], {"old_path": "src/f0.tsx",
                                    "new_path": "src/f0.tsx",
                                    "diff": "@@ -0"})
        # Renamed file: previous_filename -> old_path.
        self.assertEqual(diffs[100], {"old_path": "src/old_name.ts",
                                      "new_path": "src/new_name.ts",
                                      "diff": "@@ rename"})
        # Missing patch -> empty diff.
        self.assertEqual(diffs[101]["diff"], "")


def _pr(number, branch="", title="", login="octo", name=None,
        url="https://github.com/medtrics/medtrics-oracle/pull/{n}",
        state="open", draft=False, merged_at=None,
        updated_at="2026-09-01T00:00:00Z"):
    user = {"login": login}
    if name is not None:
        user["name"] = name
    return {"number": number, "head": {"ref": branch}, "title": title,
            "html_url": url.format(n=number), "user": user,
            "state": state, "draft": draft, "merged_at": merged_at,
            "updated_at": updated_at}


def _issue(identifier, linear="", created_at=None):
    return {"identifier": identifier, "linear_identifier": linear,
            "title": identifier, "mrs": [], "created_at": created_at}


def _merged(number, branch, merged_at, **kw):
    """A closed PR that was merged at `merged_at`."""
    return _pr(number, branch=branch, state="closed", merged_at=merged_at,
               updated_at=merged_at, **kw)


def _entries(*prs):
    """Raw PR dicts -> mrs entries, as discover_prs builds them."""
    return [oracle._pr_entry(p, "branch") for p in prs]


def _discover(issues, fake_api, overrides=None):
    """Run discover_prs against a scripted github_api; returns run_stats."""
    with mock.patch.object(oracle, "github_api", side_effect=fake_api), \
            mock.patch.object(oracle, "log"):
        _issues, run_stats = oracle.discover_prs(ENV, issues, overrides)
    return run_stats


def _mem_override(ticket, pr, resolved_at="2026-09-02T12:00:00Z",
                  note_id="note-1"):
    return {"ticket": ticket, "pr": pr, "pr_url": "",
            "resolved_by": "shubham@medtricslab.com",
            "resolved_at": resolved_at, "reason": "picked at preview",
            "note_id": note_id, "version": 1}


class SweepMatchingTests(unittest.TestCase):
    def test_match_field_reports_branch_before_title(self):
        both = _pr(1, branch="fix/orc-232/x", title="ORC-232 fix")
        self.assertEqual(oracle.match_field(both, ["ORC-232"]), "branch")
        title_only = _pr(2, branch="feature/consent", title="ORC-232 fix")
        self.assertEqual(oracle.match_field(title_only, ["ORC-232"]), "title")
        # Branch wins even when only the SECOND identifier hits the branch
        # and the first hits the title.
        mixed = _pr(3, branch="fix/gen-555/x", title="ORC-12 migrated")
        self.assertEqual(oracle.match_field(mixed, ["ORC-12", "GEN-555"]),
                         "branch")
        self.assertIsNone(oracle.match_field(
            _pr(4, branch="fix/orc-2320/x", title="ORC-2320"), ["ORC-232"]))

    def test_pr_matches_is_match_field_not_none(self):
        self.assertTrue(oracle._pr_matches(
            _pr(1, title="ORC 7 spaces"), ["ORC-7"]))
        self.assertFalse(oracle._pr_matches(_pr(1, title="ORC-70"), ["ORC-7"]))

    def test_branch_match_fix_orc_232(self):
        pulls = [_pr(456, branch="fix/orc-232/autosave-drafts",
                     title="Fix autosave")]
        matched = oracle.match_prs_to_issue(_issue("ORC-232"), pulls)
        self.assertEqual([p["number"] for p in matched], [456])

    def test_orc_59_does_not_match_orc_597(self):
        pulls = [_pr(9, branch="fix/orc-597/thing", title="ORC-597 fix")]
        self.assertEqual(oracle.match_prs_to_issue(_issue("ORC-59"), pulls), [])
        # ...but ORC-597 itself does match.
        self.assertEqual(
            [p["number"] for p in
             oracle.match_prs_to_issue(_issue("ORC-597"), pulls)], [9])

    def test_dedupe_by_number_across_branch_and_title_matches(self):
        # One PR matching by branch AND title, plus a second identifier
        # (linear) also matching — must appear exactly once.
        pulls = [_pr(7, branch="feat/orc-101/x", title="ORC-101 feature")]
        issue = _issue("ORC-101", linear="ORC-101")
        matched = oracle.match_prs_to_issue(issue, pulls)
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["number"], 7)

    def test_linear_identifier_also_tried_when_distinct(self):
        pulls = [_pr(3, branch="fix/gen-555/legacy", title="migrated work")]
        issue = _issue("ORC-12", linear="GEN-555")
        matched = oracle.match_prs_to_issue(issue, pulls)
        self.assertEqual([p["number"] for p in matched], [3])


class DiscoveryOrchestrationTests(unittest.TestCase):
    def test_single_sweep_for_multi_ticket_run(self):
        # THE core call-count assertion: /pulls list fetched exactly ONCE for
        # a 3-ticket run (one sweep per run, not per ticket).
        pulls = [_pr(1, branch="fix/orc-1/a"), _pr(2, branch="fix/orc-2/b"),
                 _pr(3, branch="fix/orc-3/c")]
        calls = []

        def fake_api(env, path, params=None):
            calls.append(path)
            if path.endswith("/pulls"):
                return pulls, {}  # short page -> single fetch
            self.fail(f"unexpected GitHub call: {path}")

        issues = [_issue("ORC-1"), _issue("ORC-2"), _issue("ORC-3")]
        with mock.patch.object(oracle, "github_api", side_effect=fake_api), \
                mock.patch.object(oracle, "log"):
            oracle.discover_prs(ENV, issues)

        pull_list_calls = [p for p in calls if p.endswith("/pulls")]
        self.assertEqual(len(pull_list_calls), 1)
        for i, issue in enumerate(issues, start=1):
            self.assertEqual([m["iid"] for m in issue["mrs"]], [i])

    def test_unmatched_ticket_runs_search_secondary_and_warns_kept_empty(self):
        calls = []

        def fake_api(env, path, params=None):
            calls.append((path, dict(params or {})))
            if path.endswith("/pulls"):
                return [], {}  # sweep finds nothing
            if path == "/search/issues":
                return {"items": []}, {}
            self.fail(f"unexpected GitHub call: {path}")

        issue = _issue("ORC-404")
        with mock.patch.object(oracle, "github_api", side_effect=fake_api), \
                mock.patch.object(oracle, "log") as fake_log:
            oracle.discover_prs(ENV, [issue])

        search_calls = [c for c in calls if c[0] == "/search/issues"]
        self.assertEqual(len(search_calls), 1)
        self.assertIn("ORC-404", search_calls[0][1]["q"])
        self.assertIn("is:pr", search_calls[0][1]["q"])
        warnings = [c.args[0] for c in fake_log.call_args_list
                    if "WARNING" in c.args[0] and "ORC-404" in c.args[0]]
        self.assertTrue(warnings, "expected a loud WARNING for the "
                                  "PR-less ticket")
        self.assertIn("without code context", warnings[0])
        # Ticket is kept with empty mrs — never silently omitted.
        self.assertEqual(issue["mrs"], [])

    def test_search_secondary_confirms_each_hit_via_pull_fetch(self):
        # Search items are issue-shaped (no head.ref) — each must be
        # confirmed by fetching the real PR and re-matching.
        def fake_api(env, path, params=None):
            if path.endswith("/pulls"):
                return [], {}
            if path == "/search/issues":
                return {"items": [{"number": 77}, {"number": 88}]}, {}
            if path.endswith("/pulls/77"):
                return _pr(77, branch="fix/orc-9/old-work"), {}
            if path.endswith("/pulls/88"):
                return _pr(88, branch="fix/orc-90/other"), {}  # ORC-90 != ORC-9
            self.fail(f"unexpected GitHub call: {path}")

        issue = _issue("ORC-9")
        with mock.patch.object(oracle, "github_api", side_effect=fake_api), \
                mock.patch.object(oracle, "log"):
            oracle.discover_prs(ENV, [issue])
        self.assertEqual([m["iid"] for m in issue["mrs"]], [77])
        # Search hits are re-matched, so they report the real field.
        self.assertEqual(issue["mrs"][0]["matched_via"], "branch")
        self.assertEqual(issue["pr_discovery"]["decision"], "matched")
        self.assertEqual(issue["pr_discovery"]["primary"], 77)

    def test_top_level_summary_counts_and_warnings_in_json(self):
        pulls = [_pr(1, branch="fix/orc-1/a"),
                 _pr(2, branch="fix/orc-2/a"), _pr(3, branch="fix/orc-2/b"),
                 _merged(4, "fix/orc-3/a", "2026-08-01T00:00:00Z")]

        def fake_api(env, path, params=None):
            if path.endswith("/pulls"):
                return pulls, {}
            if path == "/search/issues":
                return {"items": []}, {}
            self.fail(f"unexpected GitHub call: {path}")

        issues = [_issue("ORC-1"), _issue("ORC-2"), _issue("ORC-3"),
                  _issue("ORC-404")]
        run_stats = _discover(issues, fake_api)

        self.assertEqual(run_stats["summary"], {
            "override": 0, "matched": 2, "flagged_ambiguous": 1,
            "no_pr_yet": 1, "no_pr_in_window": 0})
        self.assertEqual(run_stats["sweep"]["pr_count"], 4)
        self.assertEqual(run_stats["sweep"]["pages"], 1)
        self.assertFalse(run_stats["sweep"]["window_full"])
        self.assertFalse(run_stats["sweep"]["failed"])
        self.assertEqual(run_stats["sweep"]["oldest_updated_at"],
                         "2026-08-01T00:00:00Z")
        self.assertEqual(run_stats["overrides_loaded"], 0)
        self.assertRegex(run_stats["swept_at"], r"^\d{4}-\d{2}-\d{2}T.*Z$")
        # The warning text is verbatim in the JSON (no "WARNING:" prefix) so
        # the command reads it from the file, not from scrollback.
        self.assertIn("no PRs found for ORC-404 — it will appear without "
                      "code context", run_stats["warnings"])
        self.assertEqual(issues[1]["pr_discovery"]["candidates"], [2, 3])
        self.assertIsNone(issues[1]["pr_discovery"]["primary"])
        self.assertEqual(issues[3]["pr_discovery"]["decision"], "no_pr_yet")
        for issue in issues:
            self.assertIn("pr_discovery", issue)
            self.assertIsNone(issue["pr_discovery"]["override"])

    def test_sweep_failure_sets_failed_and_warns(self):
        calls = []

        def fake_api(env, path, params=None):
            calls.append(path)
            if path.endswith("/pulls"):
                return None, {}  # exhausted retries inside github_api
            if path == "/search/issues":
                return {"items": []}, {}
            self.fail(f"unexpected GitHub call: {path}")

        issue = _issue("ORC-1")
        run_stats = _discover([issue], fake_api)
        self.assertTrue(run_stats["sweep"]["failed"])
        self.assertEqual(run_stats["sweep"]["pr_count"], 0)
        self.assertTrue(any("sweep failed" in w for w in run_stats["warnings"]),
                        run_stats["warnings"])
        # The ticket is no_pr_yet (not no_pr_in_window) and still present.
        self.assertEqual(issue["pr_discovery"]["decision"], "no_pr_yet")
        self.assertEqual(issue["mrs"], [])

    def test_search_failure_is_recorded_and_ticket_falls_to_no_pr_yet(self):
        def fake_api(env, path, params=None):
            if path.endswith("/pulls"):
                return [], {}
            if path == "/search/issues":
                return None, {}
            self.fail(f"unexpected GitHub call: {path}")

        issue = _issue("ORC-5")
        run_stats = _discover([issue], fake_api)
        self.assertEqual(issue["pr_discovery"]["decision"], "no_pr_yet")
        self.assertTrue(any("/search/issues failed" in w
                            for w in run_stats["warnings"]))


class PrimaryRuleTests(unittest.TestCase):
    """choose_primary — pure; operates on mrs entries."""

    def test_one_open_pr_is_primary(self):
        mrs = _entries(_pr(415, branch="fix/orc-232/a"))
        self.assertEqual(oracle.choose_primary(mrs), ("matched", 415, []))

    def test_one_merged_plus_one_closed_unmerged_picks_merged(self):
        mrs = _entries(_pr(10, state="closed"),
                       _merged(11, "fix/orc-1/b", "2026-08-01T00:00:00Z"))
        self.assertEqual(oracle.choose_primary(mrs), ("matched", 11, []))

    def test_two_merged_picks_newest_merged_at(self):
        # ORC-53 shape: two merged PRs; the newer merge is the primary.
        mrs = _entries(_merged(20, "fix/orc-53/a", "2026-07-01T10:00:00Z"),
                       _merged(21, "fix/orc-53/b", "2026-07-03T10:00:00Z"))
        self.assertEqual(oracle.choose_primary(mrs), ("matched", 21, []))
        # Order in mrs must not matter.
        self.assertEqual(oracle.choose_primary(list(reversed(mrs))),
                         ("matched", 21, []))

    def test_two_open_non_draft_is_flagged_with_both_candidates(self):
        mrs = _entries(_pr(415), _pr(416))
        self.assertEqual(oracle.choose_primary(mrs),
                         ("flagged_ambiguous", None, [415, 416]))

    def test_two_open_one_draft_picks_the_non_draft(self):
        mrs = _entries(_pr(415, draft=True), _pr(416))
        self.assertEqual(oracle.choose_primary(mrs), ("matched", 416, []))

    def test_two_open_both_draft_is_flagged(self):
        mrs = _entries(_pr(415, draft=True), _pr(416, draft=True))
        self.assertEqual(oracle.choose_primary(mrs),
                         ("flagged_ambiguous", None, [415, 416]))

    def test_open_pr_beats_merged_pr(self):
        mrs = _entries(_merged(20, "fix/orc-5/a", "2026-07-01T10:00:00Z"),
                       _pr(21, branch="fix/orc-5/b"))
        self.assertEqual(oracle.choose_primary(mrs), ("matched", 21, []))

    def test_only_closed_unmerged_is_matched_with_null_primary(self):
        mrs = _entries(_pr(30, state="closed"), _pr(31, state="closed"))
        self.assertEqual(oracle.choose_primary(mrs), ("matched", None, []))

    def test_no_prs_is_no_pr_yet(self):
        self.assertEqual(oracle.choose_primary([]), ("no_pr_yet", None, []))

    def test_orchestration_reorders_mrs_primary_first(self):
        pulls = [_merged(20, "fix/orc-53/a", "2026-07-01T10:00:00Z"),
                 _merged(21, "fix/orc-53/b", "2026-07-03T10:00:00Z")]

        def fake_api(env, path, params=None):
            if path.endswith("/pulls"):
                return pulls, {}
            self.fail(f"unexpected GitHub call: {path}")

        issue = _issue("ORC-53")
        _discover([issue], fake_api)
        self.assertEqual([m["iid"] for m in issue["mrs"]], [21, 20])
        self.assertEqual(issue["pr_discovery"]["primary"], 21)
        self.assertEqual(issue["pr_discovery"]["decision"], "matched")


class OverrideTests(unittest.TestCase):
    def _api(self, pulls, extra=None, calls=None):
        def fake_api(env, path, params=None):
            if calls is not None:
                calls.append(path)
            if path.endswith("/pulls"):
                return pulls, {}
            if path == "/search/issues":
                return {"items": []}, {}
            if extra and path in extra:
                return extra[path]
            self.fail(f"unexpected GitHub call: {path}")
        return fake_api

    def test_override_wins_over_sweep_and_marks_manual(self):
        # Two open PRs would be flagged_ambiguous; the override settles it.
        pulls = [_pr(415, branch="fix/orc-232/a"),
                 _pr(416, branch="fix/orc-232/b")]
        issue = _issue("ORC-232")
        overrides = {"ORC-232": _mem_override("ORC-232", 416)}
        run_stats = _discover([issue], self._api(pulls), overrides)

        disc = issue["pr_discovery"]
        self.assertEqual(disc["decision"], "override")
        self.assertEqual(disc["primary"], 416)
        self.assertEqual(disc["candidates"], [])
        self.assertEqual(disc["override"], {
            "pr": 416, "resolved_by": "shubham@medtricslab.com",
            "resolved_at": "2026-09-02T12:00:00Z",
            "reason": "picked at preview", "note_id": "note-1"})
        # Both PRs kept, primary first, override entry marked manual.
        self.assertEqual([m["iid"] for m in issue["mrs"]], [416, 415])
        self.assertEqual(issue["mrs"][0]["matched_via"], "manual")
        self.assertEqual(issue["mrs"][1]["matched_via"], "branch")
        self.assertEqual(run_stats["summary"]["override"], 1)
        self.assertEqual(run_stats["overrides_loaded"], 1)

    def test_override_pr_not_in_sweep_fetched_exactly_once(self):
        calls = []
        fetched = _pr(99, branch="fix/orc-1/old", state="closed",
                      merged_at="2026-01-01T00:00:00Z")
        api = self._api([], {"/repos/medtrics/medtrics-oracle/pulls/99":
                             (fetched, {})}, calls)
        issue = _issue("ORC-1")
        _discover([issue], api, {"ORC-1": _mem_override("ORC-1", 99)})

        pull_fetches = [p for p in calls if p.endswith("/pulls/99")]
        self.assertEqual(len(pull_fetches), 1)
        # The answer is known — no search secondary for an overridden ticket.
        self.assertNotIn("/search/issues", calls)
        self.assertEqual([m["iid"] for m in issue["mrs"]], [99])
        self.assertEqual(issue["mrs"][0]["matched_via"], "manual")
        self.assertEqual(issue["mrs"][0]["web_url"],
                         "https://github.com/medtrics/medtrics-oracle/pull/99")
        disc = issue["pr_discovery"]
        self.assertEqual(disc["decision"], "override")
        self.assertEqual(disc["primary"], 99)
        self.assertTrue(any("fetched by number" in n for n in disc["notes"]),
                        disc["notes"])

    def test_search_secondary_skipped_only_for_overridden_tickets(self):
        calls = []
        api = self._api([], {"/repos/medtrics/medtrics-oracle/pulls/99":
                             (_pr(99, branch="fix/orc-1/old"), {})}, calls)
        issues = [_issue("ORC-1"), _issue("ORC-2")]
        _discover(issues, api, {"ORC-1": _mem_override("ORC-1", 99)})
        # Exactly one search call, and it is for the non-overridden ticket.
        self.assertEqual(calls.count("/search/issues"), 1)
        self.assertEqual(issues[0]["pr_discovery"]["decision"], "override")
        self.assertEqual(issues[1]["pr_discovery"]["decision"], "no_pr_yet")

    def test_override_pr_404_warns_and_falls_back_to_sweep(self):
        calls = []
        pulls = [_pr(5, branch="fix/orc-1/a")]
        api = self._api(pulls, {"/repos/medtrics/medtrics-oracle/pulls/99":
                                (None, {})}, calls)
        issue = _issue("ORC-1")
        run_stats = _discover([issue], api,
                              {"ORC-1": _mem_override("ORC-1", 99)})

        disc = issue["pr_discovery"]
        self.assertEqual(disc["decision"], "matched")
        self.assertEqual(disc["primary"], 5)
        self.assertIsNone(disc["override"])
        self.assertEqual([m["iid"] for m in issue["mrs"]], [5])
        self.assertEqual(issue["mrs"][0]["matched_via"], "branch")
        self.assertTrue(any("override" in w and "ORC-1" in w and "99" in w
                            for w in run_stats["warnings"]),
                        run_stats["warnings"])
        self.assertEqual(run_stats["summary"]["override"], 0)

    def test_override_pr_swept_but_unmatched_is_added_from_sweep(self):
        # The override PR is in the sweep under an unrelated branch name —
        # no extra fetch; it is added and marked manual.
        calls = []
        pulls = [_pr(7, branch="chore/unrelated", title="cleanup")]
        issue = _issue("ORC-3")
        _discover([issue], self._api(pulls, None, calls),
                  {"ORC-3": _mem_override("ORC-3", 7)})
        self.assertFalse(any("/pulls/7" in p for p in calls))
        self.assertEqual([m["iid"] for m in issue["mrs"]], [7])
        self.assertEqual(issue["mrs"][0]["matched_via"], "manual")
        self.assertEqual(issue["pr_discovery"]["decision"], "override")

    def test_inline_override_beats_mem_override(self):
        d = tempfile.mkdtemp()
        dump = Path(d) / "overrides.json"
        dump.write_text(json.dumps({"notes": [{
            "id": "n1", "version": 2,
            "content": ("PR Override: ORC-232\n\n**Ticket:** ORC-232\n"
                        "**PR:** 415\n**Resolved by:** a@b.c\n"
                        "**Resolved at:** 2026-09-01T00:00:00Z\n")}]}))
        overrides, warnings = oracle.collect_overrides(
            str(dump), ["ORC-232=416", "orc-7=8,ORC-9=#10"])
        self.assertEqual(warnings, [])
        self.assertEqual(overrides["ORC-232"]["pr"], 416)
        self.assertEqual(overrides["ORC-232"]["resolved_by"], "inline")
        self.assertEqual(overrides["ORC-7"]["pr"], 8)
        self.assertEqual(overrides["ORC-9"]["pr"], 10)
        # Mem-only ticket survives; missing dump is fine.
        mem_only, _ = oracle.collect_overrides(str(dump), [])
        self.assertEqual(mem_only["ORC-232"]["pr"], 415)
        self.assertEqual(mem_only["ORC-232"]["note_id"], "n1")
        self.assertEqual(oracle.collect_overrides("", [])[0], {})

    def test_bad_inline_pair_raises(self):
        for bad in ("ORC-1", "ORC-1=abc", "M1-5=3", "ORC-1=0"):
            with self.assertRaises(ValueError, msg=bad):
                oracle.parse_inline_overrides([bad])

    def test_unreadable_dump_never_blocks_collect_overrides(self):
        d = tempfile.mkdtemp()
        bad = Path(d) / "dump.json"
        bad.write_text("{not json")
        overrides, warnings = oracle.collect_overrides(str(bad), ["ORC-1=5"])
        # Inline pairs still apply; the dump problem is a warning, not an exit.
        self.assertEqual(overrides["ORC-1"]["pr"], 5)
        self.assertEqual(len(warnings), 1)
        self.assertIn("override dump unreadable", warnings[0])


class MainEndToEndTests(unittest.TestCase):
    """main() with mocked GitHub + Optimus: --pr-overrides and
    --pr-override-inline together, the written JSON, and --dry-run output."""

    PULLS = [_pr(415, branch="fix/orc-232/a"),
             _pr(416, branch="fix/orc-232/b"),
             _merged(9, "fix/orc-53/x", "2026-07-01T00:00:00Z")]
    TASKS = [{"id": "u1", "task_identifier": "ORC-232", "title": "Consent",
              "created_at": "2026-08-01T00:00:00Z"},
             {"id": "u2", "task_identifier": "ORC-53", "title": "Old"},
             {"id": "u3", "task_identifier": "ORC-900", "title": "New",
              "createdAt": "2026-09-01T00:00:00Z"}]

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.dump = Path(self.d) / "overrides.json"
        self.dump.write_text(json.dumps({"notes": [
            {"id": "n1", "version": 2,
             "content": ("PR Override: ORC-232\n\n**Ticket:** ORC-232\n"
                         "**PR:** 415\n**Resolved by:** a@b.c\n"
                         "**Resolved at:** 2026-09-01T00:00:00Z\n")},
            {"id": "bad-note", "version": 1,
             "content": "PR Override: ORC-7\n\n**PR:** soon\n"},
        ]}))
        self.out = Path(self.d) / "out.json"

    def _run_main(self, argv):
        calls = []

        def fake_api(env, path, params=None):
            calls.append(path)
            if path == "/repos/medtrics/medtrics-oracle":
                return {"full_name": "x"}, {}
            if path.endswith("/pulls"):
                return self.PULLS, {}
            if path == "/search/issues":
                return {"items": []}, {}
            if path.endswith("/pulls/700"):
                return _pr(700, branch="hotfix/thing"), {}
            if "/files" in path:
                return [{"filename": "a.ts", "patch": "@@ x"}], {}
            if "/commits" in path:
                return [], {}
            if path.startswith("/users/"):
                return {"email": None}, {}
            self.fail(f"unexpected GitHub call: {path}")

        stdout = io.StringIO()
        env = dict(ENV, MEM_PR_OVERRIDES_COLLECTION_ID="")
        with mock.patch.object(oracle, "github_api", side_effect=fake_api), \
                mock.patch.object(oracle, "load_env", return_value=env), \
                mock.patch.object(oracle.base, "query_tasks_by_identifiers",
                                  return_value=self.TASKS), \
                mock.patch.object(oracle, "log"), \
                mock.patch.object(sys, "argv", ["gather"] + argv), \
                contextlib.redirect_stdout(stdout):
            code = 0
            try:
                oracle.main()
            except SystemExit as e:
                code = e.code
        return code, stdout.getvalue(), calls

    def test_full_run_writes_pr_discovery_with_merged_warnings_inline_wins(self):
        code, _, calls = self._run_main(
            ["tickets", "ORC-232,ORC-53,ORC-900", "--output", str(self.out),
             "--pr-overrides", str(self.dump),
             "--pr-override-inline", "ORC-232=416,ORC-900=700"])
        self.assertEqual(code, 0)
        data = json.loads(self.out.read_text())
        # Classic keys intact, additive top-level block present.
        self.assertEqual(sorted(data), ["issues", "mode", "parameter",
                                        "pr_discovery"])
        disc = data["pr_discovery"]
        self.assertEqual(disc["summary"], {
            "override": 2, "matched": 1, "flagged_ambiguous": 0,
            "no_pr_yet": 0, "no_pr_in_window": 0})
        self.assertEqual(disc["overrides_loaded"], 2)
        self.assertFalse(disc["sweep"]["failed"])
        # Override-note warnings are merged into the single warnings list.
        self.assertTrue(any("bad-note" in w and "malformed" in w
                            for w in disc["warnings"]), disc["warnings"])
        by_id = {i["identifier"]: i for i in data["issues"]}
        # Inline ORC-232=416 beat the Mem note's 415; both PRs kept,
        # primary first, override entry manual.
        orc232 = by_id["ORC-232"]
        self.assertEqual(orc232["pr_discovery"]["decision"], "override")
        self.assertEqual(orc232["pr_discovery"]["primary"], 416)
        self.assertEqual(orc232["pr_discovery"]["override"]["resolved_by"],
                         "inline")
        self.assertEqual([m["iid"] for m in orc232["mrs"]], [416, 415])
        self.assertEqual(orc232["mrs"][0]["matched_via"], "manual")
        self.assertEqual(orc232["created_at"], "2026-08-01T00:00:00Z")
        # Inline-only ticket: PR fetched by number, diffs fetched, no search.
        orc900 = by_id["ORC-900"]
        self.assertEqual([m["iid"] for m in orc900["mrs"]], [700])
        self.assertEqual(len(orc900["mrs"][0]["diffs"]), 1)
        self.assertEqual(orc900["created_at"], "2026-09-01T00:00:00Z")
        self.assertEqual(calls.count("/repos/medtrics/medtrics-oracle/pulls/700"), 1)
        self.assertNotIn("/search/issues", calls)
        self.assertEqual(calls.count("/repos/medtrics/medtrics-oracle/pulls"), 1)
        # Merged-only ticket: matched, primary the merged PR.
        self.assertEqual(by_id["ORC-53"]["pr_discovery"],
                         {"decision": "matched", "primary": 9,
                          "candidates": [], "override": None, "notes": []})

    def test_dry_run_prints_decision_column_and_summary(self):
        code, stdout, _ = self._run_main(
            ["tickets", "ORC-232,ORC-53,ORC-900", "--dry-run",
             "--pr-overrides", str(self.dump),
             "--pr-override-inline", "ORC-232=416"])
        self.assertEqual(code, 0)
        self.assertIn("Decision", stdout)
        self.assertRegex(stdout, r"ORC-232\s+PR#416, PR#415\s+override \(#416\)")
        self.assertRegex(stdout, r"ORC-53\s+PR#9\s+matched")
        self.assertRegex(stdout, r"ORC-900\s+none\s+no_pr_yet")
        self.assertIn("Decisions: override=1, matched=1, flagged_ambiguous=0, "
                      "no_pr_yet=1, no_pr_in_window=0", stdout)
        self.assertIn("Overrides loaded: 1", stdout)
        self.assertIn("WARNING: no PRs found for ORC-900", stdout)
        self.assertFalse(self.out.exists())  # dry-run writes nothing


class WindowTests(unittest.TestCase):
    """no_pr_in_window fires only when the sweep window is FULL, the ticket
    predates the oldest swept PR, and nothing was found."""

    def _full_sweep_api(self, pages=5, per_page=100, short_last=False):
        # Page p carries per_page PRs; updated_at descends across pages so
        # the oldest is on the last page. No Link header -> page fallback.
        def fake_api(env, path, params=None):
            if path.endswith("/pulls"):
                page = int(params["page"])
                n = per_page
                if short_last and page == pages:
                    n = per_page - 1
                if page > pages:
                    return [], {}
                month = 9 - page  # page 1 -> Aug, ..., page 5 -> Apr
                return [_pr(page * 1000 + i, branch=f"chore/p{page}-{i}",
                            updated_at=f"2026-0{month}-15T00:00:00Z")
                        for i in range(n)], {}
            if path == "/search/issues":
                return {"items": []}, {}
            self.fail(f"unexpected GitHub call: {path}")
        return fake_api

    def test_window_full_and_older_ticket_is_no_pr_in_window(self):
        issue = _issue("ORC-2", created_at="2026-01-10T09:00:00+00:00")
        run_stats = _discover([issue], self._full_sweep_api())
        self.assertTrue(run_stats["sweep"]["window_full"])
        self.assertEqual(run_stats["sweep"]["pages"], 5)
        self.assertEqual(run_stats["sweep"]["pr_count"], 500)
        self.assertEqual(run_stats["sweep"]["oldest_updated_at"],
                         "2026-04-15T00:00:00Z")
        self.assertEqual(issue["pr_discovery"]["decision"], "no_pr_in_window")
        self.assertEqual(run_stats["summary"]["no_pr_in_window"], 1)
        self.assertIn("no PRs found for ORC-2 — it will appear without code "
                      "context", run_stats["warnings"])

    def test_window_full_but_newer_ticket_is_no_pr_yet(self):
        issue = _issue("ORC-2", created_at="2026-06-01T00:00:00Z")
        _discover([issue], self._full_sweep_api())
        self.assertEqual(issue["pr_discovery"]["decision"], "no_pr_yet")

    def test_window_not_full_is_no_pr_yet(self):
        issue = _issue("ORC-2", created_at="2026-01-10T09:00:00Z")
        run_stats = _discover([issue], self._full_sweep_api(short_last=True))
        self.assertFalse(run_stats["sweep"]["window_full"])
        self.assertEqual(issue["pr_discovery"]["decision"], "no_pr_yet")

    def test_no_created_at_is_no_pr_yet(self):
        issue = _issue("ORC-2")  # created_at None
        run_stats = _discover([issue], self._full_sweep_api())
        self.assertTrue(run_stats["sweep"]["window_full"])
        self.assertEqual(issue["pr_discovery"]["decision"], "no_pr_yet")

    def test_oldest_updated_at_computed_from_sweep(self):
        pulls = [_pr(1, updated_at="2026-09-01T00:00:00Z"),
                 _pr(2, updated_at="2026-05-11T09:14:00Z"),
                 _pr(3, updated_at="2026-07-01T00:00:00Z"),
                 _pr(4, updated_at=None)]

        def fake_api(env, path, params=None):
            return pulls, {}

        with mock.patch.object(oracle, "github_api", side_effect=fake_api):
            _pulls, stats = oracle.sweep_pulls(ENV)
        self.assertEqual(stats["oldest_updated_at"], "2026-05-11T09:14:00Z")
        self.assertEqual(stats["pr_count"], 4)
        self.assertFalse(stats["window_full"])

    def test_github_paginate_stats_out_dict(self):
        def fake_api(env, path, params=None):
            if params["page"] == "1":
                return [{"number": i} for i in range(100)], {}
            return [{"number": 100}], {}

        stats = {}
        with mock.patch.object(oracle, "github_api", side_effect=fake_api):
            items = oracle.github_paginate(ENV, "/x", {}, per_page=100,
                                           stats=stats)
        self.assertEqual(len(items), 101)
        self.assertEqual(stats, {"pages": 2, "last_page_full": False,
                                 "failed": False})


class PrEntryTests(unittest.TestCase):
    def test_schema_matches_classic_mr_entry_keys(self):
        entry = oracle._pr_entry(_pr(42, title="Fix it", login="octo",
                                     name="Octo Cat"))
        # Classic MR keys + an additive author_email (filled later by
        # enrich_author_emails; empty at construction) are all present and
        # unchanged. Extra keys are downstream-safe — consumers read by name.
        classic = {"author_email", "author_name", "author_username",
                   "iid", "title", "web_url"}
        self.assertTrue(classic <= set(entry.keys()))
        self.assertEqual(entry["author_email"], "")
        self.assertEqual(entry["iid"], 42)
        self.assertEqual(entry["title"], "Fix it")
        self.assertEqual(entry["web_url"],
                         "https://github.com/medtrics/medtrics-oracle/pull/42")
        self.assertEqual(entry["author_name"], "Octo Cat")
        self.assertEqual(entry["author_username"], "octo")

    def test_author_name_falls_back_to_login_and_tolerates_missing_user(self):
        entry = oracle._pr_entry(_pr(1, login="octo"))  # no user.name
        self.assertEqual(entry["author_name"], "octo")
        bare = oracle._pr_entry({"number": 2})
        self.assertEqual(bare["author_name"], "")
        self.assertEqual(bare["author_username"], "")
        self.assertEqual(bare["title"], "")
        self.assertEqual(bare["web_url"], "")

    def test_additive_pr_facts_present(self):
        entry = oracle._pr_entry(
            _pr(415, branch="fix/orc-232/consent-banner",
                title="ORC-232 consent banner fix",
                updated_at="2026-09-01T18:22:10Z"), "branch")
        self.assertEqual(sorted(entry.keys()), [
            "author_email", "author_name", "author_username", "branch",
            "draft", "iid", "matched_via", "merged_at", "number", "state",
            "title", "updated_at", "web_url"])
        self.assertEqual(entry["number"], 415)
        self.assertEqual(entry["branch"], "fix/orc-232/consent-banner")
        self.assertEqual(entry["state"], "open")
        self.assertFalse(entry["draft"])
        self.assertIsNone(entry["merged_at"])
        self.assertEqual(entry["updated_at"], "2026-09-01T18:22:10Z")
        self.assertEqual(entry["matched_via"], "branch")
        merged = oracle._pr_entry(
            _merged(7, "fix/orc-53/a", "2026-07-01T10:00:00Z", draft=True),
            "manual")
        self.assertEqual(merged["state"], "closed")
        self.assertEqual(merged["merged_at"], "2026-07-01T10:00:00Z")
        self.assertTrue(merged["draft"])
        self.assertEqual(merged["matched_via"], "manual")
        # Bare PR: the new keys tolerate missing fields.
        bare = oracle._pr_entry({"number": 2})
        self.assertEqual(bare["branch"], "")
        self.assertEqual(bare["state"], "")
        self.assertFalse(bare["draft"])
        self.assertIsNone(bare["merged_at"])
        self.assertIsNone(bare["updated_at"])
        self.assertEqual(bare["matched_via"], "")


class LoadEnvTests(unittest.TestCase):
    def _write_env(self, lines):
        f = tempfile.NamedTemporaryFile("w", suffix=".env", delete=False)
        f.write("\n".join(lines) + "\n")
        f.close()
        self.addCleanup(lambda: Path(f.name).unlink(missing_ok=True))
        return f.name

    def test_full_url_repo_normalized_to_owner_repo(self):
        path = self._write_env([
            "OPTIMUS_API_KEY=ok",
            "GITHUB_TOKEN=ghp_test",
            "GITHUB_REPO=https://github.com/medtrics/medtrics-oracle.git",
        ])
        with mock.patch.object(oracle, "log"):
            env = oracle.load_env(path)
        self.assertEqual(env["GITHUB_REPO"], "medtrics/medtrics-oracle")
        self.assertEqual(env["GITHUB_API_URL"], "https://api.github.com")

    def test_trailing_slash_and_git_suffix_stripped(self):
        path = self._write_env([
            "OPTIMUS_API_KEY=ok",
            "GITHUB_TOKEN=ghp_test",
            "GITHUB_REPO=http://github.com/medtrics/medtrics-oracle.git/",
        ])
        with mock.patch.object(oracle, "log"):
            env = oracle.load_env(path)
        self.assertEqual(env["GITHUB_REPO"], "medtrics/medtrics-oracle")

    def test_plain_owner_repo_passes_through(self):
        path = self._write_env([
            "OPTIMUS_API_KEY=ok",
            "GITHUB_TOKEN=ghp_test",
            "GITHUB_REPO=medtrics/medtrics-oracle",
        ])
        with mock.patch.object(oracle, "log"):
            env = oracle.load_env(path)
        self.assertEqual(env["GITHUB_REPO"], "medtrics/medtrics-oracle")

    def test_mem_pr_overrides_collection_id_is_optional(self):
        path = self._write_env([
            "OPTIMUS_API_KEY=ok",
            "GITHUB_TOKEN=ghp_test",
            "GITHUB_REPO=medtrics/medtrics-oracle",
        ])
        with mock.patch.object(oracle, "log"):
            env = oracle.load_env(path)  # must not exit
        self.assertEqual(env["MEM_PR_OVERRIDES_COLLECTION_ID"], "")
        path = self._write_env([
            "OPTIMUS_API_KEY=ok",
            "GITHUB_TOKEN=ghp_test",
            "GITHUB_REPO=medtrics/medtrics-oracle",
            "MEM_PR_OVERRIDES_COLLECTION_ID='c0ffee-1234'",
        ])
        with mock.patch.object(oracle, "log"):
            env = oracle.load_env(path)
        self.assertEqual(env["MEM_PR_OVERRIDES_COLLECTION_ID"], "c0ffee-1234")

    def test_missing_github_token_exits_1(self):
        path = self._write_env([
            "OPTIMUS_API_KEY=ok",
            "GITHUB_REPO=medtrics/medtrics-oracle",
        ])
        with mock.patch.object(oracle, "log"), \
                self.assertRaises(SystemExit) as ctx:
            oracle.load_env(path)
        self.assertEqual(ctx.exception.code, 1)

    def test_bad_repo_shape_exits_1(self):
        for bad in ("justoneword", "a/b/c", "https://github.com/onlyowner"):
            path = self._write_env([
                "OPTIMUS_API_KEY=ok",
                "GITHUB_TOKEN=ghp_test",
                f"GITHUB_REPO={bad}",
            ])
            with mock.patch.object(oracle, "log"), \
                    self.assertRaises(SystemExit) as ctx:
                oracle.load_env(path)
            self.assertEqual(ctx.exception.code, 1, f"{bad!r} did not exit 1")


class CanaryTests(unittest.TestCase):
    def test_canary_failure_exits_nonzero_with_auth_guidance(self):
        # A bad token on a private repo surfaces as 404 — github_api returns
        # (None, {}) and the canary must hard-exit before any gathering.
        with mock.patch.object(oracle, "github_api",
                               return_value=(None, {})) as fake_api, \
                mock.patch.object(oracle, "log") as fake_log, \
                self.assertRaises(SystemExit) as ctx:
            oracle.verify_github_access(ENV)
        self.assertEqual(ctx.exception.code, 1)
        fake_api.assert_called_once_with(
            ENV, "/repos/medtrics/medtrics-oracle")
        messages = " ".join(c.args[0] for c in fake_log.call_args_list)
        self.assertIn("GITHUB_TOKEN", messages)
        self.assertIn("scope", messages)
        self.assertIn("SSO", messages)

    def test_canary_success_does_not_exit(self):
        with mock.patch.object(oracle, "github_api",
                               return_value=({"full_name": "x"}, {})), \
                mock.patch.object(oracle, "log"):
            oracle.verify_github_access(ENV)  # must not raise


class AuthorEmailTests(unittest.TestCase):
    """fetch_pr_author_email: use the commit AUTHOR email (not committer) of a
    commit whose top-level author.login matches the PR author; skip noreply
    masks; fall back to the public profile email; cache per login."""

    def _commits(self, author_login, author_email, committer_email):
        return [{
            "author": {"login": author_login},
            "commit": {
                "author": {"email": author_email},
                "committer": {"email": committer_email},
            },
        }]

    def test_uses_commit_author_not_committer(self):
        # PR #458 shape: author charles@…, committer marina@… (Marina pushed).
        commits = self._commits("CharlesMedtrics338",
                                 "charles@medtricslab.com",
                                 "marina@medtricslab.com")
        with mock.patch.object(oracle, "github_paginate", return_value=commits), \
                mock.patch.object(oracle, "github_api") as api:
            email = oracle.fetch_pr_author_email(
                ENV, 458, "CharlesMedtrics338", {})
        self.assertEqual(email, "charles@medtricslab.com")
        api.assert_not_called()   # commit hit → no profile fallback

    def test_skips_noreply_then_falls_back_to_profile(self):
        commits = self._commits("CharlesMedtrics338",
                                 "12345+charles@users.noreply.github.com",
                                 "marina@medtricslab.com")
        with mock.patch.object(oracle, "github_paginate", return_value=commits), \
                mock.patch.object(oracle, "github_api",
                                  return_value=({"email": "charles@medtricslab.com"}, {})):
            email = oracle.fetch_pr_author_email(
                ENV, 458, "CharlesMedtrics338", {})
        self.assertEqual(email, "charles@medtricslab.com")

    def test_ignores_commit_whose_author_login_differs(self):
        # A co-author's commit must not leak in; no profile email either → "".
        commits = [{
            "author": {"login": "SomeoneElse"},
            "commit": {"author": {"email": "someone@medtricslab.com"},
                       "committer": {"email": "x@y.com"}},
        }]
        with mock.patch.object(oracle, "github_paginate", return_value=commits), \
                mock.patch.object(oracle, "github_api", return_value=({"email": None}, {})):
            email = oracle.fetch_pr_author_email(
                ENV, 1, "CharlesMedtrics338", {})
        self.assertEqual(email, "")

    def test_cache_hit_skips_api(self):
        cache = {"CharlesMedtrics338": "charles@medtricslab.com"}
        with mock.patch.object(oracle, "github_paginate") as pag, \
                mock.patch.object(oracle, "github_api") as api:
            email = oracle.fetch_pr_author_email(
                ENV, 458, "CharlesMedtrics338", cache)
        self.assertEqual(email, "charles@medtricslab.com")
        pag.assert_not_called()
        api.assert_not_called()


if __name__ == "__main__":
    unittest.main()
