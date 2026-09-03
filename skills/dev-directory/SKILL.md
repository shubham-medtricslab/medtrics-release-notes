---
name: dev-directory
description: Optional Mem-backed developer directory for the release commands — ONE Mem collection ("Release Notes Developer Directory", one note per developer) that caches each developer's Slack user id keyed by email, GitHub/GitLab login, and full name. Use in Step 3 of `/release-slack` and `/oracle-release` — look each developer up in Mem first, fall back to `slack_resolve_users.py` for the rest, then store every newly resolved developer back into Mem immediately. Only active when the Mem MCP is connected; never raises a hard error and never blocks the post.
---

# Developer Directory (Mem, optional)

Both release commands need a Slack user id per developer so the post can `@`-mention them. Today that comes from `scripts/slack_resolve_users.py` (email → exact name → fuzzy name against the Slack directory) and is thrown away with the temp data file after every run. This skill adds a persistent, per-user **cache** of those answers in Mem so a developer only has to be resolved once, and so a manual correction (editing the note) sticks.

Mem is a cache, **not** a source of truth and **not** a requirement. The Slack bot token remains the authority; if Mem is not connected, or any Mem call fails, the command proceeds exactly as it did before this skill existed. There are no hard errors anywhere in this flow.

## Is Mem connected?

Mem is "connected" when the Mem MCP tools are available in the current session — look for tools named `create_collection`, `search_collections` / `list_collections`, `list_notes`, `create_note`, and `update_note` (their names carry an MCP server prefix). If they are absent, print one line — `Developer directory: Mem not connected, using the Slack resolver only.` — and skip every Mem step below. Do not ask the user to connect Mem mid-run.

## The collection

| | |
|---|---|
| Title | `Release Notes Developer Directory` (exact, case-insensitive match) |
| Cardinality | Exactly one; one **note per developer** |
| Description | Use `collection_description` from `dev_directory.py pending` output, or the constant `COLLECTION_DESCRIPTION` in the script |
| Owner | The user running the command (Mem is per-user; each teammate builds their own directory) |

Bootstrap once per run: `search_collections({query: "Release Notes Developer Directory"})` (or `list_collections`), pick the entry whose title matches exactly (case-insensitive), cache its UUID. If none matches, `create_collection({title, description})` and cache the returned id. If this fails, treat Mem as not connected for the rest of the run.

## Note schema

One note per developer. The first line is the Mem title. Rendered and parsed by `scripts/dev_directory.py` — never hand-compose it in the command.

```
Developer: Chris Tokodi

**Slack ID:** U02AB0913
**Emails:** chris@medtricslab.com
**GitHub login:** ctokodi
**GitLab username:** —
**Optimus assignee id:** 42
**Resolved via:** email
**First seen:** 2026-09-02
**Last used:** 2026-09-02
**Source:** medtrics-release-notes /oracle-release
```

`Resolved via` records the tier that first produced the id (`email`, `name`, `fuzzy`, or `manual` when a human wrote the note). A fuzzy-tier note is cached like any other — the user chose immediate write-back — so if the preview ever shows a wrong `@Name (Author Name)` pairing, fix it at the source: edit the note's `Slack ID` (or trash the note) in Mem. The next run picks up the correction. `--force` on the resolver bypasses the cache for a single run without touching Mem.

Notes in the collection that do not start with `Developer: ` or lack a plausible `Slack ID` are ignored by the parser, so stray notes cannot corrupt a run.

## Flow (Step 3 of either command)

`{prefix}` is `release_slack` or `oracle_release`; `{data}` is the command's gathered data JSON (`/tmp/release_slack_data.json` or `/tmp/oracle_notes_data.json`).

### 3a — Read the directory and prefill

1. `list_notes({collection_id: <uuid>, include_note_content: true, limit: 100})`. Follow `next_page` until exhausted (the directory is a few dozen notes at most). Merge every page into one object `{"notes": [ {id, version, content, ...}, ... ]}` and save it **verbatim** to `/tmp/{prefix}_dev_directory.json` — keep each note's `id`, `version`, and `content`; the parser needs all three.
2. Run the deterministic prefill:

   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/dev_directory.py prefill \
       --data {data} --notes /tmp/{prefix}_dev_directory.json
   ```

   It matches each ticket's developer by **exact email → GitHub/GitLab login → unique normalized full name**, writes `assignee_slack_id` + `assignee_slack_resolved_via: "mem"` onto the matched issues, and prints a `---DEV_DIRECTORY_RESULT---` block (`prefilled`, `by_email`, `by_login`, `by_name`, `unmatched`). On the Oracle shape it also writes `assignee_name_from_directory` so the analyzer sees the developer's proper display name instead of a GitHub login.

If Mem is not connected, skip 3a entirely (do not even create the notes file — the resolver then resolves everyone, exactly as before).

### 3b — Resolve the rest with the Slack resolver

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/slack_resolve_users.py --data {data}
```

The resolver **skips** every issue that already carries an `assignee_slack_id` (reported as `preresolved` in its result block) and stamps `assignee_slack_resolved_via` (`email` / `name` / `fuzzy` / `""`) on the ones it handles. Its `resolutions` list names every developer it newly resolved. Unchanged otherwise: best-effort, plain-name fallback, never blocks.

### 3c — Store newly resolved developers in Mem (immediately)

Runs right after 3b, before the analyzers — not after the preview. Skip if Mem is not connected or 3b resolved nobody new.

1. Compute the writes:

   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/dev_directory.py pending \
       --data {data} --notes /tmp/{prefix}_dev_directory.json \
       --out /tmp/{prefix}_dev_directory_pending.json --source /{command}
   ```

   The output is `{"collection_title", "collection_description", "writes": [...]}` where each write is either
   - `{"action": "create", "collection_title", "content", ...}` — a developer with no note yet (one create per developer even when they own several tickets), or
   - `{"action": "update", "note_id", "version", "content", ...}` — a known developer gained a new email/login/assignee id; the body is the full merged note.

   A developer whose identity the directory already maps to a **different** Slack id is never merged into that note — the script emits a separate create so both mappings are visible in Mem for a human to reconcile.

2. Perform each write with the Mem MCP:
   - create → `create_note({content, collection_ids: [<directory uuid>]})`
   - update → `update_note({note_id, content, version})` — pass the `version` from the write verbatim. A version-conflict error means someone edited the note during the run; skip that write and mention it.

3. Report one line in the run log and again in the preview: `Developer directory: {N} from Mem, {M} newly stored, {K} unresolved.`

## Failure handling (never block)

| Condition | Behavior |
|---|---|
| Mem tools absent | One-line notice; resolver-only run |
| `search_collections` / `create_collection` fails | Treat as not connected for this run |
| `list_notes` fails or returns malformed data | Skip 3a; run the resolver for everyone; still attempt 3c only if the collection uuid is known (writes then create notes for every newly resolved dev) |
| `dev_directory.py` exits 1 | Surface stderr, continue without the directory |
| A `create_note` / `update_note` fails | Log it, continue with the remaining writes; the Slack post is unaffected |
| Slack resolver fails (exit 1) | Existing behavior — plain names; Mem prefilled ids are kept |

Nothing here may stop, delay for a retry loop, or degrade the Slack post. The worst case is "same as 0.16.0".

## What goes into Mem

Only work identifiers: developer name, work email(s), GitHub/GitLab login, Optimus assignee id, Slack user id, dates, and which command wrote it. No ticket content, no client names, no diffs. The notes are private to the user's Mem workspace.

## What this skill does not do

- Does not call Slack. `slack_resolve_users.py` owns every Slack lookup.
- Does not compose or hand-edit note bodies — `dev_directory.py` renders and parses them so the schema stays stable.
- Does not write anything to Mem for developers served from Mem (no "last used" churn) or for developers who stayed unresolved.
- Does not share the directory across teammates; Mem collections are per user.
- Does not gate the post. It is a cache in front of an already-optional `@`-mention.
