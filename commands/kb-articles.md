---
description: Draft one Intercom KB article (.docx + PDF + markdown) per shipped legacy FEATURE ticket, in the Medtrics KB format — grouped by persona × surface, screenshots captured live on the demo tenant and ringed by script, prose written by a pinned Sonnet writer per group, every article checked by the format gates G1–G7, history kept in Mem so no ticket is drafted twice. Drafts only; publishing stays a human step.
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
/kb-articles M1-1520 dry-run                 # show the plan and exit — no agents, no capture, no files
```

Flags (combine with any mode):

| Flag | Effect |
|---|---|
| `dry-run` | Stop after the plan preview (Step 4). Spawns nothing. |
| `--force` | Re-draft tickets the ledger already covers (updates their notes) |
| `--retry-partial` | Re-draft `partial` tickets (drafts that had missing screenshots) |
| `--no-capture` | Skip the browser; every article is drafted with missing-screenshot markers |
| `--yes` | Don't wait for confirmation at the plan preview |
| `--from-release` | Set only by `/release-slack --kb` (Step 9c) with an id list: reuse that release's `/tmp/release_slack_data.json` and `/tmp/release_slack_summaries.txt` instead of gathering again, and mark the tickets' queue notes drained afterwards |

**Two ways in.** Run it yourself (an id list, `since`, `pending`), or let
`/release-slack --kb` hand off to it in the same chat right after a release is
posted: that release's feature tickets are put on the KB queue and this command
starts in `--from-release` mode, shows its plan and asks y/n. Say no and they
stay queued for a later `/kb-articles pending`.

## Pipeline overview

```
0  preflight     kb_doctor.py + Mem probe (hard stop) + .env keys
1  select        ledger + queue dumps → ids (argv | release archive | queue) → gather (features only,
                 skipped with --from-release) → kb_select filter
2  place         kb_select place → [kb-classifier, Haiku, ONE call, only if needed] → merge
3  prepare       kb_capture check · kb_select group · kb_ledger match · kb_inputs
4  preview       plan + estimate → confirm (or stop on dry-run)
5  capture       kb_capture run (Playwright, headless, saved sessions) — exit 3 on expiry, carry on
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
- Working files only under `/tmp/kb/<run_id>/`.

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
| `KB_SESSION_DIR` | where the demo sessions are saved (default `.kb-sessions`) | optional |
| `KB_OUTPUT_DIR` | output root override | optional |
| `KB_FONT` | TTF used for screenshot badges | optional |

## Required permissions

```json
"Bash(python3:*)",
"Bash(date:*)",
"Bash(mkdir:*)",
"Write(//tmp/kb/**)",
"Task"
```

`Write(//tmp/kb/**)` lets the writer agents save their own JSON, so their output
is not re-typed by the orchestrator (that would pay for it twice). Grant nothing
broader to them.

## Flow

Set once, via Bash: `RUN_ID=kb-$(date +%Y%m%d-%H%M)`, `TODAY=$(date +%Y-%m-%d)`,
`RUN=/tmp/kb/$RUN_ID`, then `mkdir -p $RUN/prose`. Never guess the date.
`K=${CLAUDE_PLUGIN_ROOT}/scripts/kb`.

### Step 0 — Preflight

1. **Mem** — follow the `kb-ledger` skill's probe. Not connected → print its stop
   line and stop. This applies to `dry-run`.
2. **.env** — `OPTIMUS_API_KEY`, `GITLAB_TOKEN`, `GITLAB_PROJECT_ID` present, else
   list the missing keys and stop.
3. **Doctor** — `python3 $K/kb_doctor.py`. Exit 1 (python-docx or Pillow missing)
   → show its install lines and stop. Otherwise keep its output for the preview:
   no Playwright means `--no-capture` is implied; no tesseract means draft-only
   anchors; no LibreOffice means no PDF this run. Show any `WARN` line (e.g.
   `CLAUDE_CODE_SUBAGENT_MODEL` overriding the pinned models).
4. **Collections** — `python3 $K/kb_ledger.py resolve`, then resolve the ledger,
   catalogue and queue collections (and, for `since`/`pending`, the release
   archive) by name per the `kb-ledger` skill, and save the dumps under `$RUN/`
   (`ledger_notes.json`, `catalogue_notes.json`, `queue_notes.json`,
   `archive_notes.json`).

### Step 1 — Select

```bash
python3 $K/kb_ledger.py index --ledger-notes $RUN/ledger_notes.json --out $RUN/ledger_index.json
```

Ticket ids:
- an id list (`M1-1520,M1-1256`, comma-separated, no keyword) → as given. Anything
  that is not `pending` or `since <date>` is read as the id list.
- `since <date>` → `python3 $K/kb_ledger.py since --archive-notes $RUN/archive_notes.json --date <date>`
- `pending` → `python3 $K/kb_ledger.py pending --archive-notes $RUN/archive_notes.json --ledger-notes $RUN/ledger_notes.json --queue-notes $RUN/queue_notes.json [--retry-partial]`
  (the queue first-class, the release archive as the safety net), then
  `python3 $K/kb_ledger.py queue-summaries --queue-notes $RUN/queue_notes.json --out $RUN/summaries.txt`
  so queued tickets reuse the client title and summary their release wrote.

Then drop what the ledger already covers **before** the gather — a done ticket
must never cost a GitLab call (`pending` already did this):

```bash
python3 $K/kb_ledger.py todo --ids <ids> --ledger-notes $RUN/ledger_notes.json [--force] [--retry-partial]
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

Then, either way:

```bash
python3 $K/kb_select.py filter --data $RUN/data.json --ledger-index $RUN/ledger_index.json \
    --ids <ids> --out $RUN/filtered.json [--force] [--retry-partial]
```

### Step 2 — Place

```bash
python3 $K/kb_select.py place --data $RUN/filtered.json --out $RUN/placed.json --bundle $RUN/classifier.md
```

Only if it reports `unplaced` > 0: spawn ONE Task with
`subagent_type: "medtrics-release-notes:kb-classifier"`, `model: "haiku"`, prompt
`Read $RUN/classifier.md and answer as instructed.` Save its reply verbatim to
`$RUN/classifier_answer.txt`, log usage (Step 8 shape, `--agent kb-classifier
--scope classify --model haiku`), then:

```bash
python3 $K/kb_select.py classifier-merge --placed $RUN/placed.json \
    --answer $RUN/classifier_answer.txt --data $RUN/filtered.json
```

### Step 3 — Prepare

```bash
python3 $K/kb_capture.py check --out $RUN/sessions.json          # skip with --no-capture / no Playwright
python3 $K/kb_select.py group --placed $RUN/placed.json --sessions $RUN/sessions.json \
    --run-id $RUN_ID --date $TODAY --mode <ids|since|pending> --out $RUN/groups.json
python3 $K/kb_ledger.py match --data $RUN/filtered.json --catalogue-notes $RUN/catalogue_notes.json \
    --ledger-notes $RUN/ledger_notes.json --out $RUN/priors.json
python3 $K/kb_inputs.py --data $RUN/filtered.json --out $RUN/inputs.json [--summaries $RUN/summaries.txt]
```

### Step 4 — Preview

Show, compactly: tickets kept and skipped (with reasons), writer groups (persona,
tickets), capture visits per persona and each persona's session state, the
uncapturable list, prior-article hits, and an estimate — writer calls =
number of groups, classifier calls = 0 or 1, input size from `kb_inputs`.

For every persona whose session is `missing` or `expired`, print the line a
person must run (never run it yourself):
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/kb/kb_capture.py login --persona <p>`.
Its tickets will be drafted without screenshots this run.

`dry-run` → stop here. Otherwise ask `Draft these N articles? (y/n)` unless `--yes`.

### Step 5 — Capture

Skip with `--no-capture` or when Playwright is missing (write `{"shots": []}` to
`$RUN/shots/shots.json`). Otherwise:

```bash
python3 $K/kb_capture.py run --plan $RUN/groups.json --inputs $RUN/inputs.json --out-dir $RUN/shots
```

Exit 3 = a session expired: say which persona, and **continue** — the shots
already taken are kept, the rest become missing-screenshot markers, and the
summary lists what was left. Never retry the sign-in.

### Step 6 — Write

```bash
python3 $K/kb_spec.py bundle --groups $RUN/groups.json --inputs $RUN/inputs.json \
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
python3 $K/kb_usage.py add --run-dir $RUN --agent kb-writer --scope <group_id> --model sonnet \
    --tokens <total_tokens> --duration-ms <ms> --tool-uses <n>
```

If a writer replied with `---KB_SPECS_START---` blocks instead of writing files,
save the reply to `$RUN/reply_<group_id>.txt` and run
`python3 $K/kb_spec.py ingest --reply $RUN/reply_<group_id>.txt --prose-dir $RUN/prose`.
Do not re-type a writer's output yourself.

### Step 7 — Assemble and retry

```bash
python3 $K/kb_spec.py assemble --groups $RUN/groups.json --prose-dir $RUN/prose \
    --shots $RUN/shots/shots.json --priors $RUN/priors.json --out-dir $RUN/specs
```

Evidence problems are fixed by the script (the image becomes a missing-screenshot
marker). If `$RUN/specs/failures.json` is non-empty, those articles broke a prose
gate:

```bash
python3 $K/kb_spec.py retry-bundle --failures $RUN/specs/failures.json \
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
python3 $K/kb_usage.py report --run-dir $RUN --groups $RUN/groups.json
python3 $K/kb_package.py --run-dir $RUN --groups $RUN/groups.json --specs-dir $RUN/specs \
    [--failed-after-retry <ids>]
```

This renders every article (.docx + Intercom .md), converts all of them to PDF in
one LibreOffice call, verifies each PDF, writes the provenance sidecars,
`RUN-SUMMARY.md`, `$RUN/run.json` and the zip.

### Step 9 — Record

```bash
python3 $K/kb_ledger.py writes --run $RUN/run.json --ledger-notes $RUN/ledger_notes.json \
    --out $RUN/ledger_writes.json [--force]
```

Perform the writes per the `kb-ledger` skill (create / update by version; one
retry; manual-paste fallback). Failed tickets get a `failed` note so the next run
retries them.

Then mark the queue entries of every ticket this run drafted (`drafted` or
`partial` in `run.json`) as drained — failed tickets stay queued:

```bash
python3 $K/kb_ledger.py drain --tickets <drafted ids> --queue-notes $RUN/queue_notes.json \
    --run-id $RUN_ID --date $TODAY --out $RUN/queue_drain.json
```

Perform those `update_note` writes the same way. A ticket that was never queued
(a manual id-list run) simply has nothing to drain.

### Step 10 — Report

Print the output folder and zip path, the counts line from `kb_package`, the
uncapturable and left-by-expiry lists, any `login` lines a person must run, the
PDF warnings, and the token estimate. Offer the files for download.

## Failure modes

| Situation | Behaviour |
|---|---|
| Mem not connected | Stop at Step 0 (dry-run too) |
| python-docx / Pillow missing | Stop at Step 0 with install lines |
| Playwright missing | Implied `--no-capture`; drafts carry markers |
| tesseract missing | `dom_box` anchors — fine for drafts, refused on publish |
| LibreOffice missing | No PDFs this run; summary says so; .docx and .md still ship |
| Session missing / expired | That persona's tickets drafted without screenshots; `login` line printed |
| Capture exits 3 mid-run | Continue with what was captured; summary lists the rest |
| Writer returns nothing for a ticket | `failed` in summary and ledger; retried next run |
| Gates still refuse after two retries | `failed`; no file written for it |
| Ledger write fails | One retry, then print the note for a manual paste |
| Queue drain write fails | One retry, then say so; the next `pending` sees the ledger note and skips the ticket anyway |
| `--from-release` files missing (`/tmp/release_slack_*` gone) | Fall back to the normal gather for the same ids |
| User says no at the preview of a release hand-off | Nothing drafted; the tickets stay on the queue for `/kb-articles pending` |

## Things this command does NOT do

- Publish anything, or build the publish render.
- Touch Oracle (ORC) tickets, bugs or work types.
- Combine tickets, or update an existing article in place.
- Type a password, run `kb_capture.py login`, or move session files.
- Upload to Drive or Intercom.

## Coding standards

- Scripts print one human line plus a `---KB_*_RESULT---` block; read the block,
  don't echo whole JSON files into the conversation.
- Never re-type agent output; never hand-edit specs to get past a gate.
- All working files under `/tmp/kb/<run_id>/`; deliverables only under the output root.
