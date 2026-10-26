---
description: Post ONE combined internal release message to #dream-team — a compact root-post index plus threaded per-ticket cards that carry BOTH a client-portable exec-voice lede AND the internal technical detail (ticket IDs, developer @-mentions, client institution names, Optimus URLs). Replaces the former split /internal-notes + /release-slack commands. Internal-only; the team ports the exec-voice lede to clients themselves. Optional `--kb` queues the release's feature tickets for KB articles after the post.
---

# Release Slack Announcement (combined internal post)

Generate ONE Slack message for the internal Medtrics team and deliver it to `#dream-team` after the user explicitly approves. This command merges what used to be two separate posts — the customer-facing `/release-slack` and the internal `/internal-notes` — into a single message that carries **all the attributes of both**:

- the client-portable **exec-voice lede** for each ticket (what an institutional admin can now do), and
- the **internal detail** the customer-facing brief used to strip out: ticket identifiers, developer Slack `@` mentions, client institution names, technical mechanism vocabulary, and direct Optimus URLs.

The internal team reads the single post, then **ports the exec-voice lede to clients themselves** — there is no separate client-channel post. Because the message carries developer mentions, client names, and internal URLs, it is internal by nature and goes only to `#dream-team`. There is therefore **no IP guard** in this pipeline: internal vocabulary is the point.

This command never posts to Slack without explicit user confirmation in the same session.

## Usage

```
/release-slack                                   # status mode (default): all current deploy-ready tickets
/release-slack status                            # explicit
/release-slack tickets M1-986,M1-1039,M1-1118    # explicit ticket list (paste from Optimus Plan Deploy view)
/release-slack mr 6567                            # summarize a single GitLab MR
/release-slack dry-run                            # print what would be gathered/posted, then exit without posting
/release-slack --kb                               # status mode, then queue + offer KB articles after the post (Step 9c)
/release-slack tickets M1-986,M1-1039 --kb        # explicit ticket list, then queue + offer KB articles
```

You may combine the mode with `dry-run`, e.g. `/release-slack tickets M1-986 dry-run`.

`--kb` is opt-in and combines with any mode and with `dry-run`. It changes nothing about the release itself — gathering, analyzers, composition, lint, preview, posting and the Step 9b archive run exactly as without it, and it never delays or blocks the post. It only adds Step 9c, after the archive.

## Pipeline overview

```
1. validate .env keys + confirm the Mem MCP is connected (hard stop — Mem is the release archive)
2. gather data (Optimus tickets + GitLab MRs, with assignee + MR-author info)
3. resolve developer Slack user IDs — (3a) optional Mem developer directory prefill via
   dev_directory.py → (3b) slack_resolve_users.py for the rest (email → name → fuzzy) →
   (3c) store newly resolved developers back into Mem immediately (best-effort, never blocks)
4. enrich each ticket with the client institution list
5. per-ticket analyzer agents (parallel) → combined-format summary blocks
     (each block carries a DRAFT EXEC_SUMMARY and an INTERNAL_SUMMARY)
   5e. client copy pass: engineering-to-client skill finalizes EXEC_SUMMARY + CLIENTS in place
6. invoke the internal-storyteller skill → root-post index + thread-reply files
     (each card: `*n. ID · client headline*`, `For clients:` paragraph, then Ticket / Developer /
      Internal / URL / MRs / Clients bullets)
7. lint gate: slack_lint.py over the root AND every reply (fix-and-relint, max 3)
8. preview root + replies in chat → user approves
9. on approval: post root via slack_upload.py → capture ts + resolved channel id →
   post each reply with --thread-ts <ts>
   9b. once the post is complete (this session, or the resume run that finishes it): archive ONE Mem
       note for this release session — trace header + the posted root and replies verbatim
       (release-archive skill → release_archive.py record; Mem is the only archive, nothing on disk)
   9c. only with --kb, after 9b: resolve the KB Queue + KB Article Ledger collections → kb_ledger.py
       enqueue (this release's feature tickets) → write queue notes → hand off to /kb-articles
       `<ids> --from-release`, which asks y/n before drafting (never a release failure)
```

The Slack shape is **root-post-as-index**: one compact top-level message (numbered `ID · title` lines under `*Enhancements &amp; New Features*` / `*Bug Fixes*`, plus a `Clients to notify:` line), with all long-form detail in thread replies. **Never post a second top-level message** — if content exceeds a cap, it spills into additional thread replies deterministically.

There is **no IP guard and no separate customer-facing markdown** in this pipeline. The exec-voice lede on each card is written to the customer-facing storyteller's voice rules so it is safe for the team to lift and send to clients, but the surrounding message stays internal.

## Output

- Slack root post + thread replies to the channel resolved from `SLACK_CHANNEL_INTERNAL` (default `#dream-team`, channel id `C1BCUHXT9`). Posted only after the user confirms.
- **One Mem note for the release session** in the collection named by `MEM_RELEASE_ARCHIVE_COLLECTION` (default `Release Notes Archive`; `release-archive` skill) — date, channel, root ts, permalink, the posted ticket list, **and the posted root + every thread reply verbatim**. This is the **only** archive of the post: nothing is written to `.claude/outputs/` or anywhere else on disk. Written once the post is complete (this session, or the resume run that finishes it).
- **With `--kb` only:** one queue note per newly queued feature ticket in the collection named by `MEM_KB_QUEUE_COLLECTION` (default `KB Queue`). KB article drafts are produced only if the user answers `y` at the `/kb-articles` plan preview — those drafts, files and ledger notes belong to `/kb-articles`, not to this command.
- Working files only in `/tmp/` — the per-ticket summary blocks (`/tmp/release_slack_summaries.txt`) and the composed root / reply files (`/tmp/release_slack_root.txt`, `/tmp/release_slack_reply_NN.txt`); the archive step reads them.

Compute the date via `date +%Y-%m-%d` in Bash — do NOT hardcode or guess.

## Required .env keys

| Key | Purpose | Required |
|---|---|---|
| `OPTIMUS_API_KEY` | gather Optimus tickets | Yes |
| `GITLAB_TOKEN` | gather GitLab MR diffs and author | Yes |
| `GITLAB_PROJECT_ID` | GitLab project to query | Yes |
| `GITLAB_URL` | GitLab base URL | optional, defaults to `https://gitlab.com` |
| `SLACK_CHANNEL_INTERNAL` | channel id (`C1BCUHXT9`) or `#dream-team` | Yes |
| `SLACK_BOT_TOKEN` | `xoxb-...` token with `chat:write` + `channels:read` scopes (`groups:read` if the channel is private) | Yes |
| `MEM_RELEASE_ARCHIVE_COLLECTION` | Name of the Mem collection that receives the archive note for every posted release (`release-archive` skill). Created under that exact name if absent; defaults to `Release Notes Archive` when unset or blank. The key is optional — the **Mem MCP connection is not** (see Step 1) | optional |
| `MEM_KB_QUEUE_COLLECTION` | `--kb` only: name of the Mem collection that holds feature tickets waiting for a KB article (`kb-ledger` skill). Created under that exact name if absent; defaults to `KB Queue` | optional |
| `MEM_KB_LEDGER_COLLECTION` | `--kb` only: name of the KB Article Ledger collection, read so tickets that already have an article are not queued (`kb-ledger` skill). Defaults to `KB Article Ledger` | optional |

`SLACK_CHANNEL_RELEASE` is **no longer used** by this command — there is no client-channel post. If any required key is missing, abort early with a clear "Missing required env keys: [...]" message — do NOT proceed to any data gathering, file generation, or Slack call. The **Mem MCP must be connected** as well: it is the only archive of every release, so its absence is a hard stop at Step 1, not a degraded run.

## Required permissions

In `.claude/settings.local.json` under `permissions.allow`:

```json
"Bash(python3:*)",
"Bash(date:*)",
"Bash(cat:*)",
"Task",
"Skill"
```

## Flow

### Step 1 — Validate environment

Read `.env` from the current working directory. Verify the five required keys above are present (`GITLAB_URL` and `MEM_RELEASE_ARCHIVE_COLLECTION` are optional). If any are missing, list them and stop.

Then confirm the **Mem MCP is connected** — the release archive lives in Mem and nowhere else, so this is a precondition, not an optional extra. Follow the `release-archive` skill's preflight: if the Mem tools (`list_collections`, `create_collection`, `create_note`, …) are not available in this session, print `Mem MCP not connected — this command archives every release to Mem and will not run without it. Connect the Mem connector and re-run.` and stop before ANY Optimus, GitLab, or Slack call. This applies to `dry-run` too. Never fall back to a disk copy.

### Step 2 — Gather data

For `status` mode (default):

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/gather_notes_data_optimus.py status --output /tmp/release_slack_data.json --include-description
```

For `tickets <id1,id2,...>`:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/gather_notes_data_optimus.py tickets <id1,id2,...> --output /tmp/release_slack_data.json --include-description
```

For `mr <iid>`:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/gather_notes_data_optimus.py mr <iid> --output /tmp/release_slack_data.json --include-description
```

For `dry-run`: append `--dry-run` to the call, print the discovered issues + MRs, and exit before any analyzer or Slack call.

The gather script (v0.7+) emits `assignee_name`, `assignee_email`, `assignee_id` on each issue and `author_name`, `author_username` on each MR. This command depends on those fields for developer attribution — older script versions fall through to "Unassigned" rendering, which is acceptable but ugly.

If zero tickets come back, print `No deploy-ready tickets found. Nothing to post.` and stop before any analyzer call.

### Step 3 — Resolve developer Slack user IDs (Mem directory → resolver → Mem write-back)

Follow the bundled `medtrics-release-notes:dev-directory` skill for the Mem half; the Slack half is the resolver script. Three sub-steps, in order:

**3a — Mem developer directory prefill (optional).** If the Mem MCP tools are available in this session, locate (or create) the single collection `Release Notes Developer Directory`, `list_notes` it with content (page through `next_page`), save the merged `{"notes": [...]}` response verbatim to `/tmp/release_slack_dev_directory.json`, and run:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/dev_directory.py prefill \
    --data /tmp/release_slack_data.json --notes /tmp/release_slack_dev_directory.json
```

This writes `assignee_slack_id` + `assignee_slack_resolved_via: "mem"` onto every issue whose developer is already known (exact email → GitLab login → unique full name). If Mem is not connected or any Mem call fails, print `Developer directory: Mem not connected, using the Slack resolver only.` and skip 3a and 3c — **no hard error, no retry loop**.

**3b — Slack resolver for the rest.** Run the resolver script — it fills `assignee_slack_id` on each still-unresolved issue using the bot token (issues pre-filled in 3a are skipped and counted as `preresolved`), then the analyzer prompts pick it up:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/slack_resolve_users.py --data /tmp/release_slack_data.json
```

**3c — Store new developers in Mem (immediately, before the analyzers).** If Mem is connected and 3b's result block lists any `resolutions`, compute and perform the writes:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/dev_directory.py pending \
    --data /tmp/release_slack_data.json --notes /tmp/release_slack_dev_directory.json \
    --out /tmp/release_slack_dev_directory_pending.json --source /release-slack
```

For each entry in the output's `writes`: `create` → `create_note({content, collection_ids: [<directory uuid>]})`; `update` → `update_note({note_id, content, version})`. A failed write is logged and skipped; the post is unaffected. Keep the one-line tally for the preview: `Developer directory: {N} from Mem, {M} newly stored, {K} unresolved.`

The resolver (3b) resolves each developer in three tiers, stopping at the first hit:

1. **Email match** — `users.lookupByEmail` on the ticket's `assignee_email` (the classic track carries it from Optimus). Exact and always safe to tag.
2. **Exact name match** — a UNIQUE, case-insensitive, multi-token full-name match against the Slack directory (`users.list`). Single-token or ambiguous names are not tagged.
3. **Fuzzy name match** — a `difflib` similarity match (default threshold `0.72`) that tags the single best directory name clearly ahead of the runner-up; near-ties are left unresolved. Lower-confidence — the preview is the safety gate (each mention shows `@Name (Author Name)`). Disable with `--no-fuzzy`; tune with `--fuzzy-threshold`.

Deactivated members and bots are skipped in tiers 2–3. Best-effort: any unresolved developer is left with an empty `assignee_slack_id` and renders as a plain name — the script never blocks the pipeline. It prints a `resolved X/Y` summary and a `---SLACK_RESOLVE_RESULT---` JSON block (now including `preresolved` and a `resolutions` list with the tier per developer). Requires the bot token to hold `users:read.email` + `users:read`. **No Slack MCP is used** — this replaces the former `slack_search_users` step. Pass `--force` only when the user explicitly asks to bypass a cached Mem id for this run.

### Step 4 — Enrich each ticket with the client institution list

The combined post carries the client list (e.g., `KFSHRC`, `MSUCVM`, `ADTALEM, AUC`). The ticket description and MR diffs are the source of truth.

For each ticket, build the `CLIENTS` field by:

1. Scanning the Optimus task description for an explicit "Requested by …", "Reported by …", or "Client(s): …" line. If found, extract the comma-separated client tags exactly as written.
2. If no explicit attribution exists, scan the description and MR titles for any all-caps client tag matching the pattern `[A-Z][A-Z0-9-]{2,}` that the team recognizes (e.g., `KFSHRC`, `MSUCVM`, `ADTALEM`, `AUC`, `CVM-NCSU`, `ETSU`, `OHSU`). Keep the original casing.
3. If still empty, set `CLIENTS` to `TBC` — the storyteller will then render a one-line scope description from the INTERNAL_SUMMARY.

Do not invent client tags. `TBC` is the correct value for platform-wide work.

### Step 4b — Persist client tags + MR links into the data file (REQUIRED)

The client tags (Step 4) were computed in memory, and each ticket's MR links live on `mrs[].web_url`. `prepare_note_prompts.py` only emits `**Clients:**` and `**MR Links:**` into an analyzer prompt when those fields are **already present on the issue object in the JSON**. So you MUST write them back into `/tmp/release_slack_data.json` before Step 5b. Skip this and every card loses its client tags and MR links while the pipeline still runs and posts the degraded form silently. (The developer `@`-mention is handled separately — Step 3's resolver script already wrote `assignee_slack_id` into this same file.)

Write the client tags — plus each ticket's **MR links** (the GitLab merge-request URLs, joined from `mrs[].web_url`) — onto each matching issue (keyed by `identifier`) with a small Python snippet, e.g.:

```bash
python3 - <<'PY'
import json
path = "/tmp/release_slack_data.json"
data = json.load(open(path))

# Client map you built in Step 4, keyed by issue identifier:
clients = {"M1-986": "KFSHRC"}   # identifier -> comma-separated tag list, or "TBC"

for issue in data.get("issues", []):
    ident = issue.get("identifier")
    if clients.get(ident):
        issue["clients"] = clients[ident]
    # MR links are deterministic — join every MR's web_url:
    urls = [m.get("web_url") for m in issue.get("mrs", []) if m.get("web_url")]
    if urls:
        issue["mr_links"] = ", ".join(urls)

json.dump(data, open(path, "w"), indent=2)
PY
```

`assignee_slack_id` is NOT set here — Step 3's resolver script already wrote it into the same JSON. This step only adds `clients` and `mr_links`. The MR URLs are stored on each `mrs[]` entry as `web_url`; the analyzer receives them as `**MR Links:**` and copies them into `MR_LINKS` verbatim (it never reconstructs a URL). Both this step and Step 3 run before Step 5b (prompt prep); order between them does not matter — each does a full load-mutate-dump and touches different fields.

### Step 5 — Per-ticket analyzer (parallel)

Each analyzer produces ONE block per ticket carrying **both** voices — a client-portable `EXEC_SUMMARY` and an internal `INTERNAL_SUMMARY`. The analyzer's `EXEC_SUMMARY` is a **draft**: Step 5e runs the `engineering-to-client` skill over every block to finalize the `For clients` paragraph and confirm the `CLIENTS` value before composition.

#### 5a. Write the analyzer instructions file

```bash
cat > /tmp/release_slack_instructions.txt << 'INSTRUCTIONS_EOF'
You are summarizing one ticket for a SINGLE combined internal Medtrics release post in Slack (#dream-team).

The post is internal, but each ticket card leads with a short client-portable lede the team can lift and send to clients as-is. So you must produce TWO summaries per ticket:
  - EXEC_SUMMARY — client-safe, executive voice, no internal detail.
  - INTERNAL_SUMMARY — technical, for the dev team, keep everything.

Inputs you receive:
  - identifier (M1-NNN / GEN-NNNNNN / MR-NNNN)
  - title
  - description (verbatim)
  - url (Optimus task URL)
  - assignee_name, assignee_email, assignee_slack_id (slack id may be empty)
  - mr titles + author names
  - mr code diffs
  - clients (already resolved by the orchestrator; one of: comma-separated tag list, or "TBC")

Classify the ticket as exactly ONE of:
  - FEATURE: entirely new capability that didn't exist before
  - ENHANCEMENT: improvement to an existing feature
  - BUG FIX: correction of broken or incorrect behavior
  - SECURITY: security-related fix or hardening
  - REFACTOR: code restructuring with no user-visible behavior change

A REFACTOR is by definition a code restructuring with no user-visible behavior change, so it does not belong in the release digest. If CATEGORY is REFACTOR, return exactly:
NO_VISIBLE_CHANGE

Otherwise return EXACTLY this block — no preamble, no commentary outside the block:

IDENTIFIER: {identifier}
TITLE: {short readable title — keep the platform vocabulary}
CATEGORY: {FEATURE|ENHANCEMENT|BUG FIX|SECURITY|REFACTOR}
URL: {url}
DEVELOPER_NAME: {full name of the developer most responsible — Optimus assignee unless the MR was authored by someone else, in which case use the MR author}
DEVELOPER_SLACK_ID: {slack user id, e.g. U02AB0913 — empty string if unknown}
DEVELOPER_FIRST_NAME: {first name only, lowercased}
CLIENTS: {comma-separated tag list verbatim from the orchestrator, or "TBC"}
MR_LINKS: {copy the orchestrator's "MR Links" value verbatim — empty string if the ticket has no MRs; NEVER construct a URL yourself}
EXEC_SUMMARY: {2–3 sentences, executive voice for institutional administrators (DIOs, deans, IT directors, program directors). Lead with what the reader's people can now do, or what got better. This text must be safe to paste to a client: NO internal system names (issue tracker, source control, cloud provider, framework, vendors), NO code artifacts (serializer, viewset, model, migration, endpoint, route, queue, cache, worker, schema, middleware, signal, admin, settings), NO file paths, NO MR references, NO specific client/institution names (abstract to the segment, e.g. "an osteopathic college of medicine"), no marketing voice ("thrilled", "game-changing"). Plain, calm, declarative. This must be a SINGLE physical line.}
INTERNAL_SUMMARY: {2–4 sentences for the dev team, on a SINGLE physical line. Keep technical specifics — module names, the mechanism in one clause, the behavior in the next. For bug fixes, lead with what was broken in one clause, then the corrected behavior. Keep "Requested by {CLIENT} to ..." / "Reported by {CLIENT}" when present in the description. Do NOT use marketing voice. Do NOT apologize.}

Voice rules:
  - EXEC_SUMMARY follows the customer-facing storyteller rules above — user-outcome first, no internal vocabulary, no client names. It is the text the team ports to clients.
  - INTERNAL_SUMMARY uses the team's vocabulary freely: "evaluation form", "case log", "preceptor approval", "duty hour violation", "bulk import", "open form", "Personal Portal", "Program Settings", "Form Settings". Files, models, endpoints, async dispatch behavior are all fine — this is internal.
  - Active voice. Past tense for completed work. Present tense for new behavior.
  - Do NOT mention the storyteller skill, the lint gate, or the deploy pipeline.
  - Length: EXEC_SUMMARY 2–3 sentences; INTERNAL_SUMMARY 2–4 sentences. Never 5+.

Each field is ONE physical line. OUTPUT FORMAT — Return EXACTLY the 11-field block above (or NO_VISIBLE_CHANGE).
INSTRUCTIONS_EOF
```

#### 5b. Prepare prompt files

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/prepare_note_prompts.py \
    --data /tmp/release_slack_data.json \
    --instructions /tmp/release_slack_instructions.txt \
    --prefix release_slack \
    --output-manifest /tmp/release_slack_manifest.json
```

Read `/tmp/release_slack_manifest.json` to get the list of prompt files.

#### 5c. Spawn ALL analyzer agents in ONE assistant message

For every prompt file in the manifest:

- Use the Task tool with `subagent_type: "general-purpose"`.
- `description`: `"Summarize <identifier>"` (chunks: `"Analyze <identifier> chunk <N>"`).
- `prompt`: `Read the file <filepath> and follow the instructions it contains. Before returning, replace any developer placeholder with the resolved values the orchestrator passed at the top of the prompt file (assignee_name, assignee_email, assignee_slack_id, clients, MR Links).`

All Task calls go in the same assistant message.

> **Important:** the per-ticket prompt files include the resolved `DEVELOPER_SLACK_ID` and `CLIENTS` values that the orchestrator computed in Steps 3–4. The analyzer should copy those into the output block verbatim — it should not attempt to re-resolve them.

#### 5d. Collect results

For each ticket, you now have either:

- An 11-field block, or
- `NO_VISIBLE_CHANGE` (drop from downstream).

For chunked tickets, combine chunks by picking the most user-visible block. Write the collected per-ticket blocks to `/tmp/release_slack_summaries.txt`, one block per ticket, separated by `---`.

#### 5e. Client copy pass — engineering-to-client skill (REQUIRED, one pass)

Invoke the bundled `medtrics-release-notes:engineering-to-client` skill in **pipeline mode** over `/tmp/release_slack_summaries.txt`. It rewrites the file in place, touching only:

- a new `CLIENT_TITLE:` line after `TITLE:` → the client-facing headline (a few words, no ticket id, no jargon, no client name) — this becomes the card title `*n. ID · {CLIENT_TITLE}*` and the root-index entry. The internal `TITLE` stays in the block untouched.
- `EXEC_SUMMARY:` → the final client-facing paragraph (1–3 sentences, one physical line, no headline) — this becomes the card's `*_For clients:_*` text.
- `CLIENTS:` → confirmed verbatim (codes or `TBC`); never invented.
- an optional `CLIENT_FLAG: {code(s)} — {reason}` line on any block whose named client's item is still unresolved (so the team sends that one to the named client only, not in a broadcast).

Every other field and the block order stay byte-identical. The skill returns `CLIENT_COPY_DONE — {N} blocks, {F} flagged`; if it returns anything else, or the block count no longer matches, stop and surface it — do not compose from a half-rewritten file. Collect the `CLIENT_FLAG` lines for the preview.

### Step 6 — Synthesize via the internal-storyteller skill

Invoke the bundled `medtrics-release-notes:internal-storyteller` skill, passing it:

- the summaries file `/tmp/release_slack_summaries.txt`,
- the output prefix `release_slack`, and
- today's date.

(The root header is the uniform `:rocket: *Release Notes — {date}*` for every caller — no product label is passed.) The skill produces:

- `/tmp/release_slack_root.txt` — the root-post index (≤ 2,500 chars): numbered one-line entries `{n}. {ID} · {CLIENT_TITLE}` (the client-facing headline from Step 5e; no developer mention in the root) under `*Enhancements &amp; New Features*` and `*Bug Fixes*` headers (blank line before each header), plus a `Clients to notify:` line at the bottom.
- `/tmp/release_slack_reply_01.txt`, `_02.txt`, … — one thread reply per section carrying the long-form per-ticket cards (≤ 3,900 chars each; long sections split at card boundaries), with escalations (SECURITY items) as their own reply. Each card is:

  ```
  *1. M1-986 · {CLIENT_TITLE from Step 5e}*
  *_For clients:_* {client paragraph from Step 5e}
  • Ticket: M1-986
  • Developer: <@SLACK_ID> (Developer Name)
  • Internal: {internal summary}
  • URL: {Optimus URL}
  • MRs: {links}                      (omitted when the ticket has none)
  • Clients: {codes} | TBC (platform-wide)
  ```

If the skill returns `NO_INTERNAL_NOTES — nothing customer-visible shipped this deploy`, stop the pipeline: do not preview, do not post, just print that line back to the user.

The internal-storyteller does **not** run through an IP guard or the copy-audit skill — the post is meant to carry internal vocabulary, ticket identifiers, developer mentions, and client names. The client-safety of the `For clients` paragraph is enforced upstream by the `engineering-to-client` pass in Step 5e, not by a downstream guard.

### Step 7 — Lint gate (mandatory, before preview)

Run the deterministic mrkdwn lint over the composed root AND every reply file:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/slack_lint.py --input /tmp/release_slack_root.txt --kind root
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/slack_lint.py --input /tmp/release_slack_reply_01.txt --kind reply
# ...one call per reply file
```

Behavior by exit code (per file):

| Exit | Meaning | Action |
|---|---|---|
| `0` | PASS | Proceed once every file passes. |
| `2` | Violations | Read the JSON violations from stdout, fix the offending file (re-invoke the internal-storyteller with the violation list, or apply the mechanical fix directly for formatting-only violations), then re-lint. Iterate up to 3 times. If still failing, stop, print residual violations, do not preview, do not post. |
| `1` | Configuration error | Surface stderr, stop. |

The lint enforces: bare `<@U…>` mentions (no trailing display names), labeled links non-empty, no `**bold**` / `#` headers / fenced blocks, escaped `&` `<` `>` outside tokens, a blank line before each section header, root ≤ 2,500 chars, each reply ≤ 3,900 chars.

### Step 8 — Preview to user

Print the composed root post and each thread reply inside fenced blocks AND show the channel it will go to. Then ask:

```
Ready to post to <#CHANNEL>?
  Channel: <channel display>  (#dream-team)
  Root length: <N> chars; <R> thread replies
  Items: <X> enhancements / <Y> bug fixes
  Developer directory: <N> from Mem, <M> newly stored, <K> unresolved   (or "Mem not connected")
  Client-specific items (send to the named client only): <CLIENT_FLAG list, or "none">

  [y] post root + replies
  [n] cancel (no Slack action taken; summaries + drafts remain on disk)
  [e] let me edit the draft first
```

If user picks `e`, accept their edits in chat, write the edited text back to the corresponding `/tmp/release_slack_*.txt` file(s), re-run the lint gate (Step 7) on every edited file, re-show the preview, loop until `y` or `n`.

### Step 9 — Post (only if user said `y`)

**Multi-invocation orchestration — root first, then replies into its thread.** Never post a second top-level message.

1. Post the root:

   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/slack_upload.py \
       --channel "$SLACK_CHANNEL_INTERNAL" \
       --message-file /tmp/release_slack_root.txt
   ```

2. Parse the `---SLACK_UPLOAD_RESULT---` JSON block from stdout — `{"ok", "channel", "ts", "error"}`, emitted on every exit path. If `ok` is false, surface `error` and stderr verbatim, stop, and do NOT retry automatically. If the error says the write outcome is unverified ("verify in channel"), tell the user to check `#dream-team` before re-running.

3. If `ok` is true, capture `ts` AND the **resolved channel id** from the result block's `channel` field (Step 9b needs it for `--channel-id` and the permalink lookup), then post each reply file **in numeric order**, one invocation per reply:

   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/slack_upload.py \
       --channel "$SLACK_CHANNEL_INTERNAL" \
       --message-file /tmp/release_slack_reply_01.txt \
       --thread-ts <ts>
   ```

4. If a reply post fails partway, stop; report which replies posted and which did not, plus the root `ts`. Resume by re-running only the remaining reply files with `--thread-ts <ts>` — the root is never re-posted.

After all posts succeed:

- Print a one-line confirmation: `Posted to <#dream-team>. ts=<ts>. (<N> items: <X> enhancements, <Y> bug fixes; <R> thread replies.)`.
- Do **not** write a markdown copy to disk. The archive of this post is the Mem note written in Step 9b — nothing lands under `.claude/outputs/`.

### Step 9b — Archive to Mem (release-archive skill — the only archive)

Runs **after** the confirmation line, exactly as the `release-archive` skill describes. It writes **one Mem note for this release session** into the collection named by `MEM_RELEASE_ARCHIVE_COLLECTION` (default `Release Notes Archive`). The note carries the trace header (date, channel, root ts, permalink, the posted ticket list with client-facing titles and code links) **and the posted root plus every thread reply verbatim**. This is the **only** archive of the post — nothing is written to `.claude/outputs/` or anywhere else on disk. The step can never affect the post itself: the post has already succeeded and is authoritative.

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
       --summaries /tmp/release_slack_summaries.txt \
       --data /tmp/release_slack_data.json \
       --root-file /tmp/release_slack_root.txt \
       --reply-files /tmp/release_slack_reply_01.txt /tmp/release_slack_reply_02.txt ... \
       --product medtrics \
       --date <the same YYYY-MM-DD value used in the Slack root header> \
       --channel "$SLACK_CHANNEL_INTERNAL" \
       --channel-id <resolved channel id from the result block> \
       --ts <ts> \
       [--permalink <url from step 2>]
   ```

   The stdout is the note body. The ticket list comes from the summaries file (the posted set — `REFACTOR` and malformed blocks dropped), not from the data JSON. If `record` exits 1 (a root or reply file is missing, typically on a resume run in a new `/tmp`), surface its stderr naming the file and stop — do not archive a partial transcript.
4. **Write it:** `create_note({content, collection_ids: [<resolved collection uuid>]})`. On success print `Release archive: wrote note "<first line of the note>" to Mem collection "<name>" (ts <ts>); if this was a re-post, trash the older note in Mem.`
5. **If the write cannot happen** (collection unresolvable or ambiguous, or `create_note` fails once and again on one immediate retry): print `Release archive: Mem write failed (<reason>). Paste the note below into the "<name>" collection by hand:` followed by the full note body from step 3 in a fenced block. Then finish. Never write the note to disk and never re-post to Slack.

### Step 9c — Queue and offer KB articles (only with `--kb`)

Runs only when `--kb` was passed, and only **after** the post is complete AND Step 9b has run — the archive note written, or its manual-paste fallback printed. On a resume run (Step 9b's gate), it runs after the resume run's archive step. It never runs before or instead of the post or the archive, and nothing in it can surface as a release failure: the Slack post and the archive are authoritative. Any failure here — Mem, script, hand-off — is reported in chat in one line and the command finishes.

Follow the `kb-ledger` skill for every Mem call.

1. **Resolve the collections and dump them.** `mkdir -p /tmp/kb`, then:

   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/kb/kb_ledger.py resolve
   ```

   It prints the resolved KB Queue and KB Article Ledger names (`MEM_KB_QUEUE_COLLECTION` / `MEM_KB_LEDGER_COLLECTION`, defaults `KB Queue` / `KB Article Ledger`) and each collection's description. Resolve each by name exactly as the `kb-ledger` skill describes — paginated `list_collections` following `next_page`, case-insensitive exact title match, prefer exact case then the oldest `created_at`, `create_collection` under the exact name when none matches. Then `list_notes({collection_id, include_note_content: true, limit: 100})` each one, following `next_page` to the end, and save the merged `{"notes": [{id, version, content}, …]}` verbatim to `/tmp/kb/release_ledger_notes.json` and `/tmp/kb/release_queue_notes.json` (a just-created collection is `{"notes": []}`). If a collection is ambiguous or a Mem call fails, print `KB: could not read the KB collections (<reason>) — nothing queued; run /kb-articles <ids> by hand later.` and finish.

2. **Enqueue this release's feature tickets.**

   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/kb/kb_ledger.py enqueue \
       --data /tmp/release_slack_data.json \
       --summaries /tmp/release_slack_summaries.txt \
       --source release-slack \
       --date <the same YYYY-MM-DD value used in the Slack root header> \
       --ts <root ts> \
       --ledger-notes /tmp/kb/release_ledger_notes.json \
       --queue-notes /tmp/kb/release_queue_notes.json \
       --out /tmp/kb/release_queue_writes.json
   ```

   The first line of stdout is the comma-joined ticket ids to draft now (it may be empty). The `---KB_LEDGER_RESULT---` block carries `queued`, `already_queued`, `tickets` and `skipped`. In `mr <iid>` mode, `MR-NNNN` identifiers are never queued — they appear under `skipped`; say `KB: MR-NNNN identifiers are never queued for KB articles.` If the script exits 1, surface its stderr in one line and finish.

   Perform each write in `/tmp/kb/release_queue_writes.json` in order: `create_note({content, collection_ids: [<queue uuid>]})`. A failed queue write is retried once; if it fails again, report it in one line (`KB: queue write failed for <ticket> (<reason>).`) and carry on with the rest. A queue failure never affects the post.

3. **Nothing to draft.** If the first stdout line is empty — no feature tickets in this release, or every one already has an article — print `KB: no feature tickets to draft from this release (<queued> queued, <already_queued> already queued, <skipped count> skipped).` and finish.

4. **Hand off to `/kb-articles`.** Otherwise print `KB: queued <queued> feature tickets (<already_queued> already queued). Handing off to /kb-articles for: <ids>.` and continue, in this same chat, with the `/kb-articles` flow (`commands/kb-articles.md`) in its **release hand-off mode**, for exactly these tickets and nothing else on the queue:

   ```
   /kb-articles <ids> --from-release
   ```

   `--from-release` tells `/kb-articles` to reuse `/tmp/release_slack_data.json` and `/tmp/release_slack_summaries.txt` instead of gathering again — no second Optimus or GitLab call. `/kb-articles` runs its own preflight, shows its plan preview and **asks the user y/n**. Never pass `--yes` from here.

   - On `n`: print `KB: not drafted — <ids> stay on the KB Queue; /kb-articles pending will draft them later.` and finish.
   - On `y`: `/kb-articles` drafts, packages, writes its ledger notes and marks the queue entries drained. That is `/kb-articles`' job, not this step's — do not write ledger or queue updates here.
   - If the hand-off fails mid-run, report where it stopped in one line; the tickets stay queued for `/kb-articles pending`. The release is not re-run, re-posted or re-archived.

**`dry-run` with `--kb`:** after the normal dry-run output, resolve and dump the two collections as in step 1 (read-only — create nothing; a missing collection is `{"notes": []}`), then run only the `enqueue` script with `--out /tmp/kb/release_queue_writes_dryrun.json`. No analyzer has run in a dry-run, so omit `--summaries` and `--ts` (the count then comes from the gathered tickets and can include one the analyzer would later drop). Do **not** perform the Mem writes and do not hand off. Print `KB: would queue N feature tickets: <ids>` (or `KB: would queue 0 feature tickets.`).

## Failure modes and behavior

| Condition | Behavior |
|---|---|
| Missing required env keys | Abort before any tool call, list missing keys |
| Mem MCP not connected at Step 1 | **Hard stop** before any tool call — Mem is the only release archive. Print the one-line notice; no gathering, no post, no disk fallback (applies to `dry-run` too) |
| Gather returns zero tickets | Print "Nothing to post" and stop |
| A Mem call fails in the developer directory (Step 3) | One-line notice; skip the directory prefill / write-back; resolver-only run. Not a hard error — the directory is a cache |
| Slack user resolution returns nothing for a developer | Render that developer with plain name only; continue |
| `engineering-to-client` pass returns anything other than `CLIENT_COPY_DONE` or changes the block count | Stop, surface it, do not compose |
| Storyteller returns `NO_INTERNAL_NOTES` | Stop, do not preview, do not post |
| `slack_lint.py` exits 2 after 3 fix-and-relint iterations | Print residual violations, do not preview, do not post |
| Channel resolves to nothing | Print error, do not post |
| Root post fails (`ok:false` in result block) | Print `error` + stderr, do not retry; if the outcome is unverified, tell the user to check the channel before re-running |
| A thread reply fails partway | Stop; report posted vs. remaining replies and the root `ts`; resume posts only the remaining replies with `--thread-ts <ts>` — never a second top-level message |
| Release archive (Step 9b): permalink fetch fails | Omit the `Permalink:` line; still write the note; never construct a URL |
| Release archive (Step 9b): `release_archive.py record` exits 1 (root or reply file missing, bad date, empty posted set) | Surface stderr naming the cause; no partial archive; fix the input and re-run the archive step by hand |
| Release archive (Step 9b): collection unresolvable or ambiguous, or `create_note` fails after one immediate retry | Print the full rendered note in chat for a manual paste into the named collection (an ambiguous name also lists every candidate id). Never a disk write, never a re-post — the Slack post already succeeded and is authoritative |
| Replies still outstanding after a partial failure | No archive yet; the resume run that posts the last reply archives the whole release |
| User declines at preview | No Slack call; summaries + drafts remain on disk |
| KB (Step 9c, `--kb`): a queue `create_note` fails after one immediate retry | One line naming the ticket and reason; carry on with the other writes. Never a release failure — the post and archive stand |
| KB (Step 9c, `--kb`): `kb_ledger.py enqueue` exits 1 | Surface its stderr in one line; nothing queued, no hand-off. The post and archive stand |
| KB (Step 9c, `--kb`): user answers `n` at the `/kb-articles` plan preview | Say the tickets stay queued and `/kb-articles pending` will draft them later; finish |
| KB (Step 9c, `--kb`): the `/kb-articles` hand-off fails mid-run | Report where it stopped in one line; the tickets stay queued for `/kb-articles pending`. No re-post, no re-archive |

## Things this command does NOT do

- Does not produce a separate customer-facing markdown or a separate client-channel post. There is one message, and it goes to `#dream-team`. The team ports the exec-voice lede to clients themselves.
- Does not run the IP guard or the copy-audit skill. The message is internal; the client-safety of the exec lede is enforced upstream by the analyzer's EXEC_SUMMARY voice rules, not a downstream gate.
- Does not produce integration-partner change notes — that lives in `/integration-notes` and `/notes`.
- Does not modify any Optimus tickets or GitLab MRs.
- Does not write a copy of the post to disk. The Mem note is the only archive (`release-archive` skill); the only files this command leaves behind are its `/tmp/` working files.
- Does not write to `.env` or to the plugin directory.
- Does not blind-retry failed posts (`slack_upload.py` retries rate limits internally; a failed or unverified write is surfaced, never re-posted automatically).
- Does not auto-schedule itself.
- Does nothing KB-related without `--kb` — no KB collection reads, no queue notes, no hand-off.
- Does not draft KB articles without the user's `y` at the `/kb-articles` plan preview, and never passes `--yes` to it.

## Coding Standards

- **Data gathering is handled by `gather_notes_data_optimus.py`.** Do NOT run individual Optimus or GitLab calls inline.
- **Developer Slack ID resolution is handled by `slack_resolve_users.py`** (bot-token `users.lookupByEmail` + a unique-name fallback). Do NOT shell out to `curl`, and do NOT use a Slack MCP — the script owns `assignee_slack_id`.
- **The Mem developer directory is optional and deterministic.** Mem calls go through the Mem MCP exactly as the `dev-directory` skill describes; all parsing, matching, and note rendering is `dev_directory.py`. Never hand-compose a directory note, never let a Mem failure stop the run.
- **The Mem release archive is required, deterministic, and strictly post-success.** Mem's presence is checked in Step 1 (hard stop when absent); the note is written in Step 9b once the post is complete — in this session or on the resume run that finishes it — exactly as the `release-archive` skill describes. `release_archive.py record` renders the note from the summaries file plus the posted root and reply files, `slack_upload.py --get-permalink` is the only source of a permalink (omit on failure, never construct one), and the note is written by resolved collection UUID. Never hand-compose an archive note, never update-in-place, never write a copy to disk, and never let a Mem or permalink failure surface as a post failure — on a failed write, print the note in chat for a manual paste.
- **Per-ticket synthesis is handled by analyzer Task agents** — one per ticket, spawned in a single assistant message, each emitting both an EXEC_SUMMARY and an INTERNAL_SUMMARY.
- **Client copy is finalized by the `engineering-to-client` skill in Step 5e** — one pass over the summaries file. Do NOT skip it, and do NOT rewrite `For clients` text in the storyteller or by hand.
- **Message composition is handled by the `internal-storyteller` skill.** Do NOT compose the Slack root or reply bodies directly in the command.
- **Formatting validation is handled by `slack_lint.py`.** Do NOT skip the lint gate — it runs over the root AND every reply before the human preview.
- **Slack delivery is handled by `slack_upload.py`.** Do NOT use the Slack MCP for the post itself; the bundled script handles channel resolution, `chat.postMessage`, and `--thread-ts` replies. One invocation per message: root first, then each reply with the captured `ts`. Never post a second top-level message.
- **Output filename uses today's date computed via `date +%Y-%m-%d`** — never hardcoded.
- **`--kb` is strictly post-archive and failure-isolated.** Step 9c runs only after Step 9b; queue notes are computed by `kb_ledger.py enqueue` (never hand-composed) and written by resolved collection UUID per the `kb-ledger` skill; drafting, ledger notes and draining the queue belong to `/kb-articles`. Never let a Step 9c failure surface as a release failure.
- **Channel resolution is exact-match only.** If `SLACK_CHANNEL_INTERNAL` is a name that matches multiple channels, the script will error — surface it and stop.
