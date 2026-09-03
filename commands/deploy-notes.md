---
description: Customer-facing release notes from Optimus tasks and GitLab MRs, written in executive voice for institutional administrators (DIOs, deans, IT directors). Replaces the prior technical-flavored client notes.
---

# Deploy Notes Generator (Executive Voice)

Generate customer-facing release notes for institutional administrators by:

1. Querying internal data sources for the in-scope tickets and code changes.
2. Producing one short, exec-voice **per-ticket summary** per ticket via parallel analyzer agents.
3. Synthesizing those summaries into one cohesive **release narrative** via the bundled `storyteller` skill.
4. Validating the narrative through the bundled `ip-guard` skill before any downstream use.

The output never names internal systems, code artifacts, MR identifiers, file paths, or architecture mechanisms. Ticket identifiers and verbatim titles ARE included (v0.8.0) — clients quote them to support. It reads as an executive briefing, not a developer changelog.

## Usage

```
/deploy-notes                                       # tickets currently deploy-ready
/deploy-notes status                                # same as above (explicit)
/deploy-notes tickets M1-583,GEN-128592,M1-848      # specific tickets from a deploy
/deploy-notes mr 6567                               # single MR
/deploy-notes status dry-run                        # preview discovery only
/deploy-notes tickets M1-583,GEN-128592 dry-run
/deploy-notes mr 6567 dry-run
```

Output directory: `.claude/outputs/notes/deploy/` (create with `mkdir -p` if missing).

Output filename:

- **status mode**: `release-notes-<YYYY-MM-DD>.md`
- **tickets mode**: `release-notes-tickets-<YYYY-MM-DD>.md`
- **mr mode**: `release-notes-mr-<iid>.md`

Compute the date via `date +%Y-%m-%d` — do not hardcode.

## Required permissions

In `.claude/settings.local.json` under `permissions.allow`:

```json
"Bash(python3:*)",
"Bash(date:*)",
"Bash(cat:*)",
"Task",
"Skill"
```

## Required env (in host-repo `.env`)

| Key | Purpose |
|---|---|
| `OPTIMUS_API_KEY` | gather upstream task data |
| `GITLAB_TOKEN` | gather upstream code-change data |
| `GITLAB_PROJECT_ID` | which project to query |
| `GITLAB_URL` | optional, defaults to `https://gitlab.com` |

A missing key aborts the command before any gather call.

## Pipeline

### Step 1 — Gather data

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/gather_notes_data_optimus.py status --output /tmp/release_notes_data.json --include-description
```

For `tickets` or `mr` modes, substitute the corresponding subcommand and parameter. For `dry-run`, append `--dry-run` to the gather call, print the summary, and stop.

### Step 2 — Per-ticket exec-voice analysis (parallel)

#### 2a. Write the analyzer instructions file

The analyzer's job is **not** to summarize the code — it is to translate one ticket into one institutional-level outcome sentence. The Storyteller will synthesize across all of them.

```bash
cat > /tmp/rn_instructions.txt << 'INSTRUCTIONS_EOF'
You are summarizing one ticket for a customer-facing release narrative.

The audience is an institutional administrator — a designated institutional
official (DIO), dean, program director, or IT director. They do not write
code. They want to know what their people can now do, what got better, and
what they need to be aware of.

You are given an internal task title, description, and code-change diffs.
You will NOT mention any of the following in your output:

  - The names of internal tools (issue tracker, source-control host, cloud,
    observability vendors, framework names, language names, datastores).
  - Code artifact concepts: serializer, viewset, model, migration, endpoint,
    queryset, schema, index, foreign key, middleware, signal, admin, settings,
    manage.py, requirements.txt, Dockerfile, route, URL pattern.
  - Architecture mechanism vocabulary: queue, worker, cache, cron, daemon,
    background job, webhook (rewrite as "notification"), task queue, race
    condition, deadlock, memory leak, load balancer.
  - Any ticket identifier (M1-NNN, GEN-NNNNNN, DEV-NNN, RD-NNN, do-NNN).
  - Any MR identifier or merge request reference (!NNNN, MR 1234, branch name).
  - Any file path (src/..., *.py, *.ts, *.tsx, /api/..., /migrations/...).
  - Any third-party customer institution name. Abstract to the segment.

Process:

  1. Read the task description and the diffs.
  2. Determine what the institutional reader's people can now do, or what
     improved for them, or what they need to be aware of.
  3. If the change is a refactor or a purely internal improvement with no
     user-visible effect, return: NO_USER_VISIBLE_CHANGE
  4. Otherwise, return EXACTLY this block:

OUTCOME: {one or two sentences, plain English, lead with the user outcome}
DOMAIN: {one of: evaluations, scheduling, duty_hours, reporting, access,
                communications, integrations, performance, other}
KIND: {one of: NEW, IMPROVED, RESOLVED, AWARENESS}

Voice rules for OUTCOME:

  - Active voice. Present tense for capabilities. Past tense for completed events.
  - Lead with WHO can now do WHAT. "Program directors can now…", "Residents
    submitting duty hours now…", "Evaluation reminders now…"
  - For bug fixes, frame as the new correct behavior, not as a confession.
    Say "Evaluation submissions now save consistently when residents work
    offline," not "We fixed a bug where evaluations weren't saving."
  - No marketing words: thrilled, excited, delighted, revolutionize,
    game-changing, next-generation, powerful, cutting-edge, best-in-class.
  - No CTAs. No "click here," "learn more," "try it today."
  - Length: 15–40 words. Cut every adverb you can.

OUTPUT FORMAT — Return EXACTLY the three lines above (or NO_USER_VISIBLE_CHANGE).
No preamble. No commentary outside the block.
INSTRUCTIONS_EOF
```

#### 2b. Prepare prompt files

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/prepare_note_prompts.py \
    --data /tmp/release_notes_data.json \
    --instructions /tmp/rn_instructions.txt \
    --prefix rn \
    --output-manifest /tmp/rn_manifest.json
```

Read `/tmp/rn_manifest.json` to get the list of prompt files.

#### 2c. Spawn ALL analyzer agents in ONE message

For every prompt file in the manifest (single or chunk):

- Use the Task tool with `subagent_type: "general-purpose"`
- `description`: `"Summarize <identifier>"` (chunks: `"Analyze <identifier> chunk <N>"`)
- `prompt`: `Read the file <filepath> and follow the instructions it contains.`

All Task calls go in the same assistant message.

#### 2d. Collect results

For each ticket, you now have either:

- A three-line `OUTCOME / DOMAIN / KIND` block, or
- `NO_USER_VISIBLE_CHANGE` (drop from downstream).

For chunked tickets, combine the chunk results into a single block by selecting the most user-visible outcome across chunks.

Now attach the ticket's **identifier** and **verbatim title** to each surviving block. Read them from `/tmp/release_notes_data.json` (`issues[].identifier` and `issues[].title`) — join by identifier. The title is reproduced EXACTLY as it appears in the source ticket; do not clean, shorten, or rephrase it (verbatim titles are the shipped default — see `skills/storyteller/SKILL.md` → "Title handling"). This is what lets the customer reference a change with support by its identifier.

Write the collected per-ticket blocks to `/tmp/rn_summaries.txt`, one block per ticket, separated by `---`. Each block has this shape:

```
IDENTIFIER: {e.g. M1-1054}
TITLE: {verbatim ticket title, exactly as in the source}
OUTCOME: {the exec-voice outcome from the analyzer}
KIND: {NEW | IMPROVED | RESOLVED | AWARENESS}
```

`AWARENESS`-kind items (default-behavior changes, new/changed permissions) become the `[!HEADSUP]` entries in the storyteller output.

### Step 3 — Synthesize via the storyteller skill

Invoke the bundled `medtrics-release-notes:storyteller` skill, passing it `/tmp/rn_summaries.txt` (identifier + verbatim title + outcome + kind per ticket) and today's date. The skill produces the exec-voice draft markdown.

The skill's output structure is fixed — one entry per ticket, each headed by its identifier and verbatim title (see `skills/storyteller/SKILL.md`):

```
# Medtrics Release: {Weekday}, {Month} {D}, {YYYY}
## Key takeaways                 (3–5 one-line bullets; each ends with its [TICKET-ID] chip; "Heads up:" prefix for any awareness item)
## Summary
## In this release
  ### [{TICKET-ID}] {verbatim ticket title}     (each entry opens with a bold one-line takeaway, then 1–3 supporting sentences)
  ### [!HEADSUP] [{TICKET-ID}] {verbatim title} (awareness entries — default-behavior/permission changes — carry the [!HEADSUP] marker; downstream consumers surface them as "Heads up" items)
## Looking ahead                 (optional)
```

The per-ticket `### [ID] Title` headers, the bold ledes, and the Key-takeaways `[ID]` chips are what make the brief scannable in 15 seconds AND let a client cite any change by its identifier. They are not optional decoration; downstream consumers treat them as the entry points. Entries are ordered new/improved first, fixes next, `[!HEADSUP]` awareness entries last.

Write the storyteller draft to `/tmp/release_notes_draft.md`. This is a draft path on purpose — the final file lands after the copy-audit and ip-guard passes.

### Step 4 — Polish via the copy-audit skill

Invoke the bundled `medtrics-release-notes:copy-audit` skill against `/tmp/release_notes_draft.md`. The skill applies the team's strict human-writing rules — strips AI-sounding language, bloated verbs, dead openings, negative-parallelism patterns, decorative metaphor verbs, and engagement bait. It returns a tightened version of the same content.

Write the polished output to `.claude/outputs/notes/deploy/release-notes-<YYYY-MM-DD>.md`. This is the file the ip-guard scans and `/release-slack` composes the client message from.

The copy-audit and storyteller skills are deliberately decoupled: the storyteller decides what to say and how to group it; the copy-audit pass decides how to say each sentence cleanly. If a copy-audit edit changes meaning, prefer the storyteller's wording — copy-audit's job is sentence-level cleanup, not content rewrites.

### Step 5 — Validate via the ip-guard

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/ip_guard.py \
    --input .claude/outputs/notes/deploy/release-notes-<YYYY-MM-DD>.md \
    --json-out /tmp/ip_guard_result.json
guard_exit=$?
```

If `guard_exit == 0`: proceed. The markdown is delivery-clean.

As of v0.8.0 the guard allows ticket identifiers everywhere and skips the `### [ID] Title` header lines (the verbatim titles are an intended client-facing reference). It still blocks internal system names, code artifacts, file paths, MR references, and mechanism vocabulary in the narrative prose — so any failure now points at the description prose of an entry, not at its header.

If `guard_exit == 2`: at least one blocking violation. Read `/tmp/ip_guard_result.json`, extract the `violations` array, and re-invoke the storyteller with the rewrite prompt:

> The following sentences in the entry descriptions contain content that must not appear in customer-facing copy. Rewrite each in the same executive voice. Do not introduce code artifacts, internal system names, MR references, file paths, or mechanism vocabulary into the description prose. Ticket identifiers and the verbatim `### [ID] Title` headers are allowed and must be preserved. Preserve the user-outcome of each sentence.
>
> Violations: {list each violation with line number, match, suggestion, and the surrounding sentence}

Re-run the guard. Iterate up to three times. If still failing after the third attempt, stop, surface the residual violations to the user with line offsets, and exit non-zero. Do not proceed to Slack.

If `guard_exit == 1`: configuration error. Surface the script's stderr and stop.

## Things this command does NOT do

- Does not generate the integration-notes document — that's `/integration-notes`, which has a different audience (technical integration partners) and is exempt from the ip-guard.
- Does not post to Slack — that's `/release-slack`.
- Does not retry data-gather failures.
