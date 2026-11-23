# Fewer Mem calls (planned for 0.25.0)

## Why

Mem refused calls on every release from Nov 17 to Nov 19, 2026: Legacy Nov 17 and Oracle Nov 17, 18 and 19. On Nov 19 it refused reads too, so pod-notify could not load the pod map. Four release archives never reached Mem.

A normal run makes about 15–30 Mem calls today:

- The release archive and KB steps page through **every collection on the account** to find theirs by name, 2–4 times per run. They do this even though `/release-note-setup` already saved the ids in `.medtrics-release-notes/config.json`.
- The developer directory and PR overrides each run a `search_collections` lookup, then list their notes.
- Pod-notify first confirms its saved id with `get_collection`, then reads **every pod-log note with full content**. That is one note per pod per release, stored in the pod map collection, and it never stops growing.
- Every failure is retried at once. Against a rate limit, an instant retry only adds another refused call.

This is probably not the whole cause. All the Medtrics plugins share one Mem account per person, so a quota question still needs to go to Mem (see "Outside this plan").

**Target:** a normal Oracle run makes about 4 Mem calls plus one write per pod: 1 PR-override page, 1 pod-map page, 1–2 pod-log pages, 1 archive write, and the pod-log writes.

## Decisions (agreed 2026-11-19)

| Question | Choice |
|---|---|
| Pod log | Move the log notes to their own **Pod Notify Log** collection and keep reading all of them. The pod map read stays small. |
| Failed archive write | Wait **30s, then 90s**, and retry. If it still fails, save the note to a **local outbox** and upload it at the start of the next run. Delete it once Mem accepts it. |
| Developer directory | Keep a **local cache for 24h**. Skip the directory whenever Mem has refused a call this run. |
| The four lost archives | A **`release_archive.py backfill`** step that checks each file and writes it to Mem. |

## Changes

### 1. Saved collection ids: no lookups in a normal run

- New `scripts/release/mem_ids.py`, built on `kb_paths.py`'s config. It covers every collection the release commands touch: `release_archive`, `pod_map`, `pod_log` (new), `dev_directory`, `pr_overrides`, and the existing `kb_*` ids.
  - `mem_ids.py need <keys…>` prints the saved ids, and the names still missing.
  - `mem_ids.py resolve --collections <one merged list_collections dump> <keys…>` matches names with the existing rule (exact title ignoring case, then exact case, then oldest `created_at`, and a stop if still tied). It saves what it finds with `merge`.
- If anything is missing, the commands page through `list_collections` **once per run** at `limit: 100`, and resolve every missing name from that one list. They never walk the list per step.
- A saved id is used directly, with no `get_collection` check first. Only when a call on that id says not found do they look the name up again (one pass) and overwrite the saved id.
- `MEM_PR_OVERRIDES_COLLECTION_ID` in `.env` still wins over the saved id. `search_collections` is gone from dev-directory and pr-overrides.
- `/release-note-setup` fills in all the ids in one pass.

### 2. Pod log in its own collection

- `pod_map.py`: a new `LOG_COLLECTION` name, `Pod Notify Log`. `route` takes `--log-notes`, a second dump, alongside the pod-map dump. Log notes still found in the pod-map dump keep working, so nothing is lost before the move.
- Pod-notify reads the pod map (small) and the log separately. It writes new log notes only to the log collection.
- `/release-note-setup pod` creates the log collection. Old log notes stay in the pod map and are read with it (the move was dropped on 2026-11-23: two calls per note for no change in behaviour).
- Pod-notify never creates the log collection itself. If it is missing, pod-notify falls back to reading log notes from the pod map and says to run setup.

### 3. When Mem refuses a call

A shared rule, written once in a short `skills/mem-calls/SKILL.md` that each skill points to:

- A refused call means a rate limit / 429 / "too many requests" error, or any error with that text.
- After the **first refusal in a run**, Mem counts as *limited* for the rest of the run:
  - Optional steps skip with one line: developer directory (resolver only) and PR overrides (sweep only). The KB queue read skips with its existing "run `/kb-articles` later" line.
  - Required writes wait **30s, then 90s**, before retrying: the release archive and the pod log.
  - Reads that pod-notify needs (map, log) also wait 30s / 90s. If they still fail, pod-notify stops as it does today ("the release stands").
- Other errors (not rate limits) keep today's single immediate retry.
- The Step 1 check stays a tool-availability check with no Mem call. A rate limit never blocks a Slack post.

### 4. Local outbox (archive and pod-log writes only)

- Location: `.medtrics-release-notes/outbox/` in the run folder. That folder already ignores itself in git. One JSON file per note: `{collection_key, content, created_at, reason}`.
- `release_archive.py outbox add|list|done <file>`. `add` is called only after both waited retries fail. The chat still shows the one-line failure and says the note is in the outbox. The full note is printed in chat only if the outbox can't be written.
- **Flush** at Step 1 of `/release-slack`, `/oracle-release` and `/pod-notify`, after the Mem tool check and before gathering. Each note gets one `create_note`. On success its file is deleted (`outbox done`). On a refusal the flush stops and the run carries on; it never waits on the flush. Pod-log notes flush **before** pod-notify reads the log, so a pending log still stops a double post.
- The success line shows how many notes went through: `Outbox: uploaded 2 pending note(s) to Mem.`
- Pod-log notes waiting in the outbox also count when `route` dedupes, so a re-run on the same laptop can't post twice while Mem is down. `route` gets a new `--pending` flag for this.
- The `archives-live-in-mem-not-disk` rule changes to: Mem is the archive; the outbox is only a queue until Mem accepts.

### 5. Developer directory cache

- `.medtrics-release-notes/cache/dev_directory.json`: the merged notes dump plus `fetched_at`.
- Step 3a uses the cache when it is under 24h old (no Mem read). Otherwise it reads Mem and rewrites the cache.
- Write-backs (new developers) still go to Mem right away, and are also written into the cache, so the next run sees them.
- `--force` on the resolver, or `--refresh-directory`, skips the cache.

### 6. Backfill

- `release_archive.py backfill <file.md>…` checks that each file is a note `record` would have written. It needs the `Release: ` title, the date, product and tickets fields, and the `## Posted message` section. It prints the writes as JSON.
- The command makes one `create_note` per file into the archive collection, with the same 30s / 90s waits. Files that don't pass are listed with the reason and never written.
- Reached through a new **`/release-note-setup backfill <files>`** step. Running setup with no argument doesn't touch it.

## Tests (`tests/release/`)

- `mem_ids`: saved id used, missing names resolved from one dump, the tie-break, an id that has gone stale.
- `pod_map route`: `--log-notes`, leftover log notes in the pod-map dump, `--pending` dedupe.
- `release_archive`: outbox add / list / done, and backfill accept/reject cases. Includes a note whose posted text contains bullet-like lines.
- The developer-directory cache's age check, if it lands in `dev_directory.py`.
- The current suite still passes. Bump to 0.25.0 and add a CHANGELOG entry.

## Outside this plan

- **The Mem quota itself.** Someone asks Mem about the plan or rate limit on the account running the releases. Code review, client-ops, squasher and QA all write to Mem from the same account, which is the likelier reason for a day of refused reads.
- Other plugins' Mem usage. Those are separate repos.

## Settled after the first round

1. The outbox holds **both** release archives and pod-log notes. Pending pod logs count when checking for duplicates.
2. Backfill is a step inside `/release-note-setup` (`backfill <files>`), not its own command.

## Measured: Mem calls for the Nov 19 production deployment (15 ORC tickets)

The tickets were ORC-886, 971, 955, 967, 1059, 1034, 882, 807, 1068, 1033, 803, 1073, 925, 895 and 1035, measured on 2026-11-19 against Shubham's Mem account. That account has 35 collections (one page), 8 developer-directory notes, 0 PR overrides, and a pod map of 15 notes (4 pods, the template, 10 log notes). Routing found **no pod** for these tickets: 12 have no client link, 1 is platform-wide and 2 are internal. So there are no pod posts or pod-log writes, before or after. Developer write-backs assume every PR author is already in the directory.

| Step (`/oracle-release`) | 0.24.0 | 0.25.0 |
|---|---|---|
| Step 1 ids + outbox | 0 | 0 (the outbox list is local) |
| PR overrides read (id pinned in `.env`) | 1 | 1 |
| Developer directory | 2 (`search_collections` + `list_notes`) | 0 with a local copy under 24h old, else 1 |
| Release archive | 2 (`list_collections` walk + `create_note`) | 1 (`create_note`) |
| Pod notify | 2 (`get_collection` + `list_notes`) | 2 (map + log `list_notes`) |
| **Total** | **7** | **4** (5 when the directory copy is stale) |

The first run after upgrading makes one extra call, a single `list_collections` walk, because this folder's config holds only the `pod_map` id. Every later run makes 4.

**What this measurement shows:**

- On this account the release commands were never heavy: 7 calls per run. The cuts save 3, about 40%. They grow with the account. Each `list_collections` walk costs one page per 50 collections, and the old flow walked once per step. The pod map gained 2–5 log notes per release.
- On a day Mem refuses calls, 0.25.0 makes about the same number of attempts. The difference is the outcome: the archive note waits 30s and 90s and then goes to the outbox, instead of being lost.
- Optimus, GitHub and Slack calls are unchanged.
- **The likely quota spender is elsewhere.** The same account holds the Oracle code-review collections (run logs, finding ledger, errors, detector health, token ledger), QA run logs and the client-ops collections, all written per run by other plugins.

## Review fixes (2026-11-23)

A review found three bugs in the first build; all are fixed in 0.25.0:

1. **Loose notes.** `create_note` silently ignores an unknown collection id. Every write into a saved collection is now checked from its response (`mem_ids.py landed`, no Mem call) and moved in with `add_note_to_collection` when it missed. An empty pod map from a saved id counts as a stale id.
2. **Pod log lookups every run.** A key the walk did not find is `absent` for 7 days, so no walk until then or until `/release-note-setup --recheck`. Pod keys are requested only when the pod step runs. The Nov 19 run is back to 4 calls after the first run, not 5.
3. **Queued notes with no collection id.** `outbox list --ready-keys` keeps them waiting (they still count in the pod duplicate check). Step 1 asks for the outbox's keys, so `/pod-notify` can upload a queued archive note.

Not done (smaller, from the same review): pod-notify still tries the map with waits after the archive was already refused in the same run; KB queue writes still retry at once on a refusal; one log note per run (dropped: a mixed-version team could double-post).

## Call cuts (2026-11-23)

- **A. No pod reads when no ticket names a client.** `route --no-map` decides from the Optimus data alone (the same `ticket_clients` the full route uses). A ticket with a client gives `needs_map` and no plan.
- **B. Pod log only when a pod is hit.** `route --notes … --log-unread` is final when no pod gets tickets; otherwise `needs_log` and no plan.
- **D. No old-log move in setup.** Old log notes are read with the map.
- **C (one log note per run) was not built**: a teammate on an older version could not read the combined note and might post again.

| Nov 19 Oracle release (15 tickets, no client links) | 0.24.0 | first 0.25.0 build | with cuts |
|---|---|---|---|
| PR overrides read | 1 | 1 | 1 |
| Developer directory | 2 | 0–1 | 0–1 |
| Release archive | 2 | 1 | 1 |
| Pod notify | 2 | 2 | 0 |
| **Total** | **7** | **4–5** | **2–3** |
