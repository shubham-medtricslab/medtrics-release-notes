---
name: pod-notify
description: Tell each client pod (a Slack channel) which tickets in a release concern its clients — one root post per pod plus one thread reply per ticket. Clients come from the Optimus client links on each ticket; the pod map is ONE hand-edited Mem collection ("Release Notes Pod Map", one note per pod). Runs after the main post and the archive in `/release-slack tickets …` and `/oracle-release tickets …` (skipped with `--no-pod`), and stand-alone as `/pod-notify <ids>` for releases announced by hand. One preview gate for every pod; a pod failure never fails the release.
---

# Pod notify

Pods miss which released tickets concern their clients: the release post goes to `#dream-team` / `#pod-oracle` only. This skill sends each pod its own short thread: a root post ("Shipped for your clients — {date}") and one card per ticket, carrying only what the pod needs and nothing that belongs to another client.

All routing, rendering, dedup and log writing is `scripts/pod_map.py` — never hand-compose a pod message or a pod-map note. Mem is reached through the Mem MCP, Slack only through `scripts/slack_upload.py` (never the Slack MCP).

## When it runs

| Caller | Runs | Inputs |
|---|---|---|
| `/release-slack tickets …` | after Step 9b (archive), before 9c (`--kb`) | `/tmp/release_slack_data.json`, `/tmp/release_slack_summaries.txt` |
| `/oracle-release tickets …` | after Step 11b (archive) | `/tmp/oracle_notes_data.json`, `/tmp/oracle_notes_summaries.txt` |
| `/pod-notify <ids>` | its whole job | `/tmp/pod_notify_data.json`, `/tmp/pod_notify_summaries.txt` |

It does **not** run in `status` or `mr` mode (those tickets are not shipped yet, or have no Optimus ticket), or when the user passed `--no-pod`. In `dry-run` it prints the routing table only (below). Nothing in it can surface as a release failure: any failure is one line in chat and the command finishes.

Below, `{data}` and `{summaries}` are the caller's files, `{source}` is the command (`/release-slack`, `/oracle-release`, `/pod-notify`), `{date}` is the date in the release header, `{link}` is the main post's permalink when the caller has one (Step 9b / 11b fetched it), and `P=${CLAUDE_PLUGIN_ROOT}/scripts`.

## The pod map (Mem)

One collection, named by `MEM_POD_MAP_COLLECTION` in `.env` (default `Release Notes Pod Map`). It holds:

- **Pod notes** — written by people, one per pod. Title `Pod: #<channel name>`:

  ```
  Pod: #impact-pod_meg

  **Channel ID:** C0AAAAAAAA1
  **Clients:**
  - King Faisal Specialty Hospital & Resource Center [<optimus client id>]
  - Purdue University - College of Veterinary Medicine
  ```

  Client names are **exactly as Optimus spells them** (look-alikes are different clients: VinUniversity GME vs VinUniversity Medicine and Nursing, UCC vs UCCPA). `/release-note-setup pod` adds each client's Optimus id in `[…]`, so a later rename in Optimus does not break the match; a name without an id still matches by name (case, `&`/`and` and punctuation ignored). The **Channel ID** is what posts go to; the title's name is for people. A client may sit in two pods (both get its tickets — shown as a warning). A note without a valid channel id is skipped with a warning; the `Pod template (copy me)` note is never read as a pod.
- **Pod-log notes** — written by this skill, one per run that posted. Title `Pod notify: {date} · {source}`, one line per pod: `- <channel id> · #<name> · ts <root ts> · <ticket ids>`. The next run skips any (channel, ticket) pair already logged, so a ticket is announced to a pod once.

## Which clients a ticket has

1. Its **Optimus client links** (`optimus_clients` in `{data}`, from the gather script).
2. With none, the ticket description's `## Reported client(s)` section — full names only, routed as **guessed** (marked on the card and with `*` in the preview). Ticket codes and all-caps words are never guessed from.
3. "All clients / platform-wide" and "Internal / not client-specific" tickets go to **no pod**; they are listed in the report.

Only tickets in `{summaries}` (the posted set) are routed. A client in no pod is listed under "Clients in no pod" — the hint to update the map.

## What a pod sees

Root: date, how many items, the pod's own clients involved, a link to the full release post when there is one. Then **one thread reply per ticket** (a summary too long for Slack's 3,900-char reply cap is cut short with …):

```
*1. {CLIENT_TITLE}*
{EXEC_SUMMARY — the client paragraph}
• Clients: {this pod's clients on that ticket}
• Ticket: <{Optimus URL}|{ID}>
• Heads-up: client-specific item still in progress — send it to that client only   (only with CLIENT_FLAG)
```

Never the internal summary, developer mentions, MR/PR links or another pod's clients. A ticket whose summary block has no `CLIENT_TITLE` or `EXEC_SUMMARY` is **not sent** (listed under "No client title or client paragraph") — the card never falls back to the internal title. Markup in the analyzer text (`**`, fences, `#`, `*`) is removed so every card passes `slack_lint`.

## Flow

### 1 — Find the collection

1. `python3 $P/pod_map.py collection` → the collection name.
2. If `.medtrics-release-notes/config.json` (`python3 $P/kb/kb_paths.py config`) holds `mem.pod_map`, confirm it with `get_collection`; else, or if that fails, resolve by name exactly as the `release-archive` skill does: paginated `list_collections` following `next_page`, case-insensitive exact title, prefer exact case then the oldest `created_at`; several still tied → stop with every candidate id. Never `search_collections`.
3. **Never create it here** — only `/release-note-setup pod` does. None found → print `Pods: no pod map in Mem — run /release-note-setup pod, then /pod-notify <ids>.` and finish.

### 2 — Read it and route

`list_notes({collection_id, include_note_content: true, limit: 100})`, following `next_page` to the end; save the merged `{"notes": [{id, version, content}, …]}` verbatim to `/tmp/pod_notify/notes.json` (`mkdir -p /tmp/pod_notify`). Then:

```bash
python3 $P/pod_map.py route --data {data} --summaries {summaries} \
    --notes /tmp/pod_notify/notes.json --date {date} --source {source} \
    [--release-link {link}] --out-dir /tmp/pod_notify
```

Add `--force` only when the user asked to re-send tickets a pod already got. The `---POD_MAP_RESULT---` block carries `plan`, `channels` and the counts. With `pods: 0`, print the preview table (step 4, without `--channels`) as the report and finish — nothing to send.

### 3 — Check every channel (reads only)

For each entry in `channels`:

```bash
python3 $P/slack_upload.py --check-channel --channel <channel_id>
```

Save the results as one JSON object `{<channel_id>: <the ---SLACK_UPLOAD_RESULT--- object>}` to `/tmp/pod_notify/channels.json`. A pod is **blocked** when its channel is archived, shared outside Medtrics (Slack Connect), the bot is not a member, or the check failed (for example a missing `groups:read` scope on a private channel). Blocked pods are shown and never posted.

### 4 — One preview for every pod

```bash
python3 $P/pod_map.py preview --plan /tmp/pod_notify/plan.json --channels /tmp/pod_notify/channels.json
```

Show its output as is: the routing table (pod → tickets, blocked pods with the reason, guesses, tickets that reach no pod, clients in no pod, already-sent pairs, warnings), any lint violations, and one sample pod (its root and first three replies). Every `preview` re-lints each unposted pod's files and re-applies the channel checks, so only a pod that passes both is `ready`; a pod fixed since the last preview becomes ready again. Then ask:

```
Send to <R> pod(s)?  (<B> blocked)
  [y] post to every ready pod
  [n] cancel (no Slack action; the files stay in /tmp/pod_notify)
  [e] edit — drop a pod, drop a ticket from a pod, or change a message
  [a] show every pod's messages in full
```

- `[a]`: print every ready pod's root and replies in fenced blocks, then ask again.
- `[e]` drop: `python3 $P/pod_map.py drop --plan /tmp/pod_notify/plan.json --channel-id <id> [--ticket <ID>]` (a ticket drop re-renders that pod). Change text: write the user's text to the file. After either, run step 3's checks again if a channel changed, then `preview` again — it re-lints, so a broken edit shows as `LINT FAILED`.
- A pod whose status is `lint_failed` is shown with its violations; fix the file or drop the pod, then preview again. Never post a pod that is not `ready`.

### 5 — Post (only after `y`)

Pod by pod, in table order, for each pod with status `ready`:

1. Root: `python3 $P/slack_upload.py --channel <channel_id> --message-file <root_file>`. On `ok: true`, record it: `python3 $P/pod_map.py mark --plan /tmp/pod_notify/plan.json --channel-id <channel_id> --kind root --ts <ts>`.
2. Each reply in order: `python3 $P/slack_upload.py --channel <channel_id> --message-file <reply_file> --thread-ts <root ts>`, then `pod_map.py mark … --kind reply --file <reply_file>`.
3. **As soon as the pod is done** — every reply posted, or stopped partway — write its pod-log note at once (step 6 with `--channel-id <channel_id>`), so a crash later in the run can never cause a second announcement to this pod.
4. A failed post stops **that pod** (log what did post, as in 3) and moves on to the next. If the error says the outcome is unverified ("verify in channel"), say `Pods: check <#name> before re-running — the post may or may not have landed.` A `not_in_channel` error means the bot needs an invite. Never retry a post automatically and never post a second root to a pod.

**Resume.** A later `/pod-notify resume` (or the user asking to finish the pod posts) first runs step 1 (it needs the collection id for the log), then reads `/tmp/pod_notify/plan.json` and runs step 3's checks and `preview` again: a `partial` pod posts only the replies `mark` reported as `remaining`, under its recorded root ts; a `ready` pod posts normally; a `posted` pod is never touched. Log each pod as in 5.3 (a second log note for a resumed pod is harmless). If `/tmp` is gone, say so — each finished pod was logged as it finished, so `/pod-notify <ids>` will skip those pairs.

### 6 — Log what was sent (per pod, as each pod finishes)

```bash
python3 $P/pod_map.py log --plan /tmp/pod_notify/plan.json --channel-id <channel_id> \
    --out /tmp/pod_notify/log_writes.json
```

Perform each write: `create_note({content, collection_ids: [<pod map collection id>]})`; retry once. If it still fails, print `Pods: could not write the pod log (<reason>) — paste this note into "<collection name>" so these tickets are not sent again:` and the note in a fenced block. No writes means nothing was posted to that pod.

### 7 — Report

One line, then the gaps:

`Pods: posted to <N> pod(s) (<C> cards); <B> blocked; <S> already sent; not sent to any pod: <K> ticket(s).` followed by the "Not sent to any pod" lines from the preview table, so the team can fix the map or tell those clients by hand.

## dry-run

The gather script's `--dry-run` writes no data file, so first fetch the tickets once more from Optimus only (cheap, no GitLab/GitHub):

```bash
python3 $P/gather_notes_data_optimus.py tickets <ids> --no-mrs --exact --include-description \
    --output /tmp/pod_notify/dryrun_data.json
```

(`--no-mrs` needs only `OPTIMUS_API_KEY`, so this works in an Oracle-only folder too; `--exact` never swaps a mistyped id for a near match.)

Then steps 1–2 read only, with `route --data /tmp/pod_notify/dryrun_data.json --routing-only` (no summaries exist yet, so every gathered ticket is routed and no messages are rendered), then step 3 and `preview` (without the post prompt). Print `Pods (dry run): would post to <N> pod(s).` Nothing is posted and nothing is written to Mem.

## Failure modes

| Condition | Behavior |
|---|---|
| No pod map collection | One line naming `/release-note-setup pod`; finish |
| Collection name ambiguous | One line with every candidate id; finish |
| A Mem read fails | One line; finish — the release stands |
| `pod_map.py` exits 1 | Surface its stderr in one line; finish |
| A channel check fails | That pod is blocked, the reason is in the table |
| A pod post fails | Stop that pod, carry on with the others; never auto-retry |
| Pod-log write fails twice | Print the note for a manual paste |
| User answers `n` | No Slack call, no Mem write |
