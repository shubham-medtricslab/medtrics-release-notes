---
name: mem-calls
description: How the release commands call Mem without wasting calls — saved collection ids (mem_ids.py, one list_collections walk at most per run), the outbox upload at Step 1, what counts as a refused call, and what each step does once Mem is limited (optional steps skip; required writes wait 30s then 90s, then go to the outbox). Read by /release-slack, /oracle-release, /pod-notify and /release-note-setup, and by the dev-directory, pr-overrides, release-archive and pod-notify skills.
---

# Mem calls (shared rules)

Mem rate-limits the account that runs the releases. Every Mem call in these commands follows the five rules below. `$P` is `${CLAUDE_PLUGIN_ROOT}/scripts/release`.

## 1. Collection ids: saved, never looked up per step

Collection ids are kept in `.medtrics-release-notes/config.json` under `mem`. The keys are `release_archive`, `pod_map`, `pod_log`, `dev_directory`, `pr_overrides`, `kb_ledger`, `kb_queue`, `kb_catalogue` and `kb_demo`. `MEM_PR_OVERRIDES_COLLECTION_ID` in `.env` still wins for `pr_overrides`.

**At Step 1, once per run**, first read the outbox (local, no Mem call), then list every key the run needs **plus the outbox's `keys`**:

```bash
python3 $P/release_archive.py outbox list
python3 $P/mem_ids.py need <the command's keys…> <the outbox's keys…>
```

Ask only for keys a step of this run will use: `pod_map` and `pod_log` only when the pod step runs (status or tickets mode, no `--no-pod`; never in `mr` mode).

- `absent` lists keys the lookup found missing in the last 7 days (today: `pod_log` before `/release-note-setup pod` has made it). Treat them as missing **without** walking again. `/release-note-setup` passes `--recheck` to look again.
- `missing` is empty → use `ids` for the whole run. **No `get_collection`, no `search_collections`, no `list_collections`.**
- Something is missing → page through `list_collections({limit: 100, order_by: "created_at"})` **once**, following `next_page` to the end. Save the merged `{"collections": [...]}` to `/tmp/mem_collections.json`, then:

  ```bash
  python3 $P/mem_ids.py resolve <the missing keys> --collections /tmp/mem_collections.json
  ```

  It saves every id it finds. Each key under `create` gets `create_collection({title: name, description})`, then `python3 $P/mem_ids.py save <key> <id>`. The exceptions are `pod_map` and `pod_log`: pod-notify never creates those, only `/release-note-setup pod` does. A key under `ambiguous` is handled the way that step already handles an ambiguous name (the archive prints the note for a manual paste; optional steps skip).

  Do this for every missing key together. Never walk the list a second time in the same run.
- **A saved id Mem says does not exist** (a not-found error on a call that uses it): `python3 $P/mem_ids.py forget <key>`, then run the one-walk lookup above for that key and retry the call once.
- **A pod map that comes back empty** from a saved id is suspect (a real map always holds the template and the pods): `forget pod_map`, the one-walk lookup, and one more read.

Never pass `collection_titles` to `create_note`. Always write by id.

## 1b. Check that every note landed

Mem **ignores a collection id it cannot find**, without an error: a stale saved id makes a note outside any collection. After every `create_note` into a saved collection (release archive, pod log, outbox upload, developer directory, PR override, KB ledger and queue), save the response and run:

```bash
python3 $P/mem_ids.py landed <key> --response /tmp/mem_create.json
```

This makes no Mem call.

- `landed: true`, or `null` (the response names no collections): nothing to do.
- `landed: false`: the saved id is already forgotten. Look the name up: reuse this run's `/tmp/mem_collections.json` if it walked already, else walk once. Then run `mem_ids.py resolve <key> --collections /tmp/mem_collections.json`, then `add_note_to_collection({note_id, collection_id: <the new id>})`.
  - Nothing found, and the key may be created here (`release_archive`, `dev_directory`, `pr_overrides`, `kb_*`): `create_collection`, `mem_ids.py save`, then `add_note_to_collection`.
  - Nothing found for `pod_log`: queue the same content too, with `release_archive.py outbox add --key pod_log --content-file <file> --reason "pod log missing"`. The queued copy keeps the duplicate check working until `/release-note-setup pod` makes the collection. Then say: `Pods: the pod log collection is gone — run /release-note-setup pod.`
- Print one line when a note was moved: `Mem: note "<title>" landed outside "<name>" (saved id was stale) — moved it in.`

## 2. The outbox: upload first

After the Step 1 checks and before any gathering, run `python3 $P/release_archive.py outbox list --ready-keys <every key in need's ids>`. Upload only `entries`. Entries under `waiting` have no collection id this run, for example a pod log queued before the log collection existed. They stay in the outbox: uploading them without an id would make a loose note, and the next `route` would no longer see them. For each entry in `entries`, oldest first:

- `create_note({content, collection_ids: [<id for its collection_key>]})`.
- On success: run the landed check (rule 1b), then `python3 $P/release_archive.py outbox done <file>`.
- **On the first refusal, stop uploading** and carry on with the run. The rest wait for the next run. The outbox never holds up a release.

Print `Outbox: uploaded <N> pending note(s) to Mem.` when anything was uploaded, and `Outbox: <K> note(s) still waiting.` when something is left, `waiting` included. An empty outbox prints nothing.

## 3. What counts as a refused call

A Mem call is **refused** when the error is a rate limit: HTTP 429, or text containing "rate limit", "too many requests", "quota" or "throttl". After the **first** refusal in a run, Mem is **limited** for the rest of the run. Say so once: `Mem is rate-limiting this account — skipping optional Mem steps for this run.`

Any other error (network, 5xx, validation) keeps that step's existing rule: one immediate retry where the step allows it.

## 4. Once Mem is limited

| Step | Behaviour |
|---|---|
| Developer directory (prefill read and write-back) | Skip. Use the Slack resolver only. A fresh local copy (24h) is still used, because it costs no call |
| PR overrides read | Skip. The sweep runs alone, with one line saying overrides were not loaded |
| PR override write (Step 10b) | Apply the pick with `--pr-override-inline` for this run only, and say it was not saved |
| KB queue / ledger read (`--kb`) | Skip with the existing "run `/kb-articles` later" line |
| Outbox upload | Stop, as above |
| **Release archive write** | Wait **30s**, retry. Refused again → wait **90s**, retry. Refused again → outbox: write the note to a file, then `python3 $P/release_archive.py outbox add --key release_archive --content-file <file> --reason "rate limited"`. Print `Release archive: Mem is rate-limiting — note saved to the outbox; the next run uploads it.` Print the full note in chat **only** if the outbox add fails |
| **Pod-notify reads (map, log)** | Wait 30s, then 90s. Still refused → stop pod-notify as today (`the release stands`) |
| **Pod-log write** | Wait 30s, then 90s. Still refused → `python3 $P/release_archive.py outbox add --writes-file /tmp/pod_notify/log_writes.json --reason "rate limited"`. Queued pod logs count as already sent on the next `route`, so a re-run never posts twice |

Wait with `python3 $P/mem_ids.py wait 30` / `wait 90`, never a shell `sleep`. A rate limit **never** blocks or delays a Slack post. All the Mem writes come after the post.

The Step 1 Mem check stays a tool-availability check. It makes no Mem call.
