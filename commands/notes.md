---
description: Generate deploy and integration release notes in parallel
---

# All Notes Generator

Generates both note types in parallel: deploy (release) notes and integration change notes. (Dev notes moved to the `medtrics-dev-workflow` plugin in 0.3.0.)

## Usage

```
/notes                              # Issues currently in QA1, QA2, Code Review, Needs Changes, Pipeline Issue
/notes status                       # Same as above (explicit)
/notes tickets M1-583,GEN-128592,M1-848   # Specific tickets from a deploy
/notes mr 6567                      # Analyze a single GitLab MR
/notes status dry-run               # Preview issues and matched MRs (no analysis)
/notes tickets M1-583,GEN-128592 dry-run
/notes mr 6567 dry-run
```

## Required Permissions

For autonomous execution without per-command approval prompts, ensure these are in `.claude/settings.local.json` under `permissions.allow`:

```json
"Bash(python3:*)",
"Bash(date:*)",
"Bash(cat:*)",
"Bash(cp:*)",
"Bash(mkdir:*)",
"Task"
```

## Arguments

Parse `$ARGUMENTS` exactly as the individual commands do:

- **No arguments or `status`**: Status mode
- **`tickets <id1,id2,...>`**: Tickets mode — comma-separated list of Optimus task identifiers
- **`mr <iid>`**: Single MR mode
- **`dry-run`** (can be appended to any mode): Preview only

## Execution

### Step 1: Gather data once, then derive per-command data files

Run the shared data gathering script **once** with `--include-description` (all commands now use it). Then copy and filter for each command's needs.

**Status mode:**
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/gather_notes_data_optimus.py status --output /tmp/release_notes_data.json --include-description
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/gather_notes_data_optimus.py status --output /tmp/integration_notes_data.json --include-description --filter-integration
```

**Tickets mode:**
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/gather_notes_data_optimus.py tickets <id1,id2,...> --output /tmp/release_notes_data.json --include-description
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/gather_notes_data_optimus.py tickets <id1,id2,...> --output /tmp/integration_notes_data.json --include-description --filter-integration
```

**MR mode:**
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/gather_notes_data_optimus.py mr <iid> --output /tmp/release_notes_data.json --include-description
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/gather_notes_data_optimus.py mr <iid> --output /tmp/integration_notes_data.json --include-description --filter-integration
```

If `dry-run`, add `--dry-run` to the first command. Display the summary output and stop. Do not proceed further.

**Note:** The integration data still requires a second script run because `--filter-integration` filters diffs at fetch time (removing non-integration files and discarding MRs with zero relevant diffs). This cannot be done by post-processing the full data file.

### Step 2: Prepare prompts for both note types (orchestrator does this)

**IMPORTANT: The orchestrator (you) must do ALL prompt preparation. Sub-agents cannot reliably use Bash or Write tools due to permission inheritance limitations.**

#### 2a. Write instruction files

Write the 2 instruction files. These contain the analysis instructions that get appended to each prompt.

**Deploy (release) instructions — `/tmp/rn_instructions.txt`:** copy the analyzer
instruction block VERBATIM from `${CLAUDE_PLUGIN_ROOT}/commands/deploy-notes.md`
Step 2a (the heredoc content that produces the `OUTCOME / DOMAIN / KIND` block).
Read that file and write its instruction text to `/tmp/rn_instructions.txt` —
do NOT paraphrase or substitute another format. `deploy-notes.md` is the single
source of truth for the exec-voice analyzer contract; the downstream assembly
in that command consumes exactly those blocks.

**Integration instructions — `/tmp/in_instructions.txt`:**

```bash
cat > /tmp/in_instructions.txt << 'INSTRUCTIONS_EOF'
Analyze this issue's integration-relevant diffs for a client-facing change notes document.

INSTRUCTIONS:
1. Read the diffs carefully — identify what fields, endpoints, permissions, or schema elements were added, changed, or removed.
2. Use codebase context — Read source files on the current branch to understand the BASELINE structure (what existed before the MR). The diff shows what changed. For example:
   - If a serializer field was added, check the model to understand the field type and whether it's nullable/optional.
   - If a viewset method was changed, check the URL routing to understand which endpoint is affected.
   - If a migration was added, the diff already contains the migration content.
3. Classify the change as:
   - BREAKING: Removals, renames, type changes, permission restrictions — anything that will break existing integrations
   - NON-BREAKING: New optional fields, new endpoints, new filters — additions that don't affect existing behavior
   - DEPRECATION: Features marked for future removal

OUTPUT FORMAT — Return EXACTLY this markdown structure (no preamble, no explanation outside the structure):

### [BREAKING|NON-BREAKING|DEPRECATION] Short description
**Source:** {issue_identifier} — {issue_title} (MR !{iid})
**Files:** {list of affected files}

**What changed:**
Description of the change with specifics (field names, types, endpoints, etc.).

**Impact:**
How this affects API consumers.

**Migration guide:** (only for BREAKING and DEPRECATION)
Step-by-step instructions for updating integrations.

If the issue's MR(s) have no meaningful integration impact after analysis (e.g., whitespace-only migration), return exactly:
SKIP: {issue_identifier} — {reason}
INSTRUCTIONS_EOF
```

#### 2b. Run the prompt preparation script for both types

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/prepare_note_prompts.py \
    --data /tmp/release_notes_data.json \
    --instructions /tmp/rn_instructions.txt \
    --prefix rn \
    --output-manifest /tmp/rn_manifest.json

python3 ${CLAUDE_PLUGIN_ROOT}/scripts/prepare_note_prompts.py \
    --data /tmp/integration_notes_data.json \
    --instructions /tmp/in_instructions.txt \
    --prefix in \
    --output-manifest /tmp/in_manifest.json
```

Read both manifests to confirm prompt counts.

### Step 3: Spawn 2 command agents in ONE message

**HARD RULE: Spawn BOTH agents in a SINGLE assistant message. Do NOT spawn them sequentially.**

The two agents follow **different** flows — deploy-notes runs a storyteller → copy-audit chain, integration-notes runs a single analyze-and-assemble pass — so they get **different prompts** (an earlier shared template silently skipped copy-audit and left the deploy output unguarded). Both return their final markdown between `---OUTPUT_START---` / `---OUTPUT_END---`; the orchestrator writes files and, for deploy only, runs the IP guard in Step 4.

**Agent 1 — Deploy Notes** (`subagent_type: "general-purpose"`, `description: "Generate deploy notes"`):
```
Read ${CLAUDE_PLUGIN_ROOT}/commands/deploy-notes.md for the exec-voice output format and assembly rules.

You are generating the client-facing DEPLOY (release) notes markdown. Data gathering (Step 1) and prompt preparation (Step 2b) are ALREADY done by the orchestrator — do NOT run the gather script, prepare_note_prompts.py, or write instruction files.

The manifest of prompt files is at /tmp/rn_manifest.json. Read it to get the prompt file list, then run deploy-notes.md's flow:
  - Step 2c: spawn ALL per-ticket analyzer agents in ONE message.
  - Step 2d: collect the surviving blocks and attach each ticket's identifier + verbatim title from /tmp/release_notes_data.json → /tmp/rn_summaries.txt.
  - Step 3: invoke the `medtrics-release-notes:storyteller` skill on /tmp/rn_summaries.txt to produce the exec-voice draft.
  - Step 4: invoke the `medtrics-release-notes:copy-audit` skill to polish that draft.

Then OUTPUT the FINAL copy-audited markdown as your response, between the exact lines "---OUTPUT_START---" and "---OUTPUT_END---", and nothing else outside the markers. Do NOT run ip_guard.py and do NOT write the .claude/outputs file — the orchestrator runs the IP guard and writes the file from your returned content. Keep any intermediate drafts in /tmp only.

Today's date: <TODAY>
```

**Agent 2 — Integration Notes** (`subagent_type: "general-purpose"`, `description: "Generate integration notes"`):
```
Read ${CLAUDE_PLUGIN_ROOT}/commands/integration-notes.md for the output format and assembly rules.

You are generating the integration change-notes markdown (technical audience; NOT guarded). Data gathering (Step 1) and prompt preparation (Step 2a) are ALREADY done by the orchestrator — do NOT run the gather script, prepare_note_prompts.py, or write instruction files.

The manifest of prompt files is at /tmp/in_manifest.json. Read it, then run integration-notes.md from Step 2b (spawn ALL analyzer agents in ONE message) through Step 3 (Assemble Output).

Instead of writing to a file, OUTPUT the full assembled markdown as your response, between the exact lines "---OUTPUT_START---" and "---OUTPUT_END---", and nothing else outside the markers. Do NOT use Bash or Write for the final output — just return the content.

Today's date: <TODAY>
```

Compute `<TODAY>` via `date +%Y-%m-%d` before spawning agents.

### Step 4: Write outputs and report results

After both agents complete, extract the markdown content from each agent's result (between `---OUTPUT_START---` and `---OUTPUT_END---` markers) and write to the final output files:

```bash
mkdir -p .claude/outputs/notes/deploy .claude/outputs/notes/integration
```

Determine the output filename suffix based on mode:
- **status mode**: `status-<YYYY-MM-DD>` (use the same date computed earlier)
- **tickets mode**: `tickets-<YYYY-MM-DD>`
- **mr mode**: `mr-<iid>`

Write each file using the Write tool:
- `.claude/outputs/notes/deploy/deploy-notes-<suffix>.md`
- `.claude/outputs/notes/integration/integration-notes-<suffix>.md`

**IP guard over the deploy markdown (orchestrator-run).** The deploy notes are customer-facing, so — exactly as the standalone `/deploy-notes` does at its Step 5 — run the guard over the written deploy file (the sub-agent could not, because `/notes` agents are barred from Bash):

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/ip_guard.py \
    --input .claude/outputs/notes/deploy/deploy-notes-<suffix>.md \
    --json-out /tmp/notes_ip_guard.json
guard_exit=$?
```

- `0` (PASS): done — report both files as generated.
- `2` (FAIL): read `/tmp/notes_ip_guard.json`, print the `violations` with their line offsets, and tell the user the deploy file did **not** pass the confidentiality guard. Do not silently present it as clean. `/notes` is a backup/paper-trail command and does not run the storyteller-rewrite remediation loop — direct the user to `/deploy-notes` (standalone, which iterates the guard up to 3×) or `/release-slack` for a guaranteed-guarded deploy document. The integration markdown is intentionally exempt (technical partner audience).
- `1` (config error): surface stderr and stop.

If an agent's result doesn't contain the output markers (e.g., the agent hit an error), check whether the agent's result message contains usable content that can be extracted; if not, report the failure for that note type. (There is no legacy temp-path fallback — the agents write nothing.)

Report which output files were generated, the deploy guard result, and any failures.

## Important Notes

- **The orchestrator handles ALL file I/O.** Sub-agents (command agents and their Task sub-agents) cannot reliably use Bash or Write tools due to permission inheritance limitations. The orchestrator writes instruction files, runs preparation scripts, and writes final output files. Agents only read files, spawn Tasks, and return assembled content.
- The data gathering script runs twice: once for the full data (deploy notes), and once with `--filter-integration` for integration notes.
- Each agent independently handles its own Task spawning (Step 2b) and result collection/assembly (Steps 2c-2d and 3). The master command does NOT manage individual issue analysis.
- If one agent fails, the others should still complete. Report any failures at the end.
