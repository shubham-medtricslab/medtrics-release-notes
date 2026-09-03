---
name: pr-overrides
description: Optional Mem-backed PR overrides for /oracle-release — ONE Mem collection (Oracle PR Overrides, one note per ORC ticket) that records the GitHub PR a human chose when the automatic PR sweep was wrong, ambiguous, or empty. Use in Step 2-pre of /oracle-release (dump the collection so the gather script can apply the overrides) and in Step 10b (record an ORC-NNN → PR pick from the preview, then re-run the gather). Overrides only, never a cache. Only active when the Mem MCP is connected; never raises a hard error and never blocks the post.
---

# PR Overrides (Mem, optional)

`/oracle-release` finds each ORC ticket's GitHub PRs with one PR-list sweep per run (branch + title matching) plus a per-ticket search secondary, then picks a **primary** PR by a fixed rule (one open PR → it; none open → newest merged; two or more open → the single non-draft, else `flagged_ambiguous`). That is right most of the time. When it is not — two open PRs and only a person knows which one ships, a PR whose branch and title never carried the ticket id, a ticket older than the ~500-PR sweep window — a human records the PR **once**, in Mem, and every later run honors it.

Mem holds **overrides only**. It is never a cache of what the sweep found: the sweep runs once per release, costs at most five requests, and a cache would only add staleness (PR A closed unmerged, PR B opened). A note exists only because a person decided. If Mem is not connected, or any Mem call fails, the command runs on the sweep alone and says so. There are no hard errors anywhere in this flow.

## Is Mem connected?

Mem is "connected" when the Mem MCP tools are available in the current session — look for tools named `create_collection`, `search_collections` / `list_collections`, `list_notes`, `create_note`, and `update_note` (their names carry an MCP server prefix). If they are absent, print one line — `PR overrides: Mem not connected, sweep only.` — and skip every Mem step below. Do not ask the user to connect Mem mid-run. A pick made at the preview is still honored for that run through `--pr-override-inline` (see Step 10b); it is just not saved.

## The collection

| | |
|---|---|
| Title | `Oracle PR Overrides` (exact, case-insensitive match) |
| Cardinality | Exactly one; one **note per ticket** |
| Description | Use `collection_description` from `pr_overrides.py pending` output, or the constant `COLLECTION_DESCRIPTION` in the script |
| Pinned id | `.env` key `MEM_PR_OVERRIDES_COLLECTION_ID` (optional) — the collection's UUID |
| Owner | The user running the command (Mem is per-user) |

Resolution, once per run:

1. If `MEM_PR_OVERRIDES_COLLECTION_ID` is set and non-empty in `.env`, use it directly — no search.
2. Else `search_collections({query: "Oracle PR Overrides"})` (or `list_collections`) and pick the entry whose title matches exactly (case-insensitive).
3. Else `create_collection({title, description})`.
4. Whenever the id came from step 2 or 3, print it once and suggest pinning it: `PR overrides collection: <uuid> — add MEM_PR_OVERRIDES_COLLECTION_ID=<uuid> to .env to skip the lookup next time.` Never write to `.env` yourself.

If any of this fails, treat Mem as not connected for the rest of the run.

## Note schema

One note per ticket. The first line is the Mem title. Rendered and parsed by `scripts/pr_overrides.py` — never hand-compose it in the command.

```
PR Override: ORC-232

**Ticket:** ORC-232
**PR:** 415
**PR URL:** https://github.com/medtrics/medtrics-oracle/pull/415
**Resolved by:** shubham@medtricslab.com
**Resolved at:** 2026-09-02T12:00:00Z
**Reason:** two open PRs; 416 is a spike branch
```

`PR` must be a positive integer (a leading `#` or a full PR URL is tolerated). `Ticket` must be `ORC-NNN`. Notes in the collection that do not start with `PR Override: `, or whose ticket or PR is malformed, are ignored by the parser (a malformed override note produces a warning naming its id), so stray notes cannot corrupt a run. Two notes for one ticket: the newest `Resolved at` wins, with a warning — trash the stale one in Mem.

**To change a decision by hand:** edit the note's `PR` line in Mem (or trash the note). The next run picks it up. Nothing else needs to change.

## Flow

`{data}` is `/tmp/oracle_notes_data.json`; the dump is `/tmp/oracle_pr_overrides.json`.

### Step 2-pre — Dump the collection for the gather

Runs before the gather, only when Mem is connected and the collection id is known.

1. `list_notes({collection_id: <uuid>, include_note_content: true, limit: 100})`. Follow `next_page` until exhausted (one note per ticket — a few dozen at most). Merge every page into one object `{"notes": [ {id, version, content, ...}, ... ]}` and save it **verbatim** to `/tmp/oracle_pr_overrides.json` — keep each note's `id`, `version`, and `content`; the parser needs all three.
2. Pass the dump to the gather: `--pr-overrides /tmp/oracle_pr_overrides.json`. The gather script imports `pr_overrides.load_overrides` itself; a missing or empty dump is simply "no overrides".

Optional, for the preview or when the user asks what is stored:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/pr_overrides.py show --notes /tmp/oracle_pr_overrides.json
```

If Mem is not connected, `rm -f /tmp/oracle_pr_overrides.json` so a stale dump from an earlier session is not applied, then run the gather without `--pr-overrides` (or with the flag pointing at a file that does not exist — both mean no overrides).

### Step 10b — Record a pick from the preview

The preview's **PR discovery** block lists the tickets that need a human (`flagged_ambiguous`, `no_pr_yet`, `no_pr_in_window`). The user replies `ORC-232 → 415` (one line per ticket; `->` and `=` are accepted too). For each pair:

1. Compute the write:

   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/pr_overrides.py pending \
       --ticket ORC-232 --pr 415 \
       --notes /tmp/oracle_pr_overrides.json \
       --out /tmp/oracle_pr_overrides_pending.json \
       --resolved-by <user email> [--reason "<what the user said, if anything>"] \
       --data /tmp/oracle_notes_data.json
   ```

   The output is `{"collection_title", "collection_description", "writes": [ one write ]}` where the write is either
   - `{"action": "create", "collection_title", "ticket", "pr", "content"}` — the ticket has no note yet, or
   - `{"action": "update", "note_id", "version", "previous_pr", "ticket", "pr", "content"}` — the ticket already has a note; the body is the full replacement.

   `--data` is used only to fill `PR URL` from a matching `mrs` entry; otherwise the URL is built from `GITHUB_REPO` in `.env` (best-effort). Exit `1` (with `ok: false` in the result block) on a ticket that is not `ORC-NNN` or a PR that is not a positive integer — tell the user and ask again; do not write.

2. Perform the write with the Mem MCP:
   - create → `create_note({content, collection_ids: [<collection uuid>]})`
   - update → `update_note({note_id, content, version})` — pass the `version` from the write verbatim. A version-conflict error means someone edited the note during the run; skip that write and mention it.

3. Re-dump the collection (Step 2-pre again — or edit the saved dump in place: **replace** the entry with the same note `id` after an update, add the new note after a create, never append a duplicate id) and **re-run Step 2** with `--pr-overrides`, then Steps 4–9, then show the preview again. The recorded ticket now shows `Overrides applied: ORC-232 → PR#415`.

**If Mem is not connected** (or a write failed): apply the pick for this run only by adding `--pr-override-inline ORC-232=415` to the Step 2 gather (repeat the flag, or comma-separate pairs). It is the same code path — the PR is fetched by number if the sweep did not carry it, marked `matched_via: manual`, decision `override` — but nothing is persisted. Say so: `PR pick applied for this run only (Mem not connected / write failed) — it will not be remembered.`

## Failure handling (never block)

| Condition | Behavior |
|---|---|
| Mem tools absent | One-line notice; gather runs on the sweep alone; picks apply via `--pr-override-inline` |
| `.env` has no collection id | Resolve by title, else create; print the id and suggest pinning it. If that fails, treat as not connected |
| `list_notes` fails or returns malformed data | Skip the dump (an unreadable dump file is also tolerated by the gather: no overrides plus a warning); gather runs on the sweep alone; still attempt Step 10b writes if the collection id is known |
| Override note malformed / `PR` not an integer | Ignored; warning names the note id (in `pr_discovery.warnings` and stderr) |
| Two notes for one ticket | Newest `Resolved at` wins; warning; user can trash the stale one |
| Override PR returns 404 from GitHub | Override ignored with a warning; the sweep result stands for that ticket |
| Override PR not in the sweep | The gather fetches it once by number (`GET /pulls/{n}`) and includes it with diffs like any other PR |
| `pr_overrides.py` exits 1 | Surface stderr; nothing is written; ask the user to restate the pick |
| A `create_note` / `update_note` fails | Log it; apply the pick via `--pr-override-inline` for this run; tell the user the note was not saved |

Nothing here may stop, delay for a retry loop, or degrade the Slack post. The worst case is "same as 0.17.0" — the sweep result, with the preview naming the tickets that need attention.

## What goes into Mem

Only a ticket id, a PR number and URL, who decided, when, and an optional one-line reason. No ticket content, no client names, no diffs. The notes are private to the user's Mem workspace.

## What this skill does not do

- Does not call GitHub. Every GitHub request — including fetching an override PR by number — happens inside `gather_notes_data_oracle.py`, never from the command.
- Does not compose or hand-edit note bodies — `pr_overrides.py` renders and parses them so the schema stays stable.
- Does not cache sweep results. A ticket whose PRs the sweep found correctly never gets a note.
- Does not write PR links back to Optimus (that field is not API-writable).
- Does not gate the post. `flagged_ambiguous` is advice in the preview; the release lists every PR either way.
