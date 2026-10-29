---
description: Draft one Intercom KB article (.docx + PDF + markdown) per shipped legacy FEATURE ticket, in the Medtrics KB format — grouped by persona × surface, screenshots captured live on the demo tenant (recorded as GIFs in your Chrome) and ringed by script, prose written by a pinned Sonnet writer per group, every article checked by the format gates G1–G7, history kept in Mem so no ticket is drafted twice. Drafts only; publishing stays a human step.
---

# KB Articles

Turn shipped legacy (M1) feature tickets into KB article **drafts** for the
Intercom help centre that CX uses to answer client questions. One article per
ticket — never combined. Output is files for download plus a run summary; Mem
holds the history.

Every token goes to prose. Selection, placement, capture, annotation, evidence,
validation, rendering, PDF, packaging and the ledger are scripts under
`${CLAUDE_PLUGIN_ROOT}/scripts/kb/`. Two agents are used, each with a pinned model:
`kb-classifier` (Haiku, at most one call per run, only for tickets the rules
can't place) and `kb-writer` (Sonnet, one call per writer group, all in parallel).

## Usage

```
/kb-articles M1-1520,M1-1256                # these tickets (non-features are skipped)
/kb-articles since 2026-10-01                # legacy tickets released on/after a date (from the Mem release archive)
/kb-articles pending                         # everything on the KB queue + every released legacy feature with no article yet
/kb-articles --kb-queue M1-1520,M1-1609      # only ADD these to the KB Queue; `pending` drafts them later
/kb-articles M1-1520 dry-run                 # show the plan and exit — no agents, no capture, no files
```

Flags (combine with any mode):

| Flag | Effect |
|---|---|
| `dry-run` | Stop after the plan preview (Step 4). Spawns nothing. |
| `--force` | Re-draft tickets the ledger already covers (updates their notes) |
| `--retry-partial` | Re-draft `partial` tickets (drafts that had missing screenshots) |
| `--no-capture` | Skip Chrome; every article is drafted with missing-screenshot markers |
| `--yes` | Don't wait for confirmation at the plan preview (named ids never ask; this matters for `pending`, `since` and the release hand-off) |
| `--allow-no-code` | Also draft features that have no merge request (skipped by default as "code missing") |
| `--from-release` | Set only by `/release-slack --kb` (Step 9c) with an id list: reuse that release's `/tmp/release_slack_data.json` and `/tmp/release_slack_summaries.txt` instead of gathering again, and mark the tickets' queue notes drained afterwards |

**Two ways in.** Run it yourself (an id list, `since`, `pending`), or let
`/release-slack --kb` hand off to it in the same chat right after a release is
posted: that release's feature tickets are put on the KB queue and this command
starts in `--from-release` mode, shows its plan and asks y/n. Say no and they
stay queued for a later `/kb-articles pending`.

## Queue only: `--kb-queue <ids>`

Adds tickets to the KB Queue and stops — no capture, no agents, no files. It
needs only Optimus (no GitLab) and Mem:

1. Mem probe and the queue + ledger collections, as in Step 0 (no setup step).
2. `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/gather_notes_data_optimus.py tickets <ids> --task-types feature,critical_feature --no-mrs --output $RUN/data.json`
3. `python3 $K/kb_ledger.py enqueue --data $RUN/data.json --source manual --date $TODAY --ids <ids> --ledger-notes $RUN/ledger_notes.json --queue-notes $RUN/queue_notes.json --out $RUN/queue_writes.json`
4. Perform the `create_note` writes (kb-ledger skill), then show the table the
   script printed — one row per id: queued, already queued, or why not (not a
   feature, not found in Optimus, already has an article). `/kb-articles
   pending` drafts them later.

## Pipeline overview

```
0  preflight     paths (kb_paths) + setup check (kb_doctor --check) + Mem probe (hard stop) + .env keys + Claude in Chrome check + Downloads folder (Cowork) + sweep
1  select        ledger + queue dumps → ids (argv | release archive | queue) → gather (features only,
                 skipped with --from-release) → kb_select filter
2  place         kb_select place → [kb-classifier, Haiku, ONE call, only if needed] → merge
3  prepare       kb_select group · kb_ledger match · kb_inputs
3b page check   route-check-js → route-fix (404 → working twin)
3c form fills   kb_data bundle → [kb-data-planner, Sonnet, only if a screen is behind a save] → kb_data check
4  preview       plan + estimate + form fills → confirm (form fills always asked)
5  capture       plan-calls → canary (you) → kb-capturer (Haiku) → kb-data-maker (Sonnet, approved form fills, saves blocked) → sign-in pauses (learner, external, back to admin) → ingest — exit 3 = some screens left, carry on
6  write         kb_spec bundle → [kb-writer, Sonnet, all groups in ONE message] → prose/<ticket>.json
7  assemble      kb_spec assemble (annotate, evidence, gates) → prose retries, ≤2, single article
8  package       kb_usage report → kb_package (docx + md + ONE soffice batch → PDF + verify, summary, zip)
9  record        kb_ledger writes → Mem create/update · kb_ledger drain → queue notes marked drained
10 report        summary in chat + output path
```

## Output

- `<root>/kb-articles/<run_id>/` with, per ticket, `CX-DRAFT_<title>.docx`, `.pdf`,
  `.md` (Intercom body) and `assets/`; `_provenance/<ticket>.md` (the internal
  audit table — kept out of the article); `RUN-SUMMARY.md`; and a zip of the lot.
  `<root>` is `$KB_OUTPUT_DIR`, else `/mnt/user-data/outputs` (Cowork), else
  `.claude/outputs/kb/`.
- Mem: one ledger note per ticket and one run note; queue notes of drafted tickets marked `drained` (`kb-ledger` skill).
- Working files only under `<folder>/.kb-runs/<run_id>/` (it ignores itself in git; the agents can read it in Cowork, where `/tmp` is out of their reach).

Every article is a **draft**. The publish render needs a person to confirm every
screenshot is free of tenant data (`tenant_confirmed_by`), so this command never
produces it.

## Required .env keys

| Key | Purpose | Required |
|---|---|---|
| `OPTIMUS_API_KEY` | gather Optimus tickets | Yes |
| `GITLAB_TOKEN`, `GITLAB_PROJECT_ID` | MR diffs (UI strings, routes) | Yes |
| `GITLAB_URL` | GitLab base URL | optional |
| `MEM_KB_LEDGER_COLLECTION` | ledger collection name (default `KB Article Ledger`) | optional |
| `MEM_KB_CATALOGUE_COLLECTION` | catalogue collection name (default `KB Catalogue`) | optional |
| `MEM_KB_QUEUE_COLLECTION` | queue collection name (default `KB Queue`) | optional |
| `MEM_RELEASE_ARCHIVE_COLLECTION` | release archive, read by `since` / `pending` | optional |
| `KB_OUTPUT_DIR` | output root override | optional |
| `KB_FONT` | TTF used for screenshot badges | optional |

## Required permissions

```json
"Bash(python3:*)",
"Bash(date:*)",
"Bash(mkdir:*)",
"Write(**/.kb-runs/**)",
"Task",
"mcp__claude-in-chrome__*",
"mcp__cowork__request_cowork_directory"
```

`Write(**/.kb-runs/**)` lets the writer and capture agents save their own JSON
inside the run folder, so their output is not re-typed by the orchestrator (that
would pay for it twice). Grant nothing broader to them. The `mcp__claude-in-chrome__*` tools are the Claude in Chrome
extension's (including `gif_creator`); the capture agent (Step 5), the canary and the Step 0 check use them. `mcp__cowork__request_cowork_directory` is Cowork-only: it mounts your Downloads folder, where the exported GIFs land (Step 0).

## Flow

Set once, via Bash, from the selected folder: `K=${CLAUDE_PLUGIN_ROOT}/scripts/kb`,
`RUN_ID=kb-$(date +%Y%m%d-%H%M)`, `TODAY=$(date +%Y-%m-%d)`, then
`python3 $K/kb_paths.py ensure` (prints `KB_RUNS=`, `KB_CONFIG=`; keep both),
`RUN=$KB_RUNS/$RUN_ID` and `mkdir -p $RUN/prose`. Never guess the date. Every
path you hand an agent is an absolute path under `$RUN`.

### Step 0 — Preflight

1. **Mem** — follow the `kb-ledger` skill's probe. Not connected → print its stop
   line and stop. This applies to `dry-run`.
2. **.env** — `python3 $K/kb_paths.py env-check`. Exit 1 → list the missing keys
   it printed and stop (`--kb-queue` needs only `OPTIMUS_API_KEY`).
3. **Setup check** — `python3 $K/kb_doctor.py --check`, run from the folder
   selected in Cowork. It installs nothing: it re-detects the packages and system
   tools that `/release-note-setup` installed, in well under a second. Exit 2 → setup was never run here or the folder was
   reset: print its line ("run /release-note-setup, then re-run") and stop. Do
   not install anything yourself.
   Set `KB_PY` to the `KB_PY=` line it prints — fresh every run, never from a
   file or an earlier chat — and run **every** `$K` script below
   with `$KB_PY`. Keep the recorded items for the preview: no tesseract means
   draft-only anchors; no LibreOffice means no PDF this run. Show any `WARN` line
   (a piece gone since setup, or `CLAUDE_CODE_SUBAGENT_MODEL` overriding the
   pinned models).
4. **Claude in Chrome** — one cheap check on every run (skip with `--no-capture`
   and for `--kb-queue`). Call ToolSearch
   `select:mcp__claude-in-chrome__tabs_context_mcp,mcp__claude-in-chrome__browser_batch,mcp__claude-in-chrome__gif_creator`
   (the canary in Step 5 needs the last two), then call `tabs_context_mcp`. If the tool is missing or the call fails (the
   extension is not installed or not connected), **ask the user**, once, in these words:
   "Screens are captured in your own Chrome with the Claude in Chrome
   extension, and it is not connected. Install it from the Chrome Web Store,
   connect it to this Claude account, sign in to https://medtrics.medtricslab.com
   in that Chrome, then re-run `/kb-articles`. Or I can continue now with
   `--no-capture` (drafts without screenshots)." Wait for the answer: they
   re-run, or say continue and the run is `--no-capture`. Ask only when the check
   fails; when it passes, say nothing. Claude never signs in for you.
5. **Downloads folder** — every run, unless `--no-capture` or `--kb-queue`.
   Capture exports each screen as a GIF with Chrome's download, so the scripts
   must be able to read your Downloads folder. ToolSearch
   `select:mcp__cowork__request_cowork_directory`.
   - **Present (Cowork):** call it with `path: "~/Downloads"` and use the path it
     **returns** as `DL` — every run, never cached from an earlier session (the
     mount path changes). If you decline the prompt, ask once: "Approve the
     Downloads folder (needed to read the screen captures), or continue with
     `--no-capture`?" Approve → call it again; continue → the run is
     `--no-capture`.
   - **Absent (Claude Code):** `DL=~/Downloads`.

   Then sweep leftovers from any crashed earlier run (they may hold tenant data;
   the sweep moves them, never deletes):

   ```bash
   $KB_PY $K/kb_capture.py sweep --downloads-dir $DL --into $RUN/shots/stale
   ```

   Say what it moved, if anything.
6. **Collections** — `python3 $K/kb_paths.py config` holds the ids setup saved
   (`mem.kb_ledger`, `mem.kb_queue`, `mem.kb_catalogue`, `mem.release_archive`).
   Use an id directly; only when one is missing, or a call on it fails, run
   `$KB_PY $K/kb_ledger.py resolve` and resolve that collection by name per the
   `kb-ledger` skill, then save it with `python3 $K/kb_paths.py merge
   '{"mem": {"<key>": "<id>"}}'`. (`since`/`pending` also need the release
   archive.) Save the dumps under `$RUN/`
   (`ledger_notes.json`, `catalogue_notes.json`, `queue_notes.json`,
   `archive_notes.json`).

### Step 1 — Select

```bash
$KB_PY $K/kb_ledger.py index --ledger-notes $RUN/ledger_notes.json --out $RUN/ledger_index.json
```

Ticket ids:
- an id list (`M1-1520,M1-1256`, comma-separated, no keyword) → as given. Anything
  that is not `pending` or `since <date>` is read as the id list.
- `since <date>` → `$KB_PY $K/kb_ledger.py since --archive-notes $RUN/archive_notes.json --date <date>`
- `pending` → `$KB_PY $K/kb_ledger.py pending --archive-notes $RUN/archive_notes.json --ledger-notes $RUN/ledger_notes.json --queue-notes $RUN/queue_notes.json [--retry-partial]`
  (the queue first-class, the release archive as the safety net), then
  `$KB_PY $K/kb_ledger.py queue-summaries --queue-notes $RUN/queue_notes.json --out $RUN/summaries.txt`
  so queued tickets reuse the client title and summary their release wrote.

Then drop what the ledger already covers **before** the gather — a done ticket
must never cost a GitLab call (`pending` already did this):

```bash
$KB_PY $K/kb_ledger.py todo --ids <ids> --ledger-notes $RUN/ledger_notes.json [--force] [--retry-partial]
```

No ids left → print `Nothing to draft — every
ticket already has an article.` and go to Step 10.

**`--from-release`:** do not gather. The release already fetched these tickets
and wrote their client copy:

```bash
cp /tmp/release_slack_data.json $RUN/data.json
cp /tmp/release_slack_summaries.txt $RUN/summaries.txt
```

Otherwise:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/gather_notes_data_optimus.py tickets <ids> \
    --include-description --task-types feature,critical_feature --output $RUN/data.json
```

Then attach each ticket's QA checklist — the `<branch>-manual-qa-checklist.txt`
file the medtrics repo's `sk-manual-qa` skill attaches to the MR (the file QA
automation runs), else a `## QA Checklist` in the description. It names the
page and the click path as they are in the current code, so placement uses it
before the ticket's own links (which go stale). It never stops the run:

```bash
$KB_PY $K/kb_checklist.py attach --data $RUN/data.json
```

Then, either way (a feature with no merge request is **skipped, not a stop**
— "code missing" — and so is an id Optimus doesn't return; both show in the
end-of-run table):

```bash
$KB_PY $K/kb_select.py filter --data $RUN/data.json --ledger-index $RUN/ledger_index.json \
    --ids <ids> --out $RUN/filtered.json [--force] [--retry-partial] [--allow-no-code]
```

### Step 2 — Place

```bash
$KB_PY $K/kb_select.py place --data $RUN/filtered.json --out $RUN/placed.json --bundle $RUN/classifier.md
```

Only if it reports `unplaced` > 0: spawn ONE Task with
`subagent_type: "medtrics-release-notes:kb-classifier"`, `model: "haiku"`, prompt
`Read $RUN/classifier.md and answer as instructed.` Save its reply verbatim to
`$RUN/classifier_answer.txt`, log usage (Step 8 shape, `--agent kb-classifier
--scope classify --model haiku`), then:

```bash
$KB_PY $K/kb_select.py classifier-merge --placed $RUN/placed.json \
    --answer $RUN/classifier_answer.txt --data $RUN/filtered.json
```

### Step 3 — Prepare

```bash
$KB_PY $K/kb_select.py group --placed $RUN/placed.json \
    --run-id $RUN_ID --date $TODAY --mode <ids|since|pending> --out $RUN/groups.json
$KB_PY $K/kb_ledger.py match --data $RUN/filtered.json --catalogue-notes $RUN/catalogue_notes.json \
    --ledger-notes $RUN/ledger_notes.json --out $RUN/priors.json
$KB_PY $K/kb_inputs.py --data $RUN/filtered.json --out $RUN/inputs.json [--summaries $RUN/summaries.txt]
```

### Step 3b — Page check (skip with `--no-capture`)

Ticket links go stale (Sessions moved from `/administration/` to
`/vadministration/`), so check the start pages before anything is planned on
them — you, in the capture tab (from `tabs_context_mcp`), one call:

```bash
mkdir -p $RUN/shots && $KB_PY $K/kb_capture.py route-check-js --plan $RUN/groups.json > $RUN/shots/route_check.js
```

Send that file's text with `javascript_tool` on the tab (it only GETs the start
pages, plus their `/administration` ↔ `/vadministration` twin, and returns a
JSON string). Save the returned string to `$RUN/shots/route_check.json`, then:

```bash
$KB_PY $K/kb_capture.py route-fix --plan $RUN/groups.json --result $RUN/shots/route_check.json --out $RUN/groups.json
```

It moves a 404 start page to its working twin, or marks it `page_not_found`
(that page is skipped and its tickets say why). Exit 3 = the site showed its
sign-in page: ask the owner to sign in to Chrome, then re-run this step. If the
call returned nothing usable (`{}`, empty, an error), say so in one line and go
on without the check.

### Step 3c — Form-fill plan (only when a screen sits behind a save)

Some screens only appear after a save (M1-465's "Notify Participants?" box).
These are reached by **filling the form with demo values and pressing Save
while the page's write guard blocks the save request**, capturing the dialog,
then clicking its cancel-type button. Nothing is ever stored on the demo site.

```bash
$KB_PY $K/kb_data.py bundle --groups $RUN/groups.json --data $RUN/filtered.json --out $RUN/data_planner.md
```

`tickets: 0` → no form fills this run; skip to Step 4. Otherwise spawn ONE Task:
`subagent_type: "medtrics-release-notes:kb-data-planner"`, `model: "sonnet"`,
prompt `Brief: $RUN/data_planner.md. Write the draft to $RUN/data_plan_draft.json.`
Log usage (`--agent kb-data-planner --scope plan --model sonnet`). Then:

```bash
$KB_PY $K/kb_data.py check --draft $RUN/data_plan_draft.json --out $RUN/data_plan.json --today $TODAY [--period-end <YYYY-MM-DD>]
```

Pass `--period-end` when the start page showed the current academic period
(e.g. "Jul 01, 2026 - Jun 30, 2027 (current)" → `2027-06-30`). It drops unsafe
form fills (listed as rejected) and prints the preview lines for Step 4.

### Step 4 — Preview

Show, compactly: tickets kept and skipped (with reasons), writer groups (persona,
tickets), the capture visits per sign-in — admin first, then faculty through
the Personal portal (same sign-in), then learner (you sign in as the learner
when asked), then the external evaluator link — for each ticket with its own
route, the start page and the click path (e.g.
`/vadministration/curriculum/sessions/` → first row → Add schedule), the
uncapturable list (including "no screen": an email or background change),
prior-article hits, and an estimate — writer calls = number of groups,
classifier calls = 0 or 1, one capture call (plus one per sign-in pause), one
form-fill planner and one form-fill agent call when there are form fills.

**Form fills** (from Step 3c): show its preview lines as they are — page and
clicks, each field and value, "presses Save — the save request is blocked;
then clicks <Cancel/No…>", "nothing is stored on the demo site".

If any capture entry is for the external evaluator persona, ask the owner for
the evaluator link now (it stays in the run folder only; it is never written to
the articles, the summary or Mem; opening it may mark that evaluation as
started). No link → those screens are skipped as "external link not given".

Say: "Chrome must be signed in to medtrics.medtricslab.com; don't use that Chrome
window until capture finishes." The capture drives one tab of your Chrome and
returns to the Admin portal at the end. Also say: "Chrome must save downloads to
Downloads without asking (Settings → Downloads → 'Ask where to save each file'
off). GIFs land in Downloads/kb-capture/ and are copied out; you can delete them
at the end." Add both only when capture will run.

`dry-run` → stop here. Then:
- Articles: named ids → go straight on. `pending`, `since` and the release
  hand-off (`--from-release`) → ask `Draft these N articles? (y/n)` unless `--yes`.
- **Form fills: always ask**, even with named ids and `--yes`:
  `Fill these N forms and press Save with saving blocked? Nothing is stored. (y/n)`.
  Yes → `$KB_PY $K/kb_data.py approve --plan $RUN/data_plan.json`. No (or no
  answer) → don't approve; those screens stay "described in words".

### Step 5 — Capture

With `--no-capture` (or a Step 0 answer that ended in "continue"), write
`{"shots": []}` to `$RUN/shots/shots.json` and go on. Otherwise each screen is
recorded with `gif_creator` and exported as a GIF into `$DL/kb-capture/<run_id>/`;
ingest takes the last frame as the screenshot. No screenshot is taken and the
capturer never receives an image.

Safety, all enforced by code, not by the model:
- **Write guard.** The page script installs, on every call, a guard that blocks
  every request that is not a GET (fetch, XHR, beacons, form posts). Nothing
  this command does can save, update or delete on the site — including the Save
  press of a form fill.
- **Click targets.** Every click is chosen by the `kbclick` page script, which
  refuses delete / remove / toggle / portal-switch links, red buttons, write
  words (Save, Send …, outside an approved form fill), submit buttons and POST
  forms, and two different targets with the same text. A first-row click
  happens only when the page shows no hard tenant data, and prefers a real row
  over a course group that only expands.
- **Keys.** The plugin hook (`hooks/kb_guard.py`) blocks typing, keys, drags and
  form filling for the capture agent, and lets the form-fill agent type but
  press only Tab and Escape. For both agents it also denies typed text holding a
  newline or tab, any `javascript_tool` text that does not start with the
  `/*KB*/` marker every generated script carries, and any `navigate` off the demo
  host (or to a delete / remove / logout / portal-switch URL other than the
  configured portal entries). The hook applies to the spawned agents only: when
  you follow `capture.md` yourself (the fallback below), the hook does not apply
  to you; the page guard and `kbclick` still do.

Each screen rings the button you press next on it (the first screen rings the
menu item); a dialog with nothing else to ring gets its title ringed.

1. Plan the calls (no model, no browser):

   ```bash
   $KB_PY $K/kb_capture.py plan-calls --plan $RUN/groups.json --inputs $RUN/inputs.json --out-dir $RUN/shots \
       [--data-plan $RUN/data_plan.json] [--external-link '<link>']
   ```

   `--data-plan` only when the owner approved the form fills. It writes
   `shots/calls.json` (visits, write visits, `accounts`, `canary`, `abort`,
   `gif_dir`) and `shots/capture.md`.
2. **Canary** — you, not an agent, with the owner present. Send the `canary`
   actions from `calls.json` in one `browser_batch` (tab from `tabs_context_mcp`,
   substituted for every `"$TAB"`; first run only: Chrome may show a download or
   privacy prompt — say so and let the owner allow it). Then:

   ```bash
   $KB_PY $K/kb_capture.py pickup --downloads-dir $DL --name kb-capture/$RUN_ID/canary.gif --timeout 20
   ```

   Exit 0 → the GIF arrived and was readable (the canary stays in Downloads
   until the cleanup at the end); go on. Exit 1 `download_missing`, 2
   `empty_export` or 3 `bad_gif` (or the batch itself erroring) → **skip capture
   with that reason**: write `{"shots": []}` to `$RUN/shots/shots.json`, say why
   in one line (Chrome download settings — "Ask where to save each file" must be
   off and Downloads must be the download folder; or a privacy / download
   prompt that was dismissed) and continue to drafting.
3. **Capture, main sign-in.** Spawn ONE Task:
   `subagent_type: "medtrics-release-notes:kb-capturer"`, `model: "haiku"`,
   prompt `Run folder: $RUN. Capture file: $RUN/shots/capture.md. Raw folder: $RUN/shots/raw/.`
   (absolute paths). It writes `raw/<batch_id>.json` and replies
   `KB_CAPTURE_DONE ok=<n> stop=<…>`. Log usage
   (`--agent kb-capturer --scope capture --model haiku`). If it replies
   `KB_CAPTURE NO_CHROME_TOOLS`, or returns without writing any raw file, follow
   `capture.md` yourself in this session with the same tools and rules (the
   QA-automation way); say so in one line.
4. **Form fills** (only when approved, and while the main sign-in is still
   active — before any learner sign-in):

   ```bash
   $KB_PY $K/kb_data.py brief --plan $RUN/data_plan.json --calls $RUN/shots/calls.json --out $RUN/shots/data.md
   ```

   Exit 2 `plan_not_approved` / `plan_changed` → skip form fills, say why. Else
   spawn ONE Task: `subagent_type: "medtrics-release-notes:kb-data-maker"`,
   `model: "sonnet"`, prompt
   `Run folder: $RUN. Brief: $RUN/shots/data.md. Raw folder: $RUN/shots/raw/.`
   It writes `raw/data-<ticket>.json`. Log usage
   (`--agent kb-data-maker --scope fill --model sonnet`).
5. **Sign-in pauses.** If step 3 stopped with `stop=needs_signin:learner`: ask
   the owner, "Sign in to medtrics.medtricslab.com as the learner in this
   Chrome, then say done." Claude never signs in. Then re-spawn the capturer
   with the same prompt plus `--from-batch <that batch id>`. Same for
   `needs_signin:external` (the evaluator link is opened by the capturer from
   `capture.md`). At `stop=needs_signin:main`: ask the owner to sign back in as
   the admin, then re-spawn with `--from-batch <the restore batch>`, which
   returns the tab to the Admin portal.
6. Ingest once, after every capture and form fill has finished (reads each GIF
   from Downloads, copies it to `shots/gifs/` — Downloads itself is never
   changed here — takes the last frame, verifies it, places the ringed boxes,
   runs the tenant screen):

   ```bash
   $KB_PY $K/kb_capture.py ingest --calls $RUN/shots/calls.json --raw-dir $RUN/shots/raw --out-dir $RUN/shots --downloads-dir $DL
   ```
7. Exit 3 = some screens were left. Read `shots/resume.json` and say which, with
   the reason: `expired` (sign in to the site again in Chrome), `wrong_persona`,
   `bridge_down`, `no_probe`, `not_reached` (its batch never ran), `retry` (a
   blocked or unreadable probe), `download_missing`, `empty_export`, `bad_gif`,
   `click_refused` (with why), `click_not_found`, `tenant_row`, `needs_write`
   (the step is behind a save and no form fill was approved), `write_failed`
   (the form fill could not finish), `page_not_found` or
   `external_link_missing`. **Continue** — the shots already taken are kept,
   the rest become missing-screenshot markers and the summary lists them.
   `--retry-partial` re-runs them later.

### Step 6 — Write

```bash
$KB_PY $K/kb_spec.py bundle --groups $RUN/groups.json --inputs $RUN/inputs.json \
    --shots $RUN/shots/shots.json --priors $RUN/priors.json --out-dir $RUN
```

Spawn one Task per writer group, **all in a single message** so they run in
parallel: `subagent_type: "medtrics-release-notes:kb-writer"`, `model: "sonnet"`
(a second pin on top of the agent's own; `sonnet` resolves to the current Sonnet,
5.5 at the time of writing — first drafts stay on it because they carry the
client voice), prompt:

`Group file: $RUN/writer_<group_id>.md. Output folder: $RUN/prose/. Write one JSON file per ticket as instructed.`

After each returns, log its usage:

```bash
$KB_PY $K/kb_usage.py add --run-dir $RUN --agent kb-writer --scope <group_id> --model sonnet \
    --tokens <total_tokens> --duration-ms <ms> --tool-uses <n>
```

If a writer replied with `---KB_SPECS_START---` blocks instead of writing files,
save the reply to `$RUN/reply_<group_id>.txt` and run
`$KB_PY $K/kb_spec.py ingest --reply $RUN/reply_<group_id>.txt --prose-dir $RUN/prose`.
Do not re-type a writer's output yourself.

### Step 7 — Assemble and retry

```bash
$KB_PY $K/kb_spec.py assemble --groups $RUN/groups.json --prose-dir $RUN/prose \
    --shots $RUN/shots/shots.json --priors $RUN/priors.json --out-dir $RUN/specs
```

Evidence problems are fixed by the script (the image becomes a missing-screenshot
marker). If `$RUN/specs/failures.json` is non-empty, those articles broke a prose
gate:

```bash
$KB_PY $K/kb_spec.py retry-bundle --failures $RUN/specs/failures.json \
    --prose-dir $RUN/prose --out-dir $RUN/retry
```

Spawn one `kb-writer` Task per `retry_<ticket>.md` (all in one message), prompt
`Retry file: $RUN/retry/retry_<ticket>.md. Output folder: $RUN/prose/.`, log
usage with `--scope retry:<ticket>`, then re-run `assemble` with
`--tickets <those ids>`. At most **two** retry rounds; tickets still failing are
passed to Step 8 as `--failed-after-retry`.

A retry only fixes what a gate named (a title phrase, a missing bold label, a
banned word), so round 1 runs on the cheaper model — `model: "haiku"` on the Task
call — and only round 2 falls back to `model: "sonnet"`. Log each with the model
it actually used.

### Step 8 — Package

```bash
$KB_PY $K/kb_usage.py report --run-dir $RUN --groups $RUN/groups.json
$KB_PY $K/kb_package.py --run-dir $RUN --groups $RUN/groups.json --specs-dir $RUN/specs \
    --requested <every id the run was given> [--failed-after-retry <ids>]
```

This renders every article (.docx + Intercom .md), converts all of them to PDF in
one LibreOffice call, verifies each PDF, writes the provenance sidecars,
`RUN-SUMMARY.md`, `$RUN/run.json` and the zip.

### Step 9 — Record

```bash
$KB_PY $K/kb_ledger.py writes --run $RUN/run.json --ledger-notes $RUN/ledger_notes.json \
    --out $RUN/ledger_writes.json [--force]
```

Perform the writes per the `kb-ledger` skill (create / update by version; one
retry; manual-paste fallback). Failed tickets get a `failed` note so the next run
retries them.

Then mark the queue entries of every ticket this run drafted (`drafted` or
`partial` in `run.json`) as drained — failed tickets stay queued:

```bash
$KB_PY $K/kb_ledger.py drain --tickets <drafted ids> --queue-notes $RUN/queue_notes.json \
    --run-id $RUN_ID --date $TODAY --out $RUN/queue_drain.json
```

Perform those `update_note` writes the same way. A ticket that was never queued
(a manual id-list run) simply has nothing to drain.

### Step 10 — Report

**Show the every-ticket table `kb_package` printed** — one row per ticket the run
was given: done (with its files and whether screenshots are missing) or not
done and why (code missing, not a feature, not found in Optimus, already has an
article, or the error). Then the output folder and zip path, the counts line, the
uncapturable and left-by-capture lists (with reasons), the
PDF warnings, and the token estimate. Offer the files for download.

**Downloads cleanup** (only when capture ran): ask once, "Delete this run's N
GIFs from Downloads/kb-capture/? (they may show demo data; copies are kept in
the run folder)". Yes → in Cowork, the delete may need your approval for the
Downloads folder; then

```bash
$KB_PY $K/kb_capture.py cleanup --downloads-dir $DL --names-from $RUN/shots/calls.json
```

and say how many it deleted and which it could not. No → list the folder so you
can delete it yourself. A later run's sweep moves any leftovers into that run.

## Failure modes

| Situation | Behaviour |
|---|---|
| Mem not connected | Stop at Step 0 (dry-run too) |
| Setup never run in this folder | Stop at Step 0: run `/release-note-setup`, then re-run |
| Setup could not install tesseract / LibreOffice (no root, no Homebrew) | Recorded once as unavailable; runs continue without OCR / PDF; `/release-note-setup --force` retries |
| `.kb-env/` deleted or the sandbox reset | The setup check stops the run: run `/release-note-setup` again |
| Claude in Chrome missing or not connected | Step 0 asks you to install and connect it and sign in, then re-run; or continue with `--no-capture` |
| Chrome not signed in (`KB_LOGIN`) | Capture stops, shots already taken are kept; sign in again in Chrome; `--retry-partial` re-runs the rest |
| Wrong portal (`KB_PERSONA`) | Capture stops with `wrong_persona`; the rest is left |
| Bridge drops mid-run | `bridge_down`; the rest is left, the Chrome tab is not restored; re-run later |
| Probe blocked or unreadable | That screen is left with reason `retry` |
| Downloads folder not mounted or declined (Cowork) | Step 0 asks once: approve it, or continue with `--no-capture` |
| Canary failed (`download_missing`, `empty_export`, `bad_gif`) | Capture skipped with that reason, drafting continues with missing-screenshot markers; check Chrome's download settings ("Ask where to save each file" off, Downloads as the folder) and any privacy / download prompt, then `--retry-partial` |
| A GIF missing, empty or bad for one screen | That screen is left (`download_missing`, `empty_export`, `bad_gif`), the others are kept; `--retry-partial` re-runs it |
| Leftover GIFs from a crashed or un-cleaned run | Step 0's sweep moves `Downloads/kb-capture/*` into `$RUN/shots/stale/` (never deletes); remove them yourself once checked |
| A click refused or not found | That ticket's remaining screens are left with the reason; the article describes those steps in words |
| Capture agent can't drive Chrome | The orchestrator follows `capture.md` itself (Step 5.3) |
| Form fills declined or not approved | Those screens are described in words (`needs_write`) |
| A form fill's field won't take the value, or the dialog doesn't appear | That screen is left as `write_failed`; nothing was saved |
| Learner / external screens | The run pauses for you to sign in as the learner (or uses the evaluator link you gave), then asks you to sign back in as admin |
| tesseract missing | `dom_box` anchors — fine for drafts, refused on publish |
| LibreOffice missing | No PDFs this run; summary says so; .docx and .md still ship |
| Capture exits 3 | Continue with what was captured; summary lists the rest |
| Writer returns nothing for a ticket | `failed` in summary and ledger; retried next run |
| Gates still refuse after two retries | `failed`; no file written for it |
| A ticket has no merge request | Skipped as "code missing"; the run continues; listed in the end table |
| An id isn't returned by Optimus | Listed as "not found in Optimus"; the run continues |
| Ledger write fails | One retry, then print the note for a manual paste |
| Queue drain write fails | One retry, then say so; the next `pending` sees the ledger note and skips the ticket anyway |
| `--from-release` files missing (`/tmp/release_slack_*` gone) | Fall back to the normal gather for the same ids |
| User says no at the preview of a release hand-off | Nothing drafted; the tickets stay on the queue for `/kb-articles pending` |

## Things this command does NOT do

- Publish anything, or build the publish render.
- Touch Oracle (ORC) tickets, bugs or work types.
- Combine tickets, or update an existing article in place.
- Type a password or sign in for you.
- Save, update or delete anything on the site: every non-GET request is blocked in the page while it runs. Form fills type demo values and press Save only inside that guard, after you approve them, and always end on Cancel / No.
- Type passwords, sign in, or press Enter.
- Close your Chrome tabs.
- Upload to Drive or Intercom.

## Coding standards

- Scripts print one human line plus a `---KB_*_RESULT---` block; read the block,
  don't echo whole JSON files into the conversation.
- Never re-type agent output; never hand-edit specs to get past a gate.
- All working files under `<folder>/.kb-runs/<run_id>/`; deliverables only under the output root.
