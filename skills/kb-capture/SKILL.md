---
name: kb-capture
description: Live screen capture for /kb-articles on the capture site (medtrics.medtricslab.com) through the Claude in Chrome extension, driving the owner's own Chrome where they have already signed in by hand — one account covers admin and faculty (via the Personal portal), and the run returns to Admin at the end. Each screen is recorded and exported as a GIF into Downloads, so it works in Cowork. Covers the flow (plan-calls, canary, the Haiku kb-capturer, ingest), the Downloads folder, sweep, the live-test facts the design depends on, raw files and resume, and how to map a surface in kb_routes.json (a URL only). Claude never types a password.
---

# KB Capture (Claude in Chrome on the demo tenant)

Screenshots are the slow, expensive part of a KB article. Scripts plan the
capture, a Haiku agent carries it out in the owner's Chrome without looking at
any page (it never receives an image), and scripts verify every frame and place
every ring.

## Why GIFs

In Cowork, `computer screenshot` with `save_to_disk` leaves no file the sandbox
can read. The route that works is the one Charles proved in QA: record the visit
with `gif_creator`, export it with `download: true` into the user's
`~/Downloads`, and read it through the Cowork-mounted Downloads folder. The
design (record, export to Downloads, poll until the file is stable, the
`wait_for_file` function) is adopted from Charles's medtrics-qa-automation
`qa-screenshot-capture` skill; this skill takes only the mechanism, since his is
bound to QA steps, GitLab and his plugin config. The screenshot for a visit is
the **last frame** of its GIF, taken by script at ingest.

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
asks the user to install, connect and sign in only when that fails. It then
gets the Downloads folder (below).

## Downloads folder

Step 0 (every run, except `--no-capture` and `--kb-queue`):

- **Cowork:** ToolSearch `select:mcp__cowork__request_cowork_directory`, call it
  with `path: "~/Downloads"`, and use the **returned** path as `DL`. Never cache
  it across sessions. If the user declines, ask once: approve, or continue with
  `--no-capture`.
- **Claude Code:** `DL=~/Downloads`.

Chrome must save downloads there without asking (Settings → Downloads → "Ask
where to save each file" off). GIFs land in `Downloads/kb-capture/<run_id>/`
and are moved out by ingest.

**Sweep.** `kb_capture.py sweep --downloads-dir $DL --into $RUN/shots/stale`
moves any leftover `Downloads/kb-capture/*` folders (a crashed earlier run; they
may hold tenant data) into the run's `shots/stale/` and prints what moved. It
never deletes.

## Flow

```bash
$KB_PY $K/kb_capture.py sweep --downloads-dir $DL --into $RUN/shots/stale            # Step 0
$KB_PY $K/kb_capture.py plan-calls --plan $RUN/groups.json --inputs $RUN/inputs.json --out-dir $RUN/shots
# canary: you send calls.json["canary"] in one browser_batch, then
$KB_PY $K/kb_capture.py pickup --downloads-dir $DL --name kb-capture/$RUN_ID/canary.gif --timeout 20
# Task: medtrics-release-notes:kb-capturer (Haiku)  →  $RUN/shots/raw/<batch_id>.json
$KB_PY $K/kb_capture.py ingest --calls $RUN/shots/calls.json --raw-dir $RUN/shots/raw --out-dir $RUN/shots --downloads-dir $DL
```

1. **plan-calls** (no model, no browser) writes `shots/calls.json` (the visits,
   each batch's exact `browser_batch` actions, the probe install, the `canary`,
   the `abort` actions, `gif_dir` and a `gif` filename per visit) and
   `shots/capture.md`, the only file the capturer reads. Per visit, no
   screenshot:
   `gif_creator start_recording` → `navigate` → `computer wait W` →
   `computer hover` at the cursor park → `javascript_tool` probe stub →
   `gif_creator stop_recording` → `gif_creator export` with `download: true` and
   `filename: kb-capture/<run_id>/<visit_id>.gif`. The first batch of a persona
   opens with that persona's portal entry URL, outside any recording. Visits
   with an empty `url` are not planned; they stay uncapturable.
2. **Settle wait.** `W` is the surface's `wait_s` in `kb_routes.json` if it has
   one, else `demo.settle_s` (default **4**), at most 10. Slow pages (Users >
   Groups) need 4 to 5 seconds; 2 showed "Loading…". The last frame, after the
   hover, is the settled page with no cursor.
3. **Export options:** `{showWatermark: false, showProgressBar: false,
   showActionLabels: false, showClickIndicators: false, showDragPaths: false,
   quality: 1}`. Frames are the viewport in CSS px (1470×802 in the test),
   130 to 600 KB per GIF.
4. **Canary** (run by the orchestrator before the capturer, owner present): one
   throwaway recording of the first persona's portal entry exported as
   `kb-capture/<run_id>/canary.gif`, then `pickup` waits for it. Exit 0 = ok
   (prints the frame size, deletes the canary); 1 `download_missing`; 2
   `empty_export`; 3 `bad_gif`. A failure skips capture with that reason (Chrome
   download settings, a privacy or download prompt) and the drafts carry
   missing-screenshot markers.
5. **kb-capturer** loads the Chrome tools (including `gif_creator`), uses the
   first tab, installs the probe once, sends each batch verbatim and writes one
   raw file per batch. It never types, clicks or closes tabs, never sees an
   image and does not read page contents. After any stop rule it sends the
   `abort` actions (a `gif_creator` clear) so a half recording is never exported
   later, except when the bridge is down.
   `KB_CAPTURE NO_CHROME_TOOLS` means the orchestrator follows `capture.md`
   itself.
6. **ingest** pairs each probe result with its GIF: `D/<gif>` is awaited until
   its size is stable over two polls (20 s for the first file, 5 s for the rest),
   **moved** to `shots/gifs/<visit_id>.gif`, and its last composited frame
   written to `shots/frames/<visit_id>.png`. Then, as before: map the probe's
   element boxes onto the frame, verify the label (OCR when tesseract is
   present, else a DOM match, which a draft accepts and a publish does not), run
   the tenant screen, write `shots/shots.json`. Entries carry
   `frame_source: gif|disk`. Empty `D/kb-capture/<run_id>` (and `D/kb-capture`)
   are removed afterwards. A 0.23.0 raw file with a readable `screenshot_path`
   still ingests the old way. Exit 3 means some visits were left;
   `shots/resume.json` holds the rest of the plan and the reason per
   `persona/surface`: `expired`, `wrong_persona`, `bridge_down`, `no_probe`,
   `retry`, `not_reached` (its batch never ran), `download_missing` (no GIF in
   Downloads), `empty_export` (a zero-byte GIF) or `bad_gif` (not readable as an
   image).

## Live-test facts the design depends on

- `gif_creator` works inside `browser_batch`. `start_recording` clears previous
  frames and `export` clears the recording, so no `clear` is needed between
  visits; back-to-back visits in one batch export cleanly.
- Exported frames are the viewport in CSS px, even with no screenshot taken, so
  frame px = page px (1:1), at least the 1200 px width the gates need.
  `innerHeight` changes between visits (802, 746), so every probe reads
  `iw`/`ih` itself.
- No screenshot is needed: `navigate`, `wait` and `hover` produce frames.
  `javascript_tool` produces none, and a thrown error stops the batch before the
  export.
- `filename: kb-capture/<run>/<vid>.gif` creates the subfolder under Downloads.
  Four or five exports in a row raise no "multiple downloads" prompt or save
  dialog.
- The mouse pointer would be drawn into frames. A `hover` at the cursor park
  (`demo.cursor_park`, `[1465, 740]`) before the export removes it.
- The first `javascript_tool` right after `navigate` can run on the old page, so
  every visit waits after navigating (the settle wait above).
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
- Not testable from Claude Code: the Cowork mount call itself. The canary is the
  first-run check that the whole route works.

## Raw files and resume

`shots/raw/<batch_id>.json`:

```json
{"batch_id": "b01", "status": "ok|expired|wrong_persona|bridge_down|no_probe", "error": null,
 "items": [{"visit_id": "v01", "probe": {…as returned…}, "gif": "kb-capture/<run>/v01.gif"}]}
```

`gif` is the filename from the export result's `Downloaded "…"` text. Items are
in visit order and a visit the batch did not reach is absent. Ingest replaces
entries by `visit_id`, so it can be re-run on the same raw folder (a GIF already
moved to `shots/gifs/` is not looked for in Downloads again). After a stop the
command carries on with what it has: tickets whose surfaces were not reached are
drafted with missing-screenshot markers and recorded as `partial`. Sign in again
in Chrome if it was `expired`, then re-run `/kb-articles … --retry-partial`.

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
- `wait_s` (optional, per surface): seconds to wait after navigate before the
  frame, for slow pages; otherwise `demo.settle_s` (default 4), at most 10.
- `demo` keys: `allow_strings` (the demo tenant's synthetic names, ignored by the
  tenant screen; keep it short), `domain` (`medtricslab.com`), `cursor_park`,
  `portal_entry` (per persona), `portal_label` (the button text, `Admin` /
  `Personal`), `home_portal` (`admin`, where the run ends) and
  `sidebar_selector`.

## Legacy: Playwright

`kb_capture.py login`, `check` and `run` (headless Playwright with saved
sessions) still exist in the script but `/kb-articles` no longer uses them, and
`kb_doctor.py --setup` installs Playwright only with `--with-playwright`.
