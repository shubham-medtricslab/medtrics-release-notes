---
name: engineering-to-client
description: Translate heavy-engineering ticket write-ups (Ticket/Developer/Internal/URL/PRs/Clients block format) into a plain-English, client-facing description for each item. Two entry points — (1) PIPELINE MODE, called by `/release-slack` and `/oracle-release` after the per-ticket analyzers to produce every card's client-facing title (`CLIENT_TITLE`), its final `For clients:` paragraph, and its `Clients:` value; (2) standalone, when the user pastes ticket blocks with an "Internal:" engineering summary and asks for the customer-facing version. Owns the client-copy voice rules for the whole plugin.
---

# Engineering → Client Translator

## Trigger

**Pipeline mode (primary):** the `/release-slack` and `/oracle-release` commands invoke this skill once per run, after all analyzer agents have returned and before composition, over the per-ticket blocks in the summaries file. See "Pipeline mode" below.

**Standalone mode:** the user pastes one or more ticket write-ups shaped like this (fields may vary slightly, order may vary):

```
N. <TICKET_ID> - <short internal title>
For clients: <optional existing draft>
• Ticket: <ID, e.g. ORC-232 or M1-583>
• Developer: <name>
• Internal: <heavy engineering description — code, tables, RPCs, migrations, endpoints, etc.>
• URL: <task link>
• PRs: <link(s)>
• Clients: <TBC | specific client code(s)>
```

and asks for the client-facing version, a translation, or says they're "mostly interested in the client ticket description, not the rest."

## What to produce

For each item, produce ONLY the client-facing description — nothing else. Drop entirely from the description:

- Ticket IDs (ORC-###, M1-###, GEN-######, etc.)
- Developer names / @-mentions
- The raw "Internal:" engineering paragraph itself (mine it for meaning — never quote or paraphrase its jargon)
- URLs and PR links
- Client institution names (see "Named clients" below — the `Clients:` attribution is metadata, never prose)

## Voice rules

1. **Plain English.** Ban platform-internals vocabulary in the output: RPC, index, migration, predicate, enum, endpoint, serializer, viewset, SECURITY DEFINER/INVOKER, cache, query key, schema, foreign key, middleware, component, hook, RLS policy, React, Supabase, GitHub, pull request — describe what the person can now do, not how it was built.
2. **Lead with the outcome:** who can do what now, or what got fixed — never "we fixed a bug where…".
3. **Active voice.** Present tense for a new/changed capability, past tense only for a completed one-time event.
4. **No marketing language** ("excited," "powerful," "seamless," "game-changing," "thrilled").
5. **No apologies** for bugs — describe the corrected behavior matter-of-factly, without pretending nothing was ever broken.
6. **Keep each item to 1–3 sentences** unless an existing "For clients:" draft is already longer and worth preserving as-is.
7. **If a rough "For clients:" draft already exists** in the input (in pipeline mode: the analyzer's `EXEC_SUMMARY`), treat it as a first pass to tighten (cut jargon leaks, cut hedging, verify it's still accurate against the "Internal:" text) rather than rewriting from nothing.
8. **If no "For clients:" draft exists,** write one from scratch using only what the "Internal:" text actually supports — never invent a capability, root cause, or fix that isn't backed by the source text.

## Named clients ("Clients: <CODE>" is not "TBC")

- If `Clients` is `TBC`, the item is platform-wide — write it as a general update, no client name. Keep the `Clients` value as `TBC`; the storyteller renders it as `TBC (platform-wide)`.
- If `Clients` names one or more specific institutions (e.g. `KFSHRC`) **AND** the item is still unresolved/investigating, do NOT fold that institution's name into a general release note meant for everyone — that update belongs in a note addressed to that client specifically. **Flag this distinction to the user** rather than silently broadcasting or silently dropping the name (in pipeline mode: emit a `CLIENT_FLAG`, below).
- If `Clients` names an institution and the item is shipped/resolved, keep the codes on the `Clients` line (it is internal metadata the team uses to know whom to notify) and keep the institution's name **out of the description** — the description must read correctly when forwarded to any client.
- Never invent a client code. If the input gives none, the value is `TBC`.

## Pipeline mode (`/release-slack`, `/oracle-release`)

**Input:** the command's summaries file (`/tmp/release_slack_summaries.txt` or `/tmp/oracle_notes_summaries.txt`) — per-ticket blocks separated by `---` with the fields `IDENTIFIER`, `TITLE`, `CATEGORY`, `URL`, `DEVELOPER_*`, `CLIENTS`, `PR_LINKS`/`MR_LINKS`, `EXEC_SUMMARY` (the analyzer's draft client lede), `INTERNAL_SUMMARY`. Treat `EXEC_SUMMARY` as the "optional existing draft" and `INTERNAL_SUMMARY` as the "Internal:" paragraph.

**Output:** rewrite the file **in place**, keeping every block, every field, and the field order intact, changing only:

- Add `CLIENT_TITLE:` directly after `TITLE:` → the client-facing **headline** (the same headline standalone mode produces: a few words, no ticket ID, no jargon, no client name, describes the capability or fix — e.g. `Filter assignments by evaluator or evaluatee role`). ONE physical line. The storyteller renders it as the card title (`*1. ORC-232 · {CLIENT_TITLE}*`) and as the root-index entry; the internal `TITLE` stays in the block untouched for traceability and as the fallback if `CLIENT_TITLE` is missing.
- `EXEC_SUMMARY:` → the final client-facing **paragraph** (1–3 sentences, ONE physical line, no headline — `CLIENT_TITLE` carries the headline). This is the text that renders as `*_For clients:_*` on the card and is what the team forwards to clients.
- `CLIENTS:` → the verbatim client code list from the input, or `TBC`. Do not add or remove codes; do not rewrite them. (The value is preserved from the orchestrator's enrichment — this pass only confirms it and decides whether to flag.)
- Add one optional field after `CLIENTS:` — `CLIENT_FLAG: {code(s)} — {one-line reason}` — on any block whose `CLIENTS` names a specific institution **and** whose item is still unresolved/investigating (read the `INTERNAL_SUMMARY` and `CATEGORY`). Omit the line otherwise. The command lists every `CLIENT_FLAG` in the preview as "client-specific items — send these to the named client only."

Never touch `INTERNAL_SUMMARY`, `TITLE`, `IDENTIFIER`, URLs, links, or developer fields. Never reorder or drop blocks — a `NO_VISIBLE_CHANGE` ticket was already removed upstream. If a block's `EXEC_SUMMARY` is already clean, leave it byte-identical.

Rewrite in one pass over all blocks so wording stays consistent across the release (the same feature described the same way twice, no two cards opening with the same phrase). Return the single line `CLIENT_COPY_DONE — {N} blocks, {F} flagged` when finished; the command then continues to the IP guard (Oracle) or straight to composition.

## Standalone output format

Return a numbered or headed list, one entry per ticket:

1. A short client-facing **headline** (a few words, no ticket ID, describes the capability or fix)
2. The polished client-facing **paragraph**

No internal metadata block underneath the description. If the user wants this delivered as a document (release notes, client email, Slack post), save it as a markdown file at `.claude/outputs/notes/client-descriptions/client-descriptions-<YYYY-MM-DD>.md` (compute the date via `date +%Y-%m-%d`; `mkdir -p` the directory) rather than leaving it only in chat, since it's content meant to go outside the conversation.

## Worked example

Input ("Internal" excerpt):

> Oracle's Assignments filter panel offered only person-level Responder/Recipient pickers, missing Legacy's Evaluator Role / Evaluatee Role filters. Added Responder Role and Recipient Role filters end-to-end: AssignmentFilters type fields, service-layer UUID validation, and server-side predicates in all three list RPCs restricting responder/recipient to live program_memberships for the chosen role_id, backed by a new partial index on (program_id, role_id, user_id)…

Pipeline-mode output (the `CLIENT_TITLE:` and `EXEC_SUMMARY:` lines):

> CLIENT_TITLE: Filter assignments by evaluator or evaluatee role
> EXEC_SUMMARY: Coordinators can now filter the assignments grid by responder role and recipient role, so a question like "show every second-year resident's outstanding evaluations" can be answered in one step. The role filters follow each person's current program role, matching what administrators had on the prior platform.

Standalone output:

> **Filter assignments by evaluator or evaluatee role**
> Coordinators can now filter the assignments grid by responder role and recipient role, so a question like "show every second-year resident's outstanding evaluations" can be answered in one step. The role filters follow each person's current program role, matching what administrators had on the prior platform.

## Related skills (context, not dependencies)

- `internal-storyteller` renders `CLIENT_TITLE` as the card title and root-index entry, `EXEC_SUMMARY` as the card's `*_For clients:_*` line (verbatim), and `CLIENTS` on the `• Clients:` line. It does not rewrite client copy.
- `ip-guard` (Oracle only) runs over the `CLIENT_TITLE` and `EXEC_SUMMARY` lines **after** this pass as a mechanical safety net.
- `copy-audit` backs the customer-facing markdown commands; this skill's voice rules deliberately mirror it — keep them in sync if `copy-audit`'s rules change.
