"""Tests for scripts/kb/kb_ledger.py — the deterministic half of the KB ledger
in Mem. Stdlib-only, hermetic (no Mem).

Pins: ledger note render/parse round-trip; non-ledger notes ignored; the
newest note wins for a ticket; collection names env > .env > default; the
release-archive scan keeps medtrics tickets only and drops MR-NNNN; todo /
pending skip done statuses (partial only without --retry-partial); writes
create vs update-with-version and keep 'Published as'; seed-legacy marks
drafted and matched tickets; kb2.csv import with loose column names; prior
matching ranks by overlap and never matches a ticket to itself.
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kb_fixtures  # noqa: F401  (puts scripts/kb on sys.path)

import kb_ledger as L


def ledger_note(ticket, status, title="How to Do It", generated="2026-10-20", nid="n1",
                version=3, published=""):
    rec = {"ticket": ticket, "status": status, "title": title, "generated": generated,
           "published_as": published}
    return {"id": nid, "version": version, "content": L.render_ledger_note(rec)}


def archive_note(date, product, tickets):
    return {"content": f"Release: {date} · {product} · #dream-team\n\n**Date:** {date}\n"
                       f"**Product:** {product}\n**Tickets:** {', '.join(tickets)}\n"}


class Notes(unittest.TestCase):
    def test_round_trip(self):
        rec = {"ticket": "M1-1520", "type": "feature", "status": "drafted",
               "title": "How to Land Schedules", "surface": "Curriculum > Setup > Migrate",
               "prior": "CX-1 How to X (catalogue, 0.50)"}
        text = L.render_ledger_note(rec)
        self.assertTrue(text.startswith("KB: M1-1520 · How to Land Schedules\n"))
        back = L.parse_ledger_note(text)
        for k, v in rec.items():
            self.assertEqual(back[k], v)
        self.assertEqual(back["published_as"], "")

    def test_foreign_notes_ignored(self):
        self.assertIsNone(L.parse_ledger_note("Release: 2026-10-01 · medtrics"))
        self.assertIsNone(L.parse_ledger_note({"content": ""}))
        idx = L.ledger_index([{"content": "Developer: X\n"}, ledger_note("M1-1", "drafted")])
        self.assertEqual(list(idx), ["M1-1"])

    def test_newest_wins(self):
        idx = L.ledger_index([ledger_note("M1-1", "failed", generated="2026-10-01", nid="a"),
                              ledger_note("M1-1", "drafted", generated="2026-10-08", nid="b")])
        self.assertEqual(idx["M1-1"]["status"], "drafted")
        self.assertEqual(idx["M1-1"]["note_id"], "b")

    def test_catalogue_round_trip(self):
        rec = {"id": "CX-780", "title": "How to Request Time Off", "module": "Benefits"}
        back = L.parse_catalogue_note(L.render_catalogue_note(rec))
        self.assertEqual((back["id"], back["title"], back["module"]),
                         ("CX-780", "How to Request Time Off", "Benefits"))

    def test_run_note_title(self):
        text = L.render_run_note({"run_id": "kb-1", "date": "2026-10-26", "totals": {"tokens": 5}})
        self.assertTrue(text.startswith("KB run: 2026-10-26 · kb-1"))


class Names(unittest.TestCase):
    def test_env_then_file_then_default(self):
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as fh:
            fh.write("MEM_KB_LEDGER_COLLECTION=From File\n")
        names = L.resolve_names(fh.name, environ={"MEM_KB_CATALOGUE_COLLECTION": "From Env"})
        self.assertEqual(names["ledger"]["name"], "From File")
        self.assertEqual(names["catalogue"]["name"], "From Env")
        self.assertEqual(names["queue"]["name"], "KB Queue")


class Released(unittest.TestCase):
    NOTES = [archive_note("2026-10-01", "medtrics", ["M1-1", "MR-6567"]),
             archive_note("2026-10-08", "oracle", ["ORC-5"]),
             archive_note("2026-10-15", "medtrics", ["M1-2", "M1-1"])]

    def test_medtrics_only_no_mr(self):
        self.assertEqual(L.released_tickets(self.NOTES),
                         [("M1-1", "2026-10-01"), ("M1-2", "2026-10-15")])

    def test_since(self):
        self.assertEqual([t for t, _ in L.released_tickets(self.NOTES, "2026-10-10")],
                         ["M1-1", "M1-2"])


class Todo(unittest.TestCase):
    def _run(self, *extra):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ledger.json"
            p.write_text(json.dumps({"notes": [ledger_note("M1-1", "drafted", nid="a"),
                                               ledger_note("M1-2", "partial", nid="b"),
                                               ledger_note("M1-3", "failed", nid="c")]}))
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                L.main(["todo", "--ids", "M1-1,M1-2,M1-3,M1-4", "--ledger-notes", str(p), *extra])
            return buf.getvalue().splitlines()[0]

    def test_default_skips_done_and_partial(self):
        self.assertEqual(self._run(), "M1-3,M1-4")

    def test_retry_partial(self):
        self.assertEqual(self._run("--retry-partial"), "M1-2,M1-3,M1-4")

    def test_force(self):
        self.assertEqual(self._run("--force"), "M1-1,M1-2,M1-3,M1-4")


class Writes(unittest.TestCase):
    def test_create_and_update(self):
        ledger = L.ledger_index([ledger_note("M1-1", "failed", nid="n9", version=7,
                                             published="CX-9")])
        w = L.ledger_writes([{"ticket": "M1-1", "status": "drafted", "title": "How to A"},
                             {"ticket": "M1-2", "status": "partial", "title": "How to B"}], ledger)
        self.assertEqual([x["action"] for x in w], ["update", "create"])
        self.assertEqual((w[0]["note_id"], w[0]["version"]), ("n9", 7))
        self.assertIn("**Published as:** CX-9", w[0]["content"])

    def test_done_ticket_not_rewritten_without_force(self):
        ledger = L.ledger_index([ledger_note("M1-1", "drafted")])
        self.assertEqual(L.ledger_writes([{"ticket": "M1-1", "status": "drafted"}], ledger), [])
        self.assertEqual(len(L.ledger_writes([{"ticket": "M1-1", "status": "drafted"}],
                                             ledger, force=True)), 1)


class Seeds(unittest.TestCase):
    def test_seed_legacy(self):
        tickets = [{"id": "M1-1", "type": "feature", "article": ""},
                   {"id": "M1-2", "type": "feature", "article": "CX-5 How to X"},
                   {"id": "M1-3", "type": "feature", "article": ""}]
        articles = [{"title": "Doing A", "provenance": ["Generated from M1-1 on the board."]}]
        recs = {r["ticket"]: r["status"] for r in L.seed_legacy_records(tickets, articles, "2026-10-26")}
        self.assertEqual(recs, {"M1-1": "legacy-draft", "M1-2": "matched"})

    def test_catalogue_csv_loose_columns(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as fh:
            fh.write("﻿Article ID,Article Title,Category,Link\n"
                     "CX-780,How to Request Time Off,Benefits,https://help/x\n"
                     ",No id,Benefits,\n")
        rows = L.catalogue_from_csv(fh.name)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["id"], rows[0]["module"]), ("CX-780", "Benefits"))


class Match(unittest.TestCase):
    def test_ranks_and_skips_self(self):
        issues = [{"identifier": "M1-1", "title": "Time off request attachments",
                   "module": "Benefits & Time Off"}]
        catalogue = [{"id": "CX-780", "title": "How to Request and Manage Time Off", "module": "Benefits"},
                     {"id": "CX-1", "title": "How to Build Evaluation Forms", "module": "Forms"}]
        ledger = {"M1-1": {"ticket": "M1-1", "title": "Time off request attachments"}}
        res = L.match(issues, catalogue, ledger)["M1-1"]
        self.assertEqual([r["id"] for r in res], ["CX-780"])
        self.assertIn("CX-780", L.prior_line(res))


if __name__ == "__main__":
    unittest.main()
