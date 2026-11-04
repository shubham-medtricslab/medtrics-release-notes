---
description: Tell each client pod (Slack channel) which tickets in a release concern its clients — for releases announced outside /release-slack and /oracle-release (those run it themselves). Routes tickets by their Optimus client links through the Mem pod map, writes one client-safe card per ticket, previews every pod, then posts one root + thread per pod.
argument-hint: "<ticket ids> [dry-run] [--force] [--summaries <file>] | resume"
---

# Pod notify

Send the pods their part of a release when the release itself was posted by hand (or with `--no-pod`). `/release-slack tickets …` and `/oracle-release tickets …` already do this after their own post — see the `pod-notify` skill, which this command follows from its step 1.

This command never posts to Slack without explicit user confirmation in the same session.

## Usage

```
/pod-notify M1-1605,M1-1550,ORC-715              # route, write the cards, preview, post
/pod-notify M1-1605,ORC-715 dry-run              # routing table only — no text written, nothing posted
/pod-notify M1-1605 --force                      # re-send to pods that already got M1-1605
/pod-notify M1-1605,M1-1550 --summaries /tmp/release_slack_summaries.txt
                                                 # reuse a release run's summaries (e.g. after --no-pod)
/pod-notify resume                               # finish a run whose pod posts stopped partway
```

Ticket ids may mix products (M1-…, ORC-…, GEN-…). `MR-NNNN` ids have no Optimus ticket and are not accepted.

## Required .env keys

| Key | Purpose |
|---|---|
| `OPTIMUS_API_KEY` | read the tickets and their client links |
| `SLACK_BOT_TOKEN` | `chat:write`, `channels:read` (+ `groups:read` for private pods); the bot must be a member of every pod |
| `MEM_POD_MAP_COLLECTION` | optional — the pod map's name; default `Release Notes Pod Map` |

Missing keys → list them and stop before any call. The **Mem MCP must be connected** (the pod map and the pod log live there): if its tools are absent, print `Mem MCP not connected — the pod map lives in Mem. Connect the Mem connector and re-run.` and stop.

## Flow

`P=${CLAUDE_PLUGIN_ROOT}/scripts/release`; today's date via `date +%Y-%m-%d`; `mkdir -p /tmp/pod_notify`.

**`resume`:** run the `pod-notify` skill's step 1 (the collection id the log needs), then its step 5 Resume, then step 7.

### 1 — Gather (Optimus only)

```bash
python3 $P/gather_notes_data_optimus.py tickets <ids> --no-mrs --exact --include-description \
    --output /tmp/pod_notify_data.json
```

`--exact` keeps only exact id matches — a mistyped id is reported, never swapped for the closest ticket. Zero tickets → `No tickets found in Optimus.` and stop. Name every id the script warned it could not find.

### 2 — Route first, so text is written only for tickets a pod gets

Find and read the pod map exactly as the skill's steps 1–2 say, then:

```bash
python3 $P/pod_map.py route --data /tmp/pod_notify_data.json --notes /tmp/pod_notify/notes.json \
    --date <today> --source /pod-notify --out-dir /tmp/pod_notify --routing-only [--force]
```

- **`dry-run`:** run the skill's step 3 (channel checks) and `pod_map.py preview` on this plan, print `Pods (dry run): would post to <N> pod(s).`, and stop.
- `pods: 0` → print the preview table as the report and stop: no pod gets anything.

### 3 — The client cards' text

**With `--summaries <file>`:** use that file as `{summaries}` (its blocks must carry `CLIENT_TITLE` and `EXEC_SUMMARY`; a block without them is reported under "No client title or client paragraph", and a requested ticket with no block under "No summary" — neither is sent). Skip to step 4.

**Otherwise:**

1. Keep only the routed tickets and give each its client line:

   ```bash
   python3 $P/pod_map.py focus --data /tmp/pod_notify_data.json \
       --plan /tmp/pod_notify/plan.json --out /tmp/pod_notify_focus.json
   ```

2. Write the analyzer instructions:

   ```bash
   cat > /tmp/pod_notify_instructions.txt << 'INSTRUCTIONS_EOF'
   You are writing one ticket's card for an internal Slack message to the Medtrics pod (account team) that looks after this ticket's clients. The pod forwards the client paragraph to the client, so it must be client-safe.

   You receive the ticket's identifier, title, Optimus URL, description and its clients. There are no code diffs: work only from the title and description, and never invent a capability or a fix the description does not support.

   Classify the ticket as exactly ONE of FEATURE, ENHANCEMENT, BUG FIX, SECURITY, REFACTOR. If it is a REFACTOR (no user-visible change), return exactly:
   NO_VISIBLE_CHANGE

   Otherwise return EXACTLY this block — no preamble:

   IDENTIFIER: {identifier}
   TITLE: {short readable title}
   CATEGORY: {FEATURE|ENHANCEMENT|BUG FIX|SECURITY|REFACTOR}
   URL: {url}
   CLIENTS: {the Clients value verbatim}
   EXEC_SUMMARY: {2–3 sentences, one physical line, executive voice for institutional administrators: what their people can now do, or what got better. No internal system names, code vocabulary, file paths, ticket or MR references, and no client or institution names. Plain, calm, declarative; no marketing voice; no apology.}
   INTERNAL_SUMMARY: {1–3 sentences, one physical line, what changed in the team's own words, for the client-copy pass to check the paragraph against.}
   INSTRUCTIONS_EOF
   ```

3. Prepare one prompt per ticket and spawn every analyzer in ONE assistant message (Task, `subagent_type: "general-purpose"`, `description: "Summarize <identifier>"`, prompt: `Read the file <filepath> and follow the instructions it contains.`):

   ```bash
   python3 $P/prepare_note_prompts.py --data /tmp/pod_notify_focus.json \
       --instructions /tmp/pod_notify_instructions.txt --prefix pod_notify \
       --output-manifest /tmp/pod_notify_manifest.json
   ```

4. Write the blocks (drop `NO_VISIBLE_CHANGE`) to `/tmp/pod_notify_summaries.txt`, separated by `---`.
5. Run the `medtrics-release-notes:engineering-to-client` skill in **pipeline mode** over that file. It adds `CLIENT_TITLE`, finalizes `EXEC_SUMMARY` and may add `CLIENT_FLAG`. Anything but `CLIENT_COPY_DONE — …`, or a changed block count → stop and surface it.

### 4 — The pod-notify skill, steps 2–7

Route again with the real text, now without `--routing-only`:

```bash
python3 $P/pod_map.py route --data /tmp/pod_notify_data.json --summaries {summaries} \
    --notes /tmp/pod_notify/notes.json --date <today> --source /pod-notify \
    --out-dir /tmp/pod_notify [--force]
```

then follow the skill from step 3 (channel checks) through step 7 (report): one preview with `[y]/[n]/[e]/[a]`, posts only after `y`, the pod log, the report.

## Things this command does NOT do

- Does not post the release itself, write the release archive, or touch `#dream-team` / `#pod-oracle`.
- Does not post to a channel the pod map does not list, to an archived or Slack Connect channel, or to one the bot is not in.
- Does not put internal summaries, developer mentions, code links or another pod's clients in a pod message.
- Does not create the pod map or edit pod notes — that is `/release-note-setup pod` and the team.
- Does not use the Slack MCP, and never retries a failed post on its own.
