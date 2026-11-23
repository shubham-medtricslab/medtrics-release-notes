# Changelog

All notable changes to `medtrics-release-notes` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.25.0] - 2026-11-23

Mem rate-limited every release from Nov 17 to Nov 19 and refused reads on the 19th. This release cuts the calls a normal run makes, and keeps a refused note safe until Mem takes it.

### Added
- **Saved collection ids (`scripts/release/mem_ids.py`).** Every collection the release commands use (`release_archive`, `pod_map`, `pod_log`, `dev_directory`, `pr_overrides`, the four KB ones) is found by name once. Its id is then kept in `.medtrics-release-notes/config.json` and used directly. When something is missing, Step 1 walks `list_collections` **once** for all of them. Gone: the per-step `list_collections` walks (archive, KB, pod map), `search_collections` (developer directory, PR overrides) and pod-notify's `get_collection` check. `MEM_PR_OVERRIDES_COLLECTION_ID` in `.env` still wins.
- **Pod Notify Log collection.** Pod-log notes (one per pod per release) now live in their own collection (`MEM_POD_LOG_COLLECTION`, default `Pod Notify Log`), so reading the pod map is one small call again. `pod_map.py route --log-notes` reads it. Log notes already in the map stay there and are read with it; setup does not move them (that would cost two calls per note). `pod_map.py old-logs` lists them for a manual move.
- **Outbox.** A release-archive or pod-log note Mem still refuses after waiting 30s and 90s goes to `.medtrics-release-notes/outbox/`. The next `/release-slack`, `/oracle-release` or `/pod-notify` uploads it at Step 1 and deletes the file (`release_archive.py outbox add|list|done`). Pod logs waiting there count as sent, so a re-run never posts to a pod twice.
- **Developer directory local copy (24h)** (`dev_directory.py cache get|put|apply|clear`). Most runs make no directory read. Write-backs still go to Mem at once and into the copy. `--refresh-directory` reads Mem anyway.
- **`/release-note-setup backfill <files>`** (`release_archive.py backfill`) checks archive notes saved as markdown, for example the ones printed in chat after a failed write, and writes them to the archive collection after a yes.
- **`mem-calls` skill**: the shared rules. A rate limit makes Mem "limited" for the run. Optional steps (developer directory, PR overrides, KB queue) then skip, and required writes wait and then go to the outbox. A rate limit never blocks or delays a Slack post. `mem_ids.py wait <s>` does the waits.

### Changed (Mem call cuts)
- **Pod reads only when they can matter.** `pod_map.py route --no-map` routes before any Mem read: when no posted ticket names a client (no Optimus link, no client in its text), no pod can be hit, so the plan is final and neither the pod map nor the pod log is read (cut A; all 15 tickets of the Nov 19 Oracle release were like this). When the map is read, `--log-unread` makes the pod log a third read only when a pod actually gets tickets (cut B). A route that still needs a read returns `needs_map` / `needs_log`, writes no plan and removes any stale one, so nothing can be posted from it.
- A release like Nov 19 now makes **2** Mem calls on Oracle (PR overrides read + archive write) and **1** on Legacy (archive write), plus 1 when the local developer-directory copy is over 24h old. It was 7.

### Fixed (Mem calls, from the review)
- **Notes that land outside their collection.** Mem's `create_note` ignores a collection id it cannot find, without an error, so a stale saved id made loose archive and pod-log notes (and a loose pod log let a pod be posted to twice). Every `create_note` into a saved collection is now checked with `mem_ids.py landed <key> --response <file>` (no Mem call): a note that missed is moved in with `add_note_to_collection` after one lookup, and the stale id is dropped. An empty pod map from a saved id is treated as a stale id too.
- **A missing pod log no longer costs a lookup every run.** A key the one walk did not find is remembered as `absent` for 7 days (`mem_ids.py need` lists it apart from `missing`); `/release-note-setup` passes `--recheck`. The release commands ask for `pod_map` / `pod_log` only when the pod step runs (not with `--no-pod`, never in `mr` mode).
- **Queued notes wait for their collection.** `release_archive.py outbox list --ready-keys <keys>` lists a note whose collection id this run lacks (a pod log queued before the log collection exists) under `waiting`; it stays queued, and still counts in the pod duplicate check, instead of being uploaded loose. Step 1 reads the outbox first and asks for its keys too, so `/pod-notify` now has the `release_archive` id it needs to upload a queued archive note.
- `oracle-release.md` and `release-slack.md` named the pod map collection as the pod log's home; they now name `MEM_POD_LOG_COLLECTION` (`Pod Notify Log`).

### Fixed (KB articles, from run kb-20261123-1952)
- **Articles dropped from packaging after a retry.** `kb_spec.py assemble --tickets <ids>` (a retry round) rewrote `assemble.json` and `failures.json` with only those tickets, so the package showed every other article as "not reached". A partial re-assemble now swaps in its tickets' rows and keeps the rest.
- **M1-1459, "View" not found: the Case Logs list loads by POST and the write guard blocked it.** A route can now list exact read-only POST paths as `read_posts` in `kb_routes.json`; the guard lets a POST to exactly that path through (same origin, no query) and blocks every other write. A path with a write verb is ignored. The Case Logs route lists `/api/rotations/procedure_logs/logs_filter_list/`. A click that fails right after a blocked request is reported as `request_blocked` with that request, not "button not found".
- **M1-1245, Time Off Requests "not reached".** Three causes: the capturer wrote batch b01's visit as `v01` (capture.md's example said `v01`), so ingest never matched the start screen; the tenant check counted typography above U+03FF (dashes, ellipses, curly quotes) as non-Latin script and blocked the page; and a hidden 1×1 "Delete" counted as a found label, which stopped the heading fallback and the reason. Now: the example uses the batch's real first visit id and says to copy it exactly, and a shortened id is matched to its planned visit; the non-Latin rule counts only letters from other scripts (probe JS and `qa_gif_to_shots.py` alike); labels under 4 px are not found and an unverified match never counts as a shot. A screen that fails the tenant check now says so (`tenant_block`, with the rule).
- **M1-1413, label not found: the ticket names its labels in bold.** `kb_inputs.py` now also takes **bold** UI terms from the ticket body (not headings or "**Lead-in:**" bold). A ticket with no labels at all is reported as `no_labels`, not an empty "label not found".

### Changed
- A normal `/oracle-release` makes about 4 Mem calls plus one write per pod (was about 15–30). See the comparison in `docs/plans/MEM-CALLS-PLAN.md`.
- Step 10b no longer re-lists the PR overrides after a pick; the dump is edited in place.

## [0.24.0] - 2026-11-16

### Added
- **Named route clicks.** A text click written into a `kb_routes.json`
  surface's `clicks` is now "named": the click guard lets it press a filter's
  Submit, a plain button that opens a popup, or a link with no address, which
  it refused before on non-Vue pages. In place of the skipped rules: its
  onclick / data-href / formaction and a submit's form action must not point
  at a delete-style URL; its words get the danger test (delete, remove,
  archive, approve, publish, invite, send, notify); a write verb is still
  refused unless the whole text is a filter word (Submit, Search, Filter,
  Apply, Go); and it is refused on a page with hard tenant data, like a row
  click. POST forms, AJAX links and danger classes / attributes are still
  refused, and the network write guard still lets only GET leave the page.
  Click paths read from a ticket's own text are never named; the form-fill
  path keeps the flag.
- **Cases > Case logs surface** for M1-1459 (Reassign Case Log Reviewer):
  `/cases/procedures/logs/` → **Submit** → **Proceed without date** → a row's
  **View**, which opens the case log popup with the reassign bar. Before, the
  ticket had no admin case-log surface at all.
- **Click paths for the other kb-20261116-2131 partials**, each checked on
  the demo in Chrome:
  - M1-1245: Time Off → **Requests** tab (the list with descriptions and
    attachment counts). The approve popup opens from **Approve**, a danger
    word, so that screen stays out.
  - M1-1446: now starts on the institution calendar
    (`/institution/calendar/`, which opens from the Admin portal too; the
    `/administration/calendar/` prefix still places tickets here) with
    **Select all** / **Clear all**, then **Create Event** opens the form.
  - M1-1413: new surface Form Builder > Step 1 (demo template), starting on
    `/forms/evaluation/templates/18/step_1/`; the list's Edit link is hidden
    behind an icon-only expand button. The shared Form Builder > Step 1
    surface keeps the list page, so other tickets don't land on that record.
  - M1-1166: new surface Curriculum > Setup > Permissions,
    `/administration/curriculum/` → **Curriculum Permissions** tab. It was
    placed on Courses before.
  - M1-1643 and M1-1571 are marked Needs SME: the demo admin has no document
    template editor, and confidential-question access needs an account with
    site-level Manage Evaluations.

### Fixed
- **`/kb-articles` `pending` reads the Mem release archive again.** Mem returns a
  markdown hard line break as a trailing backslash, and `release_archive.py
  parse_note` kept it: the date read `2026-11-10\`, the item count was lost and
  the last ticket read `M1-1520\`, so `pending` skipped most releases and KB
  runs had to name tickets by id. Header lines and bullets now drop it (as
  `kb_ledger` and `kb_data` already did); the posted-message text is kept as is.
- **A KB form fill no longer stops at the portal check.** The data maker's
  Start probe opens the Admin portal entry, which lands on the users list; on
  the demo that list holds outside email domains, so the probe said tenant
  BLOCK and the run stopped before reaching the form (M1-465 in
  kb-20261116-2131). That probe now only checks the portal and sign-in; each
  form fill now checks the tenant verdict on its own start page and skips
  just that fill (`write_error` "tenant_block") on BLOCK.
- **PR override notes read Mem's backslashes too.** `pr_overrides.py
  parse_note` had the same trailing-backslash bug: `**PR:** 415\` parsed as
  no PR and the override was silently ignored.

## [0.23.2] - 2026-11-16

### Changed
- **A developer note can hold more than one login.** `GitHub login` and
  `GitLab username` in the Mem developer directory now take a comma-separated
  list, as `Emails` already did, and `dev_directory.py prefill` matches on any
  of them (case-insensitive). Santhosh commits as `toshmd` but his note held
  `santhosh2`, so he came up unresolved on six cards in the 2026-11-16 Oracle
  run; `**GitHub login:** santhosh2, toshmd` now matches both. Before, a list
  typed into that line was read as one login and matched nothing.
- `dev_directory.py pending` now adds a known developer's new login to the
  note, instead of keeping only the first one ever seen. Notes with one login
  read and render exactly as before.

## [0.23.1] - 2026-11-10

### Added
- **Version check first.** Every command now starts by comparing the installed
  plugin with the latest version on GitHub (`scripts/version_check.py`). If
  the install is behind, it shows both versions and how to update, and asks
  before running on the old copy — an old install of `/oracle-release` had
  silently skipped the pod step. A restart or new chat does not update a
  plugin, and the message says so. No network, or a sandbox that blocks
  GitHub, prints one line and carries on.

### Fixed
- **The Mem developer directory loads again.** Mem returns note lines with a
  trailing `\` (Markdown hard break), Markdown escapes (`\_`) and emails as
  autolinks (`<a@b.com>`), so `dev_directory.py` dropped every note and each
  release fell through to the Slack resolver. It now cleans each line the way
  `pod_map.py` does, unwraps autolinks and ignores the `—` placeholder in the
  email list. All 8 notes in the live directory parse (was 0).
- A directory note that still cannot be read now prints a warning naming it
  and is counted as `notes_dropped` in the result, instead of failing silently.

## [0.23.0] - 2026-11-04

### Changed
- **Pods hear about every release.** `/release-slack` and `/oracle-release`
  now run the client-pod step in `status` mode (the default) too, not only in
  `tickets` mode — a default run on 2026-11-04 reached no pod. It still has its
  own preview and yes, and `--no-pod` still skips it; `mr` mode never posts to
  pods.
- **Folder layout.** The release scripts moved to `scripts/release/` (the KB
  scripts stay in `scripts/kb/`; `scripts/utf8_mode.py` is shared). Commands
  and skills call the new paths; `/pod-notify` and the setup's pod part set
  `P=${CLAUDE_PLUGIN_ROOT}/scripts/release`.
- The bundler moved to `tools/build_plugin.py` and writes to `dist/`. The
  feature plans moved to `docs/plans/`, the runbook and handoff PDFs to
  `docs/guides/`. Tests are split into `tests/release/`, `tests/kb/` and
  `tests/tools/`; run them with `python3 -m unittest discover -s tests -t tests`.

### Fixed
- **Bundles shipped local run data.** A build made in a folder that had run
  `/kb-articles` carried its `.kb-runs/` and `.medtrics-release-notes/config.json`.
  Both are now left out, and the build fails if either gets in.

## [0.22.3] - 2026-11-03

Fixes from run kb-20261103-2319 of `/kb-articles M1-1520`.

### Fixed
- **Migrate Curriculum frames screened BLOCK.** The tenant-slug check read
  Legacy's own typo "Migrations will be perfomed overnight" as a client slug
  ("perfo" + "med"). A word one letter off a long stop word ("performed") now
  counts as an ordinary word; short slugs ("uwmed", "kfshmed") are still
  flagged. Stored raw files are rescreened under the new rule on ingest.
- **Closed dropdowns made frames REVIEW.** The page check read every option of
  a closed `<select>` (program names such as "College of Veterinary
  Medicine") though only the chosen one shows. Unchosen options of a closed
  dropdown are left out; open lists and the chosen option are still screened.

## [0.22.2] - 2026-11-03

More fixes from the Windows retry of `/kb-articles M1-1520` (run kb-20261103-2055).

### Fixed
- **Windows: PDF step still failed once LibreOffice was found.** The temp
  profile was passed as `file://C:\...`, which soffice rejects; it is now a
  real file URL (`file:///C:/...`).
- **Labels in a QA checklist written in double quotes were ignored.** Only
  backticked names were read, so M1-1520 fell back to "Session Dates" from the
  ticket text. Double-quoted names in the UI-path line and step Actions now
  count too ("Session Schedules", "Set migrated sessions to Unpublished").

## [0.22.1] - 2026-11-03

Fixes from a Windows run of `/kb-articles M1-1520`.

### Fixed
- **M1-1520 stopped on the Curriculum Setup page.** The Chrome capture only
  clicks a path the ticket's own text gives, and this known ticket had none. A
  surface in `kb_routes.json` can now carry its own `clicks`, used when the
  ticket gives no path; "Curriculum > Setup > Migrate" opens the Migrate
  Curriculum tab and ticks Courses → Sessions → Session Schedules → Set
  migrated sessions to Unpublished. Rerun with `--retry-partial`.
- **Ticking a checkbox.** New click `{"check": "<exact label>"}`: kbclick finds
  the one visible label with those words and ticks its checkbox. It never
  presses a button, so a box inside a Save form is fine, and the write guard
  still blocks any request a change handler makes. Refused: danger words, an
  `on…` handler, a box styled as a switch, a box in a dialog or another form,
  a disabled box, and a box that is already ticked.
- **Windows: text-encoding crash.** Every script now reruns itself in Python's
  UTF-8 mode on Windows (`scripts/utf8_mode.py`), so `PYTHONUTF8=1` is no
  longer needed.
- **Windows: LibreOffice not found, so no PDF.** The PDF step now looks in
  Program Files, the per-user Programs folder, and a `KB_SOFFICE` path, as
  well as PATH. Tesseract (`Tesseract-OCR`) and fonts in the Windows Fonts
  folders are found the same way. With no apt-get or Homebrew, setup prints
  the `winget install` command to run.

## [0.22.0] - 2026-10-29

Client pods: each pod (Slack channel) now hears which released tickets concern
its clients. Before, only `#dream-team` / `#pod-oracle` got the release post.

### Added
- **Pod step in `/release-slack tickets …` and `/oracle-release tickets …`**
  (Step 9b2 / 11c, `pod-notify` skill). It runs after the post and the archive,
  is on by default, and `--no-pod` skips it. It never runs in `status` or `mr`
  mode, because those tickets haven't shipped. It has its own preview covering
  every pod (`[y]/[n]/[e]/[a]`) and its own yes, and it can never fail the
  release.
- **`/pod-notify <ids>`**: the same step on its own, for releases announced by
  hand. It reads the tickets from Optimus only, routes them first, and writes
  client cards only for tickets a pod gets (one analyzer each, then the
  engineering-to-client pass). Also `dry-run`, `--force`, `--summaries <file>`
  and `resume`.
- **Pod map in Mem**: the "Release Notes Pod Map" collection
  (`MEM_POD_MAP_COLLECTION`), with one hand-edited note per pod (`Pod: #channel`,
  `Channel ID`, exact Optimus client names). Each pod-notify run adds one
  pod-log note with each pod's ticket ids and a link to its pod post, so a
  ticket is announced to a pod once.
- **`/release-note-setup pod`**: creates the collection and a template note,
  adds each client's Optimus id to the pod notes (so a rename in Optimus doesn't
  break the map), and checks every pod channel.
- **`scripts/pod_map.py`** does the routing, rendering, channel-check
  verdicts, preview, resume state, pod log and setup helpers.
  `tests/test_pod_map.py` covers it.
- **`slack_upload.py --check-channel`**: a read-only `conversations.info` check.
  Pods whose channel is archived, shared outside Medtrics (Slack Connect, or an
  invite pending), or that the bot isn't in, are blocked. A later check that
  passes unblocks the pod.
- **`gather_notes_data_optimus.py --exact`**: tickets mode keeps only exact id
  matches (used by `/pod-notify`, so a mistyped id never reaches a pod).

### Changed
- With `--no-mrs`, the gather script no longer requires the GitLab `.env` keys.
- `task_to_issue` now keeps the Optimus REST `linked_clients` as
  `optimus_clients` (`[{id, name}]`), for both tracks. The `clients` code string
  that Step 4b writes is unchanged.

### Routing rules
- A ticket's clients are its Optimus client links. When it has none, routing
  falls back to full names under the description's `## Reported client(s)`
  heading, marked "guessed". It never guesses from codes or all-caps words.
  "All clients / platform-wide" and "Internal" tickets go to no pod and are
  reported.
- Each pod gets one root post and one thread reply per ticket. A pod's log
  note is written as soon as that pod is posted.
- Pod notes are read the way Mem returns them: Markdown escapes, trailing
  backslashes, a `#` title, plain `Channel ID:` fields and numbered lists are
  all handled. `fill-ids` changes only the client lines.
- A card carries the client title, the client paragraph, that pod's own
  clients and the ticket link. A ticket without a client title or paragraph
  is not sent. It never carries the internal summary, developer
  mentions, MR/PR links or another pod's clients.

## [0.21.0] - 2026-10-29

KB articles: one Intercom help-centre article per shipped legacy (M1) feature,
with live screenshots from the demo site. Built and tested over several Cowork
runs on this branch; this entry replaces the interim 0.21.0–0.28.1 notes.

### Added
- **`/kb-articles`** — one article per feature ticket (.docx + PDF + Intercom
  .md), never combined.
  - Modes: an id list (`/kb-articles M1-1520,M1-1256`), `since <date>`,
    `pending` (the KB queue + released features with no article),
    `--kb-queue <ids>` (queue only), `dry-run`, `recheck <run_id>` (re-check a
    finished run with the current rules and repackage, no browser),
    `demo-data` / `demo-data done <ids>`.
  - Flags: `--force`, `--retry-partial`, `--no-capture`, `--yes`,
    `--allow-no-code`.
  - Features only (Optimus `product_type` feature / critical_feature); a ticket
    with no merge request or not found in Optimus is skipped, not a stop. The
    run ends with a table of every ticket given: its files, or why not, and an
    estimated cost.
  - History in Mem: KB Article Ledger (one note per ticket + one per run, so
    nothing is drafted twice), KB Queue, KB Catalogue (an earlier article a new
    feature follows), KB Demo Data.
- **`/release-slack --kb`** — posts the release as usual, queues its feature
  tickets, then offers to draft them in the same chat (y/n; no leaves them
  queued).
- **`/release-note-setup`** — one-time setup per folder, modelled on the QA
  automation plugin's setup: installs Python packages into `.kb-env/` (and
  tesseract / LibreOffice / Carlito where allowed), checks Claude in Chrome and
  the `.env` keys, finds or creates the KB Mem collections, approves the
  Downloads folder in Cowork, and records it all in
  `.medtrics-release-notes/config.json` in the folder (no absolute paths).
  `/kb-articles` only checks it.

### How a run works
- **Where to go.** Each ticket's page and click path come from its MR's QA
  checklist (`*-manual-qa-checklist.txt`, the file QA automation runs), then
  the ticket's own URL/Page field or release checklist; a fixed surface map is
  the fallback. Stale links are caught by a page check before capture
  (`/administration` ↔ `/vadministration`).
- **Capture** in your own signed-in Chrome through the Claude in Chrome
  extension, grouped by sign-in (admin + faculty, then a pause for the learner,
  the external evaluator link you give, then back to admin). Each screen is a
  GIF exported to Downloads (the route Charles's QA plugin uses in Cowork); its
  last frame is the screenshot, ringed and numbered by script. A Haiku agent
  drives Chrome; the main chat installs the page scripts and takes over if the
  agent can't.
- **Writing** by a Sonnet writer per group, checked by format gates G1–G7.
- **Form fills** for screens behind a save, planned by script from the real
  form. The first run of a form is a dry run (every write blocked) that learns
  the dialog and the exact request; later runs save one demo record, letting
  only that request through, once, on a real click of the safe answer ("No,
  don't send notifications"). Approved by you every run. Saved records are
  logged in Mem with a delete-before date; you delete them.
- **Client copy.** The .docx goes straight to Intercom: no draft marks or
  internal text; you OK each screenshot on a contact sheet (hash-checked);
  client-data (BLOCK) frames never ship; a step without a screenshot keeps its
  text.
- **Cost.** Estimated $ per agent call, ticket and run (Haiku $0.10/$0.50,
  Sonnet $2/$10, Opus $4/$20 per 1M input/output tokens; 85 % input assumed;
  main-chat tokens not included) in the end table, the run summary and Mem.

### Safety
- A page write guard blocks every non-GET request (fetch, XHR, beacons, form
  posts, WebSockets, same-origin frames); the one allowed save is armed only by
  a real click on the checked button, for one learned request, within 10 s.
- Clicks are picked by a page script that refuses delete / remove / send /
  publish / portal-switch targets, red buttons and ambiguous matches; a plugin
  hook blocks typing for the capture agent, Enter for the form-fill agent, any
  script that isn't the plugin's own, and navigation off the demo site.
- Claude never types passwords or signs in, never deletes data, and never
  deletes files (leftover Downloads GIFs are moved or listed).
- Known limits: an in-page guard can't be airtight against deliberately hostile
  page code; the tenant-data check is heuristic, which is why you OK every
  screenshot.

### Changed
- Gather reads Optimus `product_type` as the task type and can filter by type
  (`--task-types`) and skip GitLab (`--no-mrs`).

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
