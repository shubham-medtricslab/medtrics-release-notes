---
name: kb-ledger
description: Mem-backed history for /kb-articles — the KB Article Ledger (one note per legacy feature ticket recording the article drafted for it, plus one note per run), the KB Catalogue (published Intercom articles, seeded once from the FAQ Content Planner export) and, from v2, the KB Queue. Covers collection resolution, the dumps scripts read, the writes they compute, the one-time seeds, and failure handling. Mem is required for /kb-articles; the .docx/PDF files are deliverables in the outputs folder, never stored in Mem.
---

# KB Ledger (Mem)

`/kb-articles` never drafts the same ticket twice, and it tells you which earlier
article a new feature follows. Both need history, and the history lives in Mem.
`scripts/kb/kb_ledger.py` does every deterministic half — rendering, parsing,
matching, computing writes. It never calls Mem itself; the command performs the
Mem calls listed here.

## Is Mem connected? (Step 0 — hard stop)

Mem is connected when the Mem MCP tools (`list_collections`, `create_collection`,
`list_notes`, `create_note`, `update_note`) are available in the session. If they
are not, print `Mem MCP not connected — /kb-articles keeps its ledger in Mem and
will not run without it. Connect the Mem connector and re-run.` and stop before
any Optimus or GitLab call. `dry-run` needs Mem too (it reads the ledger to show
what would be skipped).

## The collections

| Which | Env key | Default name | Notes |
|---|---|---|---|
| Ledger | `MEM_KB_LEDGER_COLLECTION` | `KB Article Ledger` | `KB: <ticket> · <title>` per ticket; `KB run: <date> · <run_id>` per run |
| Catalogue | `MEM_KB_CATALOGUE_COLLECTION` | `KB Catalogue` | `KB article: <id> · <title>` per published Intercom article |
| Queue (v2) | `MEM_KB_QUEUE_COLLECTION` | `KB Queue` | not used by v1 |

`kb_ledger.py resolve` prints the resolved names (process env > `.env` > default)
and each collection's description. Resolve each one exactly like the
`release-archive` skill does: paginated `list_collections`, exact title match
case-insensitively, prefer exact case then the oldest `created_at`, create it
under the exact name when none matches, and never write by `collection_titles` —
always by the resolved UUID.

## Reading: dumps

Before selection, `list_notes({collection_id, include_note_content: true, limit: 100})`
each collection, following `next_page` to the end, and save the merged
`{"notes": [{id, version, content}, …]}` verbatim:

| Dump | Path |
|---|---|
| Ledger | `/tmp/kb/<run>/ledger_notes.json` |
| Catalogue | `/tmp/kb/<run>/catalogue_notes.json` |
| Release archive (only for `since` / `pending`) | `/tmp/kb/<run>/archive_notes.json` — the `release-archive` collection |

A collection that does not exist yet is an empty dump (write `{"notes": []}`).
Keep `id`, `version` and `content` — the scripts need all three.

## Statuses

| Status | Meaning | Skipped by default |
|---|---|---|
| `drafted` | A draft with every screenshot it asked for | yes |
| `partial` | A draft with one or more screenshots missing | yes — `--retry-partial` re-drafts it (e.g. after demo data is added) |
| `failed` | The gates refused it after two retries | no — retried next run |
| `published` | A person set `Published as` to the Intercom id | yes |
| `legacy-draft` | Drafted by the hand-run pipeline (Oct 2026) | yes |
| `matched` | The hand-run pipeline matched it to an existing article | yes |

`--force` re-drafts a ticket whatever its status, updating its note in place.

## Writing

After packaging, `kb_ledger.py writes --run /tmp/kb/<run>/run.json --ledger-notes …`
emits a list of writes. Perform each in order:

- `create` → `create_note({content, collection_ids: [<ledger uuid>]})`
- `update` → `update_note({note_id, content, version})` with the version verbatim.
  A version conflict means someone edited the note during the run: skip it and
  say so.

The last write is always the run note. A failed write is retried once; if it
fails again, print the note body in a fenced block for a manual paste and carry
on. The articles are already in the outputs folder — a ledger failure never
deletes or re-renders them, but the next run will draft that ticket again.

A person fills `Published as:` by hand once the article is live in Intercom
(and may set `Status: published`). The scripts preserve `Published as` on update.

## One-time seeds

Run these once, in a session with Mem connected, before the first real run.

**Hand-run backlog** — marks the 53 tickets the October hand-run pipeline already
drafted or matched, so the first run only drafts the rest:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/kb/kb_ledger.py seed-legacy \
    --tickets <path>/tickets.json --articles <path>/articles_a.json <path>/articles_b.json \
    --ledger-notes /tmp/kb/seed/ledger_notes.json --out /tmp/kb/seed/writes.json
```

**Intercom catalogue** — from a fresh FAQ Content Planner export (`kb2.csv`).
Columns are matched by common names (`id`/`article id`, `title`, `module`/`category`,
`url`); rows with no id or no title are dropped:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/kb/kb_ledger.py seed-catalogue \
    --csv kb2.csv --catalogue-notes /tmp/kb/seed/catalogue_notes.json \
    --out /tmp/kb/seed/catalogue_writes.json
```

Perform the writes as above (all `create`). Until the catalogue is seeded, the
prior-article lookup only sees earlier ledger tickets.

## Prior articles

`kb_ledger.py match` scores each ticket's title and module against every
catalogue article and earlier ledger article (content-word overlap, the same
idea as the hand-run `match_candidates.json`) and keeps the top three above 0.3.
They are **listed** — in the writer input, the article's metadata, the ledger
note and the run summary — and never merged: every ticket gets its own article.

## Never in Mem

.docx / PDF / image bytes, ticket descriptions, diffs, demo-site storage states.
