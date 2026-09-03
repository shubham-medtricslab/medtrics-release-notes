"""Tests for scripts/assemble_oracle_root.py.

Stdlib-only (unittest), hermetic. The assembler is the mechanism that makes
the guarded client text land in the root VERBATIM BY CONSTRUCTION — these
tests pin that property, the marker switch, whole-line internal truncation
with the "…and N more" line, the dangling-header rule, the 2,500-char root
budget, determinism, and the hard error when the client file alone makes
the root unfit.
"""

import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "assemble_oracle_root", SCRIPTS_DIR / "assemble_oracle_root.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


assembler = _load_module()

DATE = "Tuesday, August 26, 2026"

CLIENT_TEXT = (
    "This week's Oracle update improves scheduling and reporting.\n"
    "\n"
    "Draft evaluations now save automatically, and report exports finish "
    "in seconds rather than minutes. No action is needed on your side."
)

INTERNAL_LINES = [
    "*Bug fixes*",
    "1. ORC-101 · Autosave drafts on evaluations · <@U0AAAAAAA> · "
    "<https://github.com/medtrics/medtrics-oracle/pull/42|PR#42>",
    "2. ORC-115 · Export timeout on large cohorts · <@U0BBBBBBB> · "
    "<https://github.com/medtrics/medtrics-oracle/pull/43|PR#43>",
    "",
    "*Features*",
    "3. ORC-120 · Rotation scheduling presets · <@U0CCCCCCC> · "
    "<https://github.com/medtrics/medtrics-oracle/pull/44|PR#44>",
]


class AssembleHarness(unittest.TestCase):
    def _write(self, content):
        f = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
        f.write(content)
        f.close()
        self.addCleanup(lambda: Path(f.name).unlink(missing_ok=True))
        return f.name

    def _tmp_path(self, suffix):
        d = tempfile.mkdtemp()
        return str(Path(d) / f"out{suffix}")

    def run_main(self, client, internal_lines, client_continues=False,
                 date=DATE):
        client_path = self._write(client)
        internal_path = self._write("\n".join(internal_lines) + "\n")
        out_path = self._tmp_path(".root.txt")
        overflow_path = self._tmp_path(".overflow.txt")
        argv = ["assemble_oracle_root.py",
                "--client-file", client_path,
                "--internal-file", internal_path,
                "--date", date,
                "--output", out_path,
                "--overflow-output", overflow_path]
        if client_continues:
            argv.append("--client-continues")
        stderr = io.StringIO()
        with mock.patch.object(assembler.sys, "argv", argv), \
                contextlib.redirect_stderr(stderr):
            code = assembler.main()
        root = Path(out_path).read_text() if Path(out_path).exists() else None
        overflow = (Path(overflow_path).read_text()
                    if Path(overflow_path).exists() else None)
        return code, root, overflow, stderr.getvalue()


class VerbatimAndLayoutTests(AssembleHarness):
    def test_client_text_appears_byte_verbatim_in_root(self):
        code, root, overflow, _ = self.run_main(CLIENT_TEXT, INTERNAL_LINES)
        self.assertEqual(code, 0)
        self.assertIn(CLIENT_TEXT, root)

    def test_root_layout_lines_in_order(self):
        code, root, _, _ = self.run_main(CLIENT_TEXT, INTERNAL_LINES)
        self.assertEqual(code, 0)
        lines = root.splitlines()
        self.assertEqual(lines[0],
                         f":rocket: *Medtrics Oracle Release — {DATE}*")
        self.assertEqual(lines[1], "")
        self.assertEqual(lines[2],
                         ":clipboard: *Client communication — safe to forward*")
        self.assertIn(":lock: *Internal reference — do not forward*", lines)
        self.assertEqual(lines[-1], "Details for each item are in this thread.")
        # Marker directly follows the client block.
        client_last = CLIENT_TEXT.splitlines()[-1]
        idx = lines.index(client_last)
        self.assertEqual(lines[idx + 1], assembler.MARKER_DEFAULT)

    def test_marker_switches_with_client_continues(self):
        code, root, _, _ = self.run_main(CLIENT_TEXT, INTERNAL_LINES,
                                         client_continues=True)
        self.assertEqual(code, 0)
        self.assertIn(assembler.MARKER_CONTINUES, root)
        self.assertNotIn(assembler.MARKER_DEFAULT, root)


class OverflowTests(AssembleHarness):
    def _big_internal(self, n_entries, header="*Bug fixes*"):
        lines = [header]
        for i in range(1, n_entries + 1):
            lines.append(
                f"{i}. ORC-{100 + i} · A ticket title long enough to eat "
                f"root budget quickly enough · <@U0AAAAAAA> · "
                f"<https://github.com/medtrics/medtrics-oracle/pull/{i}|PR#{i}>")
        return lines

    def test_all_fits_no_overflow_file_no_more_line(self):
        code, root, overflow, _ = self.run_main(CLIENT_TEXT, INTERNAL_LINES)
        self.assertEqual(code, 0)
        self.assertIsNone(overflow, "overflow file must only exist on overflow")
        self.assertNotIn("more — index continues in the thread", root)
        for line in INTERNAL_LINES:
            self.assertIn(line, root)

    def test_overflow_whole_lines_correct_n_and_dropped_lines_in_file(self):
        internal = self._big_internal(30)
        code, root, overflow, _ = self.run_main(CLIENT_TEXT, internal)
        self.assertEqual(code, 0)
        self.assertLessEqual(len(root), assembler.ROOT_CAP)
        self.assertIsNotNone(overflow)
        overflow_lines = overflow.splitlines()
        root_lines = root.splitlines()
        # Whole-line truncation: every internal entry is either fully in the
        # root or fully in the overflow — never split, never lost.
        kept = [ln for ln in internal if ln in root_lines]
        dropped = [ln for ln in internal if ln.strip() and ln not in root_lines]
        self.assertTrue(kept, "expected some entries kept in the root")
        self.assertTrue(dropped, "fixture must actually overflow")
        for ln in dropped:
            self.assertIn(ln, overflow_lines)
        n_dropped_entries = sum(
            1 for ln in dropped if not assembler.is_section_header(ln))
        self.assertIn(
            f"…and {n_dropped_entries} more — index continues in the thread",
            root)

    def test_dangling_header_moves_to_overflow_with_its_section(self):
        # Sized (12 section-A entries) so section B's *Features* header is
        # the last line that would fit but none of its entries do — the
        # header must move to the overflow, AHEAD of its entries, and never
        # dangle in the root.
        section_a = self._big_internal(12, header="*Bug fixes*")
        section_b = ["", "*Features*"] + [
            f"{i}. ORC-{200 + i} · Another very long feature title used to "
            f"push this section past the remaining budget entirely "
            f"· <@U0DDDDDDD> · "
            f"<https://github.com/medtrics/medtrics-oracle/pull/{200 + i}"
            f"|PR#{200 + i}>"
            for i in range(1, 12)
        ]
        code, root, overflow, _ = self.run_main(CLIENT_TEXT,
                                                section_a + section_b)
        self.assertEqual(code, 0)
        self.assertLessEqual(len(root), assembler.ROOT_CAP)
        self.assertIsNotNone(overflow)
        root_lines = root.splitlines()
        overflow_lines = overflow.splitlines()
        # The orphaned header never dangles in the root...
        self.assertNotIn("*Features*", root_lines)
        # ...and lands in the overflow FIRST, ahead of its entries.
        self.assertEqual(overflow_lines[0], "*Features*")
        entries_after = [ln for ln in overflow_lines[1:] if ln.strip()]
        self.assertTrue(entries_after)
        # All of section A survived in the root (the cut was exactly at the
        # section boundary).
        for ln in section_a:
            self.assertIn(ln, root_lines)

    def test_root_within_budget_on_big_fixture(self):
        internal = self._big_internal(60)
        code, root, _, _ = self.run_main(CLIENT_TEXT, internal)
        self.assertEqual(code, 0)
        self.assertLessEqual(len(root), 2500)

    def test_client_file_too_big_exits_1(self):
        huge_client = "All of this client copy is one enormous block. " * 80
        code, _, _, stderr = self.run_main(huge_client, INTERNAL_LINES)
        self.assertEqual(code, 1)
        self.assertIn("ERROR", stderr)


def _load_slack_lint():
    spec = importlib.util.spec_from_file_location(
        "slack_lint", SCRIPTS_DIR / "slack_lint.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


slack_lint = _load_slack_lint()


class RootLintsCleanTests(AssembleHarness):
    """Regression for C1: the assembled root must pass slack_lint --kind root.

    The internal body begins with a *…* section header; slack_lint's
    section_header_blank rule requires a blank line before every header line.
    Before the fix, build_root placed that header on the line directly after
    ':lock: *Internal reference — do not forward*', so every Oracle root
    failed its own mandatory lint gate and could never post.
    """

    def _assert_root_lints_clean(self, root):
        violations = slack_lint.lint(root, "root")
        self.assertEqual(
            violations, [],
            "assembled Oracle root must lint clean as a root message; got: "
            + repr(violations))

    def test_simple_root_lints_clean(self):
        code, root, _, _ = self.run_main(CLIENT_TEXT, INTERNAL_LINES)
        self.assertEqual(code, 0)
        self._assert_root_lints_clean(root)

    def test_client_continues_root_lints_clean(self):
        code, root, _, _ = self.run_main(CLIENT_TEXT, INTERNAL_LINES,
                                         client_continues=True)
        self.assertEqual(code, 0)
        self._assert_root_lints_clean(root)

    def test_overflowing_root_lints_clean(self):
        internal = ["*Bug fixes*"] + [
            f"{i}. ORC-{100 + i} · A ticket title long enough to eat root "
            f"budget quickly enough · <@U0AAAAAAA> · "
            f"<https://github.com/medtrics/medtrics-oracle/pull/{i}|PR#{i}>"
            for i in range(1, 31)
        ]
        code, root, overflow, _ = self.run_main(CLIENT_TEXT, internal)
        self.assertEqual(code, 0)
        self.assertIsNotNone(overflow, "fixture must actually overflow")
        self._assert_root_lints_clean(root)


class DeterminismTests(AssembleHarness):
    def test_same_inputs_identical_output_bytes(self):
        internal = ["*Bug fixes*"] + [
            f"{i}. ORC-{i} · A repeated title for determinism checking "
            f"· <@U0AAAAAAA> · "
            f"<https://github.com/medtrics/medtrics-oracle/pull/{i}|PR#{i}>"
            for i in range(1, 40)
        ]
        code1, root1, overflow1, _ = self.run_main(CLIENT_TEXT, internal)
        code2, root2, overflow2, _ = self.run_main(CLIENT_TEXT, internal)
        self.assertEqual(code1, 0)
        self.assertEqual(code2, 0)
        self.assertEqual(root1.encode(), root2.encode())
        self.assertEqual(overflow1, overflow2)


if __name__ == "__main__":
    unittest.main()
