"""Tests for scripts/slack_lint.py.

Stdlib-only (unittest). One passing and one failing fixture per rule, plus
the char caps per --kind and the CLI contract (JSON on stdout, exit codes).
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
        "slack_lint", SCRIPTS_DIR / "slack_lint.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


slack_lint = _load_module()


def rules_of(violations):
    return [v["rule"] for v in violations]


class MentionRuleTests(unittest.TestCase):
    def test_bare_mention_passes(self):
        self.assertEqual(slack_lint.lint("Shipped by <@U02AB0913>", "any"), [])

    def test_enterprise_grid_w_mention_passes(self):
        self.assertEqual(slack_lint.lint("Shipped by <@W02AB0913>", "any"), [])

    def test_mention_with_label_fails(self):
        v = slack_lint.lint("Shipped by <@U02AB0913|marina>", "any")
        self.assertIn("mention", rules_of(v))

    def test_mention_followed_by_parenthetical_name_passes(self):
        # The v0.14.0 developer rendering: bare mention token + plain (name).
        self.assertEqual(
            slack_lint.lint("<@U02AB0913> (Chris Tokodi)", "any"), [])
        self.assertEqual(
            slack_lint.lint("<@W0ABC1234> (O'Brien-Smith)", "any"), [])

    def test_malformed_mention_fails(self):
        v = slack_lint.lint("Shipped by <@u02ab>", "any")
        self.assertIn("mention", rules_of(v))


class ChannelRuleTests(unittest.TestCase):
    def test_channel_ref_passes(self):
        self.assertEqual(slack_lint.lint("See <#C1BCUHXT9>", "any"), [])

    def test_channel_ref_with_label_passes(self):
        self.assertEqual(
            slack_lint.lint("See <#C1BCUHXT9|dream-team>", "any"), [])

    def test_malformed_channel_ref_fails(self):
        v = slack_lint.lint("See <#dreamteam>", "any")
        self.assertIn("channel", rules_of(v))


class SpecialMentionRuleTests(unittest.TestCase):
    def test_here_and_channel_pass(self):
        self.assertEqual(slack_lint.lint("<!here> deploy done", "any"), [])
        self.assertEqual(slack_lint.lint("<!channel> deploy done", "any"), [])

    def test_everyone_fails(self):
        v = slack_lint.lint("<!everyone> deploy done", "any")
        self.assertIn("special_mention", rules_of(v))


class LinkRuleTests(unittest.TestCase):
    def test_labeled_link_passes(self):
        self.assertEqual(
            slack_lint.lint("See <https://example.com/x|the docs>", "any"), [])

    def test_raw_angle_link_passes(self):
        self.assertEqual(
            slack_lint.lint("See <https://example.com/x>", "any"), [])

    def test_bare_auto_linked_url_is_legal(self):
        # The internal-storyteller mandates raw URLs for ticket links —
        # never flagged, even with & and ? characters inside.
        text = "URL: https://optimus.medtricslab.com/tasks/M1-986?a=1&b=2"
        self.assertEqual(slack_lint.lint(text, "any"), [])

    def test_piped_link_with_empty_label_fails(self):
        v = slack_lint.lint("See <https://example.com/x|>", "any")
        self.assertIn("link_label", rules_of(v))


class MarkdownRejectionTests(unittest.TestCase):
    def test_single_asterisk_bold_passes(self):
        self.assertEqual(slack_lint.lint("*Bug Fixes*", "any"), [])

    def test_double_asterisk_bold_fails(self):
        v = slack_lint.lint("this is **bold** text", "any")
        self.assertIn("bold", rules_of(v))

    def test_hash_header_fails(self):
        v = slack_lint.lint("# Release Notes", "any")
        self.assertIn("header", rules_of(v))

    def test_hash_mid_line_passes(self):
        self.assertEqual(slack_lint.lint("ticket #42 fixed", "any"), [])

    def test_fenced_block_fails(self):
        v = slack_lint.lint("```\ncode\n```", "any")
        self.assertIn("fence", rules_of(v))

    def test_inline_code_span_passes(self):
        self.assertEqual(slack_lint.lint("run `date +%F` first", "any"), [])


class EscapeRuleTests(unittest.TestCase):
    def test_escaped_entities_pass(self):
        text = "*Enhancements &amp; New Features*\n\na &lt; b &gt; c"
        self.assertEqual(slack_lint.lint(text, "any"), [])

    def test_bare_ampersand_fails(self):
        v = slack_lint.lint("Enhancements & New Features", "any")
        self.assertIn("escape", rules_of(v))

    def test_bare_angle_brackets_fail(self):
        v = slack_lint.lint("a < b", "any")
        self.assertIn("escape", rules_of(v))
        v = slack_lint.lint("b > a", "any")
        self.assertIn("escape", rules_of(v))

    def test_ampersand_inside_code_span_passes(self):
        self.assertEqual(slack_lint.lint("use `a && b` here", "any"), [])

    def test_angle_inside_valid_token_passes(self):
        self.assertEqual(slack_lint.lint("ping <@U02AB0913> now", "any"), [])


class SectionHeaderBlankLineTests(unittest.TestCase):
    def test_blank_line_before_header_passes(self):
        text = "intro line\n\n*Bug Fixes*\n1. M1-1 · title"
        self.assertEqual(slack_lint.lint(text, "any"), [])

    def test_header_as_first_line_passes(self):
        self.assertEqual(slack_lint.lint("*Bug Fixes*\n1. x", "any"), [])

    def test_missing_blank_line_fails(self):
        text = "intro line\n*Bug Fixes*\n1. M1-1 · title"
        v = slack_lint.lint(text, "any")
        self.assertIn("section_header_blank", rules_of(v))


class LengthCapTests(unittest.TestCase):
    def test_root_over_2500_fails(self):
        v = slack_lint.lint("x" * 2501, "root")
        self.assertIn("length", rules_of(v))

    def test_root_at_2500_passes(self):
        self.assertEqual(slack_lint.lint("x" * 2500, "root"), [])

    def test_reply_over_3900_fails(self):
        v = slack_lint.lint("x" * 3901, "reply")
        self.assertIn("length", rules_of(v))

    def test_reply_at_3900_passes(self):
        self.assertEqual(slack_lint.lint("x" * 3900, "reply"), [])

    def test_reply_cap_not_applied_to_root_cap_size(self):
        # 3,000 chars: fails as root, passes as reply.
        self.assertIn("length", rules_of(slack_lint.lint("x" * 3000, "root")))
        self.assertEqual(slack_lint.lint("x" * 3000, "reply"), [])

    def test_api_cap_applies_to_any_kind(self):
        v = slack_lint.lint("x" * 40001, "any")
        self.assertIn("length", rules_of(v))
        self.assertEqual(slack_lint.lint("x" * 40000, "any"), [])


GOOD_ROOT = """:rocket: *Release Notes — Monday, August 24, 2026*

Evaluation UX upgrades, notification fixes, and a bulk-import performance win.

*Enhancements &amp; New Features*
1. M1-986 · Graded Form Average Score Display · <@U02AB0913> (Chris Tokodi)
2. GEN-128592 · Bulk import performance · <@U03CD45678> (Jane Dev)

*Bug Fixes*
1. M1-1039 · Case log preceptor approval push fix · <@U04EF67890> (Sam Lee)

Clients to notify: KFSHRC, MSUCVM
Details for each item are in this thread.
"""

BAD_ROOT = (
    "intro\n*Bug Fixes*\n"                    # missing blank line before header
    "1. M1-1 · **broken bold** · <@U02AB0913|marina>\n"  # bold + mention label
    + "x" * 3000 + "\n"                        # over the root cap
)


class ComposedFixtureTests(unittest.TestCase):
    def test_good_root_fixture_passes(self):
        self.assertEqual(slack_lint.lint(GOOD_ROOT, "root"), [])

    def test_bad_root_fixture_fails_on_all_expected_rules(self):
        rules = set(rules_of(slack_lint.lint(BAD_ROOT, "root")))
        self.assertLessEqual(
            {"section_header_blank", "bold", "mention", "length"}, rules)


GOOD_ORACLE_ROOT = """:rocket: *Medtrics Oracle Release — Tuesday, August 26, 2026*

:clipboard: *Client communication — safe to forward*
Draft evaluations now save automatically, &amp; report exports finish in seconds rather than minutes. No action is needed on your side.
_(everything between the markers is client-safe)_

:lock: *Internal reference — do not forward*

*Bug fixes*
1. ORC-101 · Autosave drafts &amp; evaluations · <@U0AAAAAAA> · <https://github.com/medtrics/medtrics-oracle/pull/42|PR#42>
2. ORC-115 · Export timeout on large cohorts · <@U0BBBBBBB> · <https://github.com/medtrics/medtrics-oracle/pull/43|PR#43>

*Features*
3. ORC-120 · Rotation scheduling presets · <@U0CCCCCCC> · <https://github.com/medtrics/medtrics-oracle/pull/44|PR#44>

Details for each item are in this thread.
"""


class OracleCombinedRootTests(unittest.TestCase):
    """Lint coverage for the PARKED assembler root shape (three emoji headers,
    a "safe to forward" client block + marker, internal index lines with
    <@U…> mentions and <github-pr-url|PR#n> labeled links, &amp; escaping,
    blank lines before *…* section lines). NOTE: since 0.12.0 /oracle-release
    no longer emits this shape — it uses the internal-storyteller card shape
    exercised by OracleAlignedCardTests below. These cases remain because
    assemble_oracle_root.py is still parked in the repo and its output must
    stay lint-legal; they only assert lint behavior, not current command output."""

    def test_good_combined_root_passes_kind_root(self):
        self.assertEqual(slack_lint.lint(GOOD_ORACLE_ROOT, "root"), [])

    def test_bad_variants_fail_on_exactly_mention_and_bold(self):
        bad = GOOD_ORACLE_ROOT.replace(
            "<@U0AAAAAAA>", "<@U0AAAAAAA|alice>").replace(
            "Rotation scheduling presets", "**Rotation scheduling presets**")
        rules = set(rules_of(slack_lint.lint(bad, "root")))
        self.assertEqual(rules, {"mention", "bold"})

    def test_combined_root_over_2500_fails_length(self):
        v = slack_lint.lint(GOOD_ORACLE_ROOT + "x" * 2500, "root")
        self.assertIn("length", rules_of(v))


GOOD_ALIGNED_CARD_REPLY = """*Enhancements &amp; New Features — details*

*1. Rotation scheduling presets*
*_For clients:_* Programs can now save preset rotation schedules and reuse them, cutting repetitive setup work.
*_For Internal:_* Adds a presets table and a Supabase RLS policy scoped to the program; the scheduler reads presets on load.
• Ticket: ORC-120 (https://optimus.medtricslab.com/product-task/ORC-120)
• PRs: https://github.com/medtrics/medtrics-oracle/pull/44
• Developer: <@U0CCCCCCC> (Chris Tokodi)
• Clients: TBC — affects any program using rotation scheduling
"""


class AlignedCardTests(unittest.TestCase):
    """The shared internal-storyteller card shape (v0.13.0): bold-italic
    `*_For clients:_*` and `*_For Internal:_*` prose blocks, a Ticket line with
    an inline (URL), and a combined PRs/MRs line. Confirms the bold-italic
    labels, inline-URL Ticket line, and PR/MR URL forms are all lint-clean."""

    def test_aligned_card_passes_kind_reply(self):
        self.assertEqual(slack_lint.lint(GOOD_ALIGNED_CARD_REPLY, "reply"), [])

    def test_bolditalic_labels_are_not_double_asterisk(self):
        # The *_…_* labels must not trip the bold (**) rule.
        self.assertNotIn("bold", rules_of(
            slack_lint.lint(GOOD_ALIGNED_CARD_REPLY, "reply")))

    def test_release_slack_mr_line_passes(self):
        # /release-slack cards show a • MRs: line instead of • PRs:.
        reply = GOOD_ALIGNED_CARD_REPLY.replace(
            "• PRs: https://github.com/medtrics/medtrics-oracle/pull/44",
            "• MRs: https://gitlab.com/medtrics/app/-/merge_requests/6567")
        self.assertEqual(slack_lint.lint(reply, "reply"), [])

    def test_combined_prs_and_mrs_line_passes(self):
        reply = GOOD_ALIGNED_CARD_REPLY.replace(
            "• PRs: https://github.com/medtrics/medtrics-oracle/pull/44",
            "• PRs: https://github.com/medtrics/medtrics-oracle/pull/44 | "
            "MRs: https://gitlab.com/medtrics/app/-/merge_requests/6567")
        self.assertEqual(slack_lint.lint(reply, "reply"), [])

    def test_labeled_link_form_passes(self):
        reply = GOOD_ALIGNED_CARD_REPLY.replace(
            "• PRs: https://github.com/medtrics/medtrics-oracle/pull/44",
            "• PRs: <https://github.com/medtrics/medtrics-oracle/pull/44|PR#44>")
        self.assertEqual(slack_lint.lint(reply, "reply"), [])


class CliContractTests(unittest.TestCase):
    def _run_main(self, content, kind):
        with tempfile.NamedTemporaryFile("w", suffix=".txt",
                                         delete=False) as f:
            f.write(content)
            path = f.name
        self.addCleanup(lambda: Path(path).unlink(missing_ok=True))
        argv = ["slack_lint.py", "--input", path, "--kind", kind]
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(slack_lint.sys, "argv", argv), \
                contextlib.redirect_stdout(stdout), \
                contextlib.redirect_stderr(stderr):
            code = slack_lint.main()
        return code, stdout.getvalue(), stderr.getvalue()

    def test_pass_exits_0_with_json_contract(self):
        code, out, err = self._run_main(GOOD_ROOT, "root")
        self.assertEqual(code, 0)
        result = json.loads(out)
        self.assertTrue(result["ok"])
        self.assertEqual(result["violation_count"], 0)
        self.assertEqual(result["violations"], [])
        self.assertIn("PASS", err)

    def test_fail_exits_2_with_violations_in_json(self):
        code, out, err = self._run_main("this is **bold**", "any")
        self.assertEqual(code, 2)
        result = json.loads(out)
        self.assertFalse(result["ok"])
        self.assertGreaterEqual(result["violation_count"], 1)
        for key in ("rule", "line", "column", "snippet", "hint"):
            self.assertIn(key, result["violations"][0])
        self.assertIn("FAIL", err)

    def test_missing_input_exits_1(self):
        argv = ["slack_lint.py", "--input", "/nonexistent/x.txt"]
        with mock.patch.object(slack_lint.sys, "argv", argv), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            code = slack_lint.main()
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
