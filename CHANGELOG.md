# Changelog

All notable changes to `medtrics-release-notes` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.28.0] - 2026-10-29

From the fourth Cowork run (M1-465: 2 of 3 screenshots, red "NOT FOR
PUBLICATION" banners, form fill failed on a guessed field label) and a
challenge review before building.

### Changed
- **The .docx is the client copy for Intercom.** No banner or frame, no
  "Generated from" line, no `CX-DRAFT_` file prefix, no missing-screenshot
  marker (a step without a screenshot keeps its text). Before rendering, the
  owner OKs screenshots on a numbered **contact sheet**
  (`kb_package.py contact-sheet` / `confirm`); BLOCK frames and anything not
  OK'd stay out. The writer never points at an image ("as shown below").
- **Form fills are planned from the real form**, by script (no model): capture
  reads the form's fields; date → far-future weekday, times 09:00–10:00,
  required selects → first "ZZ DEMO" option, optional fields empty. The
  `kb-data-planner` agent is gone.
- **Real demo records, in two steps.** The first run of a form is a dry run
  (all writes blocked) that learns the dialog's buttons and the exact request;
  later runs save, letting only that one request through, once, after the
  dialog appears, on the "No, don't send…" answer — never on Save. Flows and
  records live in the Mem "KB Demo Data" collection with a delete-before date;
  `/kb-articles demo-data` lists live records, `demo-data done` marks the ones
  you deleted. Claude never deletes data.
- **Estimated $ cost** per agent call, per ticket and per run (Haiku
  $0.10/$0.50, Sonnet $2/$10, Opus $4/$20 per 1M input/output tokens, 85 %
  input assumed; main-chat tokens not included) in the end table, RUN-SUMMARY,
  run.json and the Mem ledger/run notes.

### Safety (review + attack test before release)
- The save allowance is armed only by a real click on the exact checked
  button inside the open dialog, for 10 s, for one request with the learned
  method, path and query; a failed or blocked save is logged `not_saved`, never
  as a live record; the learned request must be the one new write after the
  answer click; reuse ignores dates (no daily duplicates).
- Hardened against hostile page code: no readable reference to the browser's
  raw request functions, XHR method/URL kept inside the guard, the guard's own
  clock, arming only through a per-run token (`<shots>/.arm_token`, shared by
  capture and the form-fill brief). An in-page guard still can't be airtight
  against deliberately hostile same-page code (documented in the kb-capture
  skill).
- Contact-sheet OKs are tied to each image's hash; re-packaging clears old files.

### Fixed
- The step badge sits outside the ring (it covered "Sessions").
- Ingest no longer crashes on a label outside the frame.
- The demo's own name ("School of Medicine") is an allow-string; the tenant
  check now reports the words it matched.

## [0.27.0] - 2026-10-29

From the third Cowork run (M1-465 reached the right page — the data was there —
but no row could be opened) and a challenge review of the plan.

### Added
- **Write guard.** The page script installs, on every probe and click, a guard
  that blocks every non-GET request (fetch, XHR, sendBeacon, form submit) and
  counts what it blocked. Nothing a run does can store anything on the site.
- **Form fills (approved each run, nothing saved).** For screens that only
  appear after a save, `kb_data.py bundle` → one `kb-data-planner` (Sonnet) call
  → `kb_data.py check` (cancel-type choices only — never Yes/OK/Continue;
  far-future dates, never tomorrow; audience/venue picked by rule from "ZZ
  DEMO" options; no personal data) → you approve the exact list (always asked,
  even with `--yes`; approval is fingerprinted, a changed plan is refused) →
  `kb-data-maker` (Sonnet) fills the form QA-style (value re-read after every
  field), presses Save under the guard, captures the dialog, clicks Cancel/No.
- **Sign-in groups.** Capture runs admin + faculty first, then pauses for you to
  sign in as the learner, then uses the external evaluator link you give
  (kept only in the run folder), then asks you to sign back in as admin.
  `kb-capturer --from-batch` resumes after each pause.
- `kbopt` page helper for dropdown options; hook lets the form-fill agent type
  and press only Tab / Escape (the capturer still can't type).

### Fixed
- **Vue list rows.** Rows that are clickable `<tr>`s without links (the new
  Sessions page) are now opened; a real row is preferred over a course group.
- **Vue buttons.** "Add schedule"-style buttons are allowed on Vue pages (the
  write guard makes the old toggle-only rule unnecessary).
- **Labels per screen.** Each screen rings the button you press next (the first
  screen rings the menu item); a dialog with nothing else to ring gets its
  title ringed (draft-only anchor).
- **Page check** now `await`s its result (it returned nothing before) and runs
  before planning (Step 3b).
- Email-template text is no longer used for labels.

### Safety (from an adversarial review of this release)
- The write guard is installed before every form fill, blocks an XHR opened
  before install, blocks WebSocket sends and covers same-origin iframes.
- The hook now also checks scripts (only the plugin's own `/*KB*/` scripts run)
  and navigation (demo site only, no delete/logout/portal-switch paths beyond
  the portal entries), and refuses typed newlines/tabs.
- More refused click words (notify, invite, resend, copy, duplicate, remind,
  clone, reset, unpublish, reactivate, enroll); form-fill choices must be
  cancel-type with no yes/save/confirm/continue anywhere; a form fill's start
  must be its ticket's checked page; approval requires the checked plan.
- Known limit: a `fetch` reference page code saved before the guard is not
  covered (the legacy app uses XHR, which is); the hook does not apply when the
  main chat follows `capture.md` itself (the page guard still does).

## [0.26.1] - 2026-10-29

Fixes from the second Cowork run (M1-465: the ticket's link was a 404 page).

### Fixed
- **Stale ticket links.** The route now comes first from the MR's QA checklist
  (`<branch>-manual-qa-checklist.txt`, attached by the medtrics repo's
  `sk-manual-qa` skill and run by QA automation), which names the page and UI
  path as they are in the code — e.g. M1-465 →
  `/vadministration/curriculum/sessions/` → first session → "Add schedule".
  New `kb_checklist.py attach` fetches it (read-only); the ticket's own links
  are the fallback.
- **404 pages treated as the page.** Before capture, one `route-check-js` call
  GETs each start page (and its `/administration` ↔ `/vadministration` twin);
  `route-fix` moves a 404 to the working twin or marks it `page_not_found`.
  The probe also flags a 404 page as a safety net.
- **Demo data blocked the first-row click.** "ZZ DEMO" / "ZZDEMO" are demo
  allow-strings, and only a hard (BLOCK) tenant verdict stops a row click.
- **Email text as labels.** Labels come from the checklist's button names and
  the changed templates; acceptance-criteria quotes count only when the same
  string is in the changed UI code.
- **LibreOffice leftovers.** PDFs are converted in a temp folder and only the
  PDF is copied out (Cowork refuses deletes in the selected folder).

## [0.26.0] - 2026-10-28

Fixes from the first Cowork run (M1-1596, M1-465, M1-723: every article came
back with 0 screenshots although every GIF arrived).

### Fixed
- **Wrong page.** The URL rule matched a surface whenever its path word
  (`resources`, `users`, `reports`) appeared anywhere in the diff. It now only
  matches a URL pattern added in a `urls.py` file.
- **Unseeable labels.** Code identifiers (CamelCase, UPPER_SNAKE) and email copy
  are no longer sent to the page probe; labels come from templates, `_()` /
  gettext and on-screen quotes only.
- **Cowork paths.** Setup no longer stores an absolute Python path (it pointed
  at another chat's `/sessions/<name>/`); `KB_PY` is worked out every run. Run
  files moved from `/tmp/kb` (which agents can't read in Cowork) to
  `<folder>/.kb-runs/<run_id>/`, which ignores itself in git.
- **Ledger.** Mem's trailing hard-break `\` is stripped from every note field,
  so existing notes are updated instead of duplicated and a ticket never lists
  itself as its own prior article.
- LibreOffice lock and temp files are removed from the output folders.

### Added
- **The ticket's own route** (`scripts/kb/kb_route.py`): the start page and
  click path from the ticket's "URL/Page" field, the release checklist's
  "Which portal and URL", or a QA checklist — host dropped, record-id links
  refused. Personal-portal (`/my/`) pages use the faculty persona. The fixed
  `kb_routes.json` map is now a fallback.
- **Guarded clicks, one GIF per click step** (the way Charles's QA automation
  walks a ticket). Each target is picked by the `kbclick` page script, which
  refuses submit buttons, POST forms, delete/remove/toggle/portal-switch links,
  red buttons, write words and buttons that are not a modal/tab toggle; a
  first-row click happens only when the page shows no tenant data. Steps behind
  a save are described in words (`needs_write`). New reasons: `click_refused`,
  `click_not_found`, `tenant_row`, `needs_write`, `no_screen` (email-only
  changes get a note, not a missing-screenshot marker).
- **Plugin hook** `hooks/kb_guard.py`: blocks typing, key presses, drags and
  form filling for the `kb-capturer` agent only (allow by default for
  everything else).
- `kb_capture.py cleanup` — deletes the run's GIFs (and the canary) from
  Downloads after you say yes at the end of the run.
- `scripts/kb/kb_paths.py` — the folder config
  (`.medtrics-release-notes/config.json`: setup status, Mem collection ids, `.env`
  key names; no absolute paths), `ensure`, `env-check`, `merge`.

### Changed
- **`/release-note-setup`** now follows the QA plugin's setup, lighter: install,
  Chrome check, `.env` key check, find or create the KB Mem collections and
  save their ids, approve Downloads (Cowork), a ready / to-do table. Re-running
  only redoes what is missing.
- Ingest **copies** GIFs out of Downloads (moving needed a Cowork delete
  approval every run); the canary also stays until the cleanup.
- Named tickets (`/kb-articles M1-1596`) start without the y/n; `pending`,
  `since` and the release hand-off still ask.
- If the Haiku capture agent can't drive Chrome, the main chat follows the
  same capture file itself.

### Verified
- Cowork check (`/kb-spike`): a Haiku subagent drives Claude in Chrome and
  reads/writes the selected folder; a plugin PreToolUse hook fires in the main
  chat and in subagents and its deny is honoured.

## [0.25.0] - 2026-10-28

### Added
- **`/release-note-setup`** — one command that sets the plugin up in a folder,
  once. Today it has one part, `kb`: installs the Python packages into
  `.kb-env/`, tries tesseract / LibreOffice / the Carlito font, checks the
  Claude in Chrome extension, and ends with a ready / to-do table. More parts
  are added as new sections. `/release-note-setup kb` runs only that part;
  `--force` retries everything.
- `kb_doctor.py --check` — reads `.kb-env/kb-setup.json` and confirms the
  pieces are still on disk; installs nothing; exit 2 when setup is missing.

### Changed
- `/kb-articles` no longer installs anything. Step 0 runs `kb_doctor.py
  --check` and, if setup was never run here or the folder was reset, stops
  with "run /release-note-setup, then re-run".

## [0.24.0] - 2026-10-28

### Changed
- **Screenshots now come from GIF recordings, so capture works in Cowork.**
  In Cowork, `computer screenshot save_to_disk` leaves no file the sandbox can
  read. This adopts the mechanism of Charles's `medtrics-qa-automation`
  `qa-screenshot-capture` skill (credit to him): each screen is recorded with
  `gif_creator`, exported with `download:true` into
  `~/Downloads/kb-capture/<run>/<visit>.gif` (all overlays off, quality 1), and
  read through the Downloads folder — mounted with
  `mcp__cowork__request_cowork_directory` in Cowork (path taken from the mount
  response every run), `~/Downloads` in Claude Code.
  - Per screen: start → navigate → wait (`demo.settle_s` = 4, per-surface
    `wait_s`) → park cursor → probe → stop → export. **No screenshot**: the last
    GIF frame is the settled page at full viewport size (1470×802), so the
    Haiku capturer never receives an image.
  - Step 0 asks for the Downloads folder and sweeps leftover `kb-capture/`
    folders into the run (never deleted). Step 5 runs a canary GIF + `pickup`
    while you're present, then the capturer, then `ingest --downloads-dir`,
    which waits for each file (Charles's `wait_for_file`, vendored with
    credit), moves it into `shots/gifs/`, takes the last frame as PNG and keeps
    the `shots.json` contract (`frame_source: gif`). New left reasons:
    `download_missing`, `empty_export`, `bad_gif`.
  - 0.23.0 raw files with `screenshot_path` still ingest.

### Fixed
- Rings next to the page edge were cut off; they now stay inside the frame.

### Verified live (owner's Chrome, Claude Code on the Mac)
- `gif_creator` inside `browser_batch`; exports 1470×802 with no screenshot
  taken; subfolder filenames; 5 exports in a row with no Chrome prompt; canary
  pickup; Haiku capturer → 3 screens → ingest moved all GIFs out of Downloads,
  6/6 labels verified, rings on the right controls. The Cowork mount call itself
  is not testable from Claude Code and is unverified.

## [0.23.0] - 2026-10-28

### Changed
- **Screenshots now come from your own Chrome through the Claude in Chrome
  extension**, where you are already signed in. Claude never types a password;
  `.env-kb-login` and the Playwright sign-in are no longer used. Flow:
  `kb_capture.py plan-calls` writes `capture.md` → the new **`kb-capturer`**
  agent (Haiku) runs it in your Chrome (navigate, read label positions, full-size
  screenshot; never clicks, types or closes tabs) → `kb_capture.py ingest`
  builds the unchanged `shots.json`. One account covers admin and faculty
  (Personal portal); the run returns to Admin at the end.
- **First run asks you to install and connect Claude in Chrome** when the
  extension isn't available (or continue with `--no-capture`).
- `kb_doctor.py --setup` no longer installs Playwright/Chromium unless
  `--with-playwright` is passed. The Playwright `login/check/run` code stays as
  unused legacy.

### Fixed
- The tenant-data check's "tenant slug" pattern matched ordinary words —
  `schedule` (s-ch-edu-le), `named`, `informed`, `latest` — so every page with the
  Schedule button screened BLOCK. A shared stop-list now covers both the Python
  and the in-page check. "Medtrics School of Medicine" added to the demo allowlist.

### Verified live (2026-10-28, owner's Chrome)
- A Haiku capturer copied the 3.9 KB install script and both batches exactly;
  4 screens captured (3 admin, 1 faculty via the Personal portal), 8/8 labels
  found, rings land on the right element.

## [0.22.3] - 2026-10-26

### Added
- **`/kb-articles --kb-queue <ids>`** — adds tickets straight to the KB Queue
  (no capture, no agents, no GitLab) and prints one row per id: queued, already
  queued, or why not. `/kb-articles pending` drafts them later. Backed by
  `gather_notes_data_optimus.py --no-mrs` (Optimus only) and
  `kb_ledger.py enqueue --ids`.
- **Sign-in from `.env-kb-login`.** `kb_capture.py login --env-file` fills the
  capture site's form (`KB_<PERSONA>_EMAIL` / `_PASSWORD`, or `KB_EMAIL` /
  `KB_PASSWORD`) headless and saves the session. Run by a person or their
  scheduler — never by `/kb-articles`. A missing file is reported as an issue at
  Step 0 (`kb_doctor.py --setup`). The file is gitignored and never bundled.
- **End-of-run table for every ticket the run was given** (in chat and at the
  top of `RUN-SUMMARY.md`): done with its files and screenshot status, or not
  done and why — code missing, not a feature, not found in Optimus, already has
  an article, or the error (`kb_package.py --requested`).

### Changed
- Capture site is now `https://medtrics.medtricslab.com` (sign-in at
  `/users/login/`); the surface paths in `kb_routes.json` are unchanged.
- A feature with no merge request is skipped as "code missing" and the run
  continues (`--allow-no-code` drafts it anyway); an id Optimus doesn't return is
  listed as "not found in Optimus" instead of silently vanishing (including when
  Optimus's search falls back to a different ticket).
- `gather_notes_data_optimus.py --task-types` records the tickets it dropped
  (`dropped` in the output) so callers can say why.

## [0.22.2] - 2026-10-26

### Fixed
- **Every ticket looked typeless to `/kb-articles`.** The Optimus integration
  API carries the classification as `product_type` (e.g. M1-1609 →
  `feature`), but `gather_notes_data_optimus.py` only read `type` /
  `task_type`, so the features-only filters (`--task-types`, `kb_select.py
  filter`, `kb_ledger.py enqueue`) dropped everything as `type: unknown`. It now
  reads `product_type` first, keeping the older names as fallbacks.

## [0.22.1] - 2026-10-26

### Added
- **One-time automatic setup for `/kb-articles`.** Step 0 now runs
  `kb_doctor.py --setup` in the folder selected in Cowork. The first run creates
  `.kb-env/` there, installs what is missing (python-docx, Pillow, numpy, pypdf,
  pytesseract, Playwright and its Chromium into `.kb-env/`; tesseract,
  LibreOffice and the Carlito font via apt-get or Homebrew when allowed), and
  records the result in `.kb-env/kb-setup.json`. Later runs read that file and
  only confirm the installed files are still there (~0.2 s, no imports, no
  network). Pieces that could not be installed are recorded and not retried
  until `--setup --force`; pieces that vanished (reset sandbox) are reinstalled
  alone. Every KB script runs with the printed `KB_PY`.
- `.kb-env/` is gitignored and excluded from the bundle.

## [0.22.0] - 2026-10-26

### Added
- **`/release-slack --kb`** — after the post and its Mem archive note (new
  Step 9c), the release's feature tickets go on the Mem **KB Queue** with the
  client title and summary the release wrote, and the same chat hands exactly
  those tickets to `/kb-articles <ids> --from-release`. It reuses
  `/tmp/release_slack_data.json` and `/tmp/release_slack_summaries.txt` (no
  second Optimus/GitLab fetch), shows its plan and asks y/n. "n" leaves them
  queued. `--kb` never delays or fails the release; `dry-run --kb` only reports
  what would be queued. `/notes` is unchanged.
- `kb_ledger.py`: the queue — `enqueue`, `drain`, `queue-summaries`, and
  `pending --queue-notes` (queue ∪ released-without-article). Queue notes are
  marked `drained` after a run, never deleted.
- `kb_select.py filter --ids` so the hand-off drafts only the ids asked for.

### Changed
- `/kb-articles` takes a bare ticket list: `/kb-articles M1-1520,M1-1256`
  (no `tickets` keyword); `since <date>` and `pending` are unchanged.
- Prose retries run round 1 on Haiku and round 2 on Sonnet; first drafts stay
  on Sonnet.

## [0.21.0] - 2026-10-26

### Added
- **`/kb-articles`** — one Intercom KB article draft (.docx + PDF + Intercom
  markdown) per shipped legacy **feature** ticket, in the Medtrics KB format
  (`medtrics-kb-article/1.1`). Modes `<ids>` (a bare comma-separated list), `since <date>` (from the
  Mem release archive) and `pending`; flags `dry-run`, `--force`,
  `--retry-partial`, `--no-capture`, `--yes`. Plan: `KB-PLAN.md` (v1).
- **`scripts/kb/`** — the KB format standard brought into the plugin
  (`kb_format.py`, `kb_validate.py` gates G1–G7, `build_kb_doc.py`,
  `qa_gif_to_shots.py`, `qa_annotate.py`) plus the pipeline: `kb_select.py`
  (feature filter, rule placement, persona × surface grouping, ≤ 8 tickets per
  writer group), `kb_inputs.py` (trimmed writer inputs: AC, added UI strings,
  routes — never full diffs), `kb_capture.py` (Playwright on the demo tenant with
  per-persona saved sessions a person signs in to; DOM box anchors; exit 3 +
  `resume.json` on expiry), `kb_spec.py` (writer bundles, assembly, annotation,
  evidence-vs-prose failure split, single-article retries), `kb_pdf.py` (one
  LibreOffice batch + pypdf verification), `kb_package.py` (output folder,
  `RUN-SUMMARY.md`, zip, `run.json`), `kb_ledger.py` (Mem ledger/catalogue notes,
  `todo`/`since`/`pending`, prior-article matching, one-time seeds),
  `kb_usage.py` (token apportioning), `kb_doctor.py` (dependency check), and
  `kb_routes.json` (56 surfaces seeded from the October hand-run backlog).
- **`agents/`** — the plugin's first agents, each with a pinned model:
  `kb-writer` (Sonnet; Read + Write, used only on `/tmp/kb/**`) and
  `kb-classifier` (Haiku; Read).
- Skills **`kb-ledger`** and **`kb-capture`**.
- `gather_notes_data_optimus.py`: `--task-types` drops other task types before
  any GitLab lookup; each issue now carries `module` (additive).
- Tests: `test_kb_validate.py` (one deliberate mutation per gate rule),
  `test_kb_ledger.py`, `test_kb_pipeline.py`; `test_build_plugin.py` extended.

### Changed (relative to the hand-run KB code in `extra/`)
- Draft renders no longer print the provenance appendix unless `--provenance`
  is passed; `build_kb_doc.py provenance` writes it as a sidecar.
- New `dom_box` anchor, accepted in drafts only, for environments without
  tesseract (G3 refuses it on publish). G7 refuses a publish with a missing
  screenshot; drafts print `[Screenshot missing — <reason>]`.
- Rings are orange per `kb_format.py` with no caption bar; red is only the
  watermark. Badges near the left edge snap onto the sidebar. Fonts fall back
  through `KB_FONT` → DejaVu → Arial → Pillow's scalable default.
- Tenant screen: demo allowlist, DOM-text screening, worst-of merge; course
  codes are now matched on the original-case text (they never matched before
  because the OCR text was lowercased first).
- `find_label_box` no longer needs pandas.

### Build
- `extra/`, `.kb-sessions/` and `KB-PLAN.md` are excluded from the bundle and
  asserted absent; `scripts/kb/kb_format.py` and `agents/kb-writer.md` are
  asserted present.

## [0.20.0] - 2026-09-03

### Changed
- **Mem is now the only release archive — for both `/release-slack` and
  `/oracle-release`.** The 0.19.0 Mem note was optional and sat beside a
  markdown transcript on disk; in practice the Mem step was skipped on every run
  while the disk copy was always written. Three things change:
  1. **Mem is required.** Step 1 of both commands now probes for the Mem MCP
     together with the `.env` key check and **hard-stops** before any Optimus /
     GitLab / GitHub / Slack call when it is absent (`dry-run` included):
     `Mem MCP not connected — this command archives every release to Mem and
     will not run without it.` The developer directory and PR overrides stay
     best-effort.
  2. **The note is the full archive.** `release_archive.py record` takes the
     posted message files (`--root-file`, `--reply-files` in post order) and
     appends them verbatim under a `## Posted message` heading (`### Root`,
     `### Reply n`, each in a `~~~` fence so Slack's own ``` blocks survive) after
     the unchanged trace header and item bullets. The parser reads header fields
     and bullets only up to that heading and only fenced sections after it, so a
     bullet-like line inside the message can never become an item; 0.19.0
     trace-only notes still parse (empty transcript). Result block gains
     `replies`; the advisory line now reads `rendered note for ts … (N item(s),
     root + R replies)`. A missing root or reply file is exit 1 — never a
     partial transcript.
  3. **Resume runs archive.** The gate is "the post is complete" rather than
     "everything posted in this session": when a partial failure is resumed with
     `--thread-ts` and the last reply posts, that run writes the note using the
     passed `ts` and the same `/tmp` files.
  On a failed write (collection unresolvable/ambiguous, or `create_note` failing
  after one immediate retry) the command prints the **whole rendered note in
  chat** for a manual paste — never to disk, never a re-post. The
  `release-archive` skill, both command files (Output, `.env` table, Step 1,
  Step 9 / 11 post-success, Step 9b / 11b, failure-mode rows, non-goals, coding
  standards), README, and `COLLECTION_DESCRIPTION` are rewritten accordingly.

### Removed
- **The markdown transcript copies on disk.** `/release-slack` no longer writes
  `.claude/outputs/notes/release-slack/release-slack-<date>.md` and
  `/oracle-release` no longer writes
  `.claude/outputs/notes/oracle/oracle-release-<date>.md`; the `mkdir` step and
  the `Bash(mkdir:*)` permission both commands carried for it are gone. Existing
  files in those two directories are left in place for you to delete.
  `/deploy-notes`, `/integration-notes`, and `/notes` still write their markdown
  deliverables to `.claude/outputs/notes/deploy/` and `integration/` — they
  produce documents, not archives, and are unchanged.

## [0.19.0] - 2026-09-02

### Added
- **Optional Mem release archive for ticket→release trace** (`release-archive`
  skill + `scripts/release_archive.py`). After every **fully successful**
  `/release-slack` or `/oracle-release` post (root and every thread reply, same
  session — a resume run does not archive), the command writes **one Mem note
  per release session** — never one per ticket — into a collection whose
  **name the user sets** in `.env` via the new optional key
  `MEM_RELEASE_ARCHIVE_COLLECTION` (process env overrides; unset or blank →
  `Release Notes Archive`). The collection is resolved deterministically by
  paginated `list_collections` (exact-case → oldest `created_at` → refuse and
  warn when still ambiguous) and **created under that exact name if absent**;
  notes are written by resolved UUID, never through `create_note`'s
  `collection_titles` shortcut. Each note (`Release: <date> · <product> ·
  <channel>`) carries `Date`, `Product` (`medtrics` | `oracle`), `Channel`,
  `Channel id`, `Root ts`, `Permalink`, `Item count`, a flat `Tickets:` list
  (the load-bearing line — a Mem search for `ORC-232` or `M1-1457` lands on the
  release that shipped it), `Clients`, and one bullet per item (`id ·
  CLIENT_TITLE · CATEGORY · clients · every PR/MR URL, primary-first`). The
  ticket list is read from the **summaries file** (`/tmp/{prefix}_summaries.txt`,
  the same `---`-separated blocks `internal-storyteller` parses, with the
  `REFACTOR` / malformed drop rule applied) so it is exactly the posted set with
  client-facing titles; the data JSON only backfills `url` / code links.
  `--date` is the value the command used for the Slack header, so the archive
  can never disagree with the post around midnight. New `slack_upload.py`
  mode `--get-permalink --channel <C> --ts <ts>` performs the one
  `chat.getPermalink` read (existing token resolution and read-retry path;
  result block gains a `permalink` field); on failure the `Permalink:` line is
  **omitted**, never constructed. `/release-slack` Step 9 now captures the
  resolved `channel` id from the result block (as `/oracle-release` already
  did); new post-success **Step 9b** / **Step 11b** run the archive. Duplicates
  are tolerated: a re-post in a fresh session yields a second note plus a
  reminder to trash the older one — no collection read, no update-in-place.
  Renaming the key later forks the store (documented; `release_archive.py
  pending` prints the resolved name every run). Same failure posture as
  `dev-directory` / `pr-overrides`: Mem-optional, one-line notices, strictly
  after the post confirmation, **never a hard error, never blocks the post**.
  Unlike those two skills this one deliberately stores ticket ids, client-facing
  titles, categories, and client codes (that is the trace); it never stores
  internal summaries, developer identities, or the composed mrkdwn. Deterministic
  rendering, `.env` resolution, and parsing are unit-tested in
  `tests/test_release_archive.py`; the permalink mode in `tests/test_slack_upload.py`.

## [0.18.1] - 2026-09-02

### Changed
- **Ticket identifier is now joined to its title with a middle dot (` · `) instead
  of a hyphen (` - `) on every title line** — both the root-index entries and the
  thread-reply card titles, for `/release-slack` and `/oracle-release` (which share
  the `internal-storyteller` composer). A card now reads `*1. M1-1457 · Average
  score shown on completed evaluations*`. The hyphen read ambiguously against the
  hyphen inside the identifier (`M1-1457 - …`); the middle dot makes the ticket a
  distinct, scannable element. This also realigns `internal-storyteller` with the
  rest of the system, which was already built on ` · `: the IP-guard exemption
  (`ip_guard.py` `MRKDWN_INDEX_LINE_RE`) and the `slack_lint` fixtures both already
  expected the middle-dot form. Root and reply separators stay identical to each
  other. No functional change to composition, ordering, or caps. Updated in
  `internal-storyteller`, `engineering-to-client`, and the `/release-slack` and
  `/oracle-release` command docs.

## [0.18.0] - 2026-09-02

### Added
- **PR discovery v2 for `/oracle-release`: every PR, a primary, a decision.**
  `gather_notes_data_oracle.py` still runs one PR-list sweep per run plus the
  search secondary, but now keeps **every** matching PR in `mrs[]` ordered
  **primary-first** and stamps each ticket with a `pr_discovery` block —
  `{decision, primary, candidates, override, notes}`. The primary rule
  (`choose_primary`, pure): one open PR → it; none open → newest `merged_at`;
  two or more open → the single non-draft, else `flagged_ambiguous` (all
  open PRs listed as `candidates`; posting is **not** blocked). Decisions are
  `override | matched | flagged_ambiguous | no_pr_yet | no_pr_in_window`; the
  last fires when the ~500-PR sweep window was full and the Optimus ticket
  predates the oldest swept PR, so an older ticket is labeled distinctly from
  "no PR yet". Each `mrs[]` entry gains `number`, `branch`, `state`, `draft`,
  `merged_at`, `updated_at`, and `matched_via` (`branch` | `title` | `manual`)
  — additive; every classic key is unchanged, so `prepare_note_prompts.py`,
  `internal-storyteller`, and Step 4's `pr_links` keep working as they are.
- **Top-level `pr_discovery` in the gathered JSON** — `swept_at`, `sweep`
  (`pr_count`, `pages`, `window_full`, `oldest_updated_at`, `failed`),
  `overrides_loaded`, a count per decision, and every warning verbatim
  (including the unchanged `no PRs found for ORC-… — it will appear without
  code context`). The command's preview reads PR-discovery results from the
  file instead of scraping stderr, and a failed sweep is reported as "Sweep
  failed", never as "no PRs". `github_paginate` accepts an optional `stats`
  out-dict; `search_prs_for_issue` records a failed search as a warning.
- **Optional Mem PR overrides** (`pr-overrides` skill +
  `scripts/pr_overrides.py`). One Mem collection, `Oracle PR Overrides`, one
  note per ORC ticket (`Ticket`, `PR`, `PR URL`, `Resolved by`, `Resolved at`,
  `Reason`) written **only when a human decides** — never a cache. New
  `/oracle-release` **Step 2-pre** dumps the collection (`list_notes`) to
  `/tmp/oracle_pr_overrides.json`; the gather takes it via the new
  `--pr-overrides <path>` flag and applies it before the primary rule: the
  override always wins, its PR is taken from the sweep or fetched once by
  number (`GET /pulls/{n}`), marked `matched_via: manual`, and analyzed with
  diffs like any other PR; a 404 falls back to the sweep result with a
  warning. Duplicate notes → newest `Resolved at` wins with a warning;
  malformed notes are ignored with a warning naming the note id. New
  **Step 10b**: at the preview the user replies `ORC-232 → 415`;
  `pr_overrides.py pending` emits the Mem write (`create`, or a
  version-checked `update`), the command applies it, re-runs Step 2 and
  Steps 4–9, and previews again. Without Mem the pick still applies for the
  run through the new `--pr-override-inline ORC-232=415` flag (same code
  path, nothing persisted; inline beats Mem for the same ticket). New
  optional `.env` key `MEM_PR_OVERRIDES_COLLECTION_ID` pins the collection;
  when absent the command resolves it by title (dev-directory bootstrap) and
  prints the id to pin. `pr_overrides.py show` prints the stored picks. All
  parsing, rendering, and the primary rule are deterministic and unit-tested
  (`tests/test_pr_overrides.py`, new `PrimaryRuleTests` / `OverrideTests` /
  `WindowTests` in `tests/test_gather_oracle.py`).
- `base.task_to_issue` exposes the Optimus task's creation timestamp as
  `issue["created_at"]` (`created_at` or `createdAt`; `None` when absent, in
  which case `no_pr_in_window` never fires). Additive; the classic track's
  output is otherwise byte-identical.

### Changed
- **`/oracle-release` Step 10 preview** replaces `Tickets without PRs:` with
  a **PR discovery** block — `Overrides applied`, `Multiple open PRs (pick
  one or post as-is)`, `No PR found`, `No PR within the ~500-PR window (older
  ticket)`, `Sweep failed`, `Override warnings` — followed by *"To record a
  PR for a ticket, reply `ORC-232 → 415`"*. The dead-end "give me the PR
  number and I'll re-run the gather" text is now the real Step 10b. Failure
  table gains rows for `flagged_ambiguous`, `no_pr_in_window`, sweep failure,
  override-PR-404, malformed/duplicate notes, and Mem-not-connected. HARD
  RULES unchanged: no `github.com` fetch outside the gather script, ever.
- **Developer attribution on multi-PR Oracle tickets follows the primary.**
  Because `mrs` is now primary-first, the developer `@`-mentioned on such a
  card is the primary (or override) PR's author; previously it was the author
  of the most-recently-updated PR, whichever the sweep listed first.
- The gather's `--dry-run` table gains a `Decision` column and prints the
  sweep stats, per-decision counts, overrides loaded, and warnings.
- Parked `oracle-storyteller` index rule now names the primary PR
  (`pr_discovery.primary`; `mrs` is primary-first) instead of "first PR
  only". The skill stays parked.
- `build_plugin.py` excludes `PR-MATCHER-PLAN.md` from the bundle.

## [0.17.0] - 2026-09-02

### Added
- **Optional Mem developer directory** (`dev-directory` skill +
  `scripts/dev_directory.py`). Both `/release-slack` and `/oracle-release`
  now cache developer Slack ids in ONE Mem collection, `Release Notes
  Developer Directory` (one note per developer: name, emails, GitHub/GitLab
  login, Optimus assignee id, Slack id, resolving tier, dates). Step 3 is now
  three sub-steps: **3a** prefill from Mem (exact email → login → unique full
  name), **3b** `slack_resolve_users.py` for whoever is left, **3c** store
  every newly resolved developer back into Mem **immediately** (create or
  version-checked update). Active only when the Mem MCP is connected; every
  Mem failure degrades to the 0.16.0 resolver-only behavior with a one-line
  notice — no hard errors, no retries, never blocks the post. All parsing,
  matching, and note rendering is deterministic and unit-tested
  (`tests/test_dev_directory.py`); the command only performs the Mem calls.
- `slack_resolve_users.py` gained `--force` and now **skips pre-resolved
  issues** (a non-empty `assignee_slack_id`, e.g. from Mem) instead of
  overwriting them, stamps `assignee_slack_resolved_via`
  (`email` / `name` / `fuzzy` / `""`) on each issue it handles, and reports
  `preresolved` + a per-developer `resolutions` list in its result block.
- `prepare_note_prompts.py` surfaces `**Assignee (from directory):**` when the
  Oracle prefill knows the real name behind a GitHub login.

### Changed
- **`For clients` is now written by the `engineering-to-client` skill, inside
  the pipeline.** After the analyzers return, both commands run the skill once
  in a new *pipeline mode* over the summaries file (`/release-slack` Step 5e,
  `/oracle-release` Step 6b, before the IP guard). It adds a `CLIENT_TITLE`
  (the client-facing headline that becomes the card title and root-index
  entry; the internal Optimus `TITLE` stays in the block but is no longer
  rendered), finalizes each `EXEC_SUMMARY` as a 1–3 sentence client paragraph
  (no headline), confirms
  `CLIENTS` verbatim (`TBC` = platform-wide), and adds an optional
  `CLIENT_FLAG` on any named-client item that is still unresolved — surfaced
  at the preview as "client-specific items (send to the named client only)".
  The analyzer's own `EXEC_SUMMARY` is now an explicit draft.
- **New card layout** (both commands, via `internal-storyteller`):
  ```
  *1. ORC-232 - <client-facing headline>*
  *_For clients:_* <paragraph>
  • Ticket: ORC-232
  • Developer: <@dev> (Name)
  • Internal: <technical summary>
  • URL: <Optimus URL>
  • PRs: <links>   (MRs: on the GitLab track)
  • Clients: <codes> | TBC (platform-wide)
  ```
  The title line now carries the ticket id and the client-facing headline
  (`n. ID - <headline>`); the Oracle IP guard covers `CLIENT_TITLE` as well
  as `EXEC_SUMMARY`; `For Internal`
  became the `• Internal:` bullet; the Optimus URL moved back to its own
  `• URL:` line; `TBC` renders as `TBC (platform-wide)` per the skill's rule
  instead of a free-text scope sentence. Root-index entries are now
  `n. ID - title` to match — the developer mention moved off the root and
  lives only on each card's `• Developer:` line.

### Removed
- **`/client-copy` command.** The standalone paste-in wrapper is gone now that
  the `engineering-to-client` skill runs inside both release pipelines. The
  skill itself remains and still triggers on its own when ticket blocks are
  pasted with a request for the client-facing version.

## [0.16.0] - 2026-09-01

### Added
- **`engineering-to-client` skill + `/client-copy` command.** A standalone,
  paste-in/copy-out translator: paste one or more engineering ticket blocks
  (the `Ticket/Developer/Internal/URL/PRs/Clients` card shape) and get back
  **only** the plain-English client-facing description per item — ticket IDs,
  developer names/@-mentions, the raw `Internal:` engineering paragraph, URLs,
  and PR links are all dropped. It tightens an existing `For clients:` draft or
  writes one purely from the `Internal:` text (never inventing a capability or
  fix), bans platform-internals vocabulary, and handles the `Clients:` line
  carefully — platform-wide (`TBC`) items become general updates, while a
  specific client's name on an unresolved item is flagged for a client-specific
  note rather than broadcast. Deliverables are saved to
  `.claude/outputs/notes/client-descriptions/`. Standalone — it does not modify
  or call the `/release-slack` or `/oracle-release` pipelines.

## [0.15.0] - 2026-09-01

### Added
- **GitHub PR-author email capture (Oracle).** `gather_notes_data_oracle.py`
  now fills `mrs[].author_email` from each PR's **commit author** — specifically
  a commit whose top-level `author.login` matches the PR author, so a
  committer/rebaser (a different person) is never used — skipping
  `@users.noreply.github.com` masks and falling back to a public profile email.
  This gives the resolver an exact email for Oracle devs who commit with a real
  work email (verified against a live PR: author `charles@…` vs committer
  `marina@…` — only the author is used).
- **Fuzzy name-match tier** in `slack_resolve_users.py`. After the exact email
  and exact-name tiers, a `difflib` similarity match (default threshold `0.72`,
  `--fuzzy-threshold`; `--no-fuzzy` to disable) tags the single best directory
  name that clears the threshold AND beats the runner-up by a margin — a
  near-tie between two people is left unresolved rather than risk a wrong tag.
  The command's human preview (which shows `@Name (Author Name)`) is the safety
  gate for this lower-confidence tier.

### Changed
- Resolution is now three tiers for both commands: **exact email → exact
  unique full-name → fuzzy full-name**. The email tier reads
  `assignee_email` (classic) or `mrs[].author_email` (Oracle). `--no-fuzzy`
  reverts to the 0.14.0 exact-only behavior.

## [0.14.0] - 2026-09-01

### Added
- **`scripts/slack_resolve_users.py`** — resolves developer Slack IDs using the
  **bot token** (no Slack MCP). Per issue: `users.lookupByEmail` on the ticket's
  `assignee_email` first (exact; the classic track has emails), then a **unique,
  case-insensitive full-name match** against `users.list` as a fallback (this is
  how Oracle — which has no author email — tags by PR-author name). Conservative
  by design: ambiguous / partial / first-name-only matches are left unresolved,
  and deactivated members and bots are skipped, so it never tags the wrong
  person. Best-effort — unresolved developers render as plain names. Reuses
  `slack_upload.py`'s token loading + 429/Retry-After retry conventions.

### Changed
- **Developer @-mention resolution no longer uses a Slack MCP.** `/release-slack`
  Step 3 and `/oracle-release` Step 3 now run `slack_resolve_users.py`; the
  `slack_search_users` MCP path is removed from both commands, the
  `internal-storyteller` skill, and the README. (The *posting* path never used an
  MCP and is unchanged.) Requires two new scopes on the existing bot token:
  `users:read.email` and `users:read`.
- **Developer rendering now shows the name in parentheses after the mention** —
  `<@U02AB0913> (Chris Tokodi)` — on both the card `• Developer:` line and the
  root-index entries. Three-way: id+name → `<@ID> (Name)`; name only → `Name`;
  neither → `Unassigned`. The parenthetical is plain text outside the mention
  token (lint-legal; only the `<@U…|name>` pipe form is rejected). This reverses
  the prior "bare mention only" rule.
- **`prepare_note_prompts.py`** now also emits `**MR Links:**` (GitLab), alongside
  `**PR Links:**`. The Step-4 write-backs no longer set `assignee_slack_id`
  (the resolver script owns it); they keep `clients` + `mr_links`/`pr_links`.

## [0.13.0] - 2026-09-01

### Changed
- **Card layout revamp (both `/release-slack` and `/oracle-release`).** Via the
  shared `internal-storyteller` skill, every thread card is now:
  - `*_For clients:_*` (bold-italic) — the client-portable exec lede
  - `*_For Internal:_*` (bold-italic) — the technical summary, directly beneath
  - `• Ticket: {ID} ({URL})` — the Optimus URL is inline in parentheses (the
    standalone `• URL:` line is gone)
  - `• PRs: … | MRs: …` — one code-links line showing only the populated
    side(s) (Oracle fills PRs, release-slack fills MRs); omitted when neither
  - `• Developer: <@dev>`
  - `• Clients: {tags}`
- **Root header is now just `:rocket: *Release Notes — {date}*`** for both
  commands (dropped the `Internal` / `Oracle` product word; the product-label
  parameter is removed from the skill and both commands).
- **`/release-slack` now surfaces MR links.** A new `mr_links` field is written
  into the data JSON (joined from each MR's `web_url`) in the Step 4b write-back
  and surfaced to the analyzer via a new `**MR Links:**` line in
  `prepare_note_prompts.py` — symmetric with Oracle's `pr_links`. The
  release-slack analyzer block gains an `MR_LINKS` field (now 11 fields).
- `EXEC_SUMMARY` / `INTERNAL_SUMMARY` are pinned to a single physical line in
  both analyzers (keeps the Oracle exec-lede guard extraction reliable).

## [0.12.0] - 2026-09-01

### Changed
- **`/oracle-release` now matches `/release-slack`'s shape.** It posts ONE
  combined internal message to `#pod-oracle` as a root index plus per-ticket
  thread cards — each card leads with a client-portable `_For clients:_` exec
  lede, then the internal detail (`• Ticket`, `• Developer` `<@dev>`,
  `• Internal`, `• URL`, `• PRs`, `• Clients`). This replaces the former
  separated "safe to forward" client block + internal index design.
- **Shared composition.** Message composition moved from `oracle-storyteller`
  to the shared **`internal-storyteller`** skill, now parameterized with an
  output prefix (`oracle_release`) and a product label (`Oracle`) and given an
  optional `• PRs:` card line. `/release-slack` is unchanged by this.
- **Oracle analyzer** switched to the combined 11-field block
  (`CATEGORY` = FEATURE/ENHANCEMENT/BUG FIX/SECURITY/REFACTOR, `EXEC_SUMMARY` +
  `INTERNAL_SUMMARY`, `CLIENTS`, `PR_LINKS`); `AWARENESS` items map to
  `SECURITY` or `ENHANCEMENT`. Sentinels are `NO_VISIBLE_CHANGE` /
  `NO_INTERNAL_NOTES`.
- **PR links** are now written into the data JSON deterministically (from each
  PR's `web_url`) in the same Oracle write-back that persists `assignee_slack_id`
  and `clients`, and surfaced to the analyzer via a new `**PR Links:**` prompt
  line in `prepare_note_prompts.py` — the analyzer copies them verbatim instead
  of reconstructing URLs it never received.
- **IP guard kept as a safety net** for Oracle: it now runs over the
  `EXEC_SUMMARY` ledes only (extracted before composition, ≤3 rewrite loop), so
  internal React/Supabase/PR detail doesn't false-trip. `copy-audit` is removed
  from the Oracle flow (its rules are baked into the analyzer's exec voice).

### Notes
- `skills/oracle-storyteller/` and `scripts/assemble_oracle_root.py` are now
  **parked** — unused by any command but left in the repo (and still covered by
  `test_assemble_oracle_root.py`) in case the separated-block design is wanted
  again.

## [0.11.0] - 2026-09-01

### Fixed
- **Oracle root failed its own lint gate (could never post).** `assemble_oracle_root.build_root`
  placed the internal-index's first `*…*` header on the line directly after the
  `:lock:` reference header, which `slack_lint`'s `section_header_blank` rule
  rejects — so every `/oracle-release` root failed the mandatory lint and the
  post could never complete. Added the missing blank line, plus an
  assembler→`slack_lint` integration test.
- **`/oracle-release` developer @-mentions never rendered.** Resolved Slack IDs
  were built in memory but never written back into the data JSON, so the
  analyzer prompts never received them. Added Oracle Step 3b (persist resolved
  IDs, keyed by PR author), mirroring `/release-slack` Step 4b.
- **`/notes` deploy document skipped its confidentiality controls.** The shared
  sub-agent prompt stopped the deploy agent at the storyteller step and barred
  it from Bash, silently skipping copy-audit and the IP guard on customer-facing
  copy. Split into per-agent prompts (deploy now runs storyteller → copy-audit
  and returns markers) and moved the IP guard to the orchestrator.
- Documentation: `ip-guard` and `storyteller` skill docs no longer describe the
  retired guarded-`/release-slack` pipeline; the Slack-MCP dependency for
  @-mentions is now documented; dead `milestone`-mode references removed;
  `build_plugin.py` no longer ships itself in the bundle. Added a
  `prepare_note_prompts` test for the dev-attribution field contract.

### Changed
- **`/release-slack` is now the single combined release command.** It merges the
  former customer-facing `/release-slack` and internal `/internal-notes` into
  **one message** posted to `#dream-team` (`SLACK_CHANNEL_INTERNAL`). Each
  per-ticket thread card now carries **both voices**: a client-portable
  `_For clients:_` exec-voice lede (safe to copy to a client) on top, then the
  internal detail (`• Ticket`, `• Developer` `<@dev>`, `• Internal`, `• URL`,
  `• Clients`). The root index keeps the internal shape
  (`ID · title · <@dev>` + `Clients to notify:`). The internal team ports the
  exec lede to clients themselves; there is no separate client-channel post.
- **Per-ticket analyzer emits two summaries.** Each analyzer block now returns
  `EXEC_SUMMARY` (client-safe: no internal system names, code artifacts, file
  paths, or client institution names) and `INTERNAL_SUMMARY` (technical, keeps
  mechanism and client motivation) instead of a single `SUMMARY`.
- **`skills/internal-storyteller`** extended to render the `_For clients:_` exec
  lede at the top of each card and to read the `EXEC_SUMMARY` / `INTERNAL_SUMMARY`
  fields. A legacy `SUMMARY`-only block is still accepted (treated as
  `INTERNAL_SUMMARY`, exec lede omitted). Documented I/O paths moved to the
  `release_slack_*` prefix.

### Removed
- **`/internal-notes` command** — folded into the combined `/release-slack`.
- **`SLACK_CHANNEL_RELEASE`** is no longer used (no client-channel post). It can
  stay in `.env` harmlessly.

### Notes
- No IP guard runs in this pipeline (the message is internal by nature); the
  exec lede's client-safety is enforced upstream by the analyzer's voice rules.
- The customer-facing markdown commands (`/deploy-notes`, `/notes`,
  `/integration-notes`) and their `storyteller` / `copy-audit` / `ip-guard`
  skills are unchanged. The Oracle track (`/oracle-release`) is unchanged.

## [0.10.0] - 2026-08-26

### Added
- **`/oracle-release` command — the Oracle release track.** New command for the
  **medtrics-oracle** product (React + Supabase; previously no release-notes
  track): Optimus ORC tickets + **GitHub** PRs → ONE combined Slack message to
  the internal-only `SLACK_CHANNEL_ORACLE` channel — a delimited, ip-guarded
  **client-communication block** (exec voice, safe to forward, `[ORC-ID]`
  chips) followed by an **internal reference index**
  (`n. ORC-id · verbatim title · <@dev> · <PR-url|PR#n>`), with per-ticket
  technical detail (developer, raw PR links, Optimus link, tech notes) and any
  security escalation in thread replies. Same preview-then-confirm discipline
  as the other Slack commands. New env keys: `GITHUB_TOKEN` (classic PAT,
  `repo` scope, SSO-authorized), `GITHUB_REPO` (`owner/repo` or full URL —
  normalized), optional `GITHUB_API_URL`, `SLACK_CHANNEL_ORACLE`.
- **`skills/oracle-storyteller`** — self-contained dual-register contract
  (explicitly does NOT inherit from `storyteller` / `internal-storyteller`;
  three header emoji allowed, no `Clients to notify:` line). Writes the
  guarded client-section file (≤ 1,200 chars, overflow to a guarded
  continuation reply), the internal-index file, and the technical-card reply
  files (≤ 3,900 chars each). The root itself is not written by the skill.
- **`scripts/gather_notes_data_oracle.py`** — sibling gatherer importing the
  classic module (`import gather_notes_data_optimus as base`; classic script
  unmodified). GitHub API client with load-bearing retry classification
  (429 OR `x-ratelimit-remaining: 0` OR `Retry-After` present — secondary
  limits send 403 + Retry-After with nonzero remaining), Link-header
  pagination, a startup canary (`GET /repos/{owner}/{repo}`, hard-exit on
  failure — private-repo auth failures surface as 404/422 and would otherwise
  read as "no PRs anywhere"), and Phase-0-verified PR discovery: ORC ids live
  primarily in branch names, which GitHub search does not index, so discovery
  is ONE `/pulls` list sweep per run (~500 PRs) matched locally, with
  `/search/issues` as a per-ticket secondary. Tickets with zero PRs are
  warned about loudly, never silently omitted.
- **`scripts/assemble_oracle_root.py`** — deterministic stdlib root assembler
  (`--client-file` + `--internal-file` + `--date` → root). The guarded client
  text is embedded **verbatim by construction** — no grep/cmp verification
  step, no drift window; guard rewrites and preview edits simply re-run it.
  Owns the overflow partitioning: whole internal-index lines only, client
  block keeps root priority, dropped lines land in `--overflow-output` with
  an `…and N more` line, dangling section headers move with their entries,
  and the client marker text switches when the client copy continues in the
  thread (`--client-continues`).
- **`scripts/ip_guard.py` Oracle patterns (additive only)**: `GitHub`,
  `github.com`, `Vite` in the block bucket; PR references — `PR#42` /
  `PR #42` / `PR 42` (case-sensitive) and `pull request #42`
  (case-insensitive) — block alongside the existing MR patterns.
  `Medtrics-Oracle` is a warn. No exemption-regex changes (`ORC-` ids
  already pass).
- **46 new tests** (`test_gather_oracle.py`, `test_assemble_oracle_root.py`,
  plus additions to `test_ip_guard.py` and `test_slack_lint.py`) — retry
  classification, pagination, canary hard-fail, ORC-59 vs ORC-597 matching,
  sweep/search call counts, verbatim client inclusion, overflow partitioning
  determinism, and the new guard patterns. Suite total: 144.

### Notes
- **The classic Medtrics track is untouched**: `gather_notes_data_optimus.py`
  is byte-identical, and the `ip_guard.py` changes are additive patterns only.
  `/release-slack`, `/internal-notes`, `/deploy-notes`, `/integration-notes`
  behave exactly as in 0.9.0.

## [0.9.0] - 2026-08-26

### Removed
- **The PDF track is removed entirely — an explicit design reversal.** The
  `release-brief-pdf` and `integration-brief-pdf` skills (added in 0.4.0) are
  deleted, along with `slack_upload.py`'s `upload_pdf()` path and its
  `--pdf`/`--title` arguments. The product is Slack message generation and
  delivery only: gather → narrative → guard → one linted Slack announcement.
  The `files:write` bot scope is no longer needed; `chat:write` +
  `channels:read` (+ `groups:read` for private channels resolved by name)
  remain.

### Added
- **Root-post-as-index Slack format, in two audience-specific variants.**
  Both Slack commands now post one compact root message (numbered one-line
  entries under `*Enhancements & New Features*` / `*Bug Fixes*` headers,
  root ≤ 2,500 chars) with all long-form detail in thread replies (one per
  section, ≤ 3,900 chars each; escalations get their own reply) — never a
  second top-level message. `/release-slack` root entries are `ID · title`
  only (no dev mentions, no client names, no internal URLs); `/internal-notes`
  root entries are `ID · title · <@dev>` plus a `Clients to notify:` line.
  `skills/storyteller`, `skills/internal-storyteller`, and both command
  pipelines rewritten to the index + thread structure, including the
  orchestration: post root → capture `ts` → post replies with `--thread-ts`.
- **`scripts/slack_lint.py` — deterministic, stdlib-only, fail-closed mrkdwn
  lint gate.** Tokenizes first (code spans, mentions, channel refs, special
  mentions, links, bare URLs), then enforces: bare `<@U…>`/`<@W…>` mentions
  (no trailing display names), non-empty link labels, no `**bold**` /
  `#` headers / ``` fences, escaped `&` `<` `>` outside tokens, a blank line
  before section headers, and the per-kind char caps (`--kind root|reply`).
  Wired into both Slack commands as a mandatory pre-preview gate with a
  fix-and-relint loop (max 3 iterations). Raw auto-linked URLs stay legal —
  the internal ticket-link convention depends on them.
- **`tests/` extended**: `test_slack_lint.py` (per-rule pass/fail fixtures +
  caps), `test_slack_upload.py` (channel resolution, retry policy, result
  block on every failure path, `--thread-ts`), `test_ip_guard.py` (both
  title exemptions, per-category block/warn detection).

### Changed
- **`scripts/slack_upload.py` delivery hardening.** 429 responses are retried
  on all calls honoring `Retry-After` (exponential backoff fallback, max 5
  attempts); 5xx/network errors are retried on reads only — a failed
  `chat.postMessage` write is never blind-retried (it may have landed;
  the script fails with a "verify in channel before re-running" error).
  Channel resolution now accepts any `[CG][A-Z0-9]{7,}` id verbatim — fixing
  the real bug where G-prefixed private-channel ids were shunted into a
  doomed name lookup; a leading `#` always means a name. New `--thread-ts`
  flag posts into an existing thread. The `---SLACK_UPLOAD_RESULT---` JSON
  block (`{ok, channel, ts, error}`) is emitted on every exit path, and
  message length is pre-checked against the 40,000-char API cap.
- **`scripts/ip_guard.py` re-pointed at the message.** The composed Slack
  root post and every thread reply are now scanned by `/release-slack`
  (in addition to the deploy markdown). A new mrkdwn-aware exemption skips
  numbered index lines (`N. ID · title`) the same way markdown `## [ID]`
  headings are skipped — verbatim ticket titles remain intentional.
  `/internal-notes` stays un-guarded (internal vocabulary is the point).
- **`scripts/gather_notes_data_optimus.py` correctness fixes** (from the
  Phase-1 audit): the pagination loop always terminates (empty-page /
  missing-count terminator with a logged truncation warning), both HTTP
  helpers have 30 s socket timeouts, and Optimus errors retry with
  backoff + `Retry-After` and raise a typed `OptimusError` instead of
  exiting mid-gather.
- **`scripts/build_plugin.py` hygiene + atomic output**: `.env*`, `*.pdf`,
  `*.tmp`, `*.zip`, `*.log`, `PLAN.md`, and dev directories are excluded;
  the bundle is written to a `.tmp` and renamed only after verification
  (a failed build leaves no artifact); verification hard-fails on any
  bundled `.env*` or `.pdf` entry.
- README rewritten to describe exactly what ships (message-only pipeline,
  real repository layout, stdlib `unittest` suite, corrected Slack scopes).

## [0.8.0] - 2026-07-14

### Changed
- **Per-ticket customer-facing format.** The `/deploy-notes` release brief now
  renders one entry per ticket instead of grouping changes by domain area. Each
  entry is headed by the ticket's **identifier + verbatim Optimus title**
  (`### [M1-1054] CRITICAL BUG: Security > SQL Injection & SSRF ...`), followed
  by the exec-voice bold lede and description. Every **Key takeaways** bullet
  now ends with its `[TICKET-ID]` reference chip. Rationale: clients routinely
  reference these identifiers with support, so the customer-facing brief must
  carry them.
- **`skills/storyteller`** rewritten: output structure replaced
  `## What's improved for your programs` / `## What you should know` with a
  single `## In this release` section of per-ticket `### [ID] Title` blocks.
  Awareness items (default-behavior / permission changes) carry a `[!HEADSUP]`
  marker and are ordered last. Titles are reproduced **verbatim by default**;
  cleaning is available only on explicit operator request.
- **`skills/release-brief-pdf`** layout updated: each entry renders as a card
  with a filled navy identifier chip + verbatim title header; `[!HEADSUP]`
  entries get an orange left rule, an orange "Heads up" tag, and an orange chip.
  Key-takeaways `[ID]` chips render as navy monospace tags.
- **`scripts/ip_guard.py`** relaxed: ticket identifiers (`M1-NNN`, `GEN-NNNNNN`,
  `DEV-NNN`, `RD-NNN`, `do-NNN`) are **no longer blocked**, and any markdown
  heading line containing a `[TICKET-ID]` bracket is exempt from scanning so the
  verbatim title header is preserved. MR references, internal system names, code
  artifacts, file paths, and mechanism vocabulary remain blocked in the
  narrative prose. `skills/ip-guard/SKILL.md` updated to match.

### Notes
- This is a customer-visible policy change: verbatim titles mean internal-
  flavored wording (`CRITICAL BUG:`, `FEATURE:`, `Redo M1-xxxx`, client codes,
  and security classes) appears in the client-facing brief by design. Use the
  explicit cleaned-titles override when a specific title should be softened.

## [0.7.0] - 2026-05-22

### Added
- **`/internal-notes` command.** New on-demand workflow for the internal
  Medtrics dev team Slack digest. Pulls the same Optimus tickets + GitLab MRs
  as `/deploy-notes`, but keeps everything the customer-facing brief strips:
  ticket identifiers, developer Slack `@`-mentions, client institution names
  (e.g., `KFSHRC`, `MSUCVM`, `ADTALEM`), and direct Optimus URLs. Previews the
  composed message in chat; posts to `#dream-team` (channel id `C1BCUHXT9`)
  only after explicit user approval.
- **`skills/internal-storyteller`.** Composes the Slack-mrkdwn message from
  the per-ticket analyzer blocks. Sections are `*Enhancements & New Features*`
  and `*Bug Fixes*`; each item carries Ticket, Developer (`<@SLACK_ID>` when
  resolvable, plain name otherwise), 2–4-sentence Summary, URL, and Clients
  (or `TBC` with a one-line scope description for platform-wide work).
- Slack developer-ID resolution via `slack_search_users` (best-effort; falls
  back to plain name when no match).
- `SLACK_CHANNEL_INTERNAL` env key (default `#dream-team` / `C1BCUHXT9`).

### Changed
- **`scripts/gather_notes_data_optimus.py`** now emits `assignee_name`,
  `assignee_email`, `assignee_id`, and `task_type` on each issue, and
  `author_name` / `author_username` on each MR. Backward-compatible — fields
  are empty strings when upstream omits them. Required by `/internal-notes`;
  ignored by `/deploy-notes`, `/integration-notes`, and `/release-slack`.
- **`scripts/prepare_note_prompts.py`** extends the per-prompt header to
  optionally include Assignee, Assignee Email, Assignee Slack ID, Clients,
  and MR Author(s) when those fields are present on the issue. The
  customer-facing analyzer prompts ignore them; the internal analyzer
  consumes them.

### Notes
- `/internal-notes` deliberately does **not** run through the IP guard or
  `copy-audit` skill. Both are configured for customer-facing copy and would
  refuse internal vocabulary.
- The internal post is Slack-only — no PDF.

## [0.6.0] - 2026-05-22

### Changed
- **Customer release brief is now scannable in 15 seconds.** The storyteller
  prepends a `## Key takeaways` section (3–5 one-line bullets, with a
  `Heads up:` prefix for any awareness item) and requires every domain block
  under `## What's improved for your programs` and every item under
  `## What you should know` to open with a bold one-line takeaway. Length
  budget stays at 350–650 words.
- **`release-brief-pdf` adds a visual hierarchy spec.** A Key-takeaways card
  with a hairline border sits at the top of page 1 above the Summary, and the
  `What you should know` section is marked with a 3 pt orange left rule.
  Heading sizes, weights, and spacing are now in a single table so the PDF
  renders consistently across releases.
- **`integration-brief-pdf` gains an at-a-glance card.** A counts strip
  (BREAKING / DEPRECATION / NON-BREAKING) and up to three "Top actions for
  integrators" sit at the top of page 1. Counts are derived strictly from the
  source Summary/Totals; top actions are pulled verbatim from the unchecked
  Migration Checklist items.
- `commands/deploy-notes.md` and `commands/integration-notes.md` updated to
  reflect the new output structures.

## [0.5.2] - 2026-05-22

### Added
- `CHANGELOG.md` (this file). Version history previously lived at the bottom of
  the README; it now has a proper home.
- `requirements.txt` declaring the runtime and dev dependency surface
  (scripts are currently stdlib-only; `reportlab` is bundled with the
  `release-brief-pdf` skill's renderer).
- `LICENSE` (Proprietary — internal use at Medtrics).
- `CONTRIBUTING.md` describing the contribution flow.
- `tests/` directory with a smoke test for `ip_guard.py` and a README explaining
  how to run the suite.
- `scripts/build_plugin.py` — versioned bundler that reads
  `.claude-plugin/plugin.json`, excludes dev-side files, and produces the
  shippable `.plugin` artifact. Includes a post-build self-check that refuses
  to ship a bundle missing `.claude-plugin/plugin.json` or its explicit
  directory entry, or whose manifest description exceeds 256 chars.
- Executable bit set on all `scripts/*.py` files.

### Changed
- README's "Version history" section trimmed to a pointer at `CHANGELOG.md`.

### Fixed
- The `.plugin` zip now writes explicit directory entries (`.claude-plugin/`,
  `commands/`, `skills/`, `scripts/`, and each nested skill directory).
  Earlier 0.5.x builds with file-only entries were rejected by the Cowork
  plugin loader with *"Zip must contain a .claude-plugin/plugin.json file"*
  even though the file was present — the loader requires the directory entry.
  Fixed in `scripts/build_plugin.py`; v0.4.0 used the same pattern, so this
  restores parity.

## [0.5.1] - 2026-05-22

### Added
- Bundled `copy-audit` skill. Previously installable as a separate skill, now
  ships inside the plugin so installing the `.plugin` file delivers the full
  pipeline in one shot.
- New pipeline step between `storyteller` and `ip-guard`. `copy-audit` polishes
  the storyteller's draft sentence-by-sentence — strips AI-sounding language,
  bloated verbs (`serves as`, `boasts a`, `helps to`), dead openings
  (`Furthermore`, `Moreover`), negative-parallelism patterns (`Not X. Y.`),
  decorative metaphor verbs (`mapped`, `surfaced`, `unpacked`), and engagement
  bait.

### Changed
- Storyteller now writes its draft to `/tmp/release_notes_draft.md`. The
  copy-audit pass writes the polished file to the final
  `.claude/outputs/notes/deploy/` path. This keeps the unaudited draft out of
  the deliverable directory.
- Storyteller title format updated from `# Medtrics Release — {Weekday}, ...`
  to `# Medtrics Release: {Weekday}, ...` — the em-dash violated `copy-audit`'s
  punctuation rules.
- Failure-loop now iterates the storyteller → copy-audit → ip-guard chain up to
  3 times before stopping.
- `commands/release-slack.md` step numbering shifted (Step 4 = copy-audit,
  Step 5 = ip-guard, …) and the failure-mode table updated.

### Fixed
- Plugin validation failure on install caused by overlong YAML/JSON
  `description` fields. `plugin.json` description trimmed from 577 chars to 225;
  `storyteller` SKILL.md description from 379 to 226; `ip-guard` SKILL.md from
  283 to 231. Pre-existing skill descriptions (`release-brief-pdf` 277,
  `integration-brief-pdf` 331) shipped successfully in 0.4.0 so they were left
  alone.

## [0.5.0] - 2026-05-22

### Added
- New `storyteller` skill. Transforms per-ticket release summaries into a
  cohesive executive-voice narrative aimed at institutional administrators
  (DIOs, deans, IT directors, program directors). Never names internal
  systems, code artifacts, or implementation mechanisms.
- New `ip-guard` skill plus `scripts/ip_guard.py`. Deterministic regex scanner
  that blocks customer-facing release notes from leaking internal system names
  (issue tracker, source-control host, observability vendors, framework names,
  language runtimes, datastores, cloud providers), code artifact jargon
  (`serializer`, `viewset`, `model`, `migration`, `endpoint`, `queryset`,
  `schema`, etc.), ticket / MR identifiers (`M1-NNN`, `!1234`), file paths,
  and architecture mechanism vocabulary (queue, worker, cache, cron, webhook,
  race condition).
- New Step 4 in `commands/release-slack.md` that runs `ip_guard.py` against the
  customer-facing markdown before any PDF render or Slack call. On block, the
  storyteller iterates up to 3 times to remediate.

### Changed
- `commands/deploy-notes.md` analyzer rubric rewritten for exec voice. Per-ticket
  agents now produce one user-outcome sentence per ticket with a domain tag and
  a change kind, rather than the previous category / identifier / title / summary
  block.
- README expanded with the new bundled-skills table, updated pipeline narrative,
  and an explanation of the IP guard's contract.

### Fixed
- None.

## [0.4.0] - 2026-05-20

### Added
- Bundled `release-brief-pdf` and `integration-brief-pdf` skills (originally by
  `santhosh@medtricslab.com`). Installing the plugin now delivers PDF rendering
  inline rather than requiring a separate skill install.
- `scripts/slack_upload.py` for direct Slack Web API delivery using the modern
  `files.getUploadURLExternal` + `files.completeUploadExternal` flow.
- `/release-slack` now generates branded PDFs and uploads them to the message
  thread automatically.
- New required env key: `SLACK_BOT_TOKEN` (Slack bot OAuth token with
  `chat:write` + `files:write` scopes).

### Changed
- `/release-slack` no longer uses the Slack MCP. It now talks to the Slack Web
  API directly via the bundled upload script.

### Fixed
- None.

## [0.3.0] - 2026-05-01

### Changed
- Trimmed plugin to four client-facing commands (`/notes`, `/deploy-notes`,
  `/integration-notes`, `/release-slack`). Engineering commands moved to the
  companion plugin `medtrics-dev-workflow`.

### Removed
- Internal-team commands (code review, plan, rebase, MR-feedback, bug-report,
  branch-context, dev-notes changelog) extracted to `medtrics-dev-workflow`.

## [0.2.0] - 2026-04-15

### Added
- `/release-slack` command for message-only Slack delivery via the Slack MCP.

## [0.1.0] - 2026-03-01

### Added
- Initial release: four note-generation commands (`/dev-notes`,
  `/deploy-notes`, `/integration-notes`, `/notes`).
- `scripts/gather_notes_data_optimus.py` and `scripts/prepare_note_prompts.py`.

[Unreleased]: https://gitlab.com/medtrics/medtrics-release-notes/-/compare/v0.16.0...HEAD
[0.16.0]: https://gitlab.com/medtrics/medtrics-release-notes/-/compare/v0.15.0...v0.16.0
[0.15.0]: https://gitlab.com/medtrics/medtrics-release-notes/-/compare/v0.14.0...v0.15.0
[0.14.0]: https://gitlab.com/medtrics/medtrics-release-notes/-/compare/v0.13.0...v0.14.0
[0.13.0]: https://gitlab.com/medtrics/medtrics-release-notes/-/compare/v0.12.0...v0.13.0
[0.12.0]: https://gitlab.com/medtrics/medtrics-release-notes/-/compare/v0.11.0...v0.12.0
[0.11.0]: https://gitlab.com/medtrics/medtrics-release-notes/-/compare/v0.10.0...v0.11.0
[0.10.0]: https://gitlab.com/medtrics/medtrics-release-notes/-/compare/v0.9.0...v0.10.0
[0.9.0]: https://gitlab.com/medtrics/medtrics-release-notes/-/compare/v0.8.0...v0.9.0
[0.8.0]: https://gitlab.com/medtrics/medtrics-release-notes/-/compare/v0.7.0...v0.8.0
[0.7.0]: https://gitlab.com/medtrics/medtrics-release-notes/-/compare/v0.6.0...v0.7.0
[0.6.0]: https://gitlab.com/medtrics/medtrics-release-notes/-/compare/v0.5.2...v0.6.0
[0.5.2]: https://gitlab.com/medtrics/medtrics-release-notes/-/compare/v0.5.1...v0.5.2
[0.5.1]: https://gitlab.com/medtrics/medtrics-release-notes/-/compare/v0.5.0...v0.5.1
[0.5.0]: https://gitlab.com/medtrics/medtrics-release-notes/-/compare/v0.4.0...v0.5.0
[0.4.0]: https://gitlab.com/medtrics/medtrics-release-notes/-/compare/v0.3.0...v0.4.0
[0.3.0]: https://gitlab.com/medtrics/medtrics-release-notes/-/compare/v0.2.0...v0.3.0
[0.2.0]: https://gitlab.com/medtrics/medtrics-release-notes/-/compare/v0.1.0...v0.2.0
[0.1.0]: https://gitlab.com/medtrics/medtrics-release-notes/-/releases/v0.1.0
