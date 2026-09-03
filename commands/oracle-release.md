---
description: Combined internal Oracle release post — Optimus ORC tickets + GitHub PRs → ONE #pod-oracle message shaped exactly like /release-slack: a root index plus per-ticket thread cards, each carrying a client-portable exec-voice lede AND the internal detail (ticket ID, developer @-mention, PR links, Optimus URL, client tags). The exec ledes are IP-guarded as a safety net. Previews first; never posts without an explicit yes.
---

# Oracle Release Post (combined, /release-slack shape)

Generate the release post for the **medtrics-oracle** product and deliver ONE combined message to the internal-only Oracle channel (`SLACK_CHANNEL_ORACLE`, `#pod-oracle`) after the user explicitly approves. This command has the **same shape as `/release-slack`**: a compact root-post index plus per-ticket thread cards, where each card leads with a client-portable `_For clients:_` exec-voice lede and then carries the internal detail. The internal team ports the exec lede to clients themselves.

The only deliberate difference from `/release-slack` is a safety net: because Oracle is a private, pre-GA product, the exec ledes still pass the **IP guard** before posting (over the ledes only — the internal detail, PR links, and mechanism vocabulary are internal-only and are not guarded).

Tickets come from **Optimus** (Oracle product, `ORC-` prefix); code comes from **GitHub** (`medtrics/medtrics-oracle` PRs), not GitLab. Message composition is handled by the shared `internal-storyteller` skill (the same one `/release-slack` uses). This command never posts to Slack without explicit user confirmation in the same session.

> The older separated-block design (a "safe to forward" client block assembled by `oracle-storyteller` + `assemble_oracle_root.py`) has been retired in favor of this shape. Those files remain parked in the repo but are no longer used by this command.

## Usage

```
/oracle-release tickets ORC-101,ORC-115        # explicit ticket list (primary mode)
/oracle-release status                         # all Oracle tickets in the five deploy lanes (product=oracle)
/oracle-release tickets ORC-101 dry-run        # print what would be gathered, then exit
/oracle-release status dry-run
```

You may combine either mode with `dry-run`.

## Pipeline overview

```
1. validate .env keys + confirm the Mem MCP is connected (hard stop — Mem is the release archive)
2-pre. PR overrides: if Mem is connected, dump the "Oracle PR Overrides" collection to /tmp/oracle_pr_overrides.json
   (pr-overrides skill); otherwise print one line and continue on the sweep alone
2. gather data (Optimus ORC tickets + GitHub PRs via one list sweep; startup canary first; every matching PR kept,
   primary-first, with a per-ticket decision; Mem/inline overrides applied) + verify diffs + resolve PR-author emails
   (commit author, non-noreply)
3. resolve developer Slack user IDs — (3a) optional Mem developer directory prefill via
   dev_directory.py → (3b) slack_resolve_users.py for the rest (email → exact name → fuzzy) →
   (3c) store newly resolved developers back into Mem immediately (best-effort, never blocks)
4. enrich client tags + persist clients / pr_links into the data JSON
5. analyzer instructions heredoc (11-field combined block) → prepare_note_prompts.py --prefix orc
6. per-ticket analyzer agents (parallel) → combined-format summary blocks (draft EXEC_SUMMARY + INTERNAL_SUMMARY)
   6b. client copy pass: engineering-to-client skill adds CLIENT_TITLE and finalizes EXEC_SUMMARY + CLIENTS in place
7. IP guard over the CLIENT_TITLE + EXEC_SUMMARY lines ONLY (extract → guard → ≤3 rewrite loop) — safety net
8. invoke the internal-storyteller skill (prefix oracle_release) → root index + thread cards
   (each card: `*n. ORC-id · client headline*`, `For clients:` paragraph, then Ticket / Developer / Internal / URL / PRs / Clients)
9. lint gate: slack_lint.py --kind root on the root, --kind reply on EVERY reply (fix-and-relint, max 3)
10. preview root + replies in chat (with the PR discovery block) → user approves
    10b. on an `ORC-NNN → N` reply: pr_overrides.py pending → Mem create/update (or --pr-override-inline when
         Mem is absent) → re-run Step 2 and Steps 4–9 → preview again
11. on approval: post root via slack_upload.py → capture ts + resolved channel id →
    post each reply with --thread-ts <ts> --channel <resolved id>
    11b. once the post is complete (this session, or the resume run that finishes it): archive ONE Mem
         note for this release session — trace header + the posted root and replies verbatim
         (release-archive skill → release_archive.py record; Mem is the only archive, nothing on disk)
```

The Slack shape is **root-post-as-index**: one compact top-level message (numbered `ORC-id · title` lines under `*Enhancements &amp; New Features*` / `*Bug Fixes*`, plus a `Clients to notify:` line), with each ticket's `_For clients:_` paragraph + internal detail in thread cards. **Never post a second top-level message** — overflow spills into additional thread replies deterministically.

## Output

- Slack root post + thread replies to the channel resolved from `SLACK_CHANNEL_ORACLE` (`#pod-oracle`). Posted only after the user confirms.
- **One Mem note for the release session** in the collection named by `MEM_RELEASE_ARCHIVE_COLLECTION` (default `Release Notes Archive`, shared with `/release-slack`; `release-archive` skill) — date, channel, root ts, permalink, the posted ORC list, **and the posted root + every thread reply verbatim**. This is the **only** archive of the post: nothing is written to `.claude/outputs/` or anywhere else on disk. Written once the post is complete (this session, or the resume run that finishes it).
- Working files only in `/tmp/` — the per-ticket summary blocks (`/tmp/oracle_notes_summaries.txt`) and the composed root / reply files (`/tmp/oracle_release_root.txt`, `/tmp/oracle_release_reply_NN.txt`); the archive step reads them.

Compute the date via `date +%Y-%m-%d` in Bash — do NOT hardcode or guess.

## Required .env keys

| Key | Purpose | Required |
|---|---|---|
| `OPTIMUS_API_KEY` | gather Optimus ORC tickets | Yes |
| `GITHUB_TOKEN` | GitHub PR sweep + diffs — classic PAT with `repo` scope, SSO-authorized for the org | Yes |
| `GITHUB_REPO` | `owner/repo` **or** a full GitHub URL (`https://github.com/owner/repo[.git]`) — the gather script normalizes | Yes |
| `GITHUB_API_URL` | GitHub API base URL | optional, defaults to `https://api.github.com` |
| `ORACLE_REPO_PATH` | Absolute path to a local `medtrics-oracle` checkout — lets analyzers read related source files for context when a diff is sparse | optional |
| `MEM_PR_OVERRIDES_COLLECTION_ID` | UUID of the Mem collection `Oracle PR Overrides` (human ticket → PR picks). When empty, the command resolves the collection by title and prints the id to pin here | optional |
| `MEM_RELEASE_ARCHIVE_COLLECTION` | Name of the Mem collection that receives the archive note for every posted release (`release-archive` skill). Created under that exact name if absent; defaults to `Release Notes Archive` when unset or blank. The key is optional — the **Mem MCP connection is not** (see Step 1) | optional |
| `SLACK_CHANNEL_ORACLE` | channel id (`C0123ABC`) or `#pod-oracle` | Yes |
| `SLACK_BOT_TOKEN` | `xoxb-...` token with `chat:write` + `channels:read` scopes (`groups:read` if the channel is private and configured by name) | Yes |

If any required key is missing, abort early with a clear "Missing required env keys: [...]" message — do NOT proceed to any data gathering, file generation, or Slack call. The **Mem MCP must be connected** as well: it is the only archive of every release, so its absence is a hard stop at Step 1, not a degraded run (the PR-overrides and developer-directory uses of Mem stay best-effort). The bot must be a **member of the Oracle channel** (`/invite @Medtrics Release Bot` in `#pod-oracle`) or the post will fail with `not_in_channel`.

## Required permissions

In `.claude/settings.local.json` under `permissions.allow`:

```json
"Bash(python3:*)",
"Bash(date:*)",
"Bash(cat:*)",
"Bash(grep:*)",
"Bash(rm:*)",
"Task",
"Skill"
```

## Flow

### Step 1 — Validate environment

Read `.env` from the current working directory. Verify the five required keys above are present (`GITHUB_API_URL`, `ORACLE_REPO_PATH`, `MEM_PR_OVERRIDES_COLLECTION_ID`, and `MEM_RELEASE_ARCHIVE_COLLECTION` are optional). If any are missing, list them and stop before ANY call.

Then confirm the **Mem MCP is connected** — the release archive lives in Mem and nowhere else, so this is a precondition, not an optional extra. Follow the `release-archive` skill's preflight: if the Mem tools (`list_collections`, `create_collection`, `create_note`, …) are not available in this session, print `Mem MCP not connected — this command archives every release to Mem and will not run without it. Connect the Mem connector and re-run.` and stop before ANY Optimus, GitHub, or Slack call. This applies to `dry-run` too. Never fall back to a disk copy. (With Mem confirmed present, Step 2-pre and Step 3a will find it too; their own "not connected" branches now only cover a mid-run Mem failure.)

### Step 2-pre — PR overrides dump (Mem, optional)

Follow the bundled `medtrics-release-notes:pr-overrides` skill. If the Mem MCP tools are available in this session:

1. Resolve the `Oracle PR Overrides` collection: use `MEM_PR_OVERRIDES_COLLECTION_ID` from `.env` when set; else `search_collections` by exact title (case-insensitive); else `create_collection`. When the id did not come from `.env`, print it once with the hint to pin it (`MEM_PR_OVERRIDES_COLLECTION_ID=<uuid>`). Never write to `.env`.
2. `list_notes({collection_id, include_note_content: true, limit: 100})`, page through `next_page`, and save the merged `{"notes": [...]}` response **verbatim** (each note's `id`, `version`, `content`) to `/tmp/oracle_pr_overrides.json`.

If Mem is not connected, or any of these calls fails, print `PR overrides: Mem not connected, sweep only.`, run `rm -f /tmp/oracle_pr_overrides.json` so a stale dump from an earlier session is never applied while the preview says Mem is not connected, and continue — **no hard error, no retry loop**. The gather treats a missing dump as "no overrides".

### Step 2 — Gather data

For `tickets <id1,id2,...>` (primary mode):

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/gather_notes_data_oracle.py tickets <id1,id2,...> --output /tmp/oracle_notes_data.json --include-description --pr-overrides /tmp/oracle_pr_overrides.json
```

For `status` (all Oracle tickets in the same five deploy lanes as the classic track — QA1, QA2, Code Review, Needs Changes, Pipeline Issues — filtered with `product=oracle`):

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/gather_notes_data_oracle.py status --output /tmp/oracle_notes_data.json --include-description --pr-overrides /tmp/oracle_pr_overrides.json
```

For `dry-run`: append `--dry-run` to the call, show the printed discovery table (identifier, PRs, decision, title) plus its sweep/decision summary lines, and exit before any analyzer or Slack call.

Behavior to know:

- The script runs a **startup canary** (`GET /repos/{owner}/{repo}`) before gathering and hard-exits with an auth/visibility message on failure — a bad or non-SSO-authorized token surfaces as 404/422 on private repos, and without the canary every ticket would silently "find no PRs".
- PR discovery is **one PR-list sweep per run** (branch + title matching — ORC ids live primarily in branch names, which GitHub search does not index), with a per-ticket `/search/issues` secondary for PRs older than the ~500-PR sweep window.
- **Every matching PR is kept** in `mrs[]` (several PRs per ticket is normal), ordered **primary-first**. Each entry carries the classic keys plus `number`, `branch`, `state`, `draft`, `merged_at`, `updated_at`, `matched_via` (`branch` | `title` | `manual`). Each issue carries `pr_discovery` — `{"decision", "primary", "candidates", "override", "notes"}` — where `decision` is one of `override` (a human pick from Step 2-pre / 10b), `matched` (primary chosen by the rule: one open PR → it; none open → newest merged; ≥2 open → the single non-draft), `flagged_ambiguous` (≥2 open non-draft PRs; `candidates` lists them; posting is **not** blocked), `no_pr_yet`, or `no_pr_in_window` (the sweep window was full and the ticket predates the oldest swept PR — an older ticket whose PR the sweep cannot see).
- **Read PR-discovery results from the JSON, not from stderr.** The output carries a top-level `pr_discovery` block — `swept_at`, `sweep` (`pr_count`, `pages`, `window_full`, `oldest_updated_at`, `failed`), `overrides_loaded`, `summary` (count per decision), and `warnings` (every warning the script printed, verbatim, including `no PRs found for ORC-… — it will appear without code context`). The Step 10 preview's PR discovery block is built from it. Tickets with zero PRs stay in the output with empty `mrs` and ship without code context (empty `PR_LINKS`). If `sweep.failed` is true, the scan was incomplete — say "sweep failed", never "no PRs".
- **Optimus's own "PR" field is NOT readable.** Oracle tickets carry a PR field in the Optimus UI, but the API/connector task payload does not expose it, so this pipeline cannot read it. PR links come from the GitHub branch/title sweep above **plus human overrides**. When a ticket is `no_pr_yet`, `no_pr_in_window`, or `flagged_ambiguous`, tell the user in the preview and invite the override: *"Reply `ORC-232 → 415` and I'll record it (in Mem when connected) and re-run the gather."* — that is Step 10b. Never ask for a PR number without a way to consume it.
- **PR-author emails are resolved for Slack tagging.** After discovery (non-dry-run only), the gather fills `mrs[].author_email` from each PR's **commit author** email — specifically a commit whose top-level `author.login` matches the PR author, so a committer/rebaser (a different person) is never used, and `@users.noreply.github.com` masks are skipped (falling back to a public profile email if set). This is what lets Step 3's email tier tag Oracle devs exactly. Devs with a privacy-masked or personal commit email fall through to the name tiers.

If zero tickets come back, print `No Oracle tickets found. Nothing to post.` and stop before any analyzer call.

#### 2b. Verify the diffs actually arrived (mandatory)

The full (non-dry-run) gather fetches every PR's diffs through the **authenticated GitHub API** and embeds them in the output JSON. Verify before proceeding:

```bash
python3 -c "
import json; d = json.load(open('/tmp/oracle_notes_data.json'))
for i in d['issues']:
    for mr in i['mrs']:
        chars = sum(len(x['diff']) for x in mr['diffs'])
        print(f\"{i['identifier']}: PR#{mr['iid']} — {len(mr['diffs'])} files, {chars} diff chars\")
"
```

Every PR should show a nonzero file count. If a PR shows **0 files / 0 chars**, re-run the Step 2 gather once (transient API failure); if it is still empty, surface a warning and proceed with description-only analysis for that ticket.

#### HARD RULES for this pipeline (no improvisation)

- **NEVER fetch `github.com` pages** — not with WebFetch, not with curl, not in any form. The repo is private; unauthenticated page fetches return empty and authenticated fetching outside the gather script is forbidden. **All code context comes exclusively from `/tmp/oracle_notes_data.json`**, which the gather script fills using the authenticated API.
- `--dry-run` is a **preview-only** mode for the user. The pipeline itself always uses the full gather output. Never run only the dry-run and then attempt to obtain diffs some other way.
- If data is missing (empty diffs, no PRs), the remedy is re-running the gather script or proceeding with a surfaced warning — never an alternative data source.

### Step 3 — Resolve developer Slack user IDs (Mem directory → resolver → Mem write-back)

Dev attribution's source for Oracle is the **GitHub PR author**. Step 2's gather already resolved a real email for each PR author where one is available (`mrs[].author_email`, from the PR's **commit author** — see Step 2), and each entry carries `author_name` / `author_username` (the GitHub login). Follow the bundled `medtrics-release-notes:dev-directory` skill for the Mem half; the Slack half is the resolver script. Three sub-steps, in order:

**3a — Mem developer directory prefill (optional).** If the Mem MCP tools are available in this session, locate (or create) the single collection `Release Notes Developer Directory`, `list_notes` it with content (page through `next_page`), save the merged `{"notes": [...]}` response verbatim to `/tmp/oracle_release_dev_directory.json`, and run:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/dev_directory.py prefill \
    --data /tmp/oracle_notes_data.json --notes /tmp/oracle_release_dev_directory.json
```

This writes `assignee_slack_id` + `assignee_slack_resolved_via: "mem"` onto every issue whose PR author is already known (exact email → **GitHub login** → unique full name) and, because the Oracle shape has no assignee name, also `assignee_name_from_directory` so the analyzer renders the developer's real name rather than a login. If Mem is not connected or any Mem call fails, print `Developer directory: Mem not connected, using the Slack resolver only.` and skip 3a and 3c — **no hard error, no retry loop**.

**3b — Slack resolver for the rest.** Run the resolver to fill `assignee_slack_id` on each still-unresolved issue (issues pre-filled in 3a are skipped and counted as `preresolved`):

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/slack_resolve_users.py --data /tmp/oracle_notes_data.json
```

**3c — Store new developers in Mem (immediately, before the analyzers).** If Mem is connected and 3b's result block lists any `resolutions`, compute and perform the writes:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/dev_directory.py pending \
    --data /tmp/oracle_notes_data.json --notes /tmp/oracle_release_dev_directory.json \
    --out /tmp/oracle_release_dev_directory_pending.json --source /oracle-release
```

For each entry in the output's `writes`: `create` → `create_note({content, collection_ids: [<directory uuid>]})`; `update` → `update_note({note_id, content, version})`. A failed write is logged and skipped; the post is unaffected. Keep the one-line tally for the preview: `Developer directory: {N} from Mem, {M} newly stored, {K} unresolved.`

The resolver (3b) resolves each developer in three tiers, stopping at the first hit:

1. **Email match** — `users.lookupByEmail` on `mrs[].author_email` when the gather found a usable (non-noreply) commit email. Exact; the common case for devs who commit with their work email.
2. **Exact name match** — a UNIQUE, case-insensitive, multi-token full-name match against the Slack directory (`users.list`). Single-token (first-name/login) and ambiguous names are not tagged.
3. **Fuzzy name match** — a `difflib` similarity match (default threshold `0.72`) that tags the single best directory name clearly ahead of the runner-up; near-ties are left unresolved. Lower-confidence — the preview is the safety gate (each mention shows `@Name (Author Name)` for a human to verify). Disable with `--no-fuzzy`; tune with `--fuzzy-threshold`.

Deactivated members and bots are skipped in tiers 2–3. Best-effort throughout.

Best-effort: any unresolved developer is left with an empty `assignee_slack_id` and renders as a plain name — the script never blocks the pipeline. Its `---SLACK_RESOLVE_RESULT---` block now includes `preresolved` and a `resolutions` list with the tier per developer. Requires the bot token to hold `users:read.email` + `users:read`. **No Slack MCP is used.** Pass `--force` only when the user explicitly asks to bypass a cached Mem id for this run.

### Step 4 — Enrich client tags + persist client tags + PR links (REQUIRED)

`prepare_note_prompts.py` (Step 5b) only surfaces `**Clients:**` and `**PR Links:**` into an analyzer prompt when the issue object in the JSON already carries lowercase `clients` and `pr_links`. So both must be written onto each issue **before** Step 5b. Skip this and the `• Clients` line, `Clients to notify:` line, and `• PRs` line silently vanish while the post still assembles. (The `<@dev>` mention is handled separately — Step 3's resolver script already wrote `assignee_slack_id` into this same file; do NOT set it here.)

Enrich client tags per ticket the same way `/release-slack` does:

1. Scan the Optimus task description for an explicit "Requested by …", "Reported by …", or "Client(s): …" line; extract the comma-separated tags verbatim.
2. Else scan the description for a recognized all-caps client tag matching `[A-Z][A-Z0-9-]{2,}`.
3. Else set `TBC`. Oracle is pre-GA, so most tickets will be `TBC` — that is correct and the storyteller renders it as `TBC — affects {scope}`.

Then write `clients` (from enrichment) and `pr_links` (joined PR URLs from `mrs[].web_url`) onto each issue, keyed by `identifier`. `mrs` is already **primary-first** (the gather reordered it per `pr_discovery.primary`), so the join below lists the primary PR first without any extra sorting; the shape of `pr_links` is unchanged:

```bash
python3 - <<'PY'
import json
path = "/tmp/oracle_notes_data.json"
data = json.load(open(path))

# Client map you built above, keyed by issue identifier:
clients = {"ORC-101": "TBC"}   # identifier -> comma-separated tags, or "TBC"

for issue in data.get("issues", []):
    ident = issue.get("identifier")
    if clients.get(ident):
        issue["clients"] = clients[ident]
    # PR links are deterministic — join every PR's html_url (stored as web_url):
    urls = [m.get("web_url") for m in issue.get("mrs", []) if m.get("web_url")]
    if urls:
        issue["pr_links"] = ", ".join(urls)

json.dump(data, open(path, "w"), indent=2)
PY
```

`assignee_slack_id` is NOT set here — Step 3's resolver script owns it. This step and Step 3 both run before Step 5b (prompt prep); order between them does not matter (each does a full load-mutate-dump and touches different fields). Note the field-name distinction: the gather script stores each PR's URL as `web_url` and its number as `iid`; the analyzer prompt gets `**PR Links:**` from the `pr_links` string this step writes. Do not ask the analyzer to reconstruct a PR URL — it never receives the repo slug.

### Step 5 — Analyzer instructions + prompt files

#### 5a. Write the analyzer instructions file

```bash
cat > /tmp/oracle_rn_instructions.txt << 'INSTRUCTIONS_EOF'
You are summarizing one Oracle ticket for a SINGLE combined internal Slack post (#pod-oracle), shaped like the /release-slack digest.

The post is internal, but each ticket card leads with a short client-portable lede the team can lift and send to clients. So you must produce TWO summaries per ticket:
  - EXEC_SUMMARY — client-safe, executive voice, zero internal vocabulary.
  - INTERNAL_SUMMARY — technical, for the dev team, keep everything.

IMPORTANT — the prompt file labels code changes as "MR !<n>". These are GitHub
PULL REQUESTS, not GitLab merge requests. NO PR or MR reference of any form
(PR#42, PR 42, pull request 42, !42, a branch name, a github.com URL) may EVER
appear in EXEC_SUMMARY. They are welcome in INTERNAL_SUMMARY.

Inputs you receive:
  - identifier (ORC-NNN), verbatim title, description, Optimus URL
  - PR titles + author names, PR code diffs
  - resolved developer name / slack id, clients, and PR Links passed at the top of the prompt file

Analysis process:
  1. Read the ticket description carefully — it is the primary source of what the ticket delivers.
  2. Read the diffs to understand what actually shipped, beyond what the ticket claims.
  3. If the description is sparse or a diff is unclear AND a CODEBASE CONTEXT line appears below, Read related source files from that local checkout. Do NOT fetch anything from the web.

Classify the ticket as exactly ONE of:
  - FEATURE: entirely new capability that didn't exist before
  - ENHANCEMENT: improvement to an existing feature
  - BUG FIX: correction of broken or incorrect behavior
  - SECURITY: security-related fix or hardening
  - REFACTOR: code restructuring with no user-visible behavior change

A default-behavior or permission change an administrator must know about maps to
SECURITY when it is access/permission related, otherwise to ENHANCEMENT — there
is no separate "awareness" category in this shape.

A REFACTOR has no user-visible change, so if CATEGORY is REFACTOR, return exactly:
NO_VISIBLE_CHANGE

Otherwise return EXACTLY this block — no preamble, no commentary outside the block:

IDENTIFIER: {identifier}
TITLE: {verbatim ticket title, exactly as in the source — never cleaned}
CATEGORY: {FEATURE|ENHANCEMENT|BUG FIX|SECURITY|REFACTOR}
URL: {Optimus task url}
DEVELOPER_NAME: {full name — the PR author unless the orchestrator passed a better value}
DEVELOPER_SLACK_ID: {slack user id, e.g. U02AB0913 — empty string if unknown; copy the orchestrator's resolved value verbatim, never invent one}
DEVELOPER_FIRST_NAME: {first name only, lowercased}
CLIENTS: {comma-separated tag list verbatim from the orchestrator, or "TBC"}
PR_LINKS: {copy the orchestrator's "PR Links" value verbatim — empty string if the ticket has no PRs; NEVER construct a URL yourself}
EXEC_SUMMARY: {2–3 sentences, executive voice for an institutional administrator (DIO, dean, program director, IT director) who does not write code. Lead with WHO can now do WHAT, or what got better. This text must be safe to paste to a client: NEVER mention internal tool names (issue tracker, source-control host, cloud, observability vendors) and specifically NEVER GitHub, PR/pull-request references, React, Supabase, Vite, or TypeScript; NEVER mention code artifacts (serializer, viewset, model, migration, endpoint, queryset, schema, index, foreign key, middleware, signal, route, component, hook, RLS policy, config files); NEVER mention architecture mechanisms (queue, worker, cache, cron, daemon, background job, webhook → say "notification", race condition, deadlock, memory leak, load balancer); NEVER include file paths, branch names, internal URLs, developer names, or client institution names (abstract to the segment). Active voice; bug fixes read as the new correct behavior, never a confession; no marketing words, no CTAs.}
INTERNAL_SUMMARY: {2–4 sentences for the dev team. Keep mechanism specifics — modules, components, Supabase/RLS details, migration notes, gotchas. Lead with the change; for bug fixes lead with what was broken in one clause, then the corrected behavior. Keep client motivation when present. Active voice, past tense for completed work, present tense for new behavior. No marketing voice, no apologies. Never 5+ sentences.}

Each field is ONE physical line — never wrap a value across lines. In particular EXEC_SUMMARY must be a single line (the guard extracts it line-by-line; a wrapped lede would be only partly guarded).

OUTPUT FORMAT — Return EXACTLY the 11-field block above (or NO_VISIBLE_CHANGE).
INSTRUCTIONS_EOF
```

After writing the heredoc: if `ORACLE_REPO_PATH` is set in `.env` and the directory exists, append the codebase-context line:

```bash
echo "" >> /tmp/oracle_rn_instructions.txt
echo "CODEBASE CONTEXT: a local checkout of the Oracle codebase is at <ORACLE_REPO_PATH> — when a diff is sparse or unclear, Read related source files there." >> /tmp/oracle_rn_instructions.txt
```

#### 5b. Prepare prompt files

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/prepare_note_prompts.py \
    --data /tmp/oracle_notes_data.json \
    --instructions /tmp/oracle_rn_instructions.txt \
    --prefix orc \
    --output-manifest /tmp/orc_manifest.json
```

Read `/tmp/orc_manifest.json` to get the list of prompt files.

### Step 6 — Spawn ALL analyzer agents in ONE message

For every prompt file in the manifest (single or chunk):

- Use the Task tool with `subagent_type: "general-purpose"`.
- `description`: `"Summarize <identifier>"` (chunks: `"Analyze <identifier> chunk <N>"`).
- `prompt`: `Read the file <filepath> and follow the instructions it contains. Copy the resolved developer values, clients, and PR Links the orchestrator passed at the top of the prompt file into the output block verbatim.`

All Task calls go in the same assistant message.

Collect results: for each ticket you now have either an 11-field block or `NO_VISIBLE_CHANGE` (drop from downstream). For chunked tickets, combine chunks by picking the most user-visible block. Write the collected per-ticket blocks to `/tmp/oracle_notes_summaries.txt`, one block per ticket, separated by `---`.

### Step 6b — Client copy pass — engineering-to-client skill (REQUIRED, one pass)

The analyzer's `EXEC_SUMMARY` is a draft. Invoke the bundled `medtrics-release-notes:engineering-to-client` skill in **pipeline mode** over `/tmp/oracle_notes_summaries.txt`. It rewrites the file in place, touching only:

- a new `CLIENT_TITLE:` line after `TITLE:` → the client-facing headline (a few words, no ticket id, no jargon, no client name) — this becomes the card title `*n. ORC-id · {CLIENT_TITLE}*` and the root-index entry. The internal `TITLE` stays in the block untouched.
- `EXEC_SUMMARY:` → the final client-facing paragraph (1–3 sentences, one physical line, no headline) — this becomes the card's `*_For clients:_*` text.
- `CLIENTS:` → confirmed verbatim (codes or `TBC`); never invented. Oracle is pre-GA, so most stay `TBC`.
- an optional `CLIENT_FLAG: {code(s)} — {reason}` line on any block whose named client's item is still unresolved (so the team sends that one to the named client only, not in a broadcast).

Every other field and the block order stay byte-identical. The skill returns `CLIENT_COPY_DONE — {N} blocks, {F} flagged`; if it returns anything else, or the block count no longer matches, stop and surface it — do not guard or compose from a half-rewritten file. Collect the `CLIENT_FLAG` lines for the preview. Step 7 guards the **finalized** titles and paragraphs, so this pass must run first.

### Step 7 — IP guard over the client titles + paragraphs (safety net, before composition)

The `CLIENT_TITLE` headlines and `For clients` paragraphs are the only client-portable text in the post. Guard **just the `CLIENT_TITLE` and `EXEC_SUMMARY` lines** (as finalized in Step 6b) — guarding the whole summaries file would false-trip on the internal React/Supabase/PR detail that legitimately lives in `TITLE`, `INTERNAL_SUMMARY`, and `PR_LINKS`.

```bash
grep -E '^(CLIENT_TITLE|EXEC_SUMMARY):' /tmp/oracle_notes_summaries.txt > /tmp/oracle_exec_ledes.txt
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/ip_guard.py --input /tmp/oracle_exec_ledes.txt \
    [--customer-list <roster>] --json-out /tmp/oracle_ip_ledes.json
```

Pass `--customer-list` when a client roster file is available.

| Exit | Meaning | Action |
|---|---|---|
| `0` | PASS | Proceed to Step 8. |
| `2` | FAIL — blocking violation(s) | Read the JSON `violations` array, rewrite the offending `CLIENT_TITLE` / `EXEC_SUMMARY` line(s) **in `/tmp/oracle_notes_summaries.txt`** in the same client voice (fix only those two fields, never the internal fields), re-extract, re-run the guard. Iterate up to 3 times. If still failing, stop, print residual violations with line offsets, do not compose, do not post. |
| `1` | Configuration error | Surface stderr, stop. |

The internal detail (`INTERNAL_SUMMARY`, `PR_LINKS`, developer mentions, Optimus URLs) is **deliberately not guarded** — the channel is internal-only and those attributes are the point.

### Step 8 — Synthesize via the internal-storyteller skill

Invoke the bundled `medtrics-release-notes:internal-storyteller` skill, passing it:

- the summaries file `/tmp/oracle_notes_summaries.txt`,
- the output prefix `oracle_release`, and
- today's date.

(The root header is the uniform `:rocket: *Release Notes — {date}*` for every caller — no product label is passed.) The skill produces:

- `/tmp/oracle_release_root.txt` — the root-post index (≤ 2,500 chars): `:rocket: *Release Notes — {date}*`, a one-sentence theme line, numbered `{n}. {ORC-id} · {CLIENT_TITLE}` entries (the client-facing headline from Step 6b; no developer mention in the root) under `*Enhancements &amp; New Features*` and `*Bug Fixes*`, plus a `Clients to notify:` line.
- `/tmp/oracle_release_reply_01.txt`, `_02.txt`, … — one thread reply per section carrying the per-ticket cards (≤ 3,900 chars each; long sections split at card boundaries), SECURITY items as their own escalation reply. Each card is:

  ```
  *1. ORC-232 · {CLIENT_TITLE from Step 6b}*
  *_For clients:_* {client paragraph from Step 6b}
  • Ticket: ORC-232
  • Developer: <@SLACK_ID> (Developer Name)
  • Internal: {internal summary}
  • URL: {Optimus URL}
  • PRs: {GitHub PR links}            (omitted when the ticket has none)
  • Clients: {codes} | TBC (platform-wide)
  ```

If the skill returns `NO_INTERNAL_NOTES — nothing customer-visible shipped this deploy`, stop the pipeline: do not lint, do not preview, do not post — print that line back to the user.

The storyteller does **not** run through a second IP guard — the exec ledes were already guarded in Step 7, and the rest is internal by design.

### Step 9 — Lint gate (mandatory, before preview)

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/slack_lint.py --input /tmp/oracle_release_root.txt --kind root
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/slack_lint.py --input /tmp/oracle_release_reply_01.txt --kind reply
# ...one call per reply file
```

| Exit | Meaning | Action |
|---|---|---|
| `0` | PASS | Proceed once every file passes. |
| `2` | Violations | Read the JSON violations from stdout, fix the offending file (re-invoke the storyteller with the violation list, or apply the mechanical fix directly). **A fix that touches an exec lede re-enters Step 7 (guard) before re-linting.** Iterate up to 3 times. If still failing, stop, print residual violations, do not preview, do not post. |
| `1` | Configuration error | Surface stderr, stop. |

The lint enforces: bare `<@U…>` mentions, labeled links non-empty, no `**bold**` / `#` headers / fenced blocks, escaped `&` `<` `>` outside tokens, a blank line before each section header, root ≤ 2,500 chars, each reply ≤ 3,900 chars. Raw GitHub/Optimus URLs in the cards are lint-legal by design.

### Step 10 — Preview to user

Print the composed root post and each thread reply inside fenced blocks, then the **PR discovery** block read from `/tmp/oracle_notes_data.json` (`pr_discovery` at the top level and on each issue — never from stderr scrollback), AND show the channel it will go to. Then ask:

```
Ready to post to <#CHANNEL>?
  Channel: <channel display>  (#pod-oracle)
  Root length: <N> chars; <R> thread replies
  Items: <X> enhancements / <Y> bug fixes; <S> security escalation(s)
  PR discovery:
    Overrides applied: <ORC-232 → PR#415 (from Mem | this run only), … or "none">
    Multiple open PRs (pick one or post as-is): <ORC-240 [415, 416], … or "none">
    No PR found: <list or "none">
    No PR within the ~500-PR window (older ticket): <list or "none">
    Sweep failed: <yes — scan incomplete, re-run the gather | no>
    Other warnings: <malformed / duplicate override notes, search failures, or "none">
  Developer directory: <N> from Mem, <M> newly stored, <K> unresolved   (or "Mem not connected")
  Client-specific items (send to the named client only): <CLIENT_FLAG list, or "none">

  To record a PR for a ticket, reply `ORC-232 → 415` (repeat per ticket). Otherwise:
  [y] post root + replies
  [n] cancel (no Slack action taken; summaries + drafts remain on disk)
  [e] let me edit the draft first
```

Build the block from `pr_discovery.summary` and each issue's `pr_discovery.decision`: `override` → "Overrides applied" (say `from Mem` when `override.note_id` is set, `this run only` otherwise); `flagged_ambiguous` → "Multiple open PRs" with `candidates`; `no_pr_yet` → "No PR found"; `no_pr_in_window` → "No PR within the window"; `pr_discovery.sweep.failed` → "Sweep failed: yes". When Mem was not connected in Step 2-pre, add `(Mem not connected — a pick applies to this run only)` after the "To record a PR" line. Every string in `pr_discovery.warnings` that is not already represented above goes under "Other warnings".

If the user picks `e`: accept their edits in chat and write them back to the corresponding `/tmp/oracle_release_*.txt` file(s). **Any edit touching an exec lede re-enters Step 7 (guard).** Any edit re-runs Step 9 (re-lint). Re-show the preview and loop until `y` or `n`.

### Step 10b — Record a PR pick (`ORC-NNN → N` replies)

When the user replies with one or more `ORC-NNN → N` lines (`->` and `=` are accepted; `#415` and a full PR URL are accepted for `N`), follow the `pr-overrides` skill for each pair:

1. **Compute the write** (deterministic, no network):

   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/pr_overrides.py pending \
       --ticket ORC-232 --pr 415 \
       --notes /tmp/oracle_pr_overrides.json \
       --out /tmp/oracle_pr_overrides_pending.json \
       --resolved-by <the user's email> [--reason "<their words, if any>"] \
       --data /tmp/oracle_notes_data.json
   ```

   Parse the `---PR_OVERRIDES_RESULT---` block. On `ok: false` (ticket not `ORC-NNN`, PR not a positive integer) surface the error, write nothing, and ask the user to restate that pair.

2. **Apply the write via Mem** (only when Mem is connected and the collection id is known): the single entry in `writes` is `create` → `create_note({content, collection_ids: [<collection uuid>]})`, or `update` → `update_note({note_id, content, version})` with the `version` passed verbatim (a version conflict means the note was edited during the run — skip it and say so). Then refresh the dump: re-run Step 2-pre's `list_notes`, or edit `/tmp/oracle_pr_overrides.json` in place — **replace** the entry with the same note `id` (an update) or add the new note (a create), carrying the returned `id`/`version`. Never append a second entry for an existing id.

3. **Re-run Step 2** (the gather, with `--pr-overrides /tmp/oracle_pr_overrides.json`) and then **Steps 4–9** — the override PR is fetched by number if the sweep did not carry it, marked `matched_via: manual`, the ticket's decision becomes `override`, and its diffs are analyzed like any other PR. Then show the Step 10 preview again; the ticket now appears under `Overrides applied`.

**Inline fallback — Mem not connected, or a Mem write failed.** Apply the pick for this run only by adding `--pr-override-inline ORC-232=415` to the Step 2 gather invocation (repeat the flag per ticket, or comma-separate pairs: `--pr-override-inline ORC-232=415,ORC-240=502`). Same code path, nothing persisted. Tell the user: `PR pick applied for this run only (Mem not connected / note not saved) — it will not be remembered next time.` An inline pick beats a Mem override for the same ticket in the same run.

Picks never touch `github.com` from the command — the gather script performs the one `GET /pulls/{n}` when it needs to.

### Step 11 — Post (only if user said `y`)

**Multi-invocation orchestration — root first, then replies into its thread.** Never post a second top-level message.

1. Post the root:

   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/slack_upload.py \
       --channel "$SLACK_CHANNEL_ORACLE" \
       --message-file /tmp/oracle_release_root.txt
   ```

2. Parse the `---SLACK_UPLOAD_RESULT---` JSON block from stdout — `{"ok", "channel", "ts", "error"}`, emitted on every exit path. If `ok` is false, surface `error` and stderr verbatim, stop, and do NOT retry automatically. If the error says the write outcome is unverified ("verify in channel"), tell the user to check `#pod-oracle` before re-running.

3. If `ok` is true, capture `ts` AND the **resolved channel id** from the result block's `channel` field, then post each reply file **in numeric order**, one invocation per reply:

   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/slack_upload.py \
       --channel "<resolved channel id from the result block>" \
       --message-file /tmp/oracle_release_reply_01.txt \
       --thread-ts <ts>
   ```

   Passing the resolved id avoids re-resolving `#pod-oracle` on every reply.

4. If a reply post fails partway, stop; report which replies posted and which did not, plus the root `ts`. Resume by re-running only the remaining reply files with `--thread-ts <ts>` — the root is never re-posted.

After all posts succeed:

- Print a one-line confirmation: `Posted to <#pod-oracle>. ts=<ts>. (<N> items; <R> thread replies.)`.
- Do **not** write a markdown copy to disk. The archive of this post is the Mem note written in Step 11b — nothing lands under `.claude/outputs/`.

### Step 11b — Archive to Mem (release-archive skill — the only archive)

Runs **after** the confirmation line, exactly as the `release-archive` skill describes. It writes **one Mem note for this release session** into the collection named by `MEM_RELEASE_ARCHIVE_COLLECTION` (default `Release Notes Archive`; shared with `/release-slack` — the `ORC-` prefix disambiguates). The note carries the trace header (date, channel, root ts, permalink, the posted ticket list with client-facing titles and code links) **and the posted root plus every thread reply verbatim**. This is the **only** archive of the post — nothing is written to `.claude/outputs/` or anywhere else on disk. The step can never affect the post itself: the post has already succeeded and is authoritative.

0. **Gate — the post is complete.** Run once the root **and every thread reply** have posted — either in this session, or on a **resume run** (the user re-ran only the remaining replies with `--thread-ts <ts>` after a partial failure and the last one has now posted). On a resume run use the `ts` the user passed, the resolved channel id from the reply result blocks, and the same root / reply files from `/tmp`. Never archive while replies are still outstanding.
1. **Resolve the collection by name.** Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/release_archive.py pending` to get the resolved `collection_name` and `collection_description` (the script reads `MEM_RELEASE_ARCHIVE_COLLECTION` from `.env` at CWD, process env overriding; blank → `Release Notes Archive`). Then `list_collections`, following `next_page` until exhausted, and collect every entry whose title matches the name case-insensitively: none → `create_collection({title: <name>, description})` and print `Release archive: created Mem collection "<name>".`; one → use it; several → prefer the exact-case match, then the oldest `created_at`, and if still ambiguous do not write — warn with every candidate id and fall through to step 5. Never use `search_collections` for this and never pass `collection_titles` to `create_note`.
2. **Permalink** (one Slack read; omit on failure, never construct one):

   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/slack_upload.py --get-permalink \
       --channel <resolved channel id from the result block> --ts <ts>
   ```

   Parse its `---SLACK_UPLOAD_RESULT---` block; on `ok: true` take `permalink`. On any failure, proceed without `--permalink`.
3. **Render the note** (deterministic — never hand-compose it). `--reply-files` lists **every** posted reply file in numeric order:

   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/release_archive.py record \
       --summaries /tmp/oracle_notes_summaries.txt \
       --data /tmp/oracle_notes_data.json \
       --root-file /tmp/oracle_release_root.txt \
       --reply-files /tmp/oracle_release_reply_01.txt /tmp/oracle_release_reply_02.txt ... \
       --product oracle \
       --date <the same YYYY-MM-DD value used in the Slack root header> \
       --channel "$SLACK_CHANNEL_ORACLE" \
       --channel-id <resolved channel id from the result block> \
       --ts <ts> \
       [--permalink <url from step 2>]
   ```

   The stdout is the note body. The ticket list comes from the summaries file (the posted set — `REFACTOR` and malformed blocks dropped), not from the data JSON. If `record` exits 1 (a root or reply file is missing, typically on a resume run in a new `/tmp`), surface its stderr naming the file and stop — do not archive a partial transcript.
4. **Write it:** `create_note({content, collection_ids: [<resolved collection uuid>]})`. On success print `Release archive: wrote note "<first line of the note>" to Mem collection "<name>" (ts <ts>); if this was a re-post, trash the older note in Mem.`
5. **If the write cannot happen** (collection unresolvable or ambiguous, or `create_note` fails once and again on one immediate retry): print `Release archive: Mem write failed (<reason>). Paste the note below into the "<name>" collection by hand:` followed by the full note body from step 3 in a fenced block. Then finish. Never write the note to disk and never re-post to Slack.

## Failure modes and behavior

| Condition | Behavior |
|---|---|
| Missing required env keys | Abort before any tool call, list missing keys |
| Mem MCP not connected at Step 1 | **Hard stop** before any tool call — Mem is the only release archive. Print the one-line notice; no gathering, no post, no disk fallback (applies to `dry-run` too) |
| GitHub canary fails (bad/expired token, missing `repo` scope, missing SSO auth — surfaces as 404/422) | Gather script hard-exits with a clear message; surface it and stop — the release must not silently ship with zero code context |
| Gather returns zero tickets | Print "Nothing to post" and stop |
| Ticket has zero PRs after sweep + search + overrides (`no_pr_yet`) | Loud gather warning (stderr **and** `pr_discovery.warnings`); ticket ships without code context (`PR_LINKS` empty, no `• PRs` line); preview lists it under "No PR found" and invites an `ORC-NNN → N` pick (Step 10b) |
| Ticket older than the sweep window, nothing found (`no_pr_in_window`) | Same as above, but the preview labels it distinctly ("No PR within the ~500-PR window") — the sweep cannot see the PR; an override is the remedy |
| ≥2 open non-draft PRs for one ticket (`flagged_ambiguous`) | Posting is **not** blocked — all PRs are listed on the card; preview shows the candidates under "Multiple open PRs" and invites a pick |
| PR sweep fails after retries (`pr_discovery.sweep.failed`) | Warning; tickets without an override fall to `no_pr_yet`; preview shows "Sweep failed: yes" so it is not read as "no PRs" — re-run the gather |
| `/search/issues` fails for one ticket | That ticket → `no_pr_yet` with a warning; other tickets unaffected |
| Override names a PR that returns 404 | Override ignored with a warning; the ticket keeps the sweep result; preview shows the warning |
| Override note malformed, or two notes for one ticket | Malformed → ignored with a warning naming the note id; duplicates → newest `Resolved at` wins with a warning (trash the stale note in Mem) |
| Mem not connected for PR overrides (Step 2-pre / 10b) | One-line notice; gather runs on the sweep alone; a preview pick applies via `--pr-override-inline` for this run only and the user is told it was not saved |
| Mem write fails after a pick (10b) | Pick still applied for this run via `--pr-override-inline`; user told the note was not saved |
| A Mem call fails in the developer directory (Step 3) | One-line notice; skip the directory prefill / write-back; resolver-only run. Not a hard error — the directory is a cache |
| Slack user resolution returns nothing for a developer | Render that developer with plain name only; continue |
| `engineering-to-client` pass returns anything other than `CLIENT_COPY_DONE` or changes the block count | Stop, surface it, do not guard or compose |
| Storyteller returns `NO_INTERNAL_NOTES` | Stop, do not lint, do not preview, do not post |
| `ip_guard.py` exits 2 on the exec ledes after 3 rewrite iterations | Print residual violations with line offsets, do not compose, do not post |
| `slack_lint.py` exits 2 after 3 fix-and-relint iterations | Print residual violations, do not preview, do not post |
| Channel resolves to nothing / bot not in channel | Print error, do not post; remind the user to invite the bot to `#pod-oracle` |
| Root post fails (`ok:false` in result block) | Print `error` + stderr, do not retry; if the outcome is unverified, tell the user to check the channel before re-running |
| A thread reply fails partway | Stop; report posted vs. remaining replies and the root `ts`; resume posts only the remaining replies with `--thread-ts <ts>` — never a second top-level message |
| Release archive (Step 11b): permalink fetch fails | Omit the `Permalink:` line; still write the note; never construct a URL |
| Release archive (Step 11b): `release_archive.py record` exits 1 (root or reply file missing, bad date, empty posted set) | Surface stderr naming the cause; no partial archive; fix the input and re-run the archive step by hand |
| Release archive (Step 11b): collection unresolvable or ambiguous, or `create_note` fails after one immediate retry | Print the full rendered note in chat for a manual paste into the named collection (an ambiguous name also lists every candidate id). Never a disk write, never a re-post — the Slack post already succeeded and is authoritative |
| Replies still outstanding after a partial failure | No archive yet; the resume run that posts the last reply archives the whole release |
| User declines at preview | No Slack call; summaries + drafts remain on disk |

## Things this command does NOT do

- Does not produce a separate customer-facing post or document. There is one message, and it goes to `#pod-oracle`. The team ports the exec-voice lede to clients themselves.
- Does not use the retired `oracle-storyteller` skill or `assemble_oracle_root.py` — composition is handled by the shared `internal-storyteller` skill. Those files remain parked in the repo but unused.
- Does not query GitLab — the Oracle track is GitHub-only (`gather_notes_data_oracle.py`); the classic GitLab-backed pipelines are untouched.
- Does not run the classic pipelines (`/release-slack`, `/deploy-notes`, `/integration-notes`) — the tracks are independent on purpose.
- Does not run copy-audit — its rules are baked into the analyzer's EXEC_SUMMARY voice rules (matching `/release-slack`).
- Does not guard the internal section or the PR links — the channel is internal-only and internal attributes are the point; only the exec ledes pass through `ip_guard.py`.
- Does not cache PR discovery in Mem — the `Oracle PR Overrides` collection holds human picks only; the sweep runs fresh every time.
- Does not write a copy of the post to disk. The Mem note is the only archive (`release-archive` skill); the only files this command leaves behind are its `/tmp/` working files.
- Does not modify any Optimus tickets or GitHub PRs, write to `.env`, or blind-retry failed posts.
- Does not post without explicit confirmation in the same session — a scheduled invocation still stops at the preview.

## Coding Standards

- **Data gathering is handled by `gather_notes_data_oracle.py`.** Do NOT run individual Optimus or GitHub calls inline, and do NOT touch `gather_notes_data_optimus.py` — the classic track stays byte-identical.
- **Developer Slack ID resolution is handled by `slack_resolve_users.py`** — email (the PR commit-author email captured in Step 2) → exact unique full-name → fuzzy name, best-effort. Do NOT shell out to `curl`, do NOT invent IDs, and do NOT use a Slack MCP — the script owns `assignee_slack_id`.
- **`clients` and `pr_links` are persisted into the data JSON in Step 4** (and `assignee_slack_id` by the Step 3 scripts) before prompt preparation — the analyzer copies them verbatim; it never reconstructs a PR URL.
- **The Mem developer directory is optional and deterministic.** Mem calls go through the Mem MCP exactly as the `dev-directory` skill describes; all parsing, matching, and note rendering is `dev_directory.py`. Never hand-compose a directory note, never let a Mem failure stop the run.
- **PR overrides are optional and deterministic.** The gather script applies them (`--pr-overrides` dump, `--pr-override-inline` pairs) and picks the primary PR; `pr_overrides.py` renders and parses the override notes and emits the Mem writes; the command only performs the Mem calls the `pr-overrides` skill describes. Never hand-compose an override note, never fetch a PR outside the gather script, never let a Mem failure stop the run. Read PR-discovery outcomes from `pr_discovery` in the data JSON, not from stderr.
- **The Mem release archive is required, deterministic, and strictly post-success.** Mem's presence is checked in Step 1 (hard stop when absent); the note is written in Step 11b once the post is complete — in this session or on the resume run that finishes it — exactly as the `release-archive` skill describes. `release_archive.py record` renders the note from the summaries file plus the posted root and reply files, `slack_upload.py --get-permalink` is the only source of a permalink (omit on failure, never construct one), and the note is written by resolved collection UUID. Never hand-compose an archive note, never update-in-place, never write a copy to disk, and never let a Mem or permalink failure surface as a post failure — on a failed write, print the note in chat for a manual paste.
- **Per-ticket synthesis is handled by analyzer Task agents** — one per prompt file, spawned in a single assistant message, each emitting both an EXEC_SUMMARY and an INTERNAL_SUMMARY.
- **Client copy is finalized by the `engineering-to-client` skill in Step 6b** — one pass over the summaries file, before the IP guard. Do NOT skip it, and do NOT rewrite `For clients` text in the storyteller or by hand.
- **Message composition is handled by the `internal-storyteller` skill** (prefix `oracle_release`, product `Oracle`). Do NOT compose the root or reply bodies directly in the command.
- **Confidentiality validation is handled by `ip_guard.py`** over the exec ledes only. Do NOT skip the guard; do NOT extend it over the internal content.
- **Formatting validation is handled by `slack_lint.py`.** Do NOT skip the lint gate — it runs over the root AND every reply before the human preview.
- **Slack delivery is handled by `slack_upload.py`.** Do NOT use the Slack MCP for the post itself. One invocation per message: root first, then each reply with the captured `ts` and the resolved channel id from the result block. Never post a second top-level message.
- **Output filename uses today's date computed via `date +%Y-%m-%d`** — never hardcoded.
- **Channel resolution is exact-match only.** If `SLACK_CHANNEL_ORACLE` is a name that matches multiple channels, the script will error — surface it and stop.
