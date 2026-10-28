---
name: kb-capture
description: Live screenshot capture for /kb-articles on the capture site (medtrics.medtricslab.com) through the Claude in Chrome extension, driving the owner's own Chrome where they have already signed in by hand — one account covers admin and faculty (via the Personal portal), and the run returns to Admin at the end. Covers the flow (plan-calls, the Haiku kb-capturer, ingest), the live-test facts the design depends on, raw files and resume, and how to map a surface in kb_routes.json (a URL only). Claude never types a password.
---

# KB Capture (Claude in Chrome on the demo tenant)

Screenshots are the slow, expensive part of a KB article. Scripts plan the
capture, a Haiku agent carries it out in the owner's Chrome without looking at
any page, and scripts verify every frame and place every ring.

## Sign-in

The owner signs in to `https://medtrics.medtricslab.com` **by hand** in the
Chrome that has the Claude in Chrome extension connected to their Claude
account. Claude never types a password and there is no credentials file. One
account covers both personas: the admin screens use the Administration portal
and the faculty screens use the **Personal** portal, switched by URL
(`/?portal_change=administration|personal|institution`). There are no learner
screens in `kb_routes.json`. The run ends on `/?portal_change=administration`,
so the Chrome is left on Admin.

`/kb-articles` Step 0 checks the extension on every run (ToolSearch
`select:mcp__claude-in-chrome__tabs_context_mcp`, then `tabs_context_mcp`) and
asks the user to install, connect and sign in only when that fails.

## Flow

```bash
$KB_PY $K/kb_capture.py plan-calls --plan $RUN/groups.json --inputs $RUN/inputs.json --out-dir $RUN/shots
# Task: medtrics-release-notes:kb-capturer (Haiku)  →  $RUN/shots/raw/<batch_id>.json
$KB_PY $K/kb_capture.py ingest --calls $RUN/shots/calls.json --raw-dir $RUN/shots/raw --out-dir $RUN/shots
```

1. **plan-calls** (no model, no browser) writes `shots/calls.json` (the visits,
   each batch's exact `browser_batch` actions, the probe install and the
   `restore` actions) and `shots/capture.md`, the only file the capturer reads.
   Per visit: `navigate` → `computer wait 2` → `computer hover` at the cursor
   park → `javascript_tool` probe stub → `computer screenshot`. The first batch
   of a persona opens with that persona's portal entry URL. Visits with an
   empty `url` are not planned; they stay uncapturable.
2. **kb-capturer** loads the Chrome tools, uses the first tab, installs the
   probe once, sends each batch verbatim and writes one raw file per batch. It
   never types, clicks or closes tabs, and it does not read page contents.
   `KB_CAPTURE NO_CHROME_TOOLS` means the orchestrator follows `capture.md`
   itself.
3. **ingest** pairs each probe result with its screenshot, maps the probe's
   element boxes onto the image, verifies the label (OCR when tesseract is
   present, else a DOM match, which a draft accepts and a publish does not),
   runs the tenant screen and writes `shots/shots.json` and the frames. Exit 3
   means some visits were left; `shots/resume.json` holds the rest of the plan
   and the reason per `persona/surface`: `expired`, `wrong_persona`,
   `bridge_down`, `no_probe`, `retry` or `not_reached` (its batch never ran).

Run it from Claude Code on the Mac. The screenshots are saved on the Mac
(`~/.claude/projects/…/tool-results/…jpg`) and the Cowork sandbox probably
cannot read them.

## Live-test facts the design depends on

- Screenshot at **`scale: 1`** with `save_to_disk: true`. `scale` also shrinks
  the saved file (0.1 gave 147×80), so anything below 1 ruins the frame. The
  saved JPEG is exactly `innerWidth × innerHeight` CSS px (1470×802 in the test)
  and its path comes back as `[Image: source: <path>]`.
- Page rect → image px is **1:1** (saved width ÷ the probe's `iw` = 1.0), so a
  box from the probe is a box on the image. `innerHeight` changes between shots
  (802 → 746), so every probe reads `iw`/`ih` itself.
- The mouse pointer is drawn into screenshots. A `hover` at the cursor park
  (`demo.cursor_park`, `[1465, 740]`) before the shot removes it.
- The first `javascript_tool` right after `navigate` can run on the old page, so
  every visit waits 2 seconds after navigating.
- The probe is stored once per tab in `sessionStorage.kbp` and called with a
  short stub, `(0,eval)(sessionStorage.getItem('kbp'))(<args>)`. The page
  reloads on navigation but `sessionStorage` survives. A new tab has no probe
  and the stub throws `KB_NOPROBE`.
- A JS `throw` stops `browser_batch`, so `KB_LOGIN` (sign-in page or a password
  field) and `KB_PERSONA` (the active portal button is not the one asked for)
  end a batch at once. The probe returns plain JSON objects, because the
  extension can replace a result with `[BLOCKED: Base64 encoded data]`; ingest
  leaves such a visit as `retry`.
- Label matching is an exact trimmed `innerText`, visible, first match outside
  the sidebar (`demo.sidebar_selector`), else the first match anywhere.
- Demo data includes `@gmail.com` / `@sacredheart.com` addresses. Those frames
  screen BLOCK and are watermarked. The demo domain is `medtricslab.com`.

## Raw files and resume

`shots/raw/<batch_id>.json`:

```json
{"batch_id": "b01", "status": "ok|expired|wrong_persona|bridge_down|no_probe", "error": null,
 "items": [{"visit_id": "v01", "probe": {…as returned…}, "screenshot_path": "<path>"}]}
```

Items are in visit order and a visit the batch did not reach is absent. Ingest
replaces entries by `visit_id`, so it can be re-run on the same raw folder.
After a stop the command carries on with what it has: tickets whose surfaces
were not reached are drafted with missing-screenshot markers and recorded as
`partial`. Sign in again in Chrome if it was `expired`, then re-run
`/kb-articles … --retry-partial`.

## Mapping a surface (`scripts/kb/kb_routes.json`)

A surface is a **URL**, nothing more. There are no recipes: capture is the page
each URL lands on. Anything the screen needs (a tab, an academic period, a
filter) must be expressible as a URL; if it isn't, the surface is not
capturable yet and its tickets are drafted without screenshots.

```json
{"surface": "Curriculum > Setup > Migrate", "persona": "admin", "also_personas": [],
 "url": "/administration/curriculum/",
 "match": {"modules": ["Curriculum & Sessions"], "url_prefixes": ["/administration/curriculum/"],
           "template_globs": ["src/templates/curriculum/setup/*.html"]},
 "known_tickets": {"M1-1520": "Captured"}, "demo_status": "capturable"}
```

- `url: ""` → not mapped; the surface is never planned.
- `persona` is `admin` or `faculty`; faculty visits go through the Personal portal.
- `match` places NEW tickets: a URL prefix the ticket's diff mentions, a template
  path it changes, or a module only this surface carries.
- `demo` keys: `allow_strings` (the demo tenant's synthetic names, ignored by the
  tenant screen; keep it short), `domain` (`medtricslab.com`), `cursor_park`,
  `portal_entry` (per persona), `portal_label` (the button text, `Admin` /
  `Personal`), `home_portal` (`admin`, where the run ends) and
  `sidebar_selector`.

## Legacy: Playwright

`kb_capture.py login`, `check` and `run` (headless Playwright with saved
sessions) still exist in the script but `/kb-articles` no longer uses them, and
`kb_doctor.py --setup` installs Playwright only with `--with-playwright`.
