"""Tests for scripts/pod_map.py — the deterministic half of pod-notify.
Stdlib-only, hermetic (no Mem, no Slack, no network).

Pins: pod-note parse/render round-trip (bullet and one-line client lists,
client ids in brackets, a note without a channel id skipped, the template
never read as a pod); name normalization (& vs and, punctuation, case);
the `## Reported client(s)` fallback (names, platform-wide, internal, code
spans and "(unverified)" stripped); routing by Optimus client id then name,
a multi-client ticket split so each pod sees only its own clients, a client in
two pods, a ticket hitting one pod twice deduped; only posted (summaries)
tickets routed, MR-NNNN skipped; pod-log dedup and --force; rendered files
pass slack_lint with & escaped and no internal fields; reply packing at card
boundaries; channel checks (archived, shared, bot not a member, name drift);
mark/log resume state; names/fill-ids; the result block on every exit path.
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
    spec = importlib.util.spec_from_file_location("pod_map", SCRIPTS_DIR / "pod_map.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pm = _load_module()

KFSHRC = "King Faisal Specialty Hospital & Resource Center"
PURDUE = "Purdue University - College of Veterinary Medicine"
LMU = "Lincoln Memorial University - College of Veterinary Medicine"
VINMN = "VinUniversity Medicine and Nursing"

POD_MEG = {"id": "note-meg", "version": 2, "content": (
    "Pod: #impact-pod_meg\n\n"
    "**Channel ID:** C0AAAAAAAA1\n"
    "**Clients:**\n"
    f"- {KFSHRC} [11111111-aaaa]\n"
    f"- {PURDUE}\n")}
POD_EVITTS = {"id": "note-ev", "version": 1, "content": (
    "Pod: impact-pod_evitts\n\n"
    "**Channel ID:** C0BBBBBBBB2\n"
    f"**Clients:** {LMU}; {PURDUE}\n")}
POD_NO_ID = {"id": "note-x", "version": 1, "content": (
    "Pod: #pod-nowhere\n\n**Clients:**\n- Someone\n")}
TEMPLATE = {"id": "note-t", "version": 1, "content": pm.render_template()}
LOG = {"id": "note-log", "version": 1, "content": (
    "Pod notify: 2026-10-20 · /release-slack\n\n**Date:** 2026-10-20\n\n"
    "- C0AAAAAAAA1 · #impact-pod_meg · ts 1730000000.000100 · M1-1536, M1-1500\n")}


def issue(ident, clients=(), description="", title="Internal title"):
    return {"identifier": ident, "title": title, "description": description,
            "url": f"https://optimus.medtricslab.com/tasks/{ident}",
            "optimus_clients": [{"id": cid, "name": n} for cid, n in clients]}


def block(ident, **extra):
    b = {"IDENTIFIER": ident, "TITLE": "Internal title",
         "CLIENT_TITLE": f"Client title {ident}",
         "EXEC_SUMMARY": f"Coordinators can now do the thing for {ident}.",
         "URL": f"https://optimus.medtricslab.com/tasks/{ident}",
         "CLIENTS": "KFSHRC", "INTERNAL_SUMMARY": "SECRET internal detail",
         "DEVELOPER_SLACK_ID": "U0SECRET1"}
    b.update(extra)
    return b


def summaries_text(blocks):
    return "\n---\n".join("\n".join(f"{k}: {v}" for k, v in b.items()) for b in blocks) + "\n"


def _write(d, name, payload):
    p = Path(d) / name
    p.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    return str(p)


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(pm.sys, "argv", ["pod_map.py"] + argv), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = pm.main()
    text = out.getvalue()
    result = json.loads(text.split("---POD_MAP_RESULT---")[1].strip())
    return code, result, text.split("---POD_MAP_RESULT---")[0], err.getvalue()


class NoteTests(unittest.TestCase):
    def test_bullet_list_and_ids(self):
        rec, warns = pm.parse_pod_note(POD_MEG)
        self.assertEqual(warns, [])
        self.assertEqual(rec["channel_id"], "C0AAAAAAAA1")
        self.assertEqual(rec["channel_name"], "#impact-pod_meg")
        self.assertEqual([{k: c[k] for k in ("name", "id")} for c in rec["clients"]],
                         [{"name": KFSHRC, "id": "11111111-aaaa"}, {"name": PURDUE, "id": ""}])
        self.assertEqual(pm.render_pod_note(rec), POD_MEG["content"])

    def test_one_line_list_and_hash_added(self):
        rec, _ = pm.parse_pod_note(POD_EVITTS)
        self.assertEqual(rec["channel_name"], "#impact-pod_evitts")
        self.assertEqual([c["name"] for c in rec["clients"]], [LMU, PURDUE])

    def test_parenthesised_name_kept(self):
        rec, _ = pm.parse_pod_note({"content": (
            "Pod: #p\n\n**Channel ID:** C0CCCCCCCC3\n**Clients:**\n"
            "- American University of the Caribbean SOM (AUC)\n")})
        self.assertEqual(rec["clients"][0]["name"],
                         "American University of the Caribbean SOM (AUC)")

    def test_missing_channel_id_skipped_with_warning(self):
        rec, warns = pm.parse_pod_note(POD_NO_ID)
        self.assertIsNone(rec)
        self.assertIn("no valid Channel ID", warns[0])

    def test_template_and_stray_notes_ignored(self):
        self.assertEqual(pm.parse_pod_note(TEMPLATE), (None, []))
        self.assertEqual(pm.parse_pod_note({"content": "Meeting notes\n"}), (None, []))
        self.assertEqual(pm.PodMap([TEMPLATE]).pods, [])

    def test_mem_shaped_note(self):
        """Mem returns notes with Markdown escapes, hard-break backslashes, a
        heading title, blank lines between fields and plain `Key:` fields."""
        note = {"id": "n", "version": 1, "content": (
            "# Pod: #impact-pod\\_meg\n\n"
            "Channel ID: C0AAAAAAAA1\\\n\n"
            "**Clients**:\n\n"
            "1. King Faisal Specialty Hospital &amp; Resource Center \\[11111111-aaaa\\]\n"
            "2. Purdue University \\- College of Veterinary Medicine\\\n\n"
            "**Notes:** ask Meg first\n")}
        rec, warns = pm.parse_pod_note(note)
        self.assertEqual(warns, [])
        self.assertEqual(rec["channel_name"], "#impact-pod_meg")
        self.assertEqual(rec["channel_id"], "C0AAAAAAAA1")
        self.assertEqual([(c["name"], c["id"]) for c in rec["clients"]],
                         [("King Faisal Specialty Hospital &amp; Resource Center", "11111111-aaaa"),
                          (PURDUE, "")])
        m = pm.PodMap([note])
        self.assertEqual(m.pods_for({"id": "11111111-aaaa", "name": "x"}), [0])
        self.assertEqual(m.pods_for({"id": "", "name": KFSHRC}), [0])

    def test_mem_shaped_log_note(self):
        note = {"content": ("Pod notify: 2026-10-20 · /release\\-slack\n\n"
                            "\\- C0AAAAAAAA1 · #impact-pod\\_meg · ts 1730.1 · M1-1, ORC-7\\\n")}
        self.assertEqual([e["ticket"] for e in pm.parse_log_note(note)], ["M1-1", "ORC-7"])

    def test_heading_template_recognized(self):
        self.assertEqual(pm._title({"content": "# " + pm.render_template()}), pm.TEMPLATE_TITLE)

    def test_log_note_parsed(self):
        entries = pm.parse_log_note(LOG)
        self.assertEqual([e["ticket"] for e in entries], ["M1-1536", "M1-1500"])
        self.assertEqual(entries[0]["date"], "2026-10-20")
        self.assertEqual(entries[0]["channel_id"], "C0AAAAAAAA1")


class NormalizeTests(unittest.TestCase):
    def test_ampersand_and_punctuation(self):
        self.assertEqual(pm.normalize_name("King Faisal Specialty Hospital & Resource Center"),
                         pm.normalize_name("king faisal specialty hospital and resource center"))
        self.assertEqual(pm.normalize_name("ATSU-KCOM"), "atsu kcom")
        self.assertEqual(pm.normalize_name("Universidad Central del Caribe – Pavía"),
                         "universidad central del caribe pavia")

    def test_research_is_not_resource(self):
        self.assertNotEqual(pm.normalize_name("King Faisal Specialist Hospital & Research Centre"),
                            pm.normalize_name(KFSHRC))


class ReportedClientsTests(unittest.TestCase):
    def test_names_with_codes_and_unverified(self):
        desc = ("## Summary\nx\n\n## Reported client(s)\n"
                f"- {VINMN} (`VINUNIMN`)\n- Universidad de Monterrey (unverified)\n\n"
                "## Steps\n- not a client\n")
        self.assertEqual(pm.reported_clients(desc),
                         ("names", [VINMN, "Universidad de Monterrey"]))

    def test_platform_wide(self):
        desc = "## Reported clients\n- All clients / platform-wide (first observed on KYCOM data)\n"
        self.assertEqual(pm.reported_clients(desc), ("platform", []))

    def test_internal_medicine_is_a_name(self):
        desc = "## Reported client(s)\n- UCSF Department of Internal Medicine\n"
        self.assertEqual(pm.reported_clients(desc),
                         ("names", ["UCSF Department of Internal Medicine"]))

    def test_internal(self):
        desc = "### Reported client(s)\nInternal / not client-specific\n"
        self.assertEqual(pm.reported_clients(desc), ("internal", []))

    def test_no_section_never_guesses_codes(self):
        self.assertEqual(pm.reported_clients("Seen on KYCOM and ORC-586 data, see PDF"),
                         ("none", []))

    def test_linked_clients_win(self):
        i = issue("M1-1", [("id-1", PURDUE)], description=f"## Reported client(s)\n- {LMU}\n")
        self.assertEqual(pm.ticket_clients(i), ("linked", [{"id": "id-1", "name": PURDUE}], "names"))


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.notes = _write(self.d, "notes.json", {"notes": [POD_MEG, POD_EVITTS, POD_NO_ID,
                                                             TEMPLATE, LOG]})
        self.out_dir = str(Path(self.d) / "out")

    def route(self, issues, blocks, extra=None):
        data = _write(self.d, "data.json", {"issues": issues})
        summ = _write(self.d, "summ.txt", summaries_text(blocks))
        code, result, _, err = _run(["route", "--data", data, "--summaries", summ,
                                     "--notes", self.notes, "--date", "2026-10-29",
                                     "--source", "/release-slack", "--out-dir",
                                     self.out_dir] + (extra or []))
        plan = json.loads(Path(result["plan"]).read_text()) if code == 0 else None
        return code, result, plan

    def pod(self, plan, channel_id):
        return next(p for p in plan["pods"] if p["channel_id"] == channel_id)

    def test_routing_by_id_then_name_and_multi_client_split(self):
        issues = [
            # Linked by id: renamed in Optimus, still matches by id.
            issue("M1-10", [("11111111-aaaa", "KFSHRC renamed")]),
            # Linked by name only, Purdue is in two pods.
            issue("M1-11", [("p-id", PURDUE)]),
            # Two clients in two different pods.
            issue("M1-12", [("11111111-aaaa", KFSHRC), ("l-id", LMU)]),
        ]
        code, result, plan = self.route(issues, [block("M1-10"), block("M1-11"), block("M1-12")])
        self.assertEqual(code, 0)
        meg = self.pod(plan, "C0AAAAAAAA1")
        ev = self.pod(plan, "C0BBBBBBBB2")
        self.assertEqual([t["identifier"] for t in meg["tickets"]], ["M1-10", "M1-11", "M1-12"])
        self.assertEqual([t["identifier"] for t in ev["tickets"]], ["M1-11", "M1-12"])
        m12_ev = next(t for t in ev["tickets"] if t["identifier"] == "M1-12")
        self.assertEqual(m12_ev["pod_clients"], [LMU])
        reply = Path(ev["reply_files"][0]).read_text()
        self.assertNotIn("King Faisal", reply)
        self.assertTrue(any("listed in 2 pods" in w for w in result["warnings"]))
        self.assertTrue(any("no valid Channel ID" in w for w in result["warnings"]))

    def test_only_posted_tickets_routed_and_mr_skipped(self):
        issues = [issue("M1-20", [("p", PURDUE)]), issue("M1-21", [("p", PURDUE)]),
                  issue("MR-6567")]
        code, result, plan = self.route(issues, [block("M1-20"), block("MR-6567")])
        self.assertEqual([t["identifier"] for t in self.pod(plan, "C0AAAAAAAA1")["tickets"]],
                         ["M1-20"])
        self.assertEqual(plan["unrouted"]["skipped"], ["MR-6567"])

    def test_buckets_for_unrouted(self):
        issues = [
            issue("ORC-600", description="## Reported client(s)\n- Internal / not client-specific\n"),
            issue("ORC-586", description="## Reported client(s)\n- All clients / platform-wide\n"),
            issue("ORC-601"),
            issue("ORC-459", description="## Reported client(s)\n- Rocky Vista University\n"),
            issue("ORC-699", [("v", VINMN)]),
        ]
        code, result, plan = self.route(issues, [block(i["identifier"]) for i in issues])
        u = plan["unrouted"]
        self.assertEqual(u["internal"], ["ORC-600"])
        self.assertEqual(u["platform_wide"], ["ORC-586"])
        self.assertEqual(u["no_client"], ["ORC-601"])
        self.assertEqual(u["unmapped_clients"], [
            {"identifier": "ORC-459", "client": "Rocky Vista University", "via": "guessed"},
            {"identifier": "ORC-699", "client": VINMN, "via": "linked"}])
        self.assertEqual(plan["pods"], [])

    def test_guessed_client_is_routed_and_marked(self):
        i = issue("M1-1373", description=f"## Reported client(s)\n- {KFSHRC} (`KFSHRC`)\n")
        code, result, plan = self.route([i], [block("M1-1373")])
        t = self.pod(plan, "C0AAAAAAAA1")["tickets"][0]
        self.assertEqual(t["via"], "guessed")
        self.assertIn("not linked in Optimus", Path(self.pod(plan, "C0AAAAAAAA1")
                                                   ["reply_files"][0]).read_text())
        self.assertEqual(result["guessed"], 1)

    def test_already_sent_skipped_unless_forced(self):
        issues = [issue("M1-1536", [("11111111-aaaa", KFSHRC)]),
                  issue("M1-1537", [("11111111-aaaa", KFSHRC)])]
        blocks = [block("M1-1536"), block("M1-1537")]
        code, result, plan = self.route(issues, blocks)
        self.assertEqual([t["identifier"] for t in plan["pods"][0]["tickets"]], ["M1-1537"])
        self.assertEqual(plan["already_sent"][0]["identifier"], "M1-1536")
        self.assertEqual(plan["already_sent"][0]["date"], "2026-10-20")
        code, result, plan = self.route(issues, blocks, ["--force"])
        self.assertEqual(len(plan["pods"][0]["tickets"]), 2)
        self.assertEqual(result["already_sent"], 0)

    def test_rendered_files_lint_clean_and_carry_no_internal_fields(self):
        i = issue("M1-30", [("11111111-aaaa", KFSHRC)])
        b = block("M1-30", CLIENT_TITLE="Rotations *now* sync A & B",
                  EXEC_SUMMARY="Schedulers see <changes> & updates at once.",
                  CLIENT_FLAG="KFSHRC — still investigating")
        code, result, plan = self.route([i], [b], ["--release-link",
                                                   "https://medtrics.slack.com/archives/C1/p1"])
        self.assertEqual(result["lint_failures"], 0)
        pod = plan["pods"][0]
        self.assertEqual(pod["status"], "routed")   # ready only after the channel check
        root = Path(pod["root_file"]).read_text()
        reply = Path(pod["reply_files"][0]).read_text()
        self.assertIn("1 item in this release concerns", root)
        self.assertIn("King Faisal Specialty Hospital &amp; Resource Center", root)
        self.assertIn("<https://medtrics.slack.com/archives/C1/p1|open in Slack>", root)
        self.assertIn("*1. Rotations now sync A &amp; B*", reply)
        self.assertIn("&lt;changes&gt; &amp; updates", reply)
        self.assertIn("<https://optimus.medtricslab.com/tasks/M1-30|M1-30>", reply)
        self.assertIn("Heads-up: client-specific item", reply)
        for secret in ("SECRET", "U0SECRET1", "<@"):
            self.assertNotIn(secret, root + reply)

    def test_missing_client_text_never_falls_back_to_internal(self):
        issues = [issue("M1-80", [("p", PURDUE)], title="Internal-title leaks Purdue"),
                  issue("M1-81", [("p", PURDUE)]), issue("M1-82", [("p", PURDUE)])]
        code, result, plan = self.route(issues, [block("M1-80", CLIENT_TITLE=""),
                                                 block("M1-81", EXEC_SUMMARY="")])
        self.assertEqual(plan["unrouted"]["missing_text"], ["M1-80", "M1-81"])
        self.assertEqual(plan["unrouted"]["no_summary"], ["M1-82"])
        self.assertEqual(plan["pods"], [])

    def test_one_reply_per_ticket(self):
        issues = [issue(f"M1-9{n}", [("p", PURDUE)]) for n in range(3)]
        code, result, plan = self.route(issues, [block(i["identifier"]) for i in issues])
        pod = self.pod(plan, "C0AAAAAAAA1")
        self.assertEqual(pod["reply_tickets"], [["M1-90"], ["M1-91"], ["M1-92"]])
        self.assertEqual(len(pod["reply_files"]), 3)

    def test_routing_only_needs_no_summaries_and_writes_no_files(self):
        data = _write(self.d, "data.json", {"issues": [issue("M1-40", [("p", PURDUE)])]})
        code, result, _, _ = _run(["route", "--data", data, "--notes", self.notes,
                                   "--date", "2026-10-29", "--source", "/release-slack",
                                   "--out-dir", self.out_dir, "--routing-only"])
        self.assertEqual(code, 0)
        plan = json.loads(Path(result["plan"]).read_text())
        self.assertTrue(plan["routing_only"])
        self.assertEqual(plan["pods"][0]["reply_files"], [])
        self.assertFalse(list(Path(self.out_dir).glob("pod_*")))

    def test_focus_keeps_routed_tickets_with_client_line(self):
        data = _write(self.d, "data.json", {"issues": [
            issue("M1-70", [("11111111-aaaa", KFSHRC), ("l", LMU)]), issue("M1-71")]})
        code, result, _, _ = _run(["route", "--data", data, "--notes", self.notes,
                                   "--date", "2026-10-29", "--source", "/pod-notify",
                                   "--out-dir", self.out_dir, "--routing-only"])
        out = str(Path(self.d) / "focused.json")
        code, result, text, _ = _run(["focus", "--data", data, "--plan", result["plan"],
                                      "--out", out])
        self.assertEqual(result["tickets"], ["M1-70"])
        kept = json.loads(Path(out).read_text())["issues"]
        self.assertEqual(kept[0]["clients"], f"{LMU}; {KFSHRC}")

    def test_missing_summaries_is_config_error(self):
        data = _write(self.d, "data.json", {"issues": []})
        code, result, _, _ = _run(["route", "--data", data, "--date", "2026-10-29",
                                   "--source", "/x", "--out-dir", self.out_dir])
        self.assertEqual(code, 1)
        self.assertFalse(result["ok"])
        self.assertIn("--summaries", result["error"])

    def test_empty_notes_file_is_empty_map(self):
        self.notes = _write(self.d, "empty.json", "")
        code, result, plan = self.route([issue("M1-50", [("p", PURDUE)])], [block("M1-50")])
        self.assertEqual(code, 0)
        self.assertEqual(result["pod_notes"], 0)
        self.assertEqual(plan["unrouted"]["unmapped_clients"][0]["client"], PURDUE)


class CardTests(unittest.TestCase):
    def ticket(self, **extra):
        t = {"identifier": "M1-1", "client_title": "Title", "summary": "Summary.",
             "url": "https://o/1", "via": "linked", "pod_clients": [KFSHRC], "flagged": False}
        t.update(extra)
        return t

    def test_oversized_summary_is_cut_to_fit(self):
        card = pm.render_card(1, self.ticket(summary="word " * 2000))
        self.assertLessEqual(len(card), pm.REPLY_CAP)
        self.assertIn("…", card)
        self.assertEqual(pm.slack_lint.lint(card, "reply"), [])

    def test_markup_in_analyzer_text_is_cleaned(self):
        card = pm.render_card(1, self.ticket(client_title="**Bold** title",
                                             summary="*whole bold* ```code``` **x** & y"))
        self.assertEqual(pm.slack_lint.lint(card, "reply"), [])
        self.assertIn("*1. Bold title*", card)
        self.assertNotIn("**", card)

    def test_url_pipe_encoded_and_star_only_title(self):
        card = pm.render_card(2, self.ticket(url="https://o/x?a=1|2>", client_title="***"))
        self.assertIn("<https://o/x?a=1%7C2%3E|M1-1>", card)
        self.assertIn("*2. M1-1*", card)
        self.assertEqual(pm.slack_lint.lint(card, "reply"), [])


class PreviewMarkLogTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        notes = _write(self.d, "notes.json", {"notes": [POD_MEG, POD_EVITTS]})
        data = _write(self.d, "data.json", {"issues": [
            issue("M1-60", [("11111111-aaaa", KFSHRC)]), issue("M1-61", [("l", LMU)])]})
        summ = _write(self.d, "summ.txt", summaries_text([block("M1-60"), block("M1-61")]))
        code, result, _, _ = _run(["route", "--data", data, "--summaries", summ,
                                   "--notes", notes, "--date", "2026-10-29",
                                   "--source", "/release-slack",
                                   "--out-dir", str(Path(self.d) / "out")])
        self.plan = result["plan"]
        self.good = {"C0AAAAAAAA1": {"ok": True, "info": {"name": "impact-pod_meg", "is_member": True}},
                     "C0BBBBBBBB2": {"ok": True, "info": {"name": "impact-pod_evitts",
                                                          "is_member": True}}}

    def ready_all(self):
        _run(["preview", "--plan", self.plan, "--channels",
              _write(self.d, "good.json", self.good)])

    def checks(self, meg_info, ev_info):
        return _write(self.d, "checks.json", {
            "C0AAAAAAAA1": {"ok": True, "info": meg_info},
            "C0BBBBBBBB2": {"ok": True, "info": ev_info}})

    def test_blocked_states_and_name_drift(self):
        good = {"name": "impact-pod_meg", "is_member": True}
        for info, reason in (
                ({"name": "impact-pod_evitts", "is_archived": True, "is_member": True}, "archived"),
                ({"name": "impact-pod_evitts", "is_ext_shared": True, "is_member": True}, "Slack Connect"),
                ({"name": "impact-pod_evitts", "is_pending_ext_shared": True, "is_member": True},
                 "Slack Connect"),
                ({"name": "impact-pod_evitts", "is_member": False}, "not a member")):
            code, result, text, _ = _run(["preview", "--plan", self.plan,
                                          "--channels", self.checks(good, info)])
            self.assertEqual(result["ready"], ["C0AAAAAAAA1"])
            self.assertEqual(result["blocked"], 1)
            self.assertIn(reason, text)
        # Fixed in Slack → the next preview unblocks it. Org-wide `is_shared`
        # alone (Enterprise Grid, internal) is not Slack Connect.
        code, result, text, _ = _run(["preview", "--plan", self.plan, "--channels", self.checks(
            good, {"name": "impact-pod_evitts", "is_shared": True, "is_member": True})])
        self.assertEqual(sorted(result["ready"]), ["C0AAAAAAAA1", "C0BBBBBBBB2"])
        code, result, text, _ = _run(["preview", "--plan", self.plan, "--channels",
                                      self.checks({"name": "renamed", "is_member": True}, good)])
        self.assertTrue(any("is now #renamed" in w for w in result["warnings"]))
        self.assertIn("Sample —", text)

    def test_unchecked_channel_is_blocked(self):
        path = _write(self.d, "checks.json", {"C0AAAAAAAA1": {"ok": False, "error": "missing_scope"}})
        code, result, text, _ = _run(["preview", "--plan", self.plan, "--channels", path])
        self.assertEqual(result["ready"], [])
        self.assertIn("missing_scope", text)
        self.assertIn("not checked", text)
        path = _write(self.d, "nf.json", {"C0AAAAAAAA1": {"ok": False, "error": "channel_not_found"}})
        code, result, text, _ = _run(["preview", "--plan", self.plan, "--channels", path])
        self.assertIn("invite the bot", text)

    def test_preview_without_checks_never_readies(self):
        code, result, text, _ = _run(["preview", "--plan", self.plan])
        self.assertEqual(result["ready"], [])
        self.assertIn("channel not checked", text)

    def test_lint_fix_at_preview_recovers_the_pod(self):
        plan = json.loads(Path(self.plan).read_text())
        reply = plan["pods"][0]["reply_files"][0]
        good_text = Path(reply).read_text()
        Path(reply).write_text("**broken** & bad\n")
        code, result, text, _ = _run(["preview", "--plan", self.plan, "--channels",
                                      _write(self.d, "good.json", self.good)])
        self.assertNotIn(plan["pods"][0]["channel_id"], result["ready"])
        self.assertIn("LINT FAILED", text)
        Path(reply).write_text(good_text)
        code, result, text, _ = _run(["preview", "--plan", self.plan, "--channels",
                                      _write(self.d, "good.json", self.good)])
        self.assertIn(plan["pods"][0]["channel_id"], result["ready"])

    def test_mark_refuses_a_pod_that_is_not_ready(self):
        code, result, _, _ = _run(["mark", "--plan", self.plan, "--channel-id", "C0AAAAAAAA1",
                                   "--kind", "root", "--ts", "1730.1"])
        self.assertEqual(code, 1)
        self.assertIn("not ready", result["error"])

    def test_mark_and_log_record_only_posted_replies(self):
        self.ready_all()
        plan = json.loads(Path(self.plan).read_text())
        meg = next(p for p in plan["pods"] if p["channel_id"] == "C0AAAAAAAA1")
        code, result, _, _ = _run(["mark", "--plan", self.plan, "--channel-id", "C0BBBBBBBB2",
                                   "--kind", "reply", "--file", "x"])
        self.assertEqual(code, 1)   # reply before root
        _run(["mark", "--plan", self.plan, "--channel-id", "C0AAAAAAAA1",
              "--kind", "root", "--ts", "1730.1"])
        code, result, _, _ = _run(["mark", "--plan", self.plan, "--channel-id", "C0AAAAAAAA1",
                                   "--kind", "reply", "--file", meg["reply_files"][0]])
        self.assertEqual(result["status"], "posted")
        _run(["mark", "--plan", self.plan, "--channel-id", "C0BBBBBBBB2",
              "--kind", "root", "--ts", "1730.2"])   # root only: partial, no tickets yet
        out = str(Path(self.d) / "log.json")
        code, result, _, _ = _run(["log", "--plan", self.plan, "--out", out,
                                   "--env-path", str(Path(self.d) / "none.env")])
        writes = json.loads(Path(out).read_text())["writes"]
        self.assertEqual(len(writes), 1)
        content = writes[0]["content"]
        self.assertTrue(content.startswith("Pod notify: 2026-10-29 · /release-slack"))
        entries = pm.parse_log_note({"content": content})
        self.assertEqual([(e["channel_id"], e["ticket"]) for e in entries],
                         [("C0AAAAAAAA1", "M1-60")])
        # Per-pod log, and a path spelled differently still marks.
        rel = str(Path(meg["reply_files"][0]).parent / ".." / "out" / Path(meg["reply_files"][0]).name)
        code, result, _, _ = _run(["mark", "--plan", self.plan, "--channel-id", "C0AAAAAAAA1",
                                   "--kind", "reply", "--file", rel])
        self.assertEqual(code, 0)
        code, result, _, _ = _run(["log", "--plan", self.plan, "--out", out,
                                   "--channel-id", "C0BBBBBBBB2",
                                   "--env-path", str(Path(self.d) / "none.env")])
        self.assertEqual(json.loads(Path(out).read_text())["writes"], [])

    def test_drop_ticket_rerenders_and_drop_pod(self):
        code, result, _, _ = _run(["drop", "--plan", self.plan, "--channel-id", "C0BBBBBBBB2",
                                   "--ticket", "M1-60"])
        self.assertEqual(code, 1)   # M1-60 is not in that pod
        plan = json.loads(Path(self.plan).read_text())
        meg = next(p for p in plan["pods"] if p["channel_id"] == "C0AAAAAAAA1")
        meg["tickets"].append({**meg["tickets"][0], "identifier": "M1-99"})
        Path(self.plan).write_text(json.dumps(plan))
        code, result, _, _ = _run(["drop", "--plan", self.plan, "--channel-id", "C0AAAAAAAA1",
                                   "--ticket", "M1-60"])
        self.assertEqual(result["tickets"], ["M1-99"])
        self.assertEqual(result["status"], "routed")
        plan = json.loads(Path(self.plan).read_text())
        meg = next(p for p in plan["pods"] if p["channel_id"] == "C0AAAAAAAA1")
        reply = Path(meg["reply_files"][0]).read_text()
        self.assertNotIn("|M1-60>", reply)
        self.assertIn("|M1-99>", reply)
        code, result, _, _ = _run(["drop", "--plan", self.plan, "--channel-id", "C0BBBBBBBB2"])
        self.assertEqual(result["status"], "dropped")
        code, result, text, _ = _run(["preview", "--plan", self.plan])
        self.assertIn("DROPPED", text)

    def test_unexpected_error_still_ends_in_a_result_block(self):
        bad = _write(self.d, "bad_plan.json", {"pods": []})
        code, result, _, err = _run(["log", "--plan", bad, "--out", str(Path(self.d) / "x.json")])
        self.assertEqual(code, 1)
        self.assertFalse(result["ok"])
        self.assertIn("KeyError", result["error"] + err)

    def test_nothing_posted_means_no_log_write(self):
        out = str(Path(self.d) / "log.json")
        _run(["log", "--plan", self.plan, "--out", out, "--env-path", str(Path(self.d) / "n")])
        self.assertEqual(json.loads(Path(out).read_text())["writes"], [])


class SetupHelperTests(unittest.TestCase):
    def test_names_and_fill_ids(self):
        d = tempfile.mkdtemp()
        notes = _write(d, "notes.json", {"notes": [POD_MEG, POD_EVITTS]})
        code, result, text, _ = _run(["names", "--notes", notes])
        self.assertEqual(result["missing_ids"], [PURDUE, LMU])
        self.assertEqual([c["channel_id"] for c in result["channels"]],
                         ["C0AAAAAAAA1", "C0BBBBBBBB2"])
        self.assertFalse(result["has_template"])
        resolved = _write(d, "resolved.json", {PURDUE: "p-uuid-0001", LMU: None})
        out = str(Path(d) / "writes.json")
        code, result, _, _ = _run(["fill-ids", "--notes", notes, "--resolved", resolved,
                                   "--out", out])
        self.assertEqual(result["unresolved"], [LMU])
        writes = json.loads(Path(out).read_text())["writes"]
        self.assertEqual({w["note_id"] for w in writes}, {"note-meg", "note-ev"})
        meg = next(w for w in writes if w["note_id"] == "note-meg")
        self.assertEqual(meg["version"], 2)
        self.assertIn(f"- {PURDUE} [p-uuid-0001]", meg["content"])
        self.assertIn(f"- {KFSHRC} [11111111-aaaa]", meg["content"])

    def test_fill_ids_keeps_the_rest_of_the_note(self):
        d = tempfile.mkdtemp()
        note = {"id": "n1", "version": 4, "content": (
            "Pod: #impact-pod\\_meg\n\nOwner: Meg\n\n**Channel ID:** C0AAAAAAAA1\n"
            "**Clients:**\n- Purdue University \\- College of Veterinary Medicine\\\n"
            "**Notes:** ask Meg first\n")}
        notes = _write(d, "notes.json", {"notes": [note]})
        resolved = _write(d, "resolved.json", {PURDUE: "p-uuid-0001"})
        out = str(Path(d) / "writes.json")
        _run(["fill-ids", "--notes", notes, "--resolved", resolved, "--out", out])
        content = json.loads(Path(out).read_text())["writes"][0]["content"]
        self.assertIn("Owner: Meg", content)
        self.assertIn("**Notes:** ask Meg first", content)
        self.assertIn("[p-uuid-0001]", content)
        rec, _ = pm.parse_pod_note({"content": content})
        self.assertEqual([(c["name"], c["id"]) for c in rec["clients"]], [(PURDUE, "p-uuid-0001")])

    def test_collection_name_precedence(self):
        d = tempfile.mkdtemp()
        env = _write(d, ".env", "MEM_POD_MAP_COLLECTION=Pods Test\n")
        self.assertEqual(pm.resolve_collection_name(env, environ={}), ("Pods Test", ".env"))
        self.assertEqual(pm.resolve_collection_name(env, environ={pm.ENV_KEY: "X"}), ("X", "env"))
        self.assertEqual(pm.resolve_collection_name(str(Path(d) / "none"), environ={}),
                         (pm.DEFAULT_COLLECTION_NAME, "default"))


if __name__ == "__main__":
    unittest.main()
