"""Tests for scripts/gather_notes_data_optimus.py.

Stdlib-only (unittest + unittest.mock). Covers:
  - pagination terminator (H1) — including the empty-page-while-count-remains
    regression that used to loop forever
  - mr_matches_identifier boundary cases
  - Optimus retry behavior (H3) — 429 then success, persistent 500 raises
    OptimusError instead of calling sys.exit
"""

import importlib.util
import io
import json
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "gather_notes_data_optimus", SCRIPTS_DIR / "gather_notes_data_optimus.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gather = _load_module()

ENV = {"OPTIMUS_API_KEY": "test-key"}
LIMIT = gather.PAGE_LIMIT


def _page(n_tasks, count=None, start=0):
    """Build a fake /tasks response page."""
    result = {"data": [{"id": f"task-{start + i}"} for i in range(n_tasks)]}
    if count is not None:
        result["meta"] = {"count": count}
    else:
        result["meta"] = {}
    return result


class PaginationTerminatorTests(unittest.TestCase):
    """H1 — fetch_tasks_paginated must always terminate, never truncate silently."""

    def _run_with_pages(self, pages):
        """Patch optimus_api to serve the scripted pages in order.

        Raises AssertionError if more pages are requested than scripted
        (i.e., the loop failed to terminate on time).
        """
        calls = []

        def fake_api(env, path, params=None):
            self.assertEqual(path, "/tasks")
            calls.append(dict(params))
            self.assertLess(
                len(calls), len(pages) + 1,
                f"pagination did not terminate: requested page {len(calls)} "
                f"but only {len(pages)} were scripted")
            return pages[len(calls) - 1]

        with mock.patch.object(gather, "optimus_api", side_effect=fake_api):
            tasks = gather.fetch_tasks_paginated(ENV, {"scope": "product"})
        return tasks, calls

    def test_empty_first_page(self):
        tasks, calls = self._run_with_pages([_page(0, count=0)])
        self.assertEqual(tasks, [])
        self.assertEqual(len(calls), 1)

    def test_short_page_with_correct_count(self):
        pages = [
            _page(LIMIT, count=LIMIT + 10),
            _page(10, count=LIMIT + 10, start=LIMIT),
        ]
        tasks, calls = self._run_with_pages(pages)
        self.assertEqual(len(tasks), LIMIT + 10)
        self.assertEqual(len(calls), 2)

    def test_count_exact_page(self):
        # count is an exact multiple of the page size: a full final page must
        # terminate without requesting a third page.
        pages = [
            _page(LIMIT, count=2 * LIMIT),
            _page(LIMIT, count=2 * LIMIT, start=LIMIT),
        ]
        tasks, calls = self._run_with_pages(pages)
        self.assertEqual(len(tasks), 2 * LIMIT)
        self.assertEqual(len(calls), 2)

    def test_missing_count_short_page_stops_with_warning(self):
        pages = [_page(7)]  # no meta.count, short page
        with mock.patch.object(gather, "log") as fake_log:
            tasks, calls = self._run_with_pages(pages)
        self.assertEqual(len(tasks), 7)
        self.assertEqual(len(calls), 1)
        warnings = [c.args[0] for c in fake_log.call_args_list
                    if "WARNING" in c.args[0]]
        self.assertTrue(warnings, "expected a truncation warning on a short "
                                  "page with no meta.count")

    def test_missing_count_full_pages_continue_until_short(self):
        pages = [_page(LIMIT), _page(LIMIT, start=LIMIT), _page(3, start=2 * LIMIT)]
        with mock.patch.object(gather, "log"):
            tasks, calls = self._run_with_pages(pages)
        self.assertEqual(len(tasks), 2 * LIMIT + 3)
        self.assertEqual(len(calls), 3)

    def test_h1_regression_empty_page_while_count_above_offset(self):
        # The original bug: the API claims count=200 but returns an empty
        # page at offset 50. The old terminator advanced offset by 0 and
        # looped forever. The fix must stop on the empty page.
        pages = [
            _page(LIMIT, count=200),
            _page(0, count=200, start=LIMIT),
        ]
        tasks, calls = self._run_with_pages(pages)
        self.assertEqual(len(tasks), LIMIT)
        self.assertEqual(len(calls), 2)


class MrMatchesIdentifierTests(unittest.TestCase):
    def test_does_not_match_longer_identifier(self):
        mr = {"source_branch": "feature/m1-597", "title": "M1-597 fix"}
        self.assertFalse(gather.mr_matches_identifier(mr, "M1-59"))

    def test_matches_branch(self):
        mr = {"source_branch": "m1-59-hotfix", "title": "unrelated"}
        self.assertTrue(gather.mr_matches_identifier(mr, "M1-59"))

    def test_matches_title_with_space_separator(self):
        mr = {"source_branch": "other", "title": "M1 59 change"}
        self.assertTrue(gather.mr_matches_identifier(mr, "M1-59"))

    def test_matches_revert_title_with_space_separator(self):
        mr = {"source_branch": "other", "title": "revert M1 808 change"}
        self.assertTrue(gather.mr_matches_identifier(mr, "M1-808"))

    def test_case_insensitive(self):
        mr = {"source_branch": "M1-59-HOTFIX", "title": ""}
        self.assertTrue(gather.mr_matches_identifier(mr, "m1-59"))
        mr2 = {"source_branch": "", "title": "m1-59 lowercase title"}
        self.assertTrue(gather.mr_matches_identifier(mr2, "M1-59"))


def _http_error(code, retry_after=None):
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return urllib.error.HTTPError(
        "https://example.test/tasks", code, "err", headers,
        io.BytesIO(b"upstream error"))


class _FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class OptimusRetryTests(unittest.TestCase):
    """H3 — Optimus errors retry with backoff; exhaustion raises OptimusError."""

    def test_429_then_success(self):
        responses = [_http_error(429, retry_after=1), _FakeResponse({"data": []})]

        def fake_urlopen(req, timeout=None):
            r = responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

        with mock.patch.object(gather.urllib.request, "urlopen",
                               side_effect=fake_urlopen), \
                mock.patch.object(gather.time, "sleep") as fake_sleep, \
                mock.patch.object(gather, "log"):
            result = gather.optimus_api(ENV, "/tasks", {"limit": "50"})

        self.assertEqual(result, {"data": []})
        fake_sleep.assert_called_once_with(1)  # honored Retry-After

    def test_persistent_500_raises_typed_error_not_exit(self):
        with mock.patch.object(gather.urllib.request, "urlopen",
                               side_effect=lambda req, timeout=None:
                               (_ for _ in ()).throw(_http_error(500))), \
                mock.patch.object(gather.time, "sleep"), \
                mock.patch.object(gather, "log"):
            try:
                gather.optimus_api(ENV, "/tasks")
            except gather.OptimusError:
                pass  # expected
            except SystemExit:
                self.fail("optimus_api called sys.exit instead of raising "
                          "OptimusError")
            else:
                self.fail("optimus_api did not raise OptimusError")

    def test_non_retryable_code_raises_immediately(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(1)
            raise _http_error(403)

        with mock.patch.object(gather.urllib.request, "urlopen",
                               side_effect=fake_urlopen), \
                mock.patch.object(gather.time, "sleep") as fake_sleep, \
                mock.patch.object(gather, "log"):
            with self.assertRaises(gather.OptimusError):
                gather.optimus_api(ENV, "/tasks")

        self.assertEqual(len(calls), 1)
        fake_sleep.assert_not_called()

    def test_url_error_retried_then_success(self):
        responses = [urllib.error.URLError("connection refused"),
                     TimeoutError("timed out"),
                     _FakeResponse({"data": [{"id": "t1"}]})]

        def fake_urlopen(req, timeout=None):
            r = responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

        with mock.patch.object(gather.urllib.request, "urlopen",
                               side_effect=fake_urlopen), \
                mock.patch.object(gather.time, "sleep"), \
                mock.patch.object(gather, "log"):
            result = gather.optimus_api(ENV, "/tasks")

        self.assertEqual(result, {"data": [{"id": "t1"}]})


class TaskTypeTests(unittest.TestCase):
    """The integration API carries the classification as `product_type`
    (seen on M1-1609); a ticket read without it looked typeless and was
    dropped by every features-only filter."""

    BASE = {"id": "e620e271-0000", "task_identifier": "M1-1609", "title": "T"}

    def test_reads_product_type(self):
        issue = gather.task_to_issue({**self.BASE, "product_type": "feature"})
        self.assertEqual(issue["task_type"], "feature")

    def test_older_shapes_still_read(self):
        self.assertEqual(gather.task_to_issue({**self.BASE, "type": "bug"})["task_type"], "bug")
        self.assertEqual(gather.task_to_issue({**self.BASE, "task_type": "feature"})["task_type"],
                         "feature")

    def test_missing_is_empty(self):
        self.assertEqual(gather.task_to_issue(dict(self.BASE))["task_type"], "")


if __name__ == "__main__":
    unittest.main()
