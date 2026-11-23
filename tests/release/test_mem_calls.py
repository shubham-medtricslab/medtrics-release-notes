"""Tests for the 0.25.0 Mem-call cuts. Stdlib-only, hermetic (no Mem, no network).

Pins: mem_ids — saved ids used with no lookup, the .env PR-overrides id wins,
missing names resolved from ONE list_collections dump with the archive's
tie-break (exact case, then oldest created_at, then ambiguous), ids saved,
forget; pod_map — the pod log read from its own collection (--log-notes),
old log notes in the pod map still dedupe, pod-log notes waiting in the
outbox dedupe, `log` writes to the log collection, `old-logs`; release_archive
— outbox add/list/done (dedupe, keys, only outbox files deleted), backfill
accept/reject (chat preamble + outer fence stripped, bullet-like lines in the
posted text never items); dev_directory — the 24h cache get/put/apply/clear.
"""

import contextlib
import datetime as dt
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "release"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mi = _load("mem_ids")
pm = _load("pod_map")
ra = _load("release_archive")
dd = _load("dev_directory")


class HomeCase(unittest.TestCase):
    """Each test gets its own run folder (KB_HOME) and a blank .env."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name).resolve()
        self.env = self.home / ".env"
        self.env.write_text("")
        patcher = mock.patch.dict(os.environ, {"KB_HOME": str(self.home)})
        patcher.start()
        for k in ("MEM_PR_OVERRIDES_COLLECTION_ID", "MEM_POD_LOG_COLLECTION",
                  "MEM_RELEASE_ARCHIVE_COLLECTION", "MEM_POD_MAP_COLLECTION"):
            os.environ.pop(k, None)
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def write(self, name, data):
        p = self.home / name
        p.write_text(data if isinstance(data, str) else json.dumps(data))
        return p


def _quiet(fn, *a, **k):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = fn(*a, **k)
    return code, out.getvalue(), err.getvalue()


# --------------------------------------------------------------------------- #
class MemIdsTest(HomeCase):

    def test_saved_ids_need_no_lookup(self):
        mi.kb_paths.merge(self.home, {"mem": {"release_archive": "A1", "pod_map": "P1"}})
        out = mi.need(self.home, ["release_archive", "pod_map", "pod_log"], str(self.env))
        self.assertEqual(out["ids"], {"release_archive": "A1", "pod_map": "P1"})
        self.assertEqual([m["key"] for m in out["missing"]], ["pod_log"])
        self.assertEqual(out["missing"][0]["name"], "Pod Notify Log")

    def test_env_pr_overrides_id_wins(self):
        mi.kb_paths.merge(self.home, {"mem": {"pr_overrides": "FROM-CONFIG"}})
        self.env.write_text("MEM_PR_OVERRIDES_COLLECTION_ID=FROM-ENV\n")
        out = mi.need(self.home, ["pr_overrides"], str(self.env))
        self.assertEqual(out["ids"]["pr_overrides"], "FROM-ENV")

    def test_unknown_key(self):
        with self.assertRaises(ValueError):
            mi.need(self.home, ["nope"], str(self.env))

    def test_resolve_from_one_dump_and_save(self):
        dump = self.write("cols.json", {"collections": [
            {"id": "X", "title": "Something else"},
            {"id": "ARC", "title": "release notes archive", "created_at": "2026-09-01"},
            {"id": "DEV", "title": "Release Notes Developer Directory"},
            {"id": "MAP-new", "title": "Release Notes Pod Map", "created_at": "2026-11-01"},
            {"id": "MAP-old", "title": "Release Notes Pod Map", "created_at": "2026-10-01"},
            {"id": "T1", "title": "Oracle PR Overrides", "created_at": "2026-10-01"},
            {"id": "T2", "title": "Oracle PR Overrides", "created_at": "2026-10-01"},
        ]})
        out = mi.resolve(self.home, ["release_archive", "dev_directory", "pod_map",
                                     "pod_log", "pr_overrides"], str(dump), str(self.env))
        self.assertEqual(out["saved"], {"release_archive": "ARC", "dev_directory": "DEV",
                                        "pod_map": "MAP-old"})
        self.assertEqual([m["key"] for m in out["create"]], ["pod_log"])
        self.assertEqual(out["ambiguous"][0]["candidates"], ["T1", "T2"])
        again = mi.need(self.home, ["release_archive", "pod_map"], str(self.env))
        self.assertEqual(again["missing"], [])

    def test_exact_case_beats_older(self):
        cid, _ = mi.pick([{"id": "old", "title": "release notes archive", "created_at": "1"},
                          {"id": "exact", "title": "Release Notes Archive", "created_at": "9"}],
                         "Release Notes Archive")
        self.assertEqual(cid, "exact")

    def test_save_and_forget_cli(self):
        code, _, _ = _quiet(mi.main, ["--home", str(self.home), "save", "pod_log", "L1"])
        self.assertEqual(code, 0)
        self.assertEqual(mi.saved_ids(self.home, str(self.env))["pod_log"], "L1")
        code, out, _ = _quiet(mi.main, ["--home", str(self.home), "forget", "pod_log"])
        self.assertIn('"was_saved": true', out)
        self.assertNotIn("pod_log", mi.saved_ids(self.home, str(self.env)))


# --------------------------------------------------------------------------- #
POD = {"id": "pod-1", "version": 1, "content": (
    "Pod: #impact-pod_meg\n\n**Channel ID:** C0AAAAAAAA1\n**Clients:**\n"
    "- King Faisal Specialty Hospital & Resource Center [11111111-aaaa]\n")}


def _log(ticket, date="2026-11-10"):
    return {"id": f"log-{ticket}", "version": 1, "content": (
        f"Pod notify: {date} · /oracle-release\n\n**Date:** {date}\n\n"
        f"- C0AAAAAAAA1 · #impact-pod_meg · ts 1.1 · {ticket}\n")}


DATA = {"issues": [
    {"identifier": f"ORC-{n}", "title": f"T{n}", "url": f"https://o/ORC-{n}",
     "optimus_clients": [{"id": "11111111-aaaa",
                          "name": "King Faisal Specialty Hospital & Resource Center"}]}
    for n in (1, 2, 3)]}
SUMMARIES = "\n---\n".join(
    f"IDENTIFIER: ORC-{n}\nTITLE: T{n}\nCLIENT_TITLE: Client title {n}\n"
    f"EXEC_SUMMARY: Paragraph {n}.\nCATEGORY: BUG FIX\nURL: https://o/ORC-{n}"
    for n in (1, 2, 3))


class PodLogSplitTest(HomeCase):

    def _route(self, notes, log_notes=None):
        argv = ["pod_map.py", "route", "--data", str(self.write("d.json", DATA)),
                "--summaries", str(self.write("s.txt", SUMMARIES)),
                "--notes", str(self.write("n.json", {"notes": notes})),
                "--date", "2026-11-19", "--source", "/oracle-release",
                "--out-dir", str(self.home / "out")]
        if log_notes is not None:
            argv += ["--log-notes", str(self.write("l.json", {"notes": log_notes}))]
        with mock.patch.object(sys, "argv", argv):
            code, out, _ = _quiet(pm.main)
        self.assertEqual(code, 0, out)
        return json.loads(out.split("---POD_MAP_RESULT---")[1])

    def test_log_collection_and_old_logs_both_dedupe(self):
        res = self._route([POD, _log("ORC-1")], [_log("ORC-2")])
        self.assertEqual(res["already_sent"], 2)
        self.assertEqual(res["cards"], 1)

    def test_pod_notes_in_log_collection_are_not_pods(self):
        res = self._route([], [POD])
        self.assertEqual(res["pods"], 0)

    def test_outbox_pod_logs_dedupe(self):
        ra.outbox_add("pod_log", _log("ORC-3")["content"])
        res = self._route([POD], [])
        self.assertEqual(res["pending_logs"], 1)
        self.assertEqual(res["already_sent"], 1)

    def test_log_writes_go_to_log_collection(self):
        plan = {"date": "2026-11-19", "source": "/oracle-release", "release_link": "",
                "pods": [{"channel_id": "C0AAAAAAAA1", "channel_name": "#impact-pod_meg",
                          "root_ts": "1.2", "reply_files": ["r1"], "reply_tickets": [["ORC-1"]],
                          "posted_replies": ["r1"]}]}
        argv = ["pod_map.py", "log", "--plan", str(self.write("p.json", plan)),
                "--env-path", str(self.env)]
        with mock.patch.object(sys, "argv", argv):
            code, out, _ = _quiet(pm.main)
        body = json.loads(out.split("---POD_MAP_RESULT---")[0])
        self.assertEqual(body["collection_title"], "Pod Notify Log")
        self.assertEqual(body["writes"][0]["collection_key"], "pod_log")

    def test_old_logs_lists_only_log_notes(self):
        argv = ["pod_map.py", "old-logs", "--notes",
                str(self.write("n.json", {"notes": [POD, _log("ORC-1")]}))]
        with mock.patch.object(sys, "argv", argv):
            code, out, _ = _quiet(pm.main)
        body = json.loads(out.split("---POD_MAP_RESULT---")[0])
        self.assertEqual([n["id"] for n in body["notes"]], ["log-ORC-1"])


# --------------------------------------------------------------------------- #
NOTE = ra.render_note(
    [{"id": "ORC-886", "title": "A title", "category": "BUG FIX", "clients": ["TBC"],
      "links": [], "url": ""}],
    "oracle", "2026-11-17", "#pod-oracle", "C0X", "1.1", "",
    root_text=":rocket: Root", reply_texts=["- ORC-999 · looks · like · a · bullet"])


class OutboxTest(HomeCase):

    def test_add_list_done(self):
        p = ra.outbox_add("release_archive", NOTE, "rate limited")
        again = ra.outbox_add("release_archive", NOTE)
        self.assertEqual(p, again)                     # same note queued once
        self.assertTrue(str(p).startswith(str(self.home / ".medtrics-release-notes")))
        entries = ra.outbox_entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["title"], "Release: 2026-11-17 · oracle · #pod-oracle")
        self.assertEqual(ra.outbox_done([entries[0]["file"]]), [str(Path(p).resolve())])
        self.assertEqual(ra.outbox_entries(), [])

    def test_only_known_keys_and_outbox_files(self):
        with self.assertRaises(ValueError):
            ra.outbox_add("dev_directory", NOTE)
        stray = self.write("stray.json", "{}")
        with self.assertRaises(ValueError):
            ra.outbox_done([str(stray)])
        self.assertTrue(stray.exists())

    def test_add_from_pod_log_writes_file(self):
        writes = self.write("w.json", {"collection_key": "pod_log", "writes": [
            {"action": "create", "collection_key": "pod_log", "content": _log("ORC-5")["content"]}]})
        argv = ["release_archive.py", "outbox", "add", "--writes-file", str(writes)]
        with mock.patch.object(sys, "argv", argv):
            code, _, _ = _quiet(ra.main)
        self.assertEqual(code, 0)
        self.assertEqual([e["collection_key"] for e in ra.outbox_entries()], ["pod_log"])


class BackfillTest(HomeCase):

    def test_chat_paste_accepted(self):
        text = ("Release archive: Mem write failed (rate limited). Paste the note below:\n\n"
                "````\n" + NOTE + "````\n")
        content, reason = ra.check_backfill(text)
        self.assertEqual(reason, "")
        self.assertEqual(content, NOTE)
        rec = ra.parse_note(content)
        self.assertEqual([i["id"] for i in rec["items"]], ["ORC-886"])

    def test_rejects(self):
        self.assertEqual(ra.check_backfill("hello")[1], "no `Release: ` title line")
        no_root = NOTE.split("## Posted message")[0]
        self.assertEqual(ra.check_backfill(no_root)[1], "no `## Posted message` root")
        bad_date = NOTE.replace("**Date:** 2026-11-17", "**Date:** Nov 17")
        self.assertIn("Date", ra.check_backfill(bad_date)[1])

    def test_cli_writes_and_rejected(self):
        good = self.write("good.md", NOTE)
        bad = self.write("bad.md", "nothing here")
        argv = ["release_archive.py", "backfill", str(good), str(bad),
                str(self.home / "missing.md"), "--env-file", str(self.env)]
        with mock.patch.object(sys, "argv", argv):
            code, out, _ = _quiet(ra.main)
        body = json.loads(out.split("---RELEASE_ARCHIVE_RESULT---")[0])
        self.assertEqual(code, 0)
        self.assertEqual(len(body["writes"]), 1)
        self.assertEqual(len(body["rejected"]), 2)
        self.assertEqual(body["collection_name"], "Release Notes Archive")


# --------------------------------------------------------------------------- #
DEV = {"id": "dev-1", "version": 3, "content": "Developer: A\n\n**Slack ID:** U0AAAAAA1\n"}


class DevCacheTest(HomeCase):

    def test_fresh_and_stale(self):
        dd.cache_write([DEV])
        notes, age = dd.cache_read()
        self.assertEqual(notes, [DEV])
        later = dd._now() + dt.timedelta(hours=25)
        self.assertEqual(dd.cache_read(now=later)[0], None)

    def test_apply_create_update_and_failed(self):
        dd.cache_write([DEV])
        writes = [{"action": "update", "note_id": "dev-1", "content": "Developer: A2\n"},
                  {"action": "create", "content": "Developer: B\n"},
                  {"action": "create", "content": "Developer: C\n"}]
        applied = dd.cache_apply(writes, [{"id": "dev-1", "version": 4},
                                          {"id": "dev-2", "version": 1}, None])
        self.assertEqual(applied, 2)
        notes, _ = dd.cache_read()
        self.assertEqual([(n["id"], n["version"]) for n in notes],
                         [("dev-1", 4), ("dev-2", 1)])
        self.assertEqual(notes[0]["content"], "Developer: A2\n")

    def test_apply_without_results_drops_copy(self):
        dd.cache_write([DEV])
        dd.cache_apply([{"action": "create", "content": "x"}], None)
        self.assertFalse(dd.cache_path().exists())

    def test_cli_get(self):
        out_path = self.home / "notes.json"
        argv = ["dev_directory.py", "cache", "get", "--out", str(out_path)]
        with mock.patch.object(sys, "argv", argv):
            _, out, _ = _quiet(dd.main)
        self.assertIn('"fresh": false', out)
        dd.cache_write([DEV])
        with mock.patch.object(sys, "argv", argv):
            _, out, _ = _quiet(dd.main)
        self.assertIn('"fresh": true', out)
        self.assertEqual(json.loads(out_path.read_text())["notes"], [DEV])


if __name__ == "__main__":
    unittest.main()


# --------------------------------------------------------------------------- #
# Review fixes (0.25.0): loose notes, a missing pod log, queued notes with no id.
class AbsentKeysTest(HomeCase):
    """A key the one walk did not find is not walked for again for 7 days."""

    def test_not_found_key_is_absent_not_missing(self):
        dump = self.write("cols.json", {"collections": [{"id": "MAP", "title": "Release Notes Pod Map"}]})
        out = mi.resolve(self.home, ["pod_map", "pod_log"], str(dump), str(self.env))
        self.assertEqual([m["key"] for m in out["create"]], ["pod_log"])
        again = mi.need(self.home, ["pod_map", "pod_log"], str(self.env))
        self.assertEqual(again["missing"], [])
        self.assertEqual(again["absent"], ["pod_log"])
        self.assertEqual(again["ids"], {"pod_map": "MAP"})

    def test_recheck_and_expiry_and_save(self):
        old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=8)
        mi.mark_absent(self.home, ["pod_log"], now=old)
        self.assertEqual([m["key"] for m in mi.need(self.home, ["pod_log"], str(self.env))["missing"]],
                         ["pod_log"])                                  # mark expired
        mi.mark_absent(self.home, ["pod_log"])
        self.assertEqual(mi.need(self.home, ["pod_log"], str(self.env))["absent"], ["pod_log"])
        self.assertEqual([m["key"] for m in mi.need(self.home, ["pod_log"], str(self.env),
                                                    recheck=True)["missing"]], ["pod_log"])
        argv = ["--env-path", str(self.env), "save", "pod_log", "LOG1"]
        code, _, _ = _quiet(mi.main, argv)
        self.assertEqual(code, 0)
        out = mi.need(self.home, ["pod_log"], str(self.env))
        self.assertEqual((out["ids"], out["absent"]), ({"pod_log": "LOG1"}, []))


class LandedTest(HomeCase):
    """Mem ignores an unknown collection id on create_note: the response is checked."""

    def setUp(self):
        super().setUp()
        mi.kb_paths.merge(self.home, {"mem": {"release_archive": "ARC", "pod_log": "LOG"}})

    def test_landed_true(self):
        r = mi.landed(self.home, "release_archive", {"id": "N1", "collection_ids": ["ARC"]}, str(self.env))
        self.assertEqual((r["landed"], r["note_id"]), (True, "N1"))

    def test_loose_note_forgets_the_saved_id(self):
        r = mi.landed(self.home, "pod_log", json.dumps({"note": {"id": "N2", "collections": []}}),
                      str(self.env))
        self.assertEqual((r["landed"], r["note_id"], r["collection_id"]), (False, "N2", "LOG"))
        self.assertNotIn("pod_log", mi.need(self.home, ["pod_log"], str(self.env))["ids"])

    def test_collections_as_objects(self):
        r = mi.landed(self.home, "pod_log", {"id": "N3", "collections": [{"id": "LOG", "title": "x"}]},
                      str(self.env))
        self.assertTrue(r["landed"])

    def test_response_without_collections_is_unknown(self):
        r = mi.landed(self.home, "release_archive", {"id": "N4", "title": "Release: …"}, str(self.env))
        self.assertIsNone(r["landed"])
        self.assertIn("release_archive", mi.need(self.home, ["release_archive"], str(self.env))["ids"])

    def test_cli(self):
        resp = self.write("resp.json", {"id": "N5", "collection_ids": []})
        code, out, _ = _quiet(mi.main, ["--env-path", str(self.env), "landed", "release_archive",
                                        "--response", str(resp)])
        self.assertEqual(code, 0)
        self.assertIn('"landed": false', out)


class OutboxReadyTest(HomeCase):
    """A queued note uploads only when this run has its collection id."""

    def test_split(self):
        ra.outbox_add("release_archive", NOTE, "rate limited")
        ra.outbox_add("pod_log", _log("ORC-9")["content"], "pod log missing")
        entries = ra.outbox_entries()
        ready, waiting = ra.outbox_split(entries, "release_archive")
        self.assertEqual([e["collection_key"] for e in ready], ["release_archive"])
        self.assertEqual([e["collection_key"] for e in waiting], ["pod_log"])
        self.assertEqual(ra.outbox_split(entries, None), (entries, []))
        self.assertEqual(ra.outbox_split(entries, ""), ([], entries))

    def test_cli_lists_keys_and_waiting(self):
        ra.outbox_add("pod_log", _log("ORC-9")["content"])
        argv = ["release_archive.py", "outbox", "list", "--ready-keys", "release_archive"]
        with mock.patch.object(sys, "argv", argv):
            code, out, _ = _quiet(ra.main)
        self.assertEqual(code, 0)
        body = json.loads(out.split("---")[0])
        self.assertEqual((body["entries"], body["keys"], len(body["waiting"])), ([], ["pod_log"], 1))

    def test_waiting_pod_log_still_dedupes(self):
        ra.outbox_add("pod_log", _log("ORC-9")["content"])
        self.assertTrue(pm.pending_log_notes())


# --------------------------------------------------------------------------- #
# Call cuts A and B: the same plan as a full read, or no plan at all.
NO_CLIENT_DATA = {"issues": [
    {"identifier": "ORC-10", "title": "T10", "url": "https://o/ORC-10", "description": "A fix."},
    {"identifier": "ORC-11", "title": "T11", "url": "https://o/ORC-11",
     "description": "## Reported client(s)\n\n- All clients (platform-wide)\n"},
    {"identifier": "ORC-12", "title": "T12", "url": "https://o/ORC-12",
     "description": "## Reported client(s)\n\n- Internal\n"}]}
NO_CLIENT_SUMMARIES = "\n---\n".join(
    f"IDENTIFIER: ORC-{n}\nTITLE: T{n}\nCLIENT_TITLE: Client title {n}\n"
    f"EXEC_SUMMARY: Paragraph {n}.\nCATEGORY: BUG FIX\nURL: https://o/ORC-{n}"
    for n in (10, 11, 12))
UNMAPPED_DATA = {"issues": [
    {"identifier": "ORC-20", "title": "T20", "url": "https://o/ORC-20",
     "optimus_clients": [{"id": "99999999-zzzz", "name": "A Client In No Pod"}]}]}
UNMAPPED_SUMMARIES = ("IDENTIFIER: ORC-20\nTITLE: T20\nCLIENT_TITLE: Client title 20\n"
                      "EXEC_SUMMARY: Paragraph 20.\nCATEGORY: BUG FIX\nURL: https://o/ORC-20")


class RouteCutsTest(HomeCase):

    def _route(self, data, summaries, *extra, out="out"):
        argv = ["pod_map.py", "route", "--data", str(self.write("d.json", data)),
                "--summaries", str(self.write("s.txt", summaries)),
                "--date", "2026-11-19", "--source", "/oracle-release",
                "--out-dir", str(self.home / out), *extra]
        with mock.patch.object(sys, "argv", argv):
            code, out_text, err = _quiet(pm.main)
        res = json.loads(out_text.split("---POD_MAP_RESULT---")[1])
        return code, res

    def _plan(self, res):
        plan = json.loads(Path(res["plan"]).read_text())
        return {k: plan[k] for k in ("pods", "unrouted", "already_sent")}

    def _notes(self, notes, name):
        return str(self.write(name, {"notes": notes}))

    def test_a_no_client_plan_equals_the_full_read(self):
        code, cut = self._route(NO_CLIENT_DATA, NO_CLIENT_SUMMARIES, "--no-map", out="a")
        self.assertEqual((code, cut["status"], cut["map"], cut["pods"]), (0, "routed", "skipped", 0))
        _, full = self._route(NO_CLIENT_DATA, NO_CLIENT_SUMMARIES,
                              "--notes", self._notes([POD, _log("ORC-1")], "n.json"),
                              "--log-notes", self._notes([_log("ORC-2")], "l.json"), out="full")
        self.assertEqual(self._plan(cut), self._plan(full))
        self.assertEqual((cut["no_client"], cut["platform_wide"], cut["internal"]), (1, 1, 1))

    def test_a_a_client_needs_the_map_and_leaves_no_plan(self):
        stale = self.home / "out" / "plan.json"
        stale.parent.mkdir(parents=True)
        stale.write_text("{}")
        code, res = self._route(DATA, SUMMARIES, "--no-map")
        self.assertEqual((code, res["status"], res["plan"]), (0, "needs_map", None))
        self.assertFalse(stale.exists())

    def test_a_refuses_dumps(self):
        argv = ["pod_map.py", "route", "--data", str(self.write("d.json", DATA)),
                "--summaries", str(self.write("s.txt", SUMMARIES)), "--no-map",
                "--notes", self._notes([POD], "n.json"),
                "--date", "2026-11-19", "--source", "/x", "--out-dir", str(self.home / "out")]
        with mock.patch.object(sys, "argv", argv):
            code, _, _ = _quiet(pm.main)
        self.assertNotEqual(code, 0)

    def test_b_a_pod_hit_needs_the_log(self):
        code, res = self._route(DATA, SUMMARIES, "--notes", self._notes([POD], "n.json"), "--log-unread")
        self.assertEqual((code, res["status"], res["plan"]), (0, "needs_log", None))
        self.assertFalse((self.home / "out" / "plan.json").exists())

    def test_b_no_pod_hit_plan_equals_the_full_read(self):
        _, cut = self._route(UNMAPPED_DATA, UNMAPPED_SUMMARIES,
                             "--notes", self._notes([POD], "n.json"), "--log-unread", out="b")
        self.assertEqual((cut["status"], cut["pods"], cut["unmapped_clients"]), ("routed", 0, 1))
        _, full = self._route(UNMAPPED_DATA, UNMAPPED_SUMMARIES,
                              "--notes", self._notes([POD], "n2.json"),
                              "--log-notes", self._notes([_log("ORC-20")], "l.json"), out="full")
        self.assertEqual(self._plan(cut), self._plan(full))

    def test_b_after_the_log_read_the_duplicate_check_runs(self):
        _, res = self._route(DATA, SUMMARIES, "--notes", self._notes([POD], "n.json"),
                             "--log-notes", self._notes([_log("ORC-1")], "l.json"))
        self.assertEqual((res["status"], res["already_sent"], res["cards"]), ("routed", 1, 2))

    def test_outbox_pod_logs_still_dedupe_with_no_map_read(self):
        ra.outbox_add("pod_log", _log("ORC-1")["content"])
        _, res = self._route(DATA, SUMMARIES, "--no-map")
        self.assertEqual((res["status"], res["pending_logs"]), ("needs_map", 1))
