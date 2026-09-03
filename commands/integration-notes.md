---
description: Integration-partner-facing change notes from Optimus tasks and GitLab MRs
---

# Integration Change Notes Generator

Generate client-facing integration change notes by querying Optimus tasks and GitLab merge request diffs, then analyzing them with full codebase context.

## Usage

```
/integration-notes                          # Issues currently in QA1, QA2, Code Review, Needs Changes, Pipeline Issue
/integration-notes status                   # Same as above (explicit)
/integration-notes tickets M1-583,GEN-128592,M1-848   # Specific tickets from a deploy
/integration-notes mr 6567                  # Analyze a single GitLab MR
/integration-notes status dry-run           # Preview which MRs have integration diffs (no analysis)
/integration-notes tickets M1-583,GEN-128592 dry-run
/integration-notes mr 6567 dry-run
```

Output directory: `.claude/outputs/notes/integration/` (create with `mkdir -p .claude/outputs/notes/integration` if it does not exist).

Output filename is based on the mode and parameters used:
- **status mode**: `.claude/outputs/notes/integration/integration-notes-status-<YYYY-MM-DD>.md` (e.g., `.claude/outputs/notes/integration/integration-notes-status-2026-03-15.md`). Compute the date via `date +%Y-%m-%d` in Bash — do NOT hardcode or guess the date.
- **tickets mode**: `.claude/outputs/notes/integration/integration-notes-tickets-<YYYY-MM-DD>.md` (e.g., `.claude/outputs/notes/integration/integration-notes-tickets-2026-03-15.md`). Compute the date via `date +%Y-%m-%d` in Bash.
- **mr mode**: `.claude/outputs/notes/integration/integration-notes-mr-<iid>.md` (e.g., `.claude/outputs/notes/integration/integration-notes-mr-6567.md`)

## Required Permissions

For autonomous execution without per-command approval prompts, ensure these are in `.claude/settings.local.json` under `permissions.allow`:

```json
"Bash(python3:*)",
"Bash(date:*)",
"Task"
```

## Arguments

Parse `$ARGUMENTS` as follows:

- **No arguments or `status`**: Query Optimus for tasks in QA1, QA2, Code Review, Needs Changes, Pipeline Issue lanes
- **`tickets <id1,id2,...>`**: Look up specific Optimus tasks by comma-separated identifiers (e.g., `tickets M1-583,GEN-128592,M1-848`). Use this for deploy notes — paste the ticket IDs from the Optimus Plan Deploy view.
- **`mr <iid>`**: Skip Optimus entirely, process a single GitLab MR by its IID number (e.g., `mr 6567`)
- **`dry-run`** (can be appended to any mode): Show the filtered diffs but skip the analysis step (e.g., `status dry-run`, `tickets M1-583 dry-run`)

## Step 1: Gather Data

Run the shared data gathering script with `--filter-integration` to only include integration-relevant diffs. It reads `.env` automatically, queries Optimus and GitLab, fetches MR diffs, filters to integration-relevant files, and outputs structured JSON.

**Status mode (default):**
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/gather_notes_data_optimus.py status --output /tmp/integration_notes_data.json --include-description --filter-integration
```

**Tickets mode:**
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/gather_notes_data_optimus.py tickets M1-583,GEN-128592,M1-848 --output /tmp/integration_notes_data.json --include-description --filter-integration
```

**MR mode:**
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/gather_notes_data_optimus.py mr <iid> --output /tmp/integration_notes_data.json --include-description --filter-integration
```

**Dry-run** (append `--dry-run` to any of the above):
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/gather_notes_data_optimus.py tickets M1-583,GEN-128592 --dry-run --include-description --filter-integration
```

If `dry-run`, display the script's summary output and stop. Do not proceed to Step 2.

The `--filter-integration` flag filters diffs to only these integration-relevant file patterns:

| Pattern | Category |
|---|---|
| `src/apps/api/*/serializers.py` | API Serializer (response fields) |
| `src/apps/api/*/minimal_serializers.py` | API Serializer (response fields) |
| `src/apps/api/*/urls.py` | API URL routing (endpoints) |
| `src/apps/api/*/viewsets.py` | API Viewset (behavior/logic) |
| `src/apps/*/migrations/0*.py` | Database Migration (schema) |
| `src/apps/api/*/permissions.py` | API Permissions (auth) |
| `src/apps/api/*/filters.py` | API Filters (query params) |
| `src/apps/api/*/authentication.py` | API Authentication (auth mechanisms) |
| `src/apps/api/pagination.py` | API Pagination (response format) |

It also skips migration diffs with only whitespace changes (no schema operations) and discards MRs with zero matching files after filtering.

The script outputs progress to stderr and writes JSON to the `--output` path with this structure:
```json
{
  "mode": "status",
  "parameter": null,
  "issues": [
    {
      "identifier": "M1-830",
      "title": "...",
      "branchName": "...",
      "url": "...",
      "state": "Done",
      "mrs": [
        {
          "iid": 6543,
          "title": "...",
          "web_url": "...",
          "diffs": [{ "old_path": "...", "new_path": "...", "diff": "..." }]
        }
      ]
    }
  ]
}
```

## Step 2: Analyze with Parallel Agents (skip if `dry-run`)

If `dry-run`, just display a summary table of all discovered MRs and their integration-relevant files, then stop.

Otherwise, use the **Task tool** to spawn agents for parallel analysis. Large issues (with many diff files) are automatically split into chunks so multiple agents can analyze them in parallel.

### 2a. Prepare prompt files using the preparation script

**CRITICAL: Do NOT spawn any agents yet.** This sub-step is preparation only.

First, write the analysis instructions to a temp file, then run the prompt preparation script:

**Write instructions file:**
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

**Run the prompt preparation script:**
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/prepare_note_prompts.py \
    --data /tmp/integration_notes_data.json \
    --instructions /tmp/in_instructions.txt \
    --prefix in \
    --output-manifest /tmp/in_manifest.json
```

Then read `/tmp/in_manifest.json` to get the list of prompt files and which issues are chunked.

### 2b. Spawn ALL agents in ONE message

**HARD RULE: You MUST issue a SINGLE assistant message that contains one Task tool call per prompt file. Do NOT read the prompt files yourself. Do NOT spawn agents across multiple messages. Do NOT spawn one agent and wait for its result before spawning the next.**

For **every** prompt file in the manifest (both `single` and `chunk` types):
- Use the Task tool with `subagent_type: "general-purpose"`
- Set `description` to `"Analyze <identifier>"` (for chunks: `"Analyze <identifier> chunk <N>"`)
- Set `prompt` to: `Read the file <filepath> and follow the instructions it contains.`

All Task tool calls go in the same message. This is what guarantees true parallel execution.

### 2c. Collect agent results

Wait for all agents to complete.

- **Single-type results**: Each returns either a formatted change entry or a `SKIP:` line. Use as-is.
- **Chunk-type results**: Each returns IDENTIFIER, CHUNK, FILES_ANALYZED, and CHUNK_ANALYSIS. You must **combine** all chunk results for the same identifier.

### 2d. Synthesize chunked issues

For each identifier in `chunked_issues`, combine all its chunk results into a single integration change entry:

1. Collect all CHUNK_ANALYSIS bullets from the chunk agents for that identifier.
2. Based on the combined technical details, determine if there is meaningful integration impact.
3. If yes, produce a formatted change entry matching the single-type output format (### [BREAKING|NON-BREAKING|DEPRECATION] ...).
4. If no meaningful impact, produce: `SKIP: {identifier} — {reason}`

## Step 3: Assemble Output

Combine the agent results into the final document. Write the change notes in this exact markdown structure:

```markdown
# Integration Change Notes — {today's date}

## Summary
Brief overview of all changes in this batch. Include counts: total issues, MRs with integration changes, breaking vs non-breaking. Downstream consumers read counts from this paragraph for their at-a-glance summary, so state them as explicit numbers (e.g., "2 BREAKING, 1 DEPRECATION, 4 NON-BREAKING").

## Changes

{paste each agent's returned markdown entry here, separated by ---, ordered by severity: BREAKING first, then DEPRECATION, then NON-BREAKING}

---

## Migration Checklist
A consolidated checklist of all actions integration consumers must take, synthesized from the individual agent analyses. Order the unchecked items by urgency: the first 1–3 are pulled verbatim into downstream "Top actions for integrators" summaries, so the most urgent steps must appear first.
```

If there are no integration-relevant changes (all agents returned `SKIP`), state: "No integration-impacting changes detected in this batch."

Write the output to the appropriate filename in `.claude/outputs/notes/integration/` (see Output filename rules above).

**When called from the orchestrator (`/notes`):** The orchestrator handles file writing. Instead of writing to a file, return the full markdown content in your response between `---OUTPUT_START---` and `---OUTPUT_END---` markers.

## Important Notes

- Process ALL issues found, not just a subset. Be thorough.
- When analyzing diffs, look at every changed file — don't skip or summarize prematurely.
- Use the TodoWrite tool to track overall progress (data gathering, agent spawning, assembly).
- **Agent spawning**: Step 2 is split into phases for a reason. Phase 2a (prompt preparation) MUST complete fully before phase 2b (spawning). In 2b, every Task call goes in ONE message — no exceptions.
- **Agent model**: Use the default model (inherits from parent) for agents.
- **Grouping**: Agents are spawned per issue (or per chunk for large issues).
- **Chunk synthesis**: For chunked issues, YOU (the parent agent) synthesize the final entry from chunk results during Step 2d. Do not spawn a separate synthesis agent — do it inline.
