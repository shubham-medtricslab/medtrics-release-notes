# Plan: pod-notify — release tickets to client pods

**Goal:** when a release ships, each client's pod (Slack channel) learns which released tickets concern its clients. Today only #dream-team / #pod-oracle hear about it.

**Status:** planned 2026-10-29 and reviewed by an adversarial pass; built in 0.22.0 on branch `pod-notify`.

## Decisions

| Topic | Decision |
|---|---|
| Where it runs | Default step in `/release-slack` and `/oracle-release`, **and** a standalone `/pod-notify <ticket ids>` for hand-written releases |
| Modes | Runs only in `tickets` mode. `status`, `mr` and `dry-run` skip it (`dry-run` prints the routing table only). `--no-pod` skips it everywhere |
| Ticket → clients | `linked_clients` from the Optimus REST data the gather script already pulls (kept as `optimus_clients`). If empty, fall back to full names under the description's `## Reported client(s)` heading, marked "guessed" in the preview. No all-caps-code matching |
| Platform-wide / internal | "All clients / platform-wide" and "Internal" tickets go to no pod; they're listed in the report |
| Pod map | Mem collection "Release Notes Pod Map", one hand-edited note per pod: `Pod: #channel`, `Channel ID:`, `Clients:` (exact Optimus names). Names are resolved to client UUIDs; unknown names warn |
| Slack shape | One root post per pod plus one thread reply per ticket (a summary over the 3,900-char cap is cut short) |
| Card content | Client-facing title, exec summary, client flag, Optimus link, and that pod's own clients only. No internal summary, no developer @-mentions, no other clients' names |
| Sending | `scripts/release/slack_upload.py` with the bot token (the Slack MCP stays banned). The channel ID is what counts |

## Flow (after the archive step 9b / 11b, before `--kb`)

1. Read the posted summaries (`/tmp/*_summaries.txt`); route only identifiers present there.
2. Ticket → client UUIDs from `optimus_clients`, else the guessed full-name fallback.
3. Load the pod map from Mem. Look the collection up the same strict way as the archive: paginated `list_collections`, exact title, then exact case, then oldest; if still ambiguous, skip. A missing map means a one-line skip.
4. Invert to `{channel_id: [tickets]}`; dedupe; drop (channel, ticket) pairs already in the pod log.
5. Channel preflight (read-only `conversations.info`): block Slack Connect and archived channels; flag "bot not a member" and channel-name drift.
6. Render deterministically in `pod_map.py` (escape `&`), then `slack_lint` every file.
7. One preview: a routing table (pod → tickets; unmapped tickets; unmapped clients; guesses; already-sent) plus one sample pod in full. `[y]` post, `[n]` cancel, `[e]` edit, including dropping a pod or ticket.
8. Post pod by pod and write `/tmp/pod_state.json` after every post. Resume a failed pod with its own `--thread-ts`; an unverified write stops that pod with "check #channel". The release itself never fails.
9. Write one Mem pod-log note (a non-`Release:` title) recording the channel, root ts and ticket ids per pod.
10. Report what was posted where, plus the gaps.

## Pieces

- `scripts/release/gather_notes_data_optimus.py`: `task_to_issue` passes `linked_clients` through as `optimus_clients` (Oracle reuses it).
- `scripts/release/pod_map.py`: parse the map, invert, dedupe against the log, render, emit writes; `---POD_MAP_RESULT---` block.
- `scripts/release/slack_upload.py`: read-only `--check-channel` mode.
- `skills/pod-notify/SKILL.md` and `commands/pod-notify.md`.
- A step in `commands/release-slack.md` and `commands/oracle-release.md`; `--no-pod` added to both Usage blocks.
- A `pod` part in `/release-note-setup`: find or create the collection and seed draft notes. Only setup ever creates it.
- `tests/test_pod_map.py`, plus a passthrough test in `test_gather.py`.
- README, CHANGELOG, `plugin.json` / `marketplace.json` → 0.22.0.

## Before it's useful

- The team has to fill in the pod map; nothing in Optimus, Mem or Slack says which clients belong to which pod.
- The bot has to be invited to each pod channel.
- About 43% of the tickets now in the deploy lanes (33 of 77) have no linked client; most are Oracle tickets left empty on purpose.
