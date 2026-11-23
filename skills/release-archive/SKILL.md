---
name: release-archive
description: Mem-backed release archive for the release commands — the ONLY archive of a posted release outside Slack; a note Mem refuses waits in a local outbox only until a later run uploads it. ONE Mem collection (name from the `.env` key `MEM_RELEASE_ARCHIVE_COLLECTION`, default `Release Notes Archive`; created under that exact name if absent), one note per release session, recording each posted release's date, product, channel, Slack ts + permalink, its posted ticket list, AND the posted root + every thread reply verbatim. Mem must be connected before `/release-slack` or `/oracle-release` gathers anything (Step 1 preflight); the note is written after every completed post, including a resume run that finishes the remaining replies. A failed write never un-posts anything. A rate-limited write waits 30s and 90s, then goes to the outbox; any other failure prints the note in chat for a manual paste.
---

# Release Archive (Mem — the only archive)

Every `/release-slack` and `/oracle-release` post is archived in **one place: Mem**. There is no markdown copy on disk any more — the `.claude/outputs/notes/release-slack/` and `.claude/outputs/notes/oracle/` transcript files that earlier versions wrote are gone, and nothing replaces them locally. After every completed post, the command writes **one Mem note per release session** into a user-named archive collection. The note answers two questions:

- **Trace** — *given a ticket, which release shipped it, when, and where?* A Mem search for `ORC-232` or `M1-1457` lands on the release note; the stored permalink clicks straight through to the Slack thread.
- **Copy** — *what exactly did we post?* The posted root and every thread reply are stored verbatim under a `## Posted message` heading, so the full message text exists outside Slack.

Because Mem is the only archive, **Mem is required**: both commands probe for the Mem MCP in Step 1, alongside the `.env` key check, and stop before any gathering when it is not connected. The archive step itself still runs strictly **after** the post and can never un-post or re-post anything.

**When Mem rate-limits the write** (0.25.0), the note goes to the **outbox**, `.medtrics-release-notes/outbox/` in the run folder (ignored by git). The next `/release-slack`, `/oracle-release` or `/pod-notify` uploads it at Step 1 and deletes the file once Mem accepts it (`mem-calls` skill). The outbox is a queue, not an archive: nothing stays on disk once Mem has the note. Any other failure prints the rendered note in chat for a manual paste. Notes that were saved by hand before the outbox existed go in with `/release-note-setup backfill <files>`.

## Is Mem connected? (Step 1 preflight — hard stop)

Mem is "connected" when the Mem MCP tools are available in the current session — look for tools named `create_collection`, `list_collections`, `list_notes`, `create_note`, and `update_note` (their names carry an MCP server prefix). Both commands check this in **Step 1**, together with the required `.env` keys, before any Optimus, GitLab, GitHub, or Slack call:

- **Connected** → continue.
- **Not connected** → print `Mem MCP not connected — this command archives every release to Mem and will not run without it. Connect the Mem connector and re-run.` and **stop**. Do not gather, do not compose, do not post. Do not fall back to a disk copy.

The probe is a tool-availability check only; it makes no Mem call and costs nothing. A `dry-run` still requires Mem, so a preview and a real run behave identically.

## The collection

This skill **deliberately differs** from `dev-directory` (hardcoded title) and `pr-overrides` (pinned UUID) in how the collection is found: it is resolved by a **user-configured name**.

| | |
|---|---|
| Name | From the `.env` key `MEM_RELEASE_ARCHIVE_COLLECTION` (process environment overrides `.env`). Default `Release Notes Archive` when unset or whitespace-only. Matched exact, case-insensitive |
| Cardinality | Exactly one; one **note per release session** (never per ticket) |
| Description | Use `collection_description` from `release_archive.py pending` output, or the constant `COLLECTION_DESCRIPTION` in the script |
| Create-if-missing | If no collection matches the configured name, `create_collection({title: <configured name>, description})` — created under the **exact** name the user set |
| Owner | The user running the command (Mem is per-user) |

**How the name is read.** There is no generic `.env`→process-env loader in this plugin, so `scripts/release/release_archive.py` parses `.env` at CWD itself for `MEM_RELEASE_ARCHIVE_COLLECTION`, with a process-environment value taking precedence (the same pattern as `pr_overrides.py` reading `GITHUB_REPO`). It never writes to `.env`. `release_archive.py pending` **prints the resolved name on every run**, so the user always sees where notes are going.

**Resolution — the saved id first (0.25.0).** The id is saved in the folder's config under `mem.release_archive`, and the command reads it at Step 1 with `mem_ids.py need` (`mem-calls` rule 1). A normal run makes **no lookup call**. Only when the id is missing, or Mem says it no longer exists, does Step 1 walk `list_collections` once, for every missing key together, and apply the rule below via `mem_ids.py resolve`. Do NOT use `search_collections`: it is relevance-ranked and bounded, and is not guaranteed to return an exact title match for a generic name.

1. Resolve the name (`release_archive.py pending`).
2. In the one merged `list_collections` dump, collect every entry whose title equals the configured name **case-insensitively**.
3. **Zero matches** → `create_collection({title: <name>, description})`; print once: `Release archive: created Mem collection "<name>".`
4. **Exactly one** → use it.
5. **More than one** → prefer the **exact-case** match; if still more than one, prefer the **oldest `created_at`**; if still ambiguous, **do not write** — print the rendered note in chat (see Failure handling) and warn with every matching id so the user can rename or trash the stray. Never create a duplicate when any match exists. (The tie-break applies identically when the name is the default.)

**Do not** use `create_note`'s `collection_titles` shortcut: on an unmatched title it silently creates the note *outside* any collection. Always write by the resolved collection UUID.

**Renaming the env var later forks the store.** A new name creates a fresh collection under that name; notes already written stay in the old one. This is expected and documented — the command never migrates or merges collections. `pending` echoing the resolved name every run is the mitigation.

The collection is shared across both products — `M1-`/`GEN-` vs `ORC-` prefixes disambiguate, and one collection makes ticket→release trace a single search. Each note still carries a `Product:` field.

## Data source

`record` takes four inputs the command already has at post time. Nothing is re-gathered.

- **Summaries file (primary for the trace header)** — `/tmp/release_slack_summaries.txt` / `/tmp/oracle_notes_summaries.txt`, the `---`-separated per-ticket blocks (`IDENTIFIER`, `TITLE`, `CLIENT_TITLE`, `CATEGORY`, `URL`, `CLIENTS`, `PR_LINKS` / `MR_LINKS`, …) that `internal-storyteller` already parses. Only this file carries `CATEGORY` (invented by the analyzer), `CLIENT_TITLE` (added by the `engineering-to-client` pass), and the **posted set**. `record` applies the storyteller's drop rule (skip `CATEGORY: REFACTOR`, skip malformed blocks) so the ticket list is **exactly what posted**.
- **Gathered data JSON (secondary)** — `/tmp/release_slack_data.json` / `/tmp/oracle_notes_data.json`; used only to fill `url` / code links (`mrs`) when a block lacks them. A missing data file is not an error.
- **Root message file** — `/tmp/release_slack_root.txt` / `/tmp/oracle_release_root.txt`, exactly what `slack_upload.py` posted. Required.
- **Reply message files, in post order** — `/tmp/release_slack_reply_NN.txt` / `/tmp/oracle_release_reply_NN.txt`. Pass every reply that was posted, in numeric order. A missing file is an error (exit 1) so the note can never silently lose a reply.

## Note schema

One note per release session. The first line is the Mem title. Rendered by `scripts/release/release_archive.py record` — never hand-compose it in the command.

```
Release: 2026-09-02 · oracle · #pod-oracle

**Date:** 2026-09-02
**Product:** oracle
**Channel:** #pod-oracle
**Channel id:** C0XXXXXXX
**Root ts:** 1725300000.123456
**Permalink:** https://medtrics.slack.com/archives/C0XXXXXXX/p1725300000123456
**Item count:** 3
**Tickets:** ORC-232, ORC-234, ORC-240
**Clients:** KFSHRC, CVM-NCSU

- ORC-232 · Average score shown on completed evaluations · ENHANCEMENT · KFSHRC · https://github.com/medtrics/medtrics-oracle/pull/456, https://github.com/medtrics/medtrics-oracle/pull/457
- ORC-234 · Export timeout on large cohorts · BUG FIX · TBC · https://github.com/medtrics/medtrics-oracle/pull/455
- ORC-240 · Rotation scheduling presets — heads up · FEATURE · TBC · no PR

## Posted message

### Root

~~~
:rocket: *Release Notes — Wednesday, September 2, 2026*
…the root post exactly as posted…
~~~

### Reply 1

~~~
*Enhancements &amp; New Features*

*1. ORC-232 · Average score shown on completed evaluations*
…the reply exactly as posted…
~~~

### Reply 2

~~~
…
~~~
```

Field rules:

- **`Tickets:`** is the load-bearing line for trace — a flat, comma-separated id list so a Mem search for a single id surfaces this note. `MR-NNNN` identifiers from `/release-slack mr <iid>` mode are listed as-is (not a ticket, still traceable).
- **`Product:`** is one of exactly `medtrics` | `oracle` — the Optimus product slugs, one vocabulary. `medtrics` is the classic track; no other product token is ever stored.
- **`Date:`** and the title date come from `--date`, which the command passes as the **same value it used for the Slack header** (`date +%Y-%m-%d`, local time). `record` never computes its own date, so the archive cannot disagree with the Slack post around midnight.
- **`Channel:`** stores the configured `SLACK_CHANNEL_INTERNAL` / `SLACK_CHANNEL_ORACLE` value verbatim (it may itself be an id). **`Channel id:`** is the `channel` field from the `slack_upload.py` result block.
- **`Permalink:`** is present only when obtained (Flow, step 2); the line is **omitted** on failure — never constructed by hand.
- Per-item bullets carry `{id · CLIENT_TITLE · CATEGORY · clients · code links}`. **Code links list ALL of a ticket's PRs/MRs, primary-first, comma-joined, as raw URLs**; `no PR` / `no MR` when empty.
- **`## Posted message`** holds the root and every reply, in post order, each inside a `~~~` fence. Tilde fences are used so Slack's own ``` code blocks survive inside the transcript. The parser reads header fields and item bullets only **up to** this heading, and only the fenced sections after it, so a bullet-like line inside the message text can never be mistaken for an item.
- Notes in the collection that do not start with `Release: ` are ignored by the parser, so stray notes cannot corrupt a read. A trace-only note from 0.19.0 still parses (empty transcript).

## What goes into Mem

`dev-directory` and `pr-overrides` store **no ticket content and no client names**. This skill **deliberately does** — it is the archive.

- **Stored:** ticket ids, **client-facing** titles (`CLIENT_TITLE`), categories, client institution **codes**, PR/MR URLs, Slack channel / channel id / root ts / permalink, date, product, and the **posted root and thread replies verbatim** — which means the developer `@`-mentions, client names, Optimus URLs, and `Internal:` technical summaries that the internal post already carries.
- **Not stored:** the raw per-ticket analyzer summaries file, ticket descriptions, gathered diffs, or anything that was not posted.

The notes are private to the user's Mem workspace, the same audience as the internal Slack channel the message went to.

## Flow (post step of either command)

`{summaries}`, `{data}`, `{root}` and `{replies}` are the command's existing files — `/tmp/release_slack_summaries.txt`, `/tmp/release_slack_data.json`, `/tmp/release_slack_root.txt`, `/tmp/release_slack_reply_NN.txt` for `/release-slack` (product `medtrics`, channel key `SLACK_CHANNEL_INTERNAL`), and `/tmp/oracle_notes_summaries.txt`, `/tmp/oracle_notes_data.json`, `/tmp/oracle_release_root.txt`, `/tmp/oracle_release_reply_NN.txt` for `/oracle-release` (product `oracle`, channel key `SLACK_CHANNEL_ORACLE`). `<ts>` is the root `ts` and `<channel id>` the `channel` field, both from the root post's `---SLACK_UPLOAD_RESULT---` block. The step runs **after** the command's one-line post confirmation has printed.

0. **Gate — the post is complete.** Run once the root **and every thread reply** have posted. That is either the normal path (all in this session) or a **resume run** — the user re-ran only the remaining replies with `--thread-ts <ts>` after a partial failure, and the last one has now posted. On a resume run, use the `ts` the user passed and the same root / reply files from `/tmp` (they are still there on the same machine). If a resume run cannot find the root or a reply file, `record` exits 1 — surface it and tell the user which file is missing; do not archive a partial transcript. Never archive while replies are still outstanding.
1. **The collection id** — the `release_archive` id from Step 1 (saved id, or resolved / created there by the ladder above). `release_archive.py pending` only prints the name.
2. **Permalink** — one Slack read, reusing `slack_upload.py`'s token resolution (`os.environ` → `.env`) and its read-retry (429/5xx) path; needs no scope beyond the bot token:

   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/release/slack_upload.py --get-permalink \
       --channel <channel id> --ts <ts>
   ```

   Parse the `---SLACK_UPLOAD_RESULT---` block; on `ok: true` take its `permalink`. **On any failure, omit the permalink** (drop the `--permalink` flag in step 3) — a constructed fallback is impossible because the workspace subdomain is not in `.env`. This call can never surface as a post failure: the confirmation line has already printed.
3. **Render the note** — deterministic, never hand-composed:

   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/release/release_archive.py record \
       --summaries {summaries} --data {data} \
       --root-file {root} --reply-files {reply_01} {reply_02} ... \
       --product {medtrics|oracle} --date <same value as the Slack header> \
       --channel <configured SLACK_CHANNEL_* value> --channel-id <channel id> --ts <ts> \
       [--permalink <url>]
   ```

   The stdout is the exact note body. `--date` is the value the command already computed for the root header (`date +%Y-%m-%d`) — pass it through; do not recompute. `--reply-files` takes every posted reply file in numeric order.
4. **Write it** — `create_note({content, collection_ids: [<release_archive id>]})`. By UUID only; never `collection_titles`. On success print `Release archive: wrote note "<title>" to Mem collection "<name>" (ts <ts>); if this was a re-post, trash the older note in Mem.`
   - **Landed check** (`mem-calls` rule 1b): save the response, run `mem_ids.py landed release_archive --response <file>`; on `landed: false`, look the collection up once and `add_note_to_collection` (create it if it is gone). Never write the note a second time.
5. **If the write cannot happen:**
   - **Refused (rate limit)** → `mem_ids.py wait 30`, retry; `wait 90`, retry; then `release_archive.py outbox add --key release_archive --content-file <the rendered note file> --reason "rate limited"`, and print `Release archive: Mem is rate-limiting — note saved to the outbox; the next run uploads it.`
   - **Any other failure** (collection ambiguous, `create_note` fails once and again on one immediate retry), or the outbox add fails → print `Release archive: Mem write failed (<reason>). Paste the note below into the "<name>" collection by hand:` followed by the full rendered note body in a fenced block.

   Then finish. Never re-post to Slack.

## Dedup rule

**One note per session; duplicates tolerated.** Every completed posting session writes **one new note**. The command does **not** read the collection first and does **not** update-in-place: that would require a collection-wide `list_notes` dump every run and would silently turn the rule into "one note per root ts". If the same release is ever re-posted in a fresh session, a second note appears; the success line says so, and the user trashes the older note in Mem. Nothing is auto-deleted, nothing is merged.

## Failure handling

| Condition | Behavior |
|---|---|
| Mem tools absent at Step 1 | **Hard stop before any gathering** with the one-line notice. No disk fallback |
| Mem tools disappear between Step 1 and the archive step | Treat as a failed write: print the rendered note in chat for a manual paste |
| Replies still outstanding (partial failure) | Do not archive yet; the resume run archives once the last reply posts |
| Resume run cannot find the root or a reply file in `/tmp` | `record` exits 1 naming the file; surface it; no partial archive |
| `list_collections` / `create_collection` fails | Print the rendered note in chat for a manual paste; one warning line |
| Configured name matches more than one collection and the exact-case → oldest `created_at` tie-break still leaves >1 | Do not write; warn with every candidate id; print the rendered note for a manual paste; the user renames or trashes the stray |
| `slack_upload.py --get-permalink` fails (`ok: false`, non-zero exit, unparseable output) | Omit the `Permalink:` line — still write the note; never construct a URL |
| `release_archive.py record` exits 1 (bad date, empty posted set, missing message file) | Surface stderr; nothing to write — fix the input and re-run the archive step by hand |
| `create_note` refused (rate limit) | Wait 30s, retry; wait 90s, retry; then queue it in the outbox. The next run uploads it at Step 1 |
| `create_note` fails (not a rate limit) | One immediate retry; if it fails again, print the rendered note in chat for a manual paste |
| Same release re-posted in a fresh session | A second note; reminder in the success line; user trashes the older one |

The archive step is strictly after the post, so it can never affect delivery. The Slack post is authoritative for *what was sent*; the Mem note is the only archive of it.

## What this skill does not do

- Does not keep anything on disk — no markdown copy. A rate-limited note waits in the outbox only until a later run uploads it; any other failure prints the note text in chat.
- Does not gate, delay, or retry the post. It starts only after the confirmation line and cannot change the post's outcome.
- Does not archive per ticket, and does not dedup or update-in-place — one note per session, duplicates tolerated and warned.
- Does not compose or hand-edit note bodies — `release_archive.py` renders them so the schema stays stable.
- Does not construct a permalink. The only source is `slack_upload.py --get-permalink` (`chat.getPermalink`); on failure the line is omitted.
- Does not re-gather or call Optimus, GitLab, or GitHub — it reads the files the command already produced.
- Does not migrate or merge collections when `MEM_RELEASE_ARCHIVE_COLLECTION` changes, and never writes to `.env`.
- Does not share the archive across teammates; Mem collections are per user.
