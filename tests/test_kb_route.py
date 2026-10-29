"""Tests for scripts/kb/kb_route.py — the start page and click path a ticket's
own description gives. Excerpts are verbatim from live Optimus tickets (M1-465,
M1-1596, M1-723)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kb_fixtures import KB_DIR  # noqa: F401  (puts scripts/kb on sys.path)

import kb_route as R

M1_465 = (
    "Overview:  Currently when ...\n\n**URL/Page:** \n\n"
    "[https://demo.medtricslab.com/administration/curriculum/sessions/]"
    "(https://demo.medtricslab.com/administration/curriculum/sessions/)\n\n"
    "User Opens a Session > hits View Button > Add Schedule Button\n\n"
    "**Loom Video:** \n\n[https://www.loom.com/share/15f7...](https://www.loom.com/share/15f7...)\n\n"
    "**Lovable Prototype Link:**\n\n[https://session-notifications.lovable.app/]"
    "(https://session-notifications.lovable.app/)\n\n"
    "**New Session Created - notify participants when user clicks on save button:**")

M1_1596 = (
    "Add a custom demographic question.\n\n"
    "## Release Impact Checklist - Friday release review, Oct 16 2026\n\n"
    "1. **Which portal and URL does this live on?** Admin portal: Cases > Custom Questions at "
    "/cases/administration/demographic-list/ to create the question. Trainees answer it on "
    "their case log form (/cases/create/ and /cases/<id>/<id>/edit/); the answer shows later.\n"
    "2. **Who is affected?** Everyone.")

M1_723 = ("**URL/Page:** https://lmucvm.medtrics.sg/forms/evaluation/my/#Review\n\n"
          "**Overview:** Reviewers see a new tab.")

# Trimmed from the MR attachments `<branch-slug>-manual-qa-checklist.txt`.
CL_465 = (
    "Manual QA Checklist\nBranch: m1-465-sessions-a-session-schedule-change-email-notifications\n\n"
    "Branch under test\n"
    "- `M1-465 — Sessions (A) > Session Schedule Change Email Notifications`\n"
    "- Primary workflow lives in the curriculum SPA route `'/vadministration/curriculum/sessions/'`.\n"
    "- Main UI path: open a session, go to the `Scheduling` section, then use `Add schedule` or the row `Edit` action.\n\n"
    "Blocked / Assumed Conditions\n"
    "- If internal documentation still links to `'/administration/curriculum/sessions/'`, use the active "
    "SPA route `'/vadministration/curriculum/sessions/'` for this branch.\n")

CL_1596 = (
    "Manual QA Checklist — M1-1596: Date Custom Case-Log Questions\n"
    "Direct Actions\n"
    "- As Program Admin, create a Date question on the Custom Questions page (`/cases/administration/demographic-list/`) "
    "and map it into a case template section\n\n"
    "Tester Role and Login Prerequisites\n"
    "- Supervisor (logged in): review queue at `/cases/procedure_logs/review/` and the case-log detail page\n"
    "- Supervisor (no login): `/user/<user_id>/procedure_logs/detail/<log_id>/<token>/`\n\n"
    "1. Open the Custom Questions page\n"
    "  Action: Go to `/cases/administration/demographic-list/` and start creating a new question\n")


def tk(desc):
    return {"identifier": "M1-X", "description": desc}


class Route(unittest.TestCase):
    def test_m1_465_url_page_and_clicks(self):
        r = R.extract(tk(M1_465))
        self.assertEqual(r["start"], "/administration/curriculum/sessions/")
        self.assertEqual(r["clicks"], [{"row": True}, {"text": "View"}, {"text": "Add Schedule"}])
        self.assertEqual(r["source"], "url_page")
        self.assertIn("save", r["write_gated"].lower())

    def test_m1_1596_release_checklist(self):
        r = R.extract(tk(M1_1596))
        self.assertEqual(r, {"start": "/cases/administration/demographic-list/", "clicks": [],
                             "write_gated": "", "source": "release_checklist"})

    def test_m1_723_client_tenant_hash(self):
        r = R.extract(tk(M1_723))
        self.assertEqual((r["start"], r["clicks"], r["source"]),
                         ("/forms/evaluation/my/#Review", [], "url_page"))

    def test_record_links_rejected(self):
        d = "**URL/Page:** https://demo.medtricslab.com/users/1234/edit/\n"
        self.assertIsNone(R.extract(tk(d)))
        d = "**URL/Page:** https://demo.medtricslab.com/forms/0b9c1a2e-1111-4222-8333-444455556666/\n"
        self.assertIsNone(R.extract(tk(d)))

    def test_record_link_falls_through_to_checklist(self):
        d = "**URL/Page:** https://demo.medtricslab.com/users/1234/edit/\n\n" + M1_1596
        self.assertEqual(R.extract(tk(d))["source"], "release_checklist")

    def test_other_hosts_ignored(self):
        d = ("**URL/Page:** [loom](https://www.loom.com/share/abc) "
             "https://session-notifications.lovable.app/\n")
        self.assertIsNone(R.extract(tk(d)))

    def test_qa_checklist_navigate(self):
        d = ("Intro\n\n## QA Checklist\n\n1. Navigate to https://demo.medtricslab.com/gradebook/grouped/\n"
             "2. Check the chart\n\n## Notes\nx")
        r = R.extract(tk(d))
        self.assertEqual((r["start"], r["source"]), ("/gradebook/grouped/", "qa_checklist"))

    def test_mr_checklist_beats_stale_description_link(self):
        i = {"identifier": "M1-465", "description": M1_465,
             "qa_checklist": {"text": CL_465, "source": "mr_attachment"}}
        r = R.extract(i)
        self.assertEqual(r["start"], "/vadministration/curriculum/sessions/")
        self.assertEqual(r["clicks"], [{"row": True}, {"text": "Add schedule"}])
        self.assertEqual(r["source"], "mr_checklist")
        self.assertIn("save", r["write_gated"].lower())   # from the description's hint

    def test_mr_checklist_primary_route_without_stale_note(self):
        text = CL_465.split("Blocked")[0]
        r = R.extract({"qa_checklist": {"text": text, "source": "mr_attachment"}})
        self.assertEqual(r["start"], "/vadministration/curriculum/sessions/")

    def test_mr_checklist_m1_1596_admin_route(self):
        r = R.extract({"identifier": "M1-1596", "description": M1_1596,
                       "qa_checklist": {"text": CL_1596, "source": "mr_attachment"}})
        self.assertEqual((r["start"], r["clicks"], r["source"]),
                         ("/cases/administration/demographic-list/", [], "mr_checklist"))

    def test_mr_checklist_without_route_or_optimus_source_falls_back(self):
        i = tk(M1_1596)
        i["qa_checklist"] = {"text": "Open a session and look around.", "source": "mr_attachment"}
        self.assertEqual(R.extract(i)["source"], "release_checklist")
        i["qa_checklist"] = {"text": CL_465, "source": "optimus"}
        self.assertEqual(R.extract(i)["source"], "release_checklist")

    def test_mr_checklist_keeps_host_and_id_rules(self):
        def start(text):
            r = R.extract({"qa_checklist": {"text": text, "source": "mr_attachment"}})
            return r and r["start"]
        self.assertEqual(start("- Main route: `https://demo.medtricslab.com/reports/list/`"), "/reports/list/")
        self.assertIsNone(start("- Main route: `https://example.com/reports/list/`"))
        for bad in ("- Main route: `/users/1234/edit/`", "- Main route: `/`",
                    "- Main route: `/users/<id>/edit/`"):
            self.assertIsNone(start(bad), bad)

    def test_checklist_ui_path_write_verbs_gate(self):
        clicks, gated = R._checklist_clicks("- UI path: open a session, use `Add schedule`, then `Save` and confirm")
        self.assertEqual(clicks, [{"row": True}, {"text": "Add schedule"}])
        self.assertTrue(gated.startswith("`Save`"))

    def test_none(self):
        self.assertIsNone(R.extract(tk("")))
        self.assertIsNone(R.extract({"identifier": "M1-1"}))

    def test_write_verbs_end_the_path(self):
        clicks, gated = R.parse_clicks("Opens a Session > hits Edit Button > Save > Confirm modal")
        self.assertEqual(clicks, [{"row": True}, {"text": "Edit"}])
        self.assertEqual(gated, "Save > Confirm modal")

    def test_filler_words_stripped(self):
        clicks, _ = R.parse_clicks("User clicks on the Reports tab > then Curriculum Changes link")
        self.assertEqual(clicks, [{"text": "Reports"}, {"text": "Curriculum Changes"}])

    def test_open_a_session_gives_a_row_click(self):
        clicks, _ = R.parse_clicks("Open a Session > View > Add Schedule")
        self.assertEqual(clicks, [{"row": True}, {"text": "View"}, {"text": "Add Schedule"}])
        for lead in ("Select a record", "Choose an item", "Go to Reports", "Navigate to Reports"):
            c, _ = R.parse_clicks(lead)
            self.assertEqual(c, [{"row": True}] if "record" in lead or "item" in lead else [{"text": "Reports"}], lead)

    def test_only_medtrics_hosts(self):
        for host in ("medtrics.atlassian.net", "medtrics.slack.com", "medtrics.notion.site",
                     "gitlab.com", "github.com", "medtricslab.com.evil.io"):
            d = f"**URL/Page:** https://{host}/administration/curriculum/sessions/\n"
            self.assertIsNone(R.extract(tk(d)), host)
        for host in ("demo.medtricslab.com", "medtricslab.com", "lmucvm.medtrics.sg"):
            d = f"**URL/Page:** https://{host}/administration/curriculum/sessions/\n"
            self.assertEqual(R.extract(tk(d))["start"], "/administration/curriculum/sessions/", host)

    def test_bare_slash_rejected(self):
        self.assertIsNone(R.extract(tk("**URL/Page:** https://demo.medtricslab.com/\n")))
        self.assertIsNone(R.extract(tk("**URL/Page:** https://demo.medtricslab.com\n")))
        self.assertIsNone(R._clean_path("/"))
        self.assertIsNone(R._first_path("see https://demo.medtricslab.com/ now"))
        self.assertIsNone(R._clean_path("/#Review"))

    def test_query_kept_unless_it_holds_an_id(self):
        u = "https://demo.medtricslab.com/reports/list/?type=attendance"
        self.assertEqual(R._path_of(u), "/reports/list/?type=attendance")
        self.assertEqual(R._path_of(u + "&page=2"), "/reports/list/")
        self.assertEqual(R._path_of("https://demo.medtricslab.com/reports/list/?id=0b9c1a2e-1111-4222-8333-444455556666"),
                         "/reports/list/")


if __name__ == "__main__":
    unittest.main()
