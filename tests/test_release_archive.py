"""Tests for scripts/release_archive.py — the deterministic half of the Mem
release archive (the only archive; nothing goes to disk). Stdlib-only,
hermetic (no Mem, no network, no real .env).

Pins: golden note render from a summaries fixture for both products — trace
header, item bullets, then the posted root + every reply verbatim under
`## Posted message` in `~~~` fences; REFACTOR and malformed blocks dropped;
`MR-NNNN` identifiers listed as-is; multi-PR comma-join primary-first; the
Permalink line present vs omitted; collection name resolution (process env >
.env > default, blank -> default); parse round-trip including the transcript;
bullet-like lines inside the transcript are never items; `Release: ` prefix
rejection; data-JSON fallback for url / links; `record` exits 1 on a missing
summaries / root / reply file, a bad date, or nothing left.
"""

import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "release_archive", SCRIPTS_DIR / "release_archive.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ra = _load_module()

ORACLE_SUMMARIES = """IDENTIFIER: ORC-232
TITLE: Graded Form Average Score Display
CLIENT_TITLE: Average score shown on completed evaluations
CATEGORY: ENHANCEMENT
URL: https://optimus.medtricslab.com/product-task/ORC-232
DEVELOPER_NAME: Chris Tokodi
DEVELOPER_SLACK_ID: U02AB0913
CLIENTS: KFSHRC
PR_LINKS: https://github.com/medtrics/medtrics-oracle/pull/456, https://github.com/medtrics/medtrics-oracle/pull/457
EXEC_SUMMARY: Programs can now see each evaluation's average score.
INTERNAL_SUMMARY: New "Average Score" element on the Form Complete page.
---
IDENTIFIER: ORC-234
TITLE: Export timeout fix
CLIENT_TITLE: Export timeout on large cohorts
CATEGORY: BUG FIX
URL: https://optimus.medtricslab.com/product-task/ORC-234
CLIENTS: TBC
PR_LINKS: https://github.com/medtrics/medtrics-oracle/pull/455
EXEC_SUMMARY: Large exports complete reliably.
INTERNAL_SUMMARY: Export job moved to a background worker.
---
IDENTIFIER: ORC-238
TITLE: Extract evaluation helpers
CLIENT_TITLE: Internal cleanup
CATEGORY: REFACTOR
CLIENTS: TBC
INTERNAL_SUMMARY: Pure refactor, no behavior change.
---
TITLE: Block with no identifier
CLIENT_TITLE: Should be dropped
CATEGORY: BUG FIX
CLIENTS: KFSHRC
INTERNAL_SUMMARY: malformed
---
IDENTIFIER: ORC-239
CLIENT_TITLE: Block with no TITLE
CATEGORY: BUG FIX
CLIENTS: KFSHRC
INTERNAL_SUMMARY: malformed
---
IDENTIFIER: ORC-240
TITLE: Rotation scheduling presets — heads up
CATEGORY: FEATURE
CLIENTS: CVM-NCSU, KFSHRC
INTERNAL_SUMMARY: Legacy block without CLIENT_TITLE.
"""

ROOT_TEXT = """:rocket: *Release Notes — Wednesday, September 2, 2026*

This release adds average scores to completed evaluations and fixes large exports.

*Enhancements &amp; New Features*
1. ORC-232 · Average score shown on completed evaluations

*Bug Fixes*
2. ORC-234 · Export timeout on large cohorts

Clients to notify: KFSHRC
Details for each item are in this thread.
"""

REPLY_1 = """*Enhancements &amp; New Features*

*1. ORC-232 · Average score shown on completed evaluations*
*_For clients:_* Programs can now see each evaluation's average score.
• Ticket: ORC-232
• Developer: <@U02AB0913> (Chris Tokodi)
• Internal: New "Average Score" element on the Form Complete page.
- a dash-led line that must never be parsed as an archive item · x · y · z · w
```
code block inside slack mrkdwn
```
• URL: https://optimus.medtricslab.com/product-task/ORC-232
• PRs: https://github.com/medtrics/medtrics-oracle/pull/456
• Clients: KFSHRC
"""

REPLY_2 = """*Bug Fixes*

*2. ORC-234 · Export timeout on large cohorts*
*_For clients:_* Large exports complete reliably.
• Ticket: ORC-234
• Clients: TBC (platform-wide)
"""

TRANSCRIPT_GOLDEN = """
## Posted message

### Root

~~~
""" + ROOT_TEXT.rstrip("\n") + """
~~~

### Reply 1

~~~
""" + REPLY_1.rstrip("\n") + """
~~~

### Reply 2

~~~
""" + REPLY_2.rstrip("\n") + """
~~~
"""

ORACLE_TRACE = """Release: 2026-09-02 · oracle · #pod-oracle

**Date:** 2026-09-02
**Product:** oracle
**Channel:** #pod-oracle
**Channel id:** C0XXXXXXX
**Root ts:** 1725300000.123456
**Permalink:** https://medtrics.slack.com/archives/C0XXXXXXX/p1725300000123456
**Item count:** 3
**Tickets:** ORC-232, ORC-234, ORC-240
**Clients:** KFSHRC, CVM-NCSU

- ORC-232 · Average score shown on completed evaluations · ENHANCEMENT · KFSHRC · https://github.com/medtrics/medtrics-oracle/pull/456, https://github.com/medtrics/medtrics-oracle/pull/457
- ORC-234 · Export timeout on large cohorts · BUG FIX · TBC · https://github.com/medtrics/medtrics-oracle/pull/455
- ORC-240 · Rotation scheduling presets — heads up · FEATURE · CVM-NCSU, KFSHRC · no PR
"""

ORACLE_GOLDEN = ORACLE_TRACE + TRANSCRIPT_GOLDEN

MEDTRICS_SUMMARIES = """IDENTIFIER: M1-986
TITLE: Graded Form Average Score Display
CLIENT_TITLE: Average score shown on completed evaluations
CATEGORY: ENHANCEMENT
URL: https://optimus.medtricslab.com/product-task/M1-986
CLIENTS: KFSHRC
MR_LINKS: https://gitlab.com/org/repo/-/merge_requests/6567
EXEC_SUMMARY: x
INTERNAL_SUMMARY: y
---
IDENTIFIER: MR-6570
TITLE: Hotfix: null guard on rotation export
CLIENT_TITLE: Rotation export no longer fails on empty blocks
CATEGORY: BUG FIX
CLIENTS: TBC
MR_LINKS: https://gitlab.com/org/repo/-/merge_requests/6570
EXEC_SUMMARY: x
INTERNAL_SUMMARY: y
---
IDENTIFIER: GEN-128592
TITLE: SSO login loop
CLIENT_TITLE: Single sign-on no longer loops on expired sessions
CATEGORY: BUG FIX
CLIENTS: UCSF, KFSHRC
EXEC_SUMMARY: x
INTERNAL_SUMMARY: y
"""

MEDTRICS_TRACE = """Release: 2026-09-02 · medtrics · #dream-team

**Date:** 2026-09-02
**Product:** medtrics
**Channel:** #dream-team
**Channel id:** C0YYYYYYY
**Root ts:** 1725300001.000001
**Item count:** 3
**Tickets:** M1-986, MR-6570, GEN-128592
**Clients:** KFSHRC, UCSF

- M1-986 · Average score shown on completed evaluations · ENHANCEMENT · KFSHRC · https://gitlab.com/org/repo/-/merge_requests/6567
- MR-6570 · Rotation export no longer fails on empty blocks · BUG FIX · TBC · https://gitlab.com/org/repo/-/merge_requests/6570
- GEN-128592 · Single sign-on no longer loops on expired sessions · BUG FIX · UCSF, KFSHRC · no MR
"""

MEDTRICS_GOLDEN = MEDTRICS_TRACE + TRANSCRIPT_GOLDEN


def _write_text(d, name, text):
    p = Path(d) / name
    p.write_text(text)
    return str(p)


def _transcript_args(d):
    """Write the posted-message fixtures into d; return the record flags."""
    root = _write_text(d, "root.txt", ROOT_TEXT)
    r1 = _write_text(d, "reply_01.txt", REPLY_1)
    r2 = _write_text(d, "reply_02.txt", REPLY_2)
    return ["--root-file", root, "--reply-files", r1, r2]


def _run(argv, environ=None):
    """Run main() with argv; return (code, stdout, stderr, result_block)."""
    out, err = io.StringIO(), io.StringIO()
    env = {k: v for k, v in os.environ.items() if k != ra.ENV_KEY}
    env.update(environ or {})
    with mock.patch.object(ra.sys, "argv", ["release_archive.py"] + argv), \
            mock.patch.dict(os.environ, env, clear=True), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = ra.main()
    stdout, stderr = out.getvalue(), err.getvalue()
    combined = stdout + stderr
    result = None
    if "---RELEASE_ARCHIVE_RESULT---" in combined:
        after = combined.split("---RELEASE_ARCHIVE_RESULT---")[1].strip()
        result = json.loads(after.splitlines()[0])   # one JSON line follows
    return code, stdout, stderr, result


class RecordGoldenTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.oracle = _write_text(self.d, "oracle_summaries.txt", ORACLE_SUMMARIES)
        self.medtrics = _write_text(self.d, "medtrics_summaries.txt",
                                    MEDTRICS_SUMMARIES)
        self.transcript = _transcript_args(self.d)

    def record(self, extra, transcript=None):
        return _run(["record"] + extra
                    + (self.transcript if transcript is None else transcript))

    def test_oracle_golden_render_with_permalink(self):
        code, stdout, stderr, result = self.record([
            "--summaries", self.oracle, "--product", "oracle",
            "--date", "2026-09-02", "--channel", "#pod-oracle",
            "--channel-id", "C0XXXXXXX", "--ts", "1725300000.123456",
            "--permalink",
            "https://medtrics.slack.com/archives/C0XXXXXXX/p1725300000123456"])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(stdout, ORACLE_GOLDEN)          # stdout = body only
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "record")
        self.assertEqual(result["items"], 3)
        self.assertEqual(result["dropped"], 3)           # REFACTOR + 2 malformed
        self.assertEqual(result["tickets"], ["ORC-232", "ORC-234", "ORC-240"])
        self.assertTrue(result["permalink"])
        self.assertEqual(result["replies"], 2)
        self.assertIn("Release archive: rendered note for ts 1725300000.123456 "
                      "(3 item(s), root + 2 replies); if this was a re-post, "
                      "trash the older note in Mem.", stderr)
        self.assertNotIn("---RELEASE_ARCHIVE_RESULT---", stdout)

    def test_transcript_follows_items_in_post_order_with_tilde_fences(self):
        code, stdout, _, _ = self.record([
            "--summaries", self.oracle, "--product", "oracle",
            "--date", "2026-09-02", "--channel", "#pod-oracle",
            "--channel-id", "C0XXXXXXX", "--ts", "1"])
        self.assertEqual(code, 0)
        head, _, tail = stdout.partition("\n## Posted message\n")
        self.assertTrue(head.endswith("· no PR\n"))          # items first
        self.assertLess(tail.index("### Root"), tail.index("### Reply 1"))
        self.assertLess(tail.index("### Reply 1"), tail.index("### Reply 2"))
        self.assertEqual(tail.count("~~~"), 6)                # 3 fenced blocks
        self.assertIn("```\ncode block inside slack mrkdwn\n```", tail)
        self.assertIn(ROOT_TEXT.rstrip("\n"), tail)
        self.assertIn(REPLY_2.rstrip("\n"), tail)

    def test_zero_replies_still_renders_root(self):
        root = _write_text(self.d, "only_root.txt", ROOT_TEXT)
        code, stdout, stderr, result = self.record([
            "--summaries", self.oracle, "--product", "oracle",
            "--date", "2026-09-02", "--channel", "#pod-oracle",
            "--channel-id", "C0XXXXXXX", "--ts", "1"],
            transcript=["--root-file", root])
        self.assertEqual(code, 0)
        self.assertEqual(result["replies"], 0)
        self.assertIn("### Root", stdout)
        self.assertNotIn("### Reply", stdout)
        self.assertIn("root + 0 replies", stderr)

    def test_render_note_without_transcript_has_no_heading(self):
        items, _ = ra.build_items(ra.parse_summaries(ORACLE_SUMMARIES), "oracle")
        body = ra.render_note(items, "oracle", "2026-09-02", "#c", "C1", "1")
        self.assertNotIn("## Posted message", body)
        self.assertTrue(body.endswith("· no PR\n"))

    def test_medtrics_golden_render_without_permalink(self):
        code, stdout, stderr, result = self.record([
            "--summaries", self.medtrics, "--product", "medtrics",
            "--date", "2026-09-02", "--channel", "#dream-team",
            "--channel-id", "C0YYYYYYY", "--ts", "1725300001.000001"])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(stdout, MEDTRICS_GOLDEN)
        self.assertNotIn("**Permalink:**", stdout)       # line omitted entirely
        self.assertFalse(result["permalink"])
        self.assertEqual(result["dropped"], 0)

    def test_refactor_and_malformed_blocks_are_dropped(self):
        code, stdout, stderr, _ = self.record([
            "--summaries", self.oracle, "--product", "oracle",
            "--date", "2026-09-02", "--channel", "#pod-oracle",
            "--channel-id", "C0XXXXXXX", "--ts", "1"])
        self.assertNotIn("ORC-238", stdout)              # REFACTOR
        self.assertNotIn("ORC-239", stdout)              # no TITLE
        self.assertNotIn("Should be dropped", stdout)    # no IDENTIFIER
        self.assertIn("dropped block ORC-238: REFACTOR", stderr)
        self.assertIn("dropped block ORC-239: malformed (no TITLE)", stderr)
        self.assertIn("dropped block (no IDENTIFIER): malformed", stderr)

    def test_mr_identifier_listed_as_is(self):
        code, stdout, _, result = self.record([
            "--summaries", self.medtrics, "--product", "medtrics",
            "--date", "2026-09-02", "--channel", "#dream-team",
            "--channel-id", "C0YYYYYYY", "--ts", "1"])
        self.assertIn("MR-6570", result["tickets"])
        self.assertIn("**Tickets:** M1-986, MR-6570, GEN-128592", stdout)
        self.assertIn("- MR-6570 · ", stdout)

    def test_multi_pr_comma_join_primary_first(self):
        items, _ = ra.build_items(ra.parse_summaries(ORACLE_SUMMARIES), "oracle")
        self.assertEqual(items[0]["links"], [
            "https://github.com/medtrics/medtrics-oracle/pull/456",
            "https://github.com/medtrics/medtrics-oracle/pull/457"])
        body = ra.render_note(items, "oracle", "2026-09-02", "#c", "C1", "1")
        self.assertIn("· https://github.com/medtrics/medtrics-oracle/pull/456, "
                      "https://github.com/medtrics/medtrics-oracle/pull/457\n",
                      body)

    def test_pr_links_precede_mr_links_and_dedupe(self):
        text = ("IDENTIFIER: ORC-1\nTITLE: t\nCATEGORY: FEATURE\n"
                "PR_LINKS: https://g/pull/2 https://g/pull/1\n"
                "MR_LINKS: https://gl/-/merge_requests/9, https://g/pull/2\n")
        items, _ = ra.build_items(ra.parse_summaries(text), "oracle")
        self.assertEqual(items[0]["links"], ["https://g/pull/2", "https://g/pull/1",
                                             "https://gl/-/merge_requests/9"])

    def test_no_links_placeholder_per_product(self):
        text = "IDENTIFIER: X-1\nTITLE: t\nCATEGORY: BUG FIX\n"
        items, _ = ra.build_items(ra.parse_summaries(text), "oracle")
        self.assertTrue(ra.render_note(items, "oracle", "2026-09-02", "#c",
                                       "C1", "1").endswith(" · no PR\n"))
        self.assertTrue(ra.render_note(items, "medtrics", "2026-09-02", "#c",
                                       "C1", "1").endswith(" · no MR\n"))

    def test_client_title_falls_back_to_title(self):
        items, _ = ra.build_items(ra.parse_summaries(ORACLE_SUMMARIES), "oracle")
        by_id = {it["id"]: it for it in items}
        self.assertEqual(by_id["ORC-240"]["title"],
                         "Rotation scheduling presets — heads up")
        self.assertEqual(by_id["ORC-232"]["title"],
                         "Average score shown on completed evaluations")

    def test_clients_union_dedupes_and_excludes_tbc(self):
        items, _ = ra.build_items(ra.parse_summaries(ORACLE_SUMMARIES), "oracle")
        self.assertEqual(ra._client_codes(items), ["KFSHRC", "CVM-NCSU"])
        only_tbc = "IDENTIFIER: X-1\nTITLE: t\nCATEGORY: FEATURE\nCLIENTS: TBC\n"
        items, _ = ra.build_items(ra.parse_summaries(only_tbc), "oracle")
        body = ra.render_note(items, "oracle", "2026-09-02", "#c", "C1", "1")
        self.assertIn("**Clients:** TBC\n", body)
        self.assertIn("· FEATURE · TBC · no PR", body)

    def test_out_writes_file_and_keeps_stdout_empty(self):
        out = str(Path(self.d) / "note.txt")
        code, stdout, _, result = self.record([
            "--summaries", self.oracle, "--product", "oracle",
            "--date", "2026-09-02", "--channel", "#pod-oracle",
            "--channel-id", "C0XXXXXXX", "--ts", "1725300000.123456",
            "--permalink",
            "https://medtrics.slack.com/archives/C0XXXXXXX/p1725300000123456",
            "--out", out])
        self.assertEqual(code, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(Path(out).read_text(), ORACLE_GOLDEN)
        self.assertEqual(result["out"], out)


class DataFallbackTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.summaries = _write_text(self.d, "s.txt", (
            "IDENTIFIER: ORC-300\nTITLE: t\nCLIENT_TITLE: Client title\n"
            "CATEGORY: FEATURE\nCLIENTS: KFSHRC\n"
            "---\n"
            "IDENTIFIER: ORC-301\nTITLE: t2\nCATEGORY: BUG FIX\n"
            "URL: https://optimus/ORC-301\n"
            "PR_LINKS: https://github.com/o/r/pull/9\n"))
        self.data = _write_text(self.d, "data.json", json.dumps({"issues": [
            {"identifier": "orc-300", "url": "https://optimus/ORC-300",
             "mrs": [{"iid": 5, "web_url": "https://github.com/o/r/pull/5"},
                     {"iid": 6, "html_url": "https://github.com/o/r/pull/6"}]},
            {"identifier": "ORC-301", "url": "https://optimus/other",
             "mrs": [{"iid": 1, "web_url": "https://github.com/o/r/pull/1"}]},
        ]}))

    def test_url_and_links_filled_from_data_only_when_block_lacks_them(self):
        items, _ = ra.build_items(ra.parse_summaries(Path(self.summaries).read_text()),
                                  "oracle", ra.load_data_index(self.data))
        by_id = {it["id"]: it for it in items}
        self.assertEqual(by_id["ORC-300"]["url"], "https://optimus/ORC-300")
        self.assertEqual(by_id["ORC-300"]["links"],
                         ["https://github.com/o/r/pull/5",
                          "https://github.com/o/r/pull/6"])
        # The block's own values win over the data JSON.
        self.assertEqual(by_id["ORC-301"]["url"], "https://optimus/ORC-301")
        self.assertEqual(by_id["ORC-301"]["links"], ["https://github.com/o/r/pull/9"])

    def test_record_uses_data_for_links(self):
        code, stdout, _, _ = _run([
            "record", "--summaries", self.summaries, "--data", self.data,
            "--product", "oracle", "--date", "2026-09-02", "--channel", "#c",
            "--channel-id", "C1", "--ts", "1"] + _transcript_args(self.d))
        self.assertEqual(code, 0)
        self.assertIn("- ORC-300 · Client title · FEATURE · KFSHRC · "
                      "https://github.com/o/r/pull/5, https://github.com/o/r/pull/6\n",
                      stdout)

    def test_missing_or_broken_data_is_not_an_error(self):
        self.assertEqual(ra.load_data_index(""), {})
        self.assertEqual(ra.load_data_index(str(Path(self.d) / "nope.json")), {})
        broken = _write_text(self.d, "broken.json", "{not json")
        self.assertEqual(ra.load_data_index(broken), {})
        code, stdout, _, _ = _run([
            "record", "--summaries", self.summaries, "--data", broken,
            "--product", "oracle", "--date", "2026-09-02", "--channel", "#c",
            "--channel-id", "C1", "--ts", "1"] + _transcript_args(self.d))
        self.assertEqual(code, 0)
        self.assertIn("- ORC-300 · Client title · FEATURE · KFSHRC · no PR\n", stdout)


class RecordErrorTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.summaries = _write_text(self.d, "s.txt", ORACLE_SUMMARIES)

    def base(self):
        return ["record", "--summaries", self.summaries, "--product", "oracle",
                "--date", "2026-09-02", "--channel", "#c", "--channel-id", "C1",
                "--ts", "1"] + _transcript_args(self.d)

    def test_missing_root_file_exits_1(self):
        argv = self.base()
        argv[argv.index("--root-file") + 1] = str(Path(self.d) / "no_root.txt")
        code, stdout, _, result = _run(argv)
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("root message file not found", result["error"])

    def test_missing_reply_file_exits_1(self):
        argv = self.base()
        argv[argv.index("--reply-files") + 2] = str(Path(self.d) / "no_r2.txt")
        code, stdout, _, result = _run(argv)
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("reply message 2 file not found", result["error"])

    def test_root_file_is_required(self):
        argv = self.base()
        i = argv.index("--root-file")
        del argv[i:i + 2]
        with self.assertRaises(SystemExit) as cm:
            _run(argv)
        self.assertEqual(cm.exception.code, 2)

    def test_missing_summaries_exits_1(self):
        argv = self.base()
        argv[2] = str(Path(self.d) / "missing.txt")
        code, stdout, stderr, result = _run(argv)
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertFalse(result["ok"])
        self.assertIn("summaries file not found", result["error"])

    def test_bad_date_exits_1(self):
        argv = self.base()
        argv[argv.index("--date") + 1] = "09/02/2026"
        code, stdout, _, result = _run(argv)
        self.assertEqual(code, 1)
        self.assertIn("YYYY-MM-DD", result["error"])

    def test_all_blocks_dropped_exits_1(self):
        argv = self.base()
        argv[2] = _write_text(self.d, "refactor.txt",
                              "IDENTIFIER: ORC-1\nTITLE: t\nCATEGORY: REFACTOR\n")
        code, stdout, _, result = _run(argv)
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("nothing to record", result["error"])

    def test_bad_product_rejected_by_argparse(self):
        argv = self.base()
        argv[argv.index("--product") + 1] = "legacy"
        with self.assertRaises(SystemExit) as cm:
            _run(argv)
        self.assertEqual(cm.exception.code, 2)


class ParseRoundTripTests(unittest.TestCase):
    def test_round_trip_oracle_golden(self):
        rec = ra.parse_note({"id": "n1", "version": 2, "content": ORACLE_GOLDEN})
        self.assertEqual(rec["note_id"], "n1")
        self.assertEqual(rec["version"], 2)
        self.assertEqual(rec["title"], "2026-09-02 · oracle · #pod-oracle")
        self.assertEqual(rec["date"], "2026-09-02")
        self.assertEqual(rec["product"], "oracle")
        self.assertEqual(rec["channel"], "#pod-oracle")
        self.assertEqual(rec["channel_id"], "C0XXXXXXX")
        self.assertEqual(rec["root_ts"], "1725300000.123456")
        self.assertEqual(rec["permalink"], "https://medtrics.slack.com/archives/"
                                           "C0XXXXXXX/p1725300000123456")
        self.assertEqual(rec["item_count"], 3)
        self.assertEqual(rec["tickets"], ["ORC-232", "ORC-234", "ORC-240"])
        self.assertEqual(rec["clients"], ["KFSHRC", "CVM-NCSU"])
        self.assertEqual(len(rec["items"]), 3)
        self.assertEqual(rec["items"][0], {
            "id": "ORC-232",
            "title": "Average score shown on completed evaluations",
            "category": "ENHANCEMENT", "clients": ["KFSHRC"],
            "links": ["https://github.com/medtrics/medtrics-oracle/pull/456",
                      "https://github.com/medtrics/medtrics-oracle/pull/457"]})
        self.assertEqual(rec["items"][2]["links"], [])         # "no PR"
        self.assertEqual(rec["items"][2]["clients"], ["CVM-NCSU", "KFSHRC"])
        self.assertEqual(rec["root_text"], ROOT_TEXT.rstrip("\n"))
        self.assertEqual(rec["reply_texts"],
                         [REPLY_1.rstrip("\n"), REPLY_2.rstrip("\n")])
        # Re-rendering the parsed record reproduces the golden body exactly.
        again = ra.render_note(rec["items"], rec["product"], rec["date"],
                               rec["channel"], rec["channel_id"], rec["root_ts"],
                               rec["permalink"], rec["root_text"],
                               rec["reply_texts"])
        self.assertEqual(again, ORACLE_GOLDEN)

    def test_transcript_lines_are_never_items_and_trace_is_untouched(self):
        rec = ra.parse_note(ORACLE_GOLDEN)
        self.assertEqual(len(rec["items"]), 3)      # the dash line in REPLY_1
        self.assertEqual(rec["item_count"], 3)      # did not become a 4th item
        self.assertEqual(rec["tickets"], ["ORC-232", "ORC-234", "ORC-240"])
        # And a trace-only note (older schema) still parses with no transcript.
        rec = ra.parse_note(ORACLE_TRACE)
        self.assertEqual(len(rec["items"]), 3)
        self.assertEqual(rec["root_text"], "")
        self.assertEqual(rec["reply_texts"], [])

    def test_unterminated_fence_keeps_what_was_read(self):
        body = (ORACLE_TRACE + "\n## Posted message\n\n### Root\n\n~~~\n"
                "partial root\nsecond line\n")
        rec = ra.parse_note(body)
        self.assertEqual(rec["root_text"], "partial root\nsecond line")
        self.assertEqual(rec["reply_texts"], [])

    def test_round_trip_medtrics_golden_without_permalink(self):
        rec = ra.parse_note(MEDTRICS_GOLDEN)                   # bare string ok
        self.assertEqual(rec["permalink"], "")
        self.assertEqual(rec["product"], "medtrics")
        self.assertEqual(rec["clients"], ["KFSHRC", "UCSF"])
        again = ra.render_note(rec["items"], rec["product"], rec["date"],
                               rec["channel"], rec["channel_id"], rec["root_ts"],
                               "", rec["root_text"], rec["reply_texts"])
        self.assertEqual(again, MEDTRICS_GOLDEN)

    def test_notes_without_release_prefix_are_ignored(self):
        self.assertIsNone(ra.parse_note({"content": "Meeting notes\n\nnothing"}))
        self.assertIsNone(ra.parse_note({"content": ""}))
        self.assertIsNone(ra.parse_note({"content": "Developer: Chris\n"}))
        self.assertIsNone(ra.parse_note({"content": "release: 2026-09-02\n"}))
        self.assertIsNone(ra.parse_note("PR Override: ORC-1\n**PR:** 3\n"))
        self.assertIsNone(ra.parse_note(None))
        self.assertIsNone(ra.parse_note(42))
        self.assertFalse(ra.is_archive_note({"content": "Meeting notes"}))
        self.assertTrue(ra.is_archive_note(ORACLE_GOLDEN))

    def test_bullet_with_middle_dot_in_title_survives(self):
        body = ("Release: 2026-09-02 · oracle · #c\n\n**Date:** 2026-09-02\n\n"
                "- ORC-5 · Grades · averages · shown · FEATURE · KFSHRC · no PR\n"
                "- not a bullet with enough parts\n")
        rec = ra.parse_note(body)
        self.assertEqual(len(rec["items"]), 1)
        self.assertEqual(rec["items"][0]["title"], "Grades · averages · shown")
        self.assertEqual(rec["items"][0]["category"], "FEATURE")

    def test_parse_summaries_tolerates_wrapped_values_and_blank_blocks(self):
        text = ("---\nIDENTIFIER: A-1\nTITLE: t\nINTERNAL_SUMMARY: first line\n"
                "  continued here\n---\n\n---\nIDENTIFIER: A-2\nTITLE: u\n")
        blocks = ra.parse_summaries(text)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0]["INTERNAL_SUMMARY"], "first line continued here")
        self.assertEqual(blocks[1]["IDENTIFIER"], "A-2")
        self.assertEqual(ra.parse_summaries(""), [])
        self.assertEqual(ra.parse_summaries(None), [])


class CollectionNameTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.env = Path(self.d) / ".env"

    def resolve(self, environ=None):
        env = {k: v for k, v in os.environ.items() if k != ra.ENV_KEY}
        env.update(environ or {})
        with mock.patch.dict(os.environ, env, clear=True):
            return ra.resolve_collection_name(str(self.env))

    def test_default_when_no_env_file(self):
        self.assertEqual(self.resolve(), (ra.DEFAULT_COLLECTION_NAME, "default"))
        self.assertEqual(ra.DEFAULT_COLLECTION_NAME, "Release Notes Archive")

    def test_env_file_value_with_quotes_and_comments(self):
        self.env.write_text("# archive\nSLACK_BOT_TOKEN=xoxb-1\n"
                            "MEM_RELEASE_ARCHIVE_COLLECTION='Shipped Releases'\n"
                            "GITHUB_REPO=o/r\n")
        self.assertEqual(self.resolve(), ("Shipped Releases", ".env"))
        self.env.write_text('export MEM_RELEASE_ARCHIVE_COLLECTION="Ship Log"\n')
        self.assertEqual(self.resolve(), ("Ship Log", ".env"))

    def test_process_env_overrides_env_file(self):
        self.env.write_text("MEM_RELEASE_ARCHIVE_COLLECTION=From File\n")
        self.assertEqual(self.resolve({ra.ENV_KEY: "From Env"}),
                         ("From Env", "env"))

    def test_blank_values_fall_through_to_default(self):
        self.env.write_text("MEM_RELEASE_ARCHIVE_COLLECTION=   \n")
        self.assertEqual(self.resolve(), (ra.DEFAULT_COLLECTION_NAME, "default"))
        self.assertEqual(self.resolve({ra.ENV_KEY: "  "}),
                         (ra.DEFAULT_COLLECTION_NAME, "default"))
        self.env.write_text("MEM_RELEASE_ARCHIVE_COLLECTION=From File\n")
        # Blank process env does NOT mask a real .env value.
        self.assertEqual(self.resolve({ra.ENV_KEY: ""}), ("From File", ".env"))

    def test_never_writes_env_file(self):
        self.env.write_text("MEM_RELEASE_ARCHIVE_COLLECTION=Keep\n")
        before = self.env.read_text()
        self.resolve()
        _run(["pending", "--env-file", str(self.env)])
        self.assertEqual(self.env.read_text(), before)
        missing = Path(self.d) / "absent.env"
        with mock.patch.dict(os.environ, {}, clear=False):
            ra.resolve_collection_name(str(missing))
        self.assertFalse(missing.exists())


class PendingTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.env = Path(self.d) / ".env"

    def test_pending_prints_name_description_and_result(self):
        self.env.write_text("MEM_RELEASE_ARCHIVE_COLLECTION=Shipped Releases\n")
        code, stdout, stderr, result = _run(["pending", "--env-file", str(self.env)])
        self.assertEqual(code, 0)
        payload = json.loads(stdout.split("---RELEASE_ARCHIVE_RESULT---")[0])
        self.assertEqual(payload, {
            "collection_name": "Shipped Releases",
            "collection_description": ra.COLLECTION_DESCRIPTION,
            "source": ".env"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "pending")
        self.assertEqual(result["collection_name"], "Shipped Releases")
        self.assertIn('Release archive: notes go to Mem collection '
                      '"Shipped Releases"', stderr)

    def test_pending_default_and_env_override(self):
        code, stdout, stderr, result = _run(["pending", "--env-file", str(self.env)])
        self.assertEqual(result["collection_name"], "Release Notes Archive")
        self.assertEqual(result["source"], "default")
        self.assertIn('"Release Notes Archive"', stderr)
        code, stdout, stderr, result = _run(
            ["pending", "--env-file", str(self.env)], {ra.ENV_KEY: "Override"})
        self.assertEqual((result["collection_name"], result["source"]),
                         ("Override", "env"))
        self.assertIn('"Override"', stderr)


if __name__ == "__main__":
    unittest.main()
