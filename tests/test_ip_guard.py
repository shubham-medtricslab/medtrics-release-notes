"""Tests for scripts/ip_guard.py.

Stdlib-only (unittest). Covers:
  - the mrkdwn numbered-index-line exemption (`N. ID · title`) — new in 0.9.0,
    used by the Slack root-post-as-index format
  - the markdown `## [ID] Title` heading exemption — unchanged from 0.8.0
  - at least one block and one warn violation per category still detected
  - the CLI exit-code contract (0 pass / 2 fail)
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
        "ip_guard", SCRIPTS_DIR / "ip_guard.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ip_guard = _load_module()

COMPILED = ip_guard.compile_patterns()


def scan(content):
    return ip_guard.scan(content, COMPILED, allowlist=[])


def matches(violations, category, severity):
    return [v for v in violations
            if v["category"] == category and v["severity"] == severity]


# A ticket title that would trip several categories if scanned.
DIRTY_TITLE = "CRITICAL BUG: Django endpoint serializer webhook leak"


class ExemptionTests(unittest.TestCase):
    def test_markdown_heading_exemption_still_works(self):
        line = f"### [M1-1054] {DIRTY_TITLE}"
        self.assertEqual(scan(line), [])

    def test_mrkdwn_numbered_index_line_exempt(self):
        line = f"3. M1-1054 · {DIRTY_TITLE}"
        self.assertEqual(scan(line), [])

    def test_mrkdwn_index_exemption_covers_all_id_prefixes(self):
        for ident in ("M1-1054", "GEN-128592", "DEV-42", "RD-7", "do-15",
                      "MR-6567"):
            line = f"1. {ident} · {DIRTY_TITLE}"
            self.assertEqual(scan(line), [], f"{ident} index line was scanned")

    def test_reply_entry_title_line_exempt(self):
        # Thread-reply entries wrap the title in bold: *<n>. <ID> · <title>*
        line = f"*3. M1-1054 · {DIRTY_TITLE}*"
        self.assertEqual(scan(line), [])

    def test_heads_up_opener_exempt(self):
        # [!HEADSUP] replies open with *Heads up — <ID> · <title>*
        for dash in ("—", "-"):
            line = f"*Heads up {dash} M1-1060 · {DIRTY_TITLE}*"
            self.assertEqual(scan(line), [], f"heads-up line with '{dash}' was scanned")

    def test_bold_prose_is_still_scanned(self):
        # A bold line that is not a title carrier must not sneak through.
        self.assertTrue(scan("*The Django serializer was rewritten.*"))
        self.assertTrue(scan(f"*Heads up — we rewrote the serializer*"))

    def test_numbered_line_without_ticket_id_is_still_scanned(self):
        line = f"3. {DIRTY_TITLE}"
        self.assertTrue(scan(line), "index-lookalike without an ID must scan")

    def test_prose_line_with_same_words_is_still_scanned(self):
        self.assertTrue(scan(f"We fixed the {DIRTY_TITLE}."))

    def test_exemption_is_per_line_only(self):
        content = (f"1. M1-1054 · {DIRTY_TITLE}\n"
                   "The Django serializer was rewritten.\n")
        violations = scan(content)
        self.assertTrue(violations)
        self.assertTrue(all(v["line"] == 2 for v in violations))


class CategoryDetectionTests(unittest.TestCase):
    """One block + one warn (where the category defines both) per category."""

    def test_internal_system_block_and_warn(self):
        v = scan("The Optimus rollout also touched Medtrics-Oracle.")
        self.assertTrue(matches(v, "internal_system", "block"))   # Optimus
        self.assertTrue(matches(v, "internal_system", "warn"))    # Medtrics-Oracle

    def test_tech_stack_block_and_warn(self):
        v = scan("A new serializer updates the underlying model nightly.")
        self.assertTrue(matches(v, "tech_stack", "block"))        # serializer
        self.assertTrue(matches(v, "tech_stack", "warn"))         # model

    def test_mr_id_blocks(self):
        v = scan("Shipped in !1234 (see MR 5678).")
        self.assertGreaterEqual(len(matches(v, "mr_id", "block")), 2)

    def test_file_path_blocks(self):
        v = scan("The change lives in src/apps/evals and views.py.")
        self.assertTrue(matches(v, "file_path", "block"))

    def test_architecture_block_and_warn(self):
        v = scan("A webhook now invalidates the cache.")
        self.assertTrue(matches(v, "architecture", "block"))      # webhook
        self.assertTrue(matches(v, "architecture", "warn"))       # cache

    def test_marketing_voice_warns(self):
        v = scan("We're thrilled to ship this game-changing update.")
        self.assertGreaterEqual(len(matches(v, "marketing_voice", "warn")), 2)

    def test_customer_name_blocks_via_customer_list(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("KFSHRC\nMSUCVM\n")
            roster = f.name
        self.addCleanup(lambda: Path(roster).unlink(missing_ok=True))
        compiled = COMPILED + ip_guard.load_customer_patterns(roster)
        v = ip_guard.scan("Requested by KFSHRC for their residents.",
                          compiled, allowlist=[])
        self.assertTrue(matches(v, "customer_name", "block"))

    def test_customer_name_exempt_on_index_line(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("KFSHRC\n")
            roster = f.name
        self.addCleanup(lambda: Path(roster).unlink(missing_ok=True))
        compiled = COMPILED + ip_guard.load_customer_patterns(roster)
        v = ip_guard.scan("2. M1-986 · [KFSHRC] Graded Form Average Score",
                          compiled, allowlist=[])
        self.assertEqual(v, [])


class OracleGuardPatternTests(unittest.TestCase):
    """v0.10.0 additive Oracle-track patterns: GitHub / github.com / Vite in
    internal_system, and PR-reference blocks (PR#42 / PR #42 / PR 42 in the
    case-sensitive bucket; 'pull request #42' case-insensitive). No allow
    files anywhere — the exemption regexes already cover ORC- carrier lines.
    """

    def test_github_github_com_and_vite_block_in_prose(self):
        v = scan("We moved the GitHub build off github.com and onto Vite.")
        blocks = matches(v, "internal_system", "block")
        matched = " ".join(b["match"] for b in blocks)
        self.assertIn("GitHub", matched)
        self.assertIn("github.com", matched)
        self.assertIn("Vite", matched)

    def test_pr_hash_forms_block(self):
        # The case-sensitive pattern must match "PR#42", "PR #42", "PR 42".
        for form in ("PR#42", "PR #42", "PR 42"):
            v = scan(f"Shipped in {form} yesterday.")
            self.assertTrue(matches(v, "mr_id", "block"),
                            f"{form!r} was not blocked")

    def test_pull_request_phrase_blocks_case_insensitively(self):
        for form in ("Pull Request #42", "pull request 42",
                     "PULL REQUEST #7"):
            v = scan(f"Fixed in {form}.")
            self.assertTrue(matches(v, "mr_id", "block"),
                            f"{form!r} was not blocked")

    def test_orc_forms_still_exempt_on_carrier_lines(self):
        dirty = "GitHub Vite autosave PR#42 pull request 7"
        for line in (f"### [ORC-101] {dirty}",
                     f"1. ORC-101 · {dirty}",
                     f"*1. ORC-101 · {dirty}*"):
            self.assertEqual(scan(line), [], f"carrier line scanned: {line!r}")

    def test_realistic_client_section_with_internal_vocab_fails(self):
        fixture = (
            "Scheduling and reporting improvements arrive this week.\n"
            "\n"
            "Draft evaluations now save automatically while faculty type, "
            "so unfinished feedback is never lost.\n"
            "The GitHub build now runs on Vite — shipped in PR #12 and "
            "pull request #13.\n"
            "No action is needed from your team.\n"
        )
        v = scan(fixture)
        blocking = [x for x in v if x["severity"] == "block"]
        self.assertTrue(blocking)
        categories = {x["category"] for x in blocking}
        self.assertIn("internal_system", categories)
        self.assertIn("mr_id", categories)

    def test_realistic_clean_client_section_passes(self):
        fixture = (
            "Scheduling and reporting improvements arrive this week.\n"
            "\n"
            "Draft evaluations now save automatically while faculty type, "
            "so unfinished feedback is never lost.\n"
            "Report exports that previously took minutes now finish in "
            "seconds, even for the largest cohorts.\n"
            "No action is needed from your team — the improvements are "
            "already available to every program.\n"
        )
        v = scan(fixture)
        blocking = [x for x in v if x["severity"] == "block"]
        self.assertEqual(blocking, [], f"clean fixture blocked: {blocking}")


class CliContractTests(unittest.TestCase):
    def _run_main(self, content):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write(content)
            path = f.name
        self.addCleanup(lambda: Path(path).unlink(missing_ok=True))
        argv = ["ip_guard.py", "--input", path]
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(ip_guard.sys, "argv", argv), \
                contextlib.redirect_stdout(stdout), \
                contextlib.redirect_stderr(stderr):
            code = ip_guard.main()
        return code, stdout.getvalue()

    def test_clean_root_index_passes(self):
        root = (":rocket: *Medtrics Release — Monday, August 24, 2026*\n"
                "\n"
                "*Enhancements &amp; New Features*\n"
                f"1. M1-1054 · {DIRTY_TITLE}\n"
                "\n"
                "Details for each item are in this thread.\n")
        code, out = self._run_main(root)
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["ok"])

    def test_leaking_prose_fails_with_exit_2(self):
        code, out = self._run_main("We migrated the Django endpoint.")
        self.assertEqual(code, 2)
        result = json.loads(out)
        self.assertFalse(result["ok"])
        self.assertGreaterEqual(result["blocking_count"], 1)


if __name__ == "__main__":
    unittest.main()
