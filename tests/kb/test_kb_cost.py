"""Dollar-cost estimates: kb_usage prices Task usage totals at a fixed
input/output split, and the figure shows up in the end table, RUN-SUMMARY,
run.json and the Mem notes (kb_ledger) labelled as an estimate."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kb_fixtures  # noqa: F401

import kb_ledger as L
import kb_package as PK
import kb_usage as U


class Pricing(unittest.TestCase):
    def test_price_table(self):
        self.assertEqual(U.PRICE_PER_MTOK, {"haiku": (0.10, 0.50), "sonnet": (2.00, 10.00),
                                            "opus": (4.00, 20.00)})
        self.assertEqual(U.INPUT_SHARE, 0.85)

    def test_models_match_by_substring(self):
        self.assertEqual(U.price_for("claude-sonnet-5-5"), (2.00, 10.00))
        self.assertEqual(U.price_for("Haiku"), (0.10, 0.50))
        self.assertEqual(U.price_for("claude-opus-4"), (4.00, 20.00))
        self.assertIsNone(U.price_for("mystery"))
        self.assertIsNone(U.price_for(None))

    def test_row_cost_uses_the_85_15_split(self):
        # 1M sonnet tokens: 0.85M * $2 + 0.15M * $10 = $3.20
        self.assertAlmostEqual(U.row_cost({"model": "sonnet", "total_tokens": 1_000_000}), 3.20)
        self.assertAlmostEqual(U.row_cost({"model": "opus", "total_tokens": 100_000}), 0.64)
        self.assertIsNone(U.row_cost({"model": "mystery", "total_tokens": 5}))

    def test_total_is_none_when_any_call_is_unpriced(self):
        rows = [{"model": "haiku", "total_tokens": 1_000_000}]
        self.assertAlmostEqual(U.cost(rows), 0.16)
        self.assertIsNone(U.cost(rows + [{"model": "x", "total_tokens": 1}]))

    def test_money(self):
        self.assertEqual(U.money(0.04), "$0.04")
        self.assertEqual(U.money(None), "n/a")

    def test_per_ticket_cost_follows_the_token_apportioning(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "writer_a-01.md").write_text("=== TICKET M1-1 ===" + "a" * 300 + "=== TICKET M1-2 ===" + "b" * 100)
            groups = {"writer_groups": [{"group_id": "a-01", "tickets": ["M1-1", "M1-2"]}]}
            rows = [{"agent": "kb-writer", "scope": "a-01", "model": "sonnet", "total_tokens": 1_000_000},
                    {"agent": "kb-writer", "scope": "retry:M1-2", "model": "haiku", "total_tokens": 1_000_000}]
            per = U.apportion_cost(rows, groups, d)
            self.assertGreater(per["M1-1"], 0)
            self.assertAlmostEqual(sum(per.values()), 3.20 + 0.16, places=3)
            self.assertGreater(per["M1-2"], 0.16)

    GROUPS = {"writer_groups": [{"group_id": "a-01", "tickets": ["M1-1", "M1-2", "M1-3"]}]}
    ROWS = [{"agent": "kb-classifier", "scope": "classify", "model": "haiku", "total_tokens": 300_000},
            {"agent": "kb-capturer", "scope": "capture", "model": "haiku", "total_tokens": 900_000},
            {"agent": "kb-data-maker", "scope": "fill", "model": "sonnet", "total_tokens": 200_000},
            {"agent": "kb-writer", "scope": "a-01", "model": "sonnet", "total_tokens": 600_000},
            {"agent": "kb-writer", "scope": "retry:M1-3", "model": "sonnet", "total_tokens": 50_000}]

    @staticmethod
    def calls():
        v = lambda vid, *t: {"visit_id": vid, "tickets": list(t)}
        return {"visits": [v("v01", "M1-1"), v("v02", "M1-1", "M1-2"), v("v03", "M1-1"), v("v04", "M1-2")]}

    def test_capture_is_split_by_each_tickets_share_of_the_capture_visits(self):
        with tempfile.TemporaryDirectory() as d:
            shares = U.capture_shares(self.calls(), ["M1-1", "M1-2", "M1-3"])
            self.assertEqual(shares, {"M1-1": 3 / 5, "M1-2": 2 / 5})            # M1-3 was never captured
            per = U.apportion([self.ROWS[1]], self.GROUPS, d, self.calls(), {})
            self.assertEqual(per, {"M1-1": 540_000, "M1-2": 360_000})
            # without calls.json: evenly over the placed tickets
            per = U.apportion([self.ROWS[1]], self.GROUPS, d, {}, {})
            self.assertEqual(per, {"M1-1": 300_000, "M1-2": 300_000, "M1-3": 300_000})

    def test_form_fill_goes_to_the_tickets_of_the_data_plan(self):
        plan = {"tickets": {"M1-2": {"records": [{"id": "r1"}, {"id": "r2"}]}, "M1-3": {"records": [{"id": "r1"}]}}}
        with tempfile.TemporaryDirectory() as d:
            per = U.apportion([self.ROWS[2]], self.GROUPS, d, {}, plan)
            self.assertEqual(per, {"M1-2": round(200_000 * 2 / 3), "M1-3": round(200_000 / 3)})
            self.assertEqual(U.apportion([self.ROWS[2]], self.GROUPS, d, {}, {}), {t: round(200_000 / 3) for t in ("M1-1", "M1-2", "M1-3")})

    def test_per_ticket_sums_equal_the_run_total(self):
        plan = {"tickets": {"M1-2": {"records": [{"id": "r1"}]}}}
        with tempfile.TemporaryDirectory() as d:
            per = U.apportion_cost(self.ROWS, self.GROUPS, d, self.calls(), plan)
            self.assertAlmostEqual(sum(per.values()), U.cost(self.ROWS), places=3)
            tok = U.apportion(self.ROWS, self.GROUPS, d, self.calls(), plan)
            self.assertAlmostEqual(sum(tok.values()), sum(r["total_tokens"] for r in self.ROWS), delta=len(tok))
            self.assertGreater(per["M1-2"], per["M1-3"] - 1)                  # M1-2 carries the whole form fill
            # calls.json / data_plan.json in the run folder are picked up by report
            Path(d, "shots").mkdir()
            Path(d, "shots", "calls.json").write_text(json.dumps(self.calls()))
            Path(d, "data_plan.json").write_text(json.dumps(plan))
            Path(d, "groups.json").write_text(json.dumps(self.GROUPS))
            for r in self.ROWS:
                U.main(["add", "--run-dir", d, "--agent", r["agent"], "--scope", r["scope"], "--model", r["model"],
                        "--tokens", str(r["total_tokens"])])
            U.main(["report", "--run-dir", d, "--groups", str(Path(d, "groups.json"))])
            rep = json.loads(Path(d, "usage_report.json").read_text())
            self.assertAlmostEqual(sum(rep["per_ticket_cost"].values()), rep["est_cost_usd"], places=3)
            self.assertEqual(set(rep["per_ticket_cost"]), {"M1-1", "M1-2", "M1-3"})

    def test_a_ticket_with_an_unpriced_model_is_n_a_not_a_partial_number(self):
        rows = [{"agent": "kb-writer", "scope": "a-01", "model": "sonnet", "total_tokens": 600_000},
                {"agent": "kb-data-maker", "scope": "fill", "model": "mystery-1", "total_tokens": 200_000}]
        plan = {"tickets": {"M1-2": {"records": [{"id": "r1"}]}}}
        with tempfile.TemporaryDirectory() as d:
            per = U.apportion_cost(rows, self.GROUPS, d, {}, plan)
            self.assertIsNone(per["M1-2"])                                      # n/a, though its writer share is priced
            self.assertGreater(per["M1-1"], 0)
            self.assertEqual(U.money(per["M1-2"]), "n/a")
            self.assertIsNone(U.cost(rows))
            # the run's table shows n/a for that ticket, the others keep their number
            t = PK.ticket_table(["M1-1", "M1-2"], [{"ticket": t, "status": "drafted", "files": [], "reason": ""}
                                                    for t in ("M1-1", "M1-2")], {"skipped": []}, d, costs=per)
            self.assertEqual((t[0]["cost"] != "n/a", t[1]["cost"]), (True, "n/a"))

    def test_report_writes_cost_everywhere(self):
        with tempfile.TemporaryDirectory() as d:
            U.main(["add", "--run-dir", d, "--agent", "kb-writer", "--scope", "retry:M1-1",
                    "--model", "sonnet", "--tokens", "100000"])
            U.main(["report", "--run-dir", d])
            rep = json.loads(Path(d, "usage_report.json").read_text())
            self.assertAlmostEqual(rep["est_cost_usd"], 0.32)
            self.assertAlmostEqual(rep["rows"][0]["est_cost_usd"], 0.32)
            self.assertAlmostEqual(rep["per_ticket_cost"]["M1-1"], 0.32)
            self.assertIn("main-chat tokens not included", rep["cost_note"])
            self.assertEqual(rep["unpriced_models"], [])


class Table(unittest.TestCase):
    ROWS = [{"ticket": "M1-1", "status": "drafted", "files": ["How to A.docx"], "reason": ""}]

    def test_cost_column_and_total_row(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "M1-1").mkdir()
            Path(d, "M1-1", "How to A.docx").write_text("x")
            t = PK.ticket_table(["M1-1", "M1-2"], self.ROWS, {"skipped": []}, d, costs={"M1-1": 0.04})
            self.assertEqual(t[0]["cost"], "$0.04")
            self.assertEqual(t[1]["cost"], "—")                   # not drafted
            md = PK.table_md(t, 0.31)
            self.assertIn("| Ticket | Result | Files | Details | Est. cost |", md[0])
            self.assertEqual(md[2], "| M1-1 | done | M1-1/How to A (.docx) | all screenshots | $0.04 |")
            self.assertEqual(md[-1], "| **Run total** |  |  | all agent calls | $0.31 |")

    def test_no_usage_shows_n_a_and_no_costs_means_no_column(self):
        with tempfile.TemporaryDirectory() as d:
            t = PK.ticket_table(["M1-1"], self.ROWS, {"skipped": []}, d, costs={})
            self.assertEqual(t[0]["cost"], "n/a")
            self.assertIn("n/a |", PK.table_md(t, None)[-1])
            plain = PK.ticket_table(["M1-1"], self.ROWS, {"skipped": []}, d)
            self.assertNotIn("Est. cost", PK.table_md(plain)[0])

    def test_summary_md_states_the_estimate(self):
        run = {"run_id": "r", "date": "d", "totals": dict(drafted=1, partial=0, failed=0, skipped=0),
               "tickets": [{"ticket": "M1-1", "done": "done", "files": "—", "detail": "x", "cost": "$0.04"}],
               "records": [], "left_by_expiry": [], "cost": {"est_usd": 0.31}}
        usage = {"total_tokens": 1000, "est_cost_usd": 0.31,
                 "rows": [{"agent": "kb-writer", "scope": "a", "model": "sonnet", "total_tokens": 1000,
                           "est_cost_usd": 0.31}]}
        md = PK.summary_md(run, [], {}, [], "", usage)
        self.assertIn("| **Run total** |  |  | all agent calls | $0.31 |", md)
        self.assertIn("est. cost $0.31 (estimate; main-chat tokens not included)", md)
        self.assertIn("kb-writer · a · sonnet · 1,000 · $0.31", md)
        self.assertIn("85% input / 15% output", md)


class Ledger(unittest.TestCase):
    def test_ledger_note_carries_the_cost_line_and_round_trips(self):
        rec = {"ticket": "M1-1", "title": "How to A", "status": "drafted",
               "cost": "$0.04 (estimate; main-chat tokens not included)"}
        text = L.render_ledger_note(rec)
        self.assertIn("**Est. cost:** $0.04 (estimate; main-chat tokens not included)", text)
        self.assertEqual(L.parse_ledger_note(text)["cost"], rec["cost"])

    def test_run_note_has_total_and_per_call_cost(self):
        text = L.render_run_note({"run_id": "kb-1", "date": "2026-10-29", "totals": {"tokens": 5, "est_cost_usd": 0.31},
                                  "usage": [{"agent": "kb-writer", "scope": "a", "model": "sonnet",
                                             "total_tokens": 1000, "est_cost_usd": 0.0032}]})
        self.assertIn("**Est. cost:** $0.31 (estimate; main-chat tokens not included)", text)
        self.assertIn("1,000 tokens · $0.00", text)
        none = L.render_run_note({"run_id": "kb-1", "date": "2026-10-29", "totals": {}})
        self.assertIn("**Est. cost:** n/a (estimate", none)


if __name__ == "__main__":
    unittest.main()
