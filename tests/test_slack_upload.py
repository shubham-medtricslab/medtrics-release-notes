"""Tests for scripts/slack_upload.py.

Stdlib-only (unittest + unittest.mock), hermetic — everything is mocked at
the urllib / slack_call boundary; no network. Covers:
  - resolve_channel: C-id and G-id accepted verbatim, "#name" resolved via a
    mocked conversations.list, lowercase/garbage falls back to name lookup
    and errors cleanly
  - retry policy: 429 (Retry-After honored) retried on chat.postMessage;
    5xx on chat.postMessage NOT retried (verify-before-rerun error);
    5xx on conversations.list retried
  - the ---SLACK_UPLOAD_RESULT--- JSON block emitted on every failure path
    of main(), with ok:false and an error string
  - --thread-ts lands in the chat.postMessage payload
  - --get-permalink mode: chat.getPermalink called as a GET with channel +
    message_ts; permalink surfaces in the result block on success; API
    ok:false / HTTP / network failures -> ok:false, exit 2, permalink null;
    5xx retried (read path); argparse rejects --get-permalink without --ts
    or --channel; the post path's result shape stays backward compatible
"""

import contextlib
import io
import importlib.util
import json
import tempfile
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "slack_upload", SCRIPTS_DIR / "slack_upload.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


su = _load_module()

TOKEN = "xoxb-test-token"


def _http_error(code, retry_after=None):
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return urllib.error.HTTPError(
        "https://slack.com/api/x", code, "err", headers,
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


def _urlopen_from_script(script, calls=None):
    """Build a fake urlopen serving `script` items in order.

    Items are payload dicts (returned as responses) or exceptions (raised).
    `calls` (if given) collects the urllib Request objects.
    """
    remaining = list(script)

    def fake_urlopen(req, timeout=None):
        if calls is not None:
            calls.append(req)
        item = remaining.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeResponse(item)

    return fake_urlopen


CHANNELS_PAGE = {
    "ok": True,
    "channels": [
        {"id": "C1BCUHXT9", "name": "dream-team"},
        {"id": "G0AAABBBC", "name": "secret-club"},
    ],
    "response_metadata": {"next_cursor": ""},
}


class ResolveChannelTests(unittest.TestCase):
    def test_c_prefixed_id_accepted_verbatim(self):
        with mock.patch.object(su.urllib.request, "urlopen",
                               side_effect=AssertionError("no API call expected")):
            self.assertEqual(su.resolve_channel("C0123ABCD", TOKEN), "C0123ABCD")

    def test_g_prefixed_id_accepted_verbatim(self):
        # The 0.8.0 heuristic shunted G-ids into a doomed name lookup.
        with mock.patch.object(su.urllib.request, "urlopen",
                               side_effect=AssertionError("no API call expected")):
            self.assertEqual(su.resolve_channel("G0123ABCD", TOKEN), "G0123ABCD")

    def test_hash_name_resolved_via_conversations_list(self):
        calls = []
        fake = _urlopen_from_script([CHANNELS_PAGE], calls)
        with mock.patch.object(su.urllib.request, "urlopen", side_effect=fake):
            self.assertEqual(su.resolve_channel("#dream-team", TOKEN),
                             "C1BCUHXT9")
        self.assertEqual(len(calls), 1)
        self.assertIn("conversations.list", calls[0].full_url)

    def test_hash_prefixed_id_lookalike_is_treated_as_name(self):
        # Leading '#' always means a name, even if the rest looks like an id.
        page = {"ok": True,
                "channels": [{"id": "C9ZZZZZZZ", "name": "C0123ABCD"}],
                "response_metadata": {"next_cursor": ""}}
        with mock.patch.object(su.urllib.request, "urlopen",
                               side_effect=_urlopen_from_script([page])):
            self.assertEqual(su.resolve_channel("#C0123ABCD", TOKEN),
                             "C9ZZZZZZZ")

    def test_bare_name_falls_back_to_lookup(self):
        with mock.patch.object(su.urllib.request, "urlopen",
                               side_effect=_urlopen_from_script([CHANNELS_PAGE])):
            self.assertEqual(su.resolve_channel("dream-team", TOKEN),
                             "C1BCUHXT9")

    def test_lowercase_id_lookalike_falls_back_and_errors_cleanly(self):
        # 'c0123abcd' is not a valid id (ids are uppercase) — it goes through
        # the name lookup and, matching nothing, raises a clean SlackError.
        with mock.patch.object(su.urllib.request, "urlopen",
                               side_effect=_urlopen_from_script([CHANNELS_PAGE])):
            with self.assertRaises(su.SlackError) as ctx:
                su.resolve_channel("c0123abcd", TOKEN)
        self.assertIn("not found", str(ctx.exception))

    def test_garbage_errors_cleanly(self):
        with mock.patch.object(su.urllib.request, "urlopen",
                               side_effect=_urlopen_from_script([CHANNELS_PAGE])):
            with self.assertRaises(su.SlackError):
                su.resolve_channel("not a channel!!", TOKEN)


class RetryPolicyTests(unittest.TestCase):
    def test_429_on_post_message_retries_then_succeeds(self):
        script = [_http_error(429, retry_after=3),
                  {"ok": True, "ts": "1724650000.000100"}]
        with mock.patch.object(su.urllib.request, "urlopen",
                               side_effect=_urlopen_from_script(script)), \
                mock.patch.object(su.time, "sleep") as fake_sleep:
            ts = su.post_message("C0123ABCD", "hello", TOKEN)
        self.assertEqual(ts, "1724650000.000100")
        fake_sleep.assert_called_once_with(3)  # honored Retry-After

    def test_500_on_post_message_does_not_retry(self):
        calls = []
        script = [_http_error(500)]
        with mock.patch.object(su.urllib.request, "urlopen",
                               side_effect=_urlopen_from_script(script, calls)), \
                mock.patch.object(su.time, "sleep") as fake_sleep:
            with self.assertRaises(su.SlackPostUnverifiedError) as ctx:
                su.post_message("C0123ABCD", "hello", TOKEN)
        self.assertEqual(len(calls), 1, "a 5xx write must never be retried")
        fake_sleep.assert_not_called()
        self.assertIn("verify in the channel before re-running",
                      str(ctx.exception))

    def test_timeout_on_post_message_does_not_retry(self):
        calls = []
        script = [TimeoutError("timed out")]
        with mock.patch.object(su.urllib.request, "urlopen",
                               side_effect=_urlopen_from_script(script, calls)):
            with self.assertRaises(su.SlackPostUnverifiedError):
                su.post_message("C0123ABCD", "hello", TOKEN)
        self.assertEqual(len(calls), 1)

    def test_500_on_conversations_list_does_retry(self):
        script = [_http_error(500), _http_error(500), CHANNELS_PAGE]
        with mock.patch.object(su.urllib.request, "urlopen",
                               side_effect=_urlopen_from_script(script)), \
                mock.patch.object(su.time, "sleep") as fake_sleep:
            self.assertEqual(su.resolve_channel("#dream-team", TOKEN),
                             "C1BCUHXT9")
        self.assertEqual(fake_sleep.call_count, 2)

    def test_network_error_on_read_retried_then_exhausted(self):
        script = [urllib.error.URLError("refused")] * su.MAX_RETRIES
        with mock.patch.object(su.urllib.request, "urlopen",
                               side_effect=_urlopen_from_script(script)), \
                mock.patch.object(su.time, "sleep"):
            with self.assertRaises(su.SlackError) as ctx:
                su.slack_get("conversations.list", TOKEN)
        self.assertIn("exhausted", str(ctx.exception))

    def test_thread_ts_included_in_payload(self):
        calls = []
        script = [{"ok": True, "ts": "1724650001.000200"}]
        with mock.patch.object(su.urllib.request, "urlopen",
                               side_effect=_urlopen_from_script(script, calls)):
            su.post_message("C0123ABCD", "reply", TOKEN,
                            thread_ts="1724650000.000100")
        payload = json.loads(calls[0].data.decode("utf-8"))
        self.assertEqual(payload["thread_ts"], "1724650000.000100")

    def test_no_thread_ts_key_without_flag(self):
        calls = []
        script = [{"ok": True, "ts": "1724650001.000200"}]
        with mock.patch.object(su.urllib.request, "urlopen",
                               side_effect=_urlopen_from_script(script, calls)):
            su.post_message("C0123ABCD", "root", TOKEN)
        payload = json.loads(calls[0].data.decode("utf-8"))
        self.assertNotIn("thread_ts", payload)


class MainResultBlockTests(unittest.TestCase):
    """main() must emit the ---SLACK_UPLOAD_RESULT--- block on every exit."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.msg_path = Path(self._tmp.name) / "message.txt"
        self.msg_path.write_text("hello channel")
        self.env_path = Path(self._tmp.name) / "nonexistent.env"

    def _run_main(self, argv_tail, environ=None):
        argv = ["slack_upload.py"] + argv_tail
        stdout, stderr = io.StringIO(), io.StringIO()
        env = {"SLACK_BOT_TOKEN": TOKEN} if environ is None else environ
        with mock.patch.object(su.sys, "argv", argv), \
                mock.patch.object(su.os, "environ", env), \
                contextlib.redirect_stdout(stdout), \
                contextlib.redirect_stderr(stderr):
            code = su.main()
        return code, stdout.getvalue(), stderr.getvalue()

    def _parse_result(self, out):
        lines = out.splitlines()
        self.assertIn("---SLACK_UPLOAD_RESULT---", lines,
                      f"result block missing from output: {out!r}")
        idx = lines.index("---SLACK_UPLOAD_RESULT---")
        result = json.loads(lines[idx + 1])
        for key in ("ok", "channel", "ts", "error"):
            self.assertIn(key, result)
        return result

    def _assert_failure_contract(self, code, out, expected_code):
        self.assertEqual(code, expected_code)
        result = self._parse_result(out)
        self.assertFalse(result["ok"])
        self.assertTrue(result["error"], "error string must be non-empty")
        self.assertIsNone(result["ts"])
        return result

    def test_missing_token_emits_result_and_exits_1(self):
        code, out, _ = self._run_main(
            ["--channel", "C0123ABCD", "--message-file", str(self.msg_path),
             "--env-path", str(self.env_path)],
            environ={})
        self._assert_failure_contract(code, out, 1)

    def test_bad_token_prefix_emits_result_and_exits_1(self):
        code, out, _ = self._run_main(
            ["--channel", "C0123ABCD", "--message-file", str(self.msg_path),
             "--env-path", str(self.env_path)],
            environ={"SLACK_BOT_TOKEN": "xoxp-user-token"})
        self._assert_failure_contract(code, out, 1)

    def test_missing_message_file_emits_result_and_exits_1(self):
        code, out, _ = self._run_main(
            ["--channel", "C0123ABCD",
             "--message-file", str(self.msg_path) + ".missing",
             "--env-path", str(self.env_path)])
        self._assert_failure_contract(code, out, 1)

    def test_over_length_message_emits_result_and_exits_1(self):
        self.msg_path.write_text("x" * (su.MAX_MESSAGE_CHARS + 1))
        with mock.patch.object(su.urllib.request, "urlopen",
                               side_effect=AssertionError("no API call expected")):
            code, out, _ = self._run_main(
                ["--channel", "C0123ABCD", "--message-file", str(self.msg_path),
                 "--env-path", str(self.env_path)])
        result = self._assert_failure_contract(code, out, 1)
        self.assertIn("40000", result["error"])

    def test_channel_resolution_failure_emits_result_and_exits_2(self):
        page = {"ok": True, "channels": [],
                "response_metadata": {"next_cursor": ""}}
        with mock.patch.object(su.urllib.request, "urlopen",
                               side_effect=_urlopen_from_script([page])):
            code, out, _ = self._run_main(
                ["--channel", "#nope", "--message-file", str(self.msg_path),
                 "--env-path", str(self.env_path)])
        result = self._assert_failure_contract(code, out, 2)
        self.assertIn("not found", result["error"])

    def test_unverified_post_failure_emits_result_and_exits_2(self):
        with mock.patch.object(su.urllib.request, "urlopen",
                               side_effect=_urlopen_from_script([_http_error(500)])):
            code, out, _ = self._run_main(
                ["--channel", "C0123ABCD", "--message-file", str(self.msg_path),
                 "--env-path", str(self.env_path)])
        result = self._assert_failure_contract(code, out, 2)
        self.assertIn("verify in the channel", result["error"])
        self.assertEqual(result["channel"], "C0123ABCD")

    def test_slack_api_error_response_emits_result_and_exits_2(self):
        script = [{"ok": False, "error": "channel_not_found"}]
        with mock.patch.object(su.urllib.request, "urlopen",
                               side_effect=_urlopen_from_script(script)):
            code, out, _ = self._run_main(
                ["--channel", "C0123ABCD", "--message-file", str(self.msg_path),
                 "--env-path", str(self.env_path)])
        result = self._assert_failure_contract(code, out, 2)
        self.assertIn("channel_not_found", result["error"])

    def test_success_emits_ok_result_with_ts(self):
        script = [{"ok": True, "ts": "1724650000.000100"}]
        with mock.patch.object(su.urllib.request, "urlopen",
                               side_effect=_urlopen_from_script(script)):
            code, out, _ = self._run_main(
                ["--channel", "C0123ABCD", "--message-file", str(self.msg_path),
                 "--env-path", str(self.env_path)])
        self.assertEqual(code, 0)
        result = self._parse_result(out)
        self.assertTrue(result["ok"])
        self.assertEqual(result["ts"], "1724650000.000100")
        self.assertEqual(result["channel"], "C0123ABCD")
        self.assertIsNone(result["error"])


PERMALINK = "https://example.slack.com/archives/C0123ABCD/p1724650000000100"
ROOT_TS = "1724650000.000100"


class GetPermalinkTests(unittest.TestCase):
    """get_permalink(): a READ through slack_get -> chat.getPermalink."""

    def test_success_returns_permalink_via_get(self):
        calls = []
        script = [{"ok": True, "channel": "C0123ABCD", "permalink": PERMALINK}]
        with mock.patch.object(su.urllib.request, "urlopen",
                               side_effect=_urlopen_from_script(script, calls)):
            self.assertEqual(su.get_permalink("C0123ABCD", ROOT_TS, TOKEN),
                             PERMALINK)
        self.assertEqual(len(calls), 1)
        req = calls[0]
        self.assertEqual(req.get_method(), "GET")
        self.assertIsNone(req.data, "chat.getPermalink must carry no body")
        parsed = su.urllib.parse.urlparse(req.full_url)
        self.assertTrue(parsed.path.endswith("/chat.getPermalink"), req.full_url)
        qs = su.urllib.parse.parse_qs(parsed.query)
        self.assertEqual(qs, {"channel": ["C0123ABCD"], "message_ts": [ROOT_TS]})
        self.assertEqual(req.get_header("Authorization"), f"Bearer {TOKEN}")

    def test_api_error_raises_slack_error(self):
        script = [{"ok": False, "error": "message_not_found"}]
        with mock.patch.object(su.urllib.request, "urlopen",
                               side_effect=_urlopen_from_script(script)):
            with self.assertRaises(su.SlackError) as ctx:
                su.get_permalink("C0123ABCD", ROOT_TS, TOKEN)
        self.assertIn("message_not_found", str(ctx.exception))

    def test_ok_without_permalink_field_raises(self):
        # Never fabricate a URL from a half-formed response.
        script = [{"ok": True}]
        with mock.patch.object(su.urllib.request, "urlopen",
                               side_effect=_urlopen_from_script(script)):
            with self.assertRaises(su.SlackError):
                su.get_permalink("C0123ABCD", ROOT_TS, TOKEN)

    def test_5xx_is_retried_then_succeeds(self):
        # A read: 5xx goes through the retry path (unlike chat.postMessage).
        calls = []
        script = [_http_error(503), _http_error(502),
                  {"ok": True, "permalink": PERMALINK}]
        with mock.patch.object(su.urllib.request, "urlopen",
                               side_effect=_urlopen_from_script(script, calls)), \
                mock.patch.object(su.time, "sleep") as fake_sleep:
            self.assertEqual(su.get_permalink("C0123ABCD", ROOT_TS, TOKEN),
                             PERMALINK)
        self.assertEqual(len(calls), 3)
        self.assertEqual(fake_sleep.call_count, 2)

    def test_429_honors_retry_after(self):
        script = [_http_error(429, retry_after=4), {"ok": True, "permalink": PERMALINK}]
        with mock.patch.object(su.urllib.request, "urlopen",
                               side_effect=_urlopen_from_script(script)), \
                mock.patch.object(su.time, "sleep") as fake_sleep:
            su.get_permalink("C0123ABCD", ROOT_TS, TOKEN)
        fake_sleep.assert_called_once_with(4)

    def test_network_errors_exhaust_into_slack_error(self):
        script = [urllib.error.URLError("refused")] * su.MAX_RETRIES
        with mock.patch.object(su.urllib.request, "urlopen",
                               side_effect=_urlopen_from_script(script)), \
                mock.patch.object(su.time, "sleep"):
            with self.assertRaises(su.SlackError) as ctx:
                su.get_permalink("C0123ABCD", ROOT_TS, TOKEN)
        self.assertIn("exhausted", str(ctx.exception))


class MainGetPermalinkTests(unittest.TestCase):
    """main() in --get-permalink mode: result block on every exit, never raises."""

    # Reuse the post-path harness so both modes are driven identically.
    _run_main = MainResultBlockTests._run_main
    _parse_result = MainResultBlockTests._parse_result

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.env_path = Path(self._tmp.name) / "nonexistent.env"
        self.argv = ["--get-permalink", "--channel", "C0123ABCD", "--ts", ROOT_TS,
                     "--env-path", str(self.env_path)]

    def _assert_no_permalink_failure(self, code, out, expected_code):
        self.assertEqual(code, expected_code)
        result = self._parse_result(out)
        self.assertFalse(result["ok"])
        self.assertTrue(result["error"])
        self.assertIn("permalink", result)
        self.assertIsNone(result["permalink"], "failure must never carry a permalink")
        return result

    def test_success_emits_permalink_in_result(self):
        calls = []
        script = [{"ok": True, "permalink": PERMALINK}]
        with mock.patch.object(su.urllib.request, "urlopen",
                               side_effect=_urlopen_from_script(script, calls)):
            code, out, _ = self._run_main(self.argv)
        self.assertEqual(code, 0)
        result = self._parse_result(out)
        self.assertTrue(result["ok"])
        self.assertEqual(result["permalink"], PERMALINK)
        self.assertEqual(result["channel"], "C0123ABCD")
        self.assertEqual(result["ts"], ROOT_TS)
        self.assertIsNone(result["error"])
        # Exactly one call, a GET to chat.getPermalink — nothing was posted.
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].get_method(), "GET")
        self.assertIn("chat.getPermalink", calls[0].full_url)
        self.assertNotIn("chat.postMessage", calls[0].full_url)

    def test_slack_api_error_emits_ok_false_exit_2_no_permalink(self):
        script = [{"ok": False, "error": "message_not_found"}]
        with mock.patch.object(su.urllib.request, "urlopen",
                               side_effect=_urlopen_from_script(script)):
            code, out, _ = self._run_main(self.argv)
        result = self._assert_no_permalink_failure(code, out, 2)
        self.assertIn("message_not_found", result["error"])
        self.assertEqual(result["channel"], "C0123ABCD")

    def test_http_4xx_emits_ok_false_exit_2(self):
        # Non-retryable HTTP failure: surfaces cleanly, no raise, no permalink.
        with mock.patch.object(su.urllib.request, "urlopen",
                               side_effect=_urlopen_from_script([_http_error(403)])):
            code, out, _ = self._run_main(self.argv)
        result = self._assert_no_permalink_failure(code, out, 2)
        self.assertIn("403", result["error"])

    def test_network_errors_exhaust_emits_ok_false_exit_2(self):
        script = [urllib.error.URLError("refused")] * su.MAX_RETRIES
        with mock.patch.object(su.urllib.request, "urlopen",
                               side_effect=_urlopen_from_script(script)), \
                mock.patch.object(su.time, "sleep"):
            code, out, _ = self._run_main(self.argv)
        result = self._assert_no_permalink_failure(code, out, 2)
        self.assertIn("exhausted", result["error"])

    def test_5xx_retried_then_succeeds_end_to_end(self):
        script = [_http_error(500), {"ok": True, "permalink": PERMALINK}]
        with mock.patch.object(su.urllib.request, "urlopen",
                               side_effect=_urlopen_from_script(script)), \
                mock.patch.object(su.time, "sleep"):
            code, out, _ = self._run_main(self.argv)
        self.assertEqual(code, 0)
        self.assertEqual(self._parse_result(out)["permalink"], PERMALINK)

    def test_missing_token_emits_result_and_exits_1(self):
        with mock.patch.object(su.urllib.request, "urlopen",
                               side_effect=AssertionError("no API call expected")):
            code, out, _ = self._run_main(self.argv, environ={})
        self._assert_no_permalink_failure(code, out, 1)

    def test_bad_token_prefix_emits_result_and_exits_1(self):
        with mock.patch.object(su.urllib.request, "urlopen",
                               side_effect=AssertionError("no API call expected")):
            code, out, _ = self._run_main(
                self.argv, environ={"SLACK_BOT_TOKEN": "xoxp-user-token"})
        self._assert_no_permalink_failure(code, out, 1)

    def test_channel_name_resolved_before_lookup(self):
        # A '#name' still goes through resolve_channel; the id is what reaches
        # chat.getPermalink.
        calls = []
        script = [CHANNELS_PAGE, {"ok": True, "permalink": PERMALINK}]
        argv = ["--get-permalink", "--channel", "#dream-team", "--ts", ROOT_TS,
                "--env-path", str(self.env_path)]
        with mock.patch.object(su.urllib.request, "urlopen",
                               side_effect=_urlopen_from_script(script, calls)):
            code, out, _ = self._run_main(argv)
        self.assertEqual(code, 0)
        self.assertEqual(self._parse_result(out)["channel"], "C1BCUHXT9")
        qs = su.urllib.parse.parse_qs(su.urllib.parse.urlparse(calls[1].full_url).query)
        self.assertEqual(qs["channel"], ["C1BCUHXT9"])

    def test_requires_ts(self):
        with self.assertRaises(SystemExit) as ctx:
            self._run_main(["--get-permalink", "--channel", "C0123ABCD",
                            "--env-path", str(self.env_path)])
        self.assertEqual(ctx.exception.code, 2)

    def test_requires_channel(self):
        with self.assertRaises(SystemExit) as ctx:
            self._run_main(["--get-permalink", "--ts", ROOT_TS,
                            "--env-path", str(self.env_path)])
        self.assertEqual(ctx.exception.code, 2)

    def test_rejects_message_file_in_permalink_mode(self):
        msg = Path(self._tmp.name) / "m.txt"
        msg.write_text("x")
        with self.assertRaises(SystemExit) as ctx:
            self._run_main(self.argv + ["--message-file", str(msg)])
        self.assertEqual(ctx.exception.code, 2)

    def test_ts_without_get_permalink_rejected(self):
        msg = Path(self._tmp.name) / "m.txt"
        msg.write_text("x")
        with self.assertRaises(SystemExit) as ctx:
            self._run_main(["--channel", "C0123ABCD", "--message-file", str(msg),
                            "--ts", ROOT_TS, "--env-path", str(self.env_path)])
        self.assertEqual(ctx.exception.code, 2)


class PostPathShapeUnchangedTests(unittest.TestCase):
    """Adding permalink mode must not alter the normal post path's contract."""

    _run_main = MainResultBlockTests._run_main
    _parse_result = MainResultBlockTests._parse_result

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.msg_path = Path(self._tmp.name) / "message.txt"
        self.msg_path.write_text("hello channel")
        self.env_path = Path(self._tmp.name) / "nonexistent.env"
        self.argv = ["--channel", "C0123ABCD", "--message-file", str(self.msg_path),
                     "--env-path", str(self.env_path)]

    def test_post_success_keys_and_values(self):
        script = [{"ok": True, "ts": ROOT_TS}]
        with mock.patch.object(su.urllib.request, "urlopen",
                               side_effect=_urlopen_from_script(script)):
            code, out, _ = self._run_main(self.argv)
        self.assertEqual(code, 0)
        result = self._parse_result(out)
        self.assertEqual(set(result), {"ok", "channel", "ts", "error", "permalink"})
        self.assertEqual(result["ok"], True)
        self.assertEqual(result["channel"], "C0123ABCD")
        self.assertEqual(result["ts"], ROOT_TS)
        self.assertIsNone(result["error"])
        self.assertIsNone(result["permalink"], "post path never fills permalink")

    def test_post_failure_keys_and_values(self):
        script = [{"ok": False, "error": "channel_not_found"}]
        with mock.patch.object(su.urllib.request, "urlopen",
                               side_effect=_urlopen_from_script(script)):
            code, out, _ = self._run_main(self.argv)
        self.assertEqual(code, 2)
        result = self._parse_result(out)
        self.assertEqual(set(result), {"ok", "channel", "ts", "error", "permalink"})
        self.assertFalse(result["ok"])
        self.assertIsNone(result["ts"])
        self.assertIsNone(result["permalink"])
        self.assertIn("channel_not_found", result["error"])

    def test_missing_message_file_flag_is_still_a_usage_error(self):
        # --message-file used to be argparse-required (exit 2, no result
        # block); it must still behave that way on the post path.
        with self.assertRaises(SystemExit) as ctx:
            self._run_main(["--channel", "C0123ABCD", "--env-path", str(self.env_path)])
        self.assertEqual(ctx.exception.code, 2)

    def test_post_path_makes_no_permalink_call(self):
        calls = []
        script = [{"ok": True, "ts": ROOT_TS}]
        with mock.patch.object(su.urllib.request, "urlopen",
                               side_effect=_urlopen_from_script(script, calls)):
            self._run_main(self.argv)
        self.assertEqual(len(calls), 1)
        self.assertIn("chat.postMessage", calls[0].full_url)


if __name__ == "__main__":
    unittest.main()
