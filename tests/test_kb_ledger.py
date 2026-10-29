"""Tests for scripts/kb/kb_ledger.py — the deterministic half of the KB ledger
in Mem. Stdlib-only, hermetic (no Mem).

Pins: ledger note render/parse round-trip; non-ledger notes ignored; the
newest note wins for a ticket; collection names env > .env > default; the
release-archive scan keeps medtrics tickets only and drops MR-NNNN; todo /
pending skip done statuses (partial only without --retry-partial); writes
create vs update-with-version and keep 'Published as'; seed-legacy marks
drafted and matched tickets; kb2.csv import with loose column names; prior
matching ranks by overlap and never matches a ticket to itself; the KB queue —
queue note round-trip, enqueue (features only, no MR-*, ledger-done skipped,
already-pending drafted but not re-queued, drained-but-failed re-queued,
client title/summary with fallback), drain (pending only, carries version),
queue-summaries read back by kb_inputs, and pending unioning the queue.
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

import kb_inputs
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
        self.assertEqual(names["demo"]["name"], "KB Demo Data")


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


MEM_BODY = ("KB: M1-1596 · How to Add a Case Log Question\n\n"
            "**Ticket:** M1-1596\\\n**Optimus type:** feature\\\n**Status:** drafted\\\n"
            "**Article title:** How to Add a Case Log Question\\\n**Generated:** 2026-10-28\\\n"
            "**Run:** kb-20261028-1200\\\n**Prior article:** M1-1596 How to Add a Case Log Question (ledger, 1.00)\\\n"
            "**Published as:** —\\\n")


class MemHardBreaks(unittest.TestCase):
    """Mem hands notes back with a trailing backslash on each line."""

    def test_values_are_clean_and_dates_parse(self):
        rec = L.parse_ledger_note({"id": "n9", "version": 1, "content": MEM_BODY})
        self.assertEqual(rec["ticket"], "M1-1596")
        self.assertEqual(rec["status"], "drafted")
        self.assertEqual(rec["generated"], "2026-10-28")
        self.assertEqual(rec["published_as"], "")
        self.assertFalse(any(v.endswith("\\") for v in rec.values() if isinstance(v, str)))

    def test_index_key_is_the_plain_id(self):
        idx = L.ledger_index([{"id": "n9", "version": 1, "content": MEM_BODY}])
        self.assertEqual(list(idx), ["M1-1596"])

    def test_catalogue_and_queue_values_clean_too(self):
        q = L.parse_queue_note("KB queue: M1-9 · T\n\n**Ticket:** M1-9\\\n**Status:** pending\\\n"
                               "**Queued:** 2026-10-20  \\\n")
        self.assertEqual((q["ticket"], q["status"], q["queued"]), ("M1-9", "pending", "2026-10-20"))

    def test_ticket_never_its_own_prior(self):
        ledger = L.ledger_index([{"id": "n9", "version": 1, "content": MEM_BODY}])
        issues = [{"identifier": "M1-1596\\", "title": "How to Add a Case Log Question",
                   "module": "Cases"}]
        catalogue = [{"id": "CX-1", "title": "How to Add a Case Log Question", "module": "Cases"}]
        res = L.match(issues, catalogue, ledger)["M1-1596\\"]
        self.assertEqual([r["id"] for r in res], ["CX-1"])
        res = L.match([{"identifier": "m1-1596", "title": "How to Add a Case Log Question"}],
                      [], ledger)["m1-1596"]
        self.assertEqual(res, [])


def queue_note(ticket, status="pending", queued="2026-10-20", nid="q1", version=2, **kw):
    rec = {"ticket": ticket, "status": status, "source": "release-slack",
           "released": "2026-10-20", "title": f"Title {ticket}", "queued": queued, **kw}
    return {"id": nid, "version": version, "content": L.render_queue_note(rec)}


def _cli(*argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = L.main(list(argv))
    lines = buf.getvalue().splitlines()
    return code, lines[0], json.loads(lines[lines.index("---KB_LEDGER_RESULT---") + 1])


ISSUES = [{"identifier": "M1-1", "title": "Raw one", "task_type": "feature"},
          {"identifier": "M1-2", "title": "Raw two", "task_type": "critical_feature"},
          {"identifier": "M1-3", "title": "A bug", "task_type": "bug"},
          {"identifier": "MR-6567", "title": "Single MR", "task_type": "feature"},
          {"identifier": "M1-4", "title": "Done already", "task_type": "feature"},
          {"identifier": "M1-5", "title": "Pending already", "task_type": "feature"},
          {"identifier": "M1-6", "title": "Failed before", "task_type": "feature"}]
SUMMARIES = ("IDENTIFIER: M1-1\nTITLE: Summary one\nCLIENT_TITLE: Client one\n"
             "EXEC_SUMMARY: Admins can now do one.\n---\nIDENTIFIER: M1-2\nTITLE: Summary two\n")


class Queue(unittest.TestCase):
    def test_round_trip(self):
        rec = {"ticket": "M1-1520", "status": "pending", "source": "release-slack",
               "released": "2026-10-27", "root_ts": "1761571234.000100",
               "title": "Migrate schedules", "client_title": "Land schedules unpublished",
               "client_summary": "Admins can now\nland them.", "queued": "2026-10-27"}
        text = L.render_queue_note(rec)
        self.assertTrue(text.startswith("KB queue: M1-1520 · Land schedules unpublished\n"))
        self.assertIn("**Drained by:** —", text)
        back = L.parse_queue_note({"id": "x", "version": 4, "content": text})
        self.assertEqual(back["client_summary"], "Admins can now land them.")
        for k in ("ticket", "status", "source", "released", "root_ts", "title", "client_title"):
            self.assertEqual(back[k], rec[k])
        self.assertEqual((back["note_id"], back["version"], back["drained"]), ("x", 4, ""))

    def test_foreign_notes_ignored(self):
        self.assertIsNone(L.parse_queue_note(ledger_note("M1-1", "drafted")))
        self.assertIsNone(L.parse_ledger_note(queue_note("M1-1")))
        self.assertIsNone(L.parse_queue_note("Release: 2026-10-01 · medtrics"))
        self.assertEqual(list(L.queue_index([ledger_note("M1-9", "drafted"), queue_note("m1-1")])),
                         ["M1-1"])

    def test_index_prefers_pending_then_newest(self):
        idx = L.queue_index([queue_note("M1-1", "pending", "2026-10-01", nid="a"),
                             queue_note("M1-1", "drained", "2026-10-09", nid="b"),
                             queue_note("M1-2", "drained", "2026-10-01", nid="c"),
                             queue_note("M1-2", "drained", "2026-10-09", nid="d")])
        self.assertEqual((idx["M1-1"]["note_id"], idx["M1-2"]["note_id"]), ("a", "d"))


class Enqueue(unittest.TestCase):
    LEDGER = L.ledger_index([ledger_note("M1-4", "drafted", nid="l4"),
                             ledger_note("M1-6", "failed", nid="l6")])
    QUEUE = L.queue_index([queue_note("M1-5", "pending", nid="q5"),
                           queue_note("M1-6", "drained", nid="q6")])

    def _run(self):
        import release_archive as RA
        return L.enqueue_records(ISSUES, RA.parse_summaries(SUMMARIES), self.LEDGER,
                                 self.QUEUE, "release-slack", "2026-10-26", "17.1", "2026-10-27")

    def test_rules(self):
        recs, ids = self._run()
        self.assertEqual(ids, ["M1-1", "M1-2", "M1-5", "M1-6"])
        self.assertEqual([r["ticket"] for r in recs], ["M1-1", "M1-2", "M1-6"])
        self.assertTrue(all(r["status"] == "pending" and r["queued"] == "2026-10-27"
                            and r["released"] == "2026-10-26" and r["root_ts"] == "17.1"
                            for r in recs))

    def test_client_fields_and_fallback(self):
        recs = {r["ticket"]: r for r in self._run()[0]}
        self.assertEqual((recs["M1-1"]["client_title"], recs["M1-1"]["client_summary"]),
                         ("Client one", "Admins can now do one."))
        self.assertEqual((recs["M1-2"]["title"], recs["M1-2"]["client_title"],
                          recs["M1-2"]["client_summary"]), ("Summary two", "Summary two", ""))
        self.assertEqual((recs["M1-6"]["title"], recs["M1-6"]["client_title"]),
                         ("Failed before", "Failed before"))

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "data.json").write_text(json.dumps({"issues": ISSUES}))
            (d / "ledger.json").write_text(json.dumps({"notes": [ledger_note("M1-4", "drafted")]}))
            (d / "queue.json").write_text(json.dumps([queue_note("M1-5")]))
            code, line, res = _cli("enqueue", "--data", str(d / "data.json"),
                                   "--summaries", str(d / "missing.txt"),
                                   "--source", "release-slack", "--date", "2026-10-26",
                                   "--ledger-notes", str(d / "ledger.json"),
                                   "--queue-notes", str(d / "queue.json"),
                                   "--out", str(d / "w.json"))
            self.assertEqual(code, 0)
            self.assertEqual(line, "M1-1,M1-2,M1-5,M1-6")
            self.assertEqual((res["ok"], res["queued"], res["already_queued"], res["skipped"]),
                             (True, 3, 1, 3))
            w = json.loads((d / "w.json").read_text())
            self.assertEqual({x["action"] for x in w} | {x["collection"] for x in w},
                             {"create", "queue"})
            self.assertTrue(w[0]["title"].startswith("KB queue: M1-1 · Raw one"))
            self.assertEqual(w[0]["content"].splitlines()[0], w[0]["title"])
            code, _, res = _cli("enqueue", "--data", str(d / "data.json"), "--source", "x",
                                "--date", "26-10-2026", "--out", str(d / "w2.json"))
            self.assertEqual((code, res["ok"]), (1, False))


class Drain(unittest.TestCase):
    def test_only_pending_with_version(self):
        notes = [queue_note("M1-1", "pending", nid="a", version=5),
                 queue_note("M1-2", "drained", nid="b"),
                 queue_note("M1-3", "pending", nid="c")]
        with tempfile.TemporaryDirectory() as d:
            q, out = Path(d) / "q.json", Path(d) / "w.json"
            q.write_text(json.dumps({"notes": notes}))
            code, _, res = _cli("drain", "--tickets", "M1-1,M1-2,M1-9", "--queue-notes", str(q),
                                "--run-id", "kb-20261027-0930", "--date", "2026-10-27",
                                "--out", str(out))
            w = json.loads(out.read_text())
        self.assertEqual((code, res["drained"]), (0, 1))
        self.assertEqual((w[0]["action"], w[0]["collection"], w[0]["note_id"], w[0]["version"]),
                         ("update", "queue", "a", 5))
        back = L.parse_queue_note(w[0]["content"])
        self.assertEqual((back["status"], back["drained_by"], back["drained"], back["title"]),
                         ("drained", "kb-20261027-0930", "2026-10-27", "Title M1-1"))


class QueueSummaries(unittest.TestCase):
    def test_read_back_by_kb_inputs(self):
        notes = [queue_note("M1-1", client_title="Client one", client_summary="Admins can now do one."),
                 queue_note("M1-2", nid="q2"),
                 queue_note("M1-3", "drained", nid="q3")]
        with tempfile.TemporaryDirectory() as d:
            q, out = Path(d) / "q.json", Path(d) / "sums.txt"
            q.write_text(json.dumps(notes))
            code, _, res = _cli("queue-summaries", "--queue-notes", str(q), "--out", str(out))
            sums = kb_inputs.load_summaries(str(out))
            self.assertEqual((code, res["tickets"], sorted(sums)), (0, 2, ["M1-1", "M1-2"]))
            self.assertEqual((sums["M1-1"]["CLIENT_TITLE"], sums["M1-1"]["EXEC_SUMMARY"]),
                             ("Client one", "Admins can now do one."))
            _cli("queue-summaries", "--queue-notes", str(q), "--tickets", "M1-2", "--out", str(out))
            self.assertEqual(list(kb_inputs.load_summaries(str(out))), ["M1-2"])


class PendingQueue(unittest.TestCase):
    def test_union_dedup(self):
        archive = [archive_note("2026-10-01", "medtrics", ["M1-1", "M1-2", "M1-3"])]
        ledger = [ledger_note("M1-2", "drafted", nid="l2"), ledger_note("M1-5", "drafted", nid="l5")]
        queue = [queue_note("M1-4", nid="a"), queue_note("M1-1", nid="b"),
                 queue_note("M1-5", nid="c"), queue_note("M1-6", "drained", nid="d")]
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for name, notes in (("a", archive), ("l", ledger), ("q", queue)):
                (d / f"{name}.json").write_text(json.dumps(notes))
            code, line, res = _cli("pending", "--archive-notes", str(d / "a.json"),
                                   "--ledger-notes", str(d / "l.json"),
                                   "--queue-notes", str(d / "q.json"))
            _, line_no_q, _ = _cli("pending", "--archive-notes", str(d / "a.json"),
                                   "--ledger-notes", str(d / "l.json"))
        self.assertEqual((code, line, res["tickets"]), (0, "M1-1,M1-3,M1-4", 3))
        self.assertEqual(line_no_q, "M1-1,M1-3")


class QueueReport(unittest.TestCase):
    def test_rows_for_every_requested_id(self):
        data = {"issues": [{"identifier": "M1-1", "task_type": "feature"},
                           {"identifier": "M1-2", "task_type": "feature"},
                           {"identifier": "M1-3", "task_type": "feature"}],
                "dropped": [{"identifier": "M1-4", "task_type": "bug"}]}
        ledger = {"M1-2": {"status": "drafted"}}
        queue = {"M1-3": {"status": "pending"}}
        recs = [{"ticket": "M1-1"}]
        rows = {r["ticket"]: r["result"] for r in
                L.enqueue_report(["M1-1", "M1-2", "M1-3", "M1-4", "M1-5"], data, ledger, queue, recs)}
        self.assertEqual(rows, {"M1-1": "queued", "M1-2": "not queued", "M1-3": "already queued",
                                "M1-4": "not queued", "M1-5": "not queued"})


if __name__ == "__main__":
    unittest.main()
