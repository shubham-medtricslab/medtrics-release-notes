---
name: internal-storyteller
description: Compose the combined internal release announcement in Slack mrkdwn — a compact root-post index (`n. ID · title`, grouped by Enhancements & New Features vs. Bug Fixes) plus per-section thread replies whose cards are `*n. ID · title*`, a client-portable `For clients:` paragraph, then Ticket / Developer / Internal / URL / PRs-or-MRs / Clients bullets. Use whenever a combined internal release post is being produced — the `/release-slack` (#dream-team) or `/oracle-release` (#pod-oracle) command.
---

# Internal Storyteller (combined release post)

The Internal Storyteller takes a set of per-ticket release summaries (produced upstream by the calling command's analyzer agents) and writes the single combined Slack message that lands in the calling command's internal channel after each deploy — `#dream-team` for `/release-slack`, `#pod-oracle` for `/oracle-release`.

This is the **combined** release storyteller — it replaces the former split between a customer-facing and an internal post. The audience is the internal Medtrics team — engineers, CX, account managers, leadership — and the message keeps everything the old customer-facing brief stripped out: ticket identifiers, developer attribution with Slack `@` mentions, client institution names, and direct links to the task. Keep all of it.

On top of that internal detail, **each ticket card leads with a short client-portable paragraph** (the upstream `EXEC_SUMMARY`, finalized by the `engineering-to-client` skill) that the team can lift and send to clients verbatim. There is no separate client-channel post; this one message carries both voices.

Card shape at a glance (bullet order is fixed):

```
*1. ORC-232 · Client-facing headline (CLIENT_TITLE from the engineering-to-client pass)*
*_For clients:_* <client-portable paragraph>
• Ticket: ORC-232
• Developer: <@U02AB0913> (Dev Name)
• Internal: <technical summary>
• URL: https://optimus.medtricslab.com/product-task/ORC-232
• PRs: <links>          (or • MRs: <links> on the GitLab track)
• Clients: <codes> | TBC (platform-wide)
```

## Audience

Single unified voice for:

- The Medtrics engineering team (dev, infra, QA).
- CX, account management, and customer-success.
- Leadership and operations.

These readers know the platform, know the ticket vocabulary, and want to scan the deploy in under a minute and know exactly who shipped what, for whom, and where to read more.

## Inputs

The calling command passes **a summaries file path** and **an output prefix** (the command chooses both). `/release-slack` passes `/tmp/release_slack_summaries.txt` and prefix `release_slack`; `/oracle-release` passes `/tmp/oracle_notes_summaries.txt` and prefix `oracle_release`. Everywhere this doc names `/tmp/release_slack_root.txt` / `_reply_NN.txt`, substitute `/tmp/{prefix}_root.txt` / `/tmp/{prefix}_reply_NN.txt`. The root header is the same for every caller — `:rocket: *Release Notes — {date}*` — so no product label is passed.

The summaries file contains per-ticket blocks separated by `---`. Each block has this exact shape:

```
IDENTIFIER: M1-986
TITLE: Graded Form Average Score Display
CLIENT_TITLE: Average score shown on completed evaluations
CATEGORY: ENHANCEMENT
URL: https://optimus.medtricslab.com/product-task/M1-986
DEVELOPER_NAME: Chris Tokodi
DEVELOPER_SLACK_ID: U02AB0913
DEVELOPER_FIRST_NAME: chris
CLIENTS: KFSHRC
PR_LINKS: https://github.com/org/repo/pull/42
MR_LINKS: https://gitlab.com/org/repo/-/merge_requests/6567
EXEC_SUMMARY: Programs can now show each evaluation's average score on the completed-form view, giving reviewers an at-a-glance read on overall performance without re-tallying individual items.
INTERNAL_SUMMARY: New "Average Score" element on the Form Complete page using a 51% majority-dominant scale threshold...
```

`CATEGORY` is one of: `FEATURE`, `ENHANCEMENT`, `BUG FIX`, `SECURITY`, `REFACTOR`.

`CLIENT_TITLE` is the **client-facing headline** written by the `engineering-to-client` pass (a few words, no ticket id, no jargon, no client name). It is the title of the card and of the root-index entry. `TITLE` is the internal Optimus ticket title — kept in the block for traceability and used as the title **only** when `CLIENT_TITLE` is missing (legacy input).

`EXEC_SUMMARY` is the **client-portable paragraph** — by the time this skill runs, the calling command has already passed every block through the `engineering-to-client` skill, so the value is the final client copy (1–3 sentences, no headline, no internal system names, no code artifacts, no file paths, no client institution names). Render it verbatim on the `*_For clients:_*` line so the team can copy it to clients. `INTERNAL_SUMMARY` is the technical detail for the dev team — keep module names, mechanism, and client motivation — rendered on the `• Internal:` line.

A block may also carry an optional `CLIENT_FLAG: {code(s)} — {reason}` field (added by the `engineering-to-client` pass when a named client's item is still unresolved). This skill does **not** render it — the calling command surfaces flags at the preview. Ignore it for layout; never let it leak into a card.

`PR_LINKS` and `MR_LINKS` are both **optional** code-change URL lists (comma- or space-separated). A given ticket typically carries only one: `/oracle-release` (GitHub) fills `PR_LINKS`; `/release-slack` (GitLab) fills `MR_LINKS`. Render them together on one `• PRs: … | MRs: …` line showing only the populated side(s); omit the whole line when both are empty. See the per-card rules for the exact rendering.

For backward compatibility, a block that carries a single `SUMMARY` field and no `EXEC_SUMMARY`/`INTERNAL_SUMMARY` is treated as `INTERNAL_SUMMARY: {SUMMARY}` with no client lede; render the card without the `*_For clients:_*` line in that case.

`DEVELOPER_SLACK_ID` may be empty if the analyzer could not resolve the developer's Slack user. `CLIENTS` may be a single client tag, a comma-separated list, or the literal string `TBC` (to-be-confirmed — e.g., a platform-wide change).

Drop any block whose `CATEGORY` is `REFACTOR` — a refactor has no user-visible behavior change and does not belong in the release digest. (Upstream normally returns `NO_VISIBLE_CHANGE` for these before they reach this skill; drop any that slip through so they never land in a section.)

## Output — root-post index + thread replies

Produce one **root post** (the index) plus one or more **thread-reply bodies** (the detail). Never a second top-level message — everything beyond the root index lands in the thread.

Write the files byte-for-byte, no surrounding fences, no leading/trailing whitespace beyond a single trailing newline:

| File | Content | Hard cap |
|---|---|---|
| `/tmp/{prefix}_root.txt` | The root-post index | 2,500 chars |
| `/tmp/{prefix}_reply_01.txt`, `_02.txt`, … | One reply per section (long-form detail), escalations as their own reply, in posting order | 3,900 chars each |

`{prefix}` is the output prefix the command passed (`release_slack` for `/release-slack`, `oracle_release` for `/oracle-release`). The calling command posts the root first, captures its `ts`, then posts each reply file in numeric order with `--thread-ts <ts>`.

**Slack mrkdwn rules (all files):**

- Bold: `*text*` (single asterisks). **Never** `**text**`.
- Italic: `_text_`.
- Code: backticks.
- Lists: leading `• ` for bullets.
- No tables, no headers (`#`), no fenced code blocks, no images.
- Escape literal ampersands and angle brackets outside tokens: `&amp;`, `&lt;`, `&gt;` (Slack renders them back). Section headers are literally `*Enhancements &amp; New Features*` in the file.
- Hyperlinks: `<https://...|label>`. Plain URLs auto-link as well — use the raw-URL form for Optimus ticket URLs to keep replies compact (this raw-URL convention is deliberate and lint-legal).
- Slack user mentions: `<@U02AB0913>` when a Slack ID is known, followed by the developer's name in parentheses as plain text — `<@U02AB0913> (Chris Tokodi)`. Never use the `<@U…|name>` pipe form (the lint gate rejects it); the parenthetical is outside the token, which is legal. When no ID is known, fall back to the developer's plain name with no `@` and no parentheses.
- A blank line before every section-header line (a line that is entirely a `*…*` bold span).
- No emoji except a single `:rocket:` in the root header line.

## Root post — exact structure

```
:rocket: *Release Notes — {Weekday}, {Month} {D}, {YYYY}*

{One sentence summarizing the release theme. ≤ 30 words. Pick the two or three most-load-bearing changes and name them. No marketing voice.}

*Enhancements &amp; New Features*
1. {IDENTIFIER} · {CLIENT_TITLE}
2. {IDENTIFIER} · {CLIENT_TITLE}

*Bug Fixes*
1. {IDENTIFIER} · {CLIENT_TITLE}
2. ...

Clients to notify: {deduped, comma-separated union of every non-TBC CLIENTS tag across the release, or "TBC" if none}
Details for each item are in this thread.
```

Root rules:

- Each entry is exactly one line: `{n}. {ID} · {CLIENT_TITLE}` — the id and the client-facing headline joined with ` · ` (space, middle dot, space), identical to the card title (fall back to the internal `TITLE` only when a block has no `CLIENT_TITLE`). **No developer mention in the root** — attribution lives on each card's `• Developer:` line in the thread.
- Numbering restarts at `1` in each section.
- The root must stay ≤ 2,500 characters. If the index itself cannot fit, keep as many complete entry lines as fit, end the last section with a line `…and {N} more — index continues in the thread`, and prepend the overflow index lines (same format, under the same section headers) as the **first** thread reply. Never let the root spill into a second top-level message.
- The `Clients to notify:` line always appears at the bottom of the root.

## Thread replies — exact structure

One reply per section, in this order: (1) Enhancements &amp; New Features detail, (2) Bug Fixes detail. Any `SECURITY` block — or any item the orchestrator marked as an escalation — is **excluded from the section reply and gets its own dedicated reply**, posted after the section replies.

Each reply opens with its section context on the first line, then one card per ticket. The card title carries the ticket id and title; the client-portable paragraph comes first, then the internal metadata bullets:

```
*Enhancements &amp; New Features — details*

*1. {IDENTIFIER} · {CLIENT_TITLE}*
*_For clients:_* {EXEC_SUMMARY — copy verbatim from the upstream block; this is the text the team ports to clients}
• Ticket: {IDENTIFIER}
• Developer: {<@SLACK_ID> (Developer Name)}
• Internal: {INTERNAL_SUMMARY — 2–4 sentences from the upstream block. Keep technical specifics. Keep client motivation if it appears in the source — "Requested by {CLIENT} to ...". Do not invent motivation.}
• URL: {URL}
• PRs: {PR_LINKS} | MRs: {MR_LINKS}   — show only the populated side(s); omit the whole line if both empty
• Clients: {CLIENTS, or "TBC (platform-wide)" when the upstream value is TBC}

*2. {IDENTIFIER} · {next enhancement title}*
*_For clients:_* ...
• Ticket: ...
• Developer: ...
• Internal: ...
• URL: ...
• PRs: ...
• Clients: ...
```

Leave one blank line between consecutive cards (the blank line before each `*n. …*` title provides it). The bullet order is fixed: **Ticket, Developer, Internal, URL, PRs/MRs, Clients**. The `*_For clients:_*` label is **bold-italic** (`*_…_*`); its value is the upstream `EXEC_SUMMARY`, copied verbatim — already client-safe (it went through the `engineering-to-client` pass), so never add internal detail to it. If a block has no `EXEC_SUMMARY` (legacy `SUMMARY`-only input), omit the `*_For clients:_*` line and render the summary on the `• Internal:` line. Bug-fix cards lead `• Internal:` with what was broken in one clause, then the corrected behavior. Escalation replies open with `*Escalation — {IDENTIFIER} · {CLIENT_TITLE}*` and carry the same card fields.

Reply rules:

- Card numbers match the root-index numbers for that section.
- A blank line before every `*…*` card-title line.
- Each reply must stay ≤ 3,900 characters. If a section's cards exceed that, split at card boundaries into consecutive reply files, the continuation opening with `*{Section} — details (cont.)*`. The split is deterministic: fill each reply with whole cards in order until the next card would break the cap.
- Never move detail back into the root to save a reply — detail always lives in the thread.

### Section ordering rules

- `*Enhancements &amp; New Features*` comes first if any block is `FEATURE` or `ENHANCEMENT`. Within the section, list features ahead of enhancements; otherwise keep upstream order.
- `*Bug Fixes*` comes second. List `SECURITY` blocks first inside this section's index lines, then `BUG FIX` (the SECURITY detail cards still go to their own escalation reply).
- Omit a section entirely if it has no blocks — in the root and in the thread. Do not print an empty section header.
- Number items within each section starting at `1`. Numbering restarts in each section.

### Per-card formatting rules

- **Escape literal `&`, `<`, `>`** in every copied title, client paragraph, and internal summary — `&`→`&amp;`, `<`→`&lt;`, `>`→`&gt;` — outside Slack tokens, exactly as the section headers already are. A ticket titled `Duty Hours & Case Logs` renders as `*1. M1-986 · Duty Hours &amp; Case Logs*`; an internal summary saying `min < max` renders `min &lt; max`. This keeps the deterministic lint gate mechanical instead of forcing a relint rewrite on every release that mentions `&`, `<`, or `>`.
- **Title line** is `*{n}. {IDENTIFIER} · {CLIENT_TITLE}*` — the card number, the raw ticket identifier (`M1-986`, `GEN-128592`, `ORC-101`; no brackets, no prefix), a ` · ` separator (space, middle dot, space — the same middle dot the root index and the IP-guard/lint exemptions use; never a hyphen, which reads ambiguously against the hyphen inside the identifier), then the block's `CLIENT_TITLE` copied verbatim (it is already client-facing; never swap the internal `TITLE` back in, never append the internal title), then escaped per the rule above. The whole line is one bold span. Only when a block has no `CLIENT_TITLE` (legacy input) use the internal `TITLE`, lightly cleaned (drop trailing punctuation; leave `API`, `SSO`, `JWT` alone; expand `PD` to `Program Director` only on first appearance).
- **`*_For clients:_*`** is bold-italic (the literal token is `*_For clients:_*`), followed by the upstream `EXEC_SUMMARY` copied verbatim — a paragraph only, no headline (the title line is the headline). It is already client-safe (no internal system names, code artifacts, file paths, or client institution names). Never add internal detail to it. Omit the whole line for a legacy `SUMMARY`-only block (render that summary on `• Internal:` instead). It is the line directly under the title.
- **Ticket** line is `• Ticket: {IDENTIFIER}` — the raw identifier only. The URL has its own line (below).
- **Developer** (card line only — the root index carries no developer) renders three ways from the upstream block: (a) `DEVELOPER_SLACK_ID` present → `<@SLACK_ID> (DEVELOPER_NAME)` — the bare mention, then the developer's name in parentheses, e.g. `<@U02AB0913> (Chris Tokodi)`; (b) no Slack id but a name → the plain `DEVELOPER_NAME`; (c) both empty → `Unassigned`. The parenthetical name is plain text OUTSIDE the mention token — never fold it into the token. The lint gate rejects only the pipe form `<@U…|name>`; the `<@ID> (Name)` form is lint-legal and is the intended rendering.
- **Internal** line is `• Internal: {INTERNAL_SUMMARY}` — the upstream `INTERNAL_SUMMARY` (or a legacy `SUMMARY`), copied verbatim *unless* it contains language the storyteller must rewrite (see Voice rules below). Do not invent details. Do not strip ticket motivation clauses ("Requested by CVM-NCSU to...") — they are exactly the signal the team wants.
- **URL** line is `• URL: {URL}` — the ticket's Optimus URL, raw (lint-legal; Slack auto-links it). Omit the line if the block has no `URL`.
- **PRs / MRs** is one line, `• PRs: {PR_LINKS} | MRs: {MR_LINKS}`, but render **only the populated side(s)**: with only PR links → `• PRs: {PR_LINKS}`; with only MR links → `• MRs: {MR_LINKS}`; with both → `• PRs: {PR_LINKS} | MRs: {MR_LINKS}`; with neither → omit the line entirely. Render each URL raw, space-separated when there is more than one. In practice `/oracle-release` populates PRs and `/release-slack` populates MRs, so a card shows one side.
- **Clients** line lists every client tag that appears in the upstream `CLIENTS` field, comma-separated, in the order they appeared — exactly as the `engineering-to-client` pass left it. If the upstream value is `TBC`, render `TBC (platform-wide)`. Never add a client name that is not in the block. It is the last line of the card.

### Voice rules

- **Plain technical English.** This is internal — engineers and CX both read it. Use the platform's vocabulary as the team uses it (e.g., "evaluation form", "case log", "preceptor approval", "duty hour violation", "bulk import").
- **Active voice.** Lead with the change, not with self-congratulation.
- **Past tense for completed work.** Present tense for the new behavior ("the trigger now respects the program-level toggle").
- **No marketing voice.** No "thrilled," "delighted," "game-changing," "powerful," "next-generation," "revolutionize."
- **No apologetic framing** for bug fixes. State what was broken in one clause, then the correct behavior. The team does not need a confession.
- **Keep client institution names in the message.** Internal-only. Do not abstract them.
- **Keep developer Slack mentions in the message.** This is an internal digest — attribution is the whole point.
- **Do not invent diagnostic detail.** If the upstream summary says "logic now waits for the full weekly window to close before evaluating min violations", that's what goes in. If the upstream block doesn't describe the mechanism, do not guess at one.

### Header sentence rules

The single sentence under the date is the root post's only synthesis line. Pick the two or three most load-bearing items in the release and name them — by capability, not by ticket. Examples:

- "This release packs evaluation UX upgrades, critical permission and notification fixes, and a major bulk-import performance win."
- "Today's release ships duty-hour rule corrections, preceptor notification fixes, and the long-awaited evaluation question navigator upgrade."

Do not write a list. Do not write "we're excited". 30 words or fewer.

### Closing line rules

The **last thread reply** (after all section and escalation replies) ends with a trailing line: `Please flag any unusual behavior across {modules}!`. It names two to four modules **drawn from the actual ticket set of this release** — never a fixed list. Pick the domains the tickets actually touched. For the legacy platform those are concepts like:

- evaluations
- scheduling
- duty hours / work-hour violations
- case logs
- notifications
- open forms
- bulk imports
- reporting
- access / permissions
- integrations

Pick the ones most directly touched by the tickets in this release. Do not name every module in the platform. For a different product (e.g. Oracle), draw the module names from that product's own domain as the tickets reveal it — never force the legacy list onto it.

## Length

Hard caps, enforced by the deterministic lint gate (`scripts/slack_lint.py`) before any preview:

- Root post: **≤ 2,500 characters** (Slack collapses long messages around ~3,000 — the root must never collapse).
- Each thread reply: **≤ 3,900 characters** (split at card boundaries when a section runs long).
- Absolute API cap on any single message: 40,000 characters — nothing should ever approach it.

Each per-card summary stays at 2–4 sentences, no exceptions. If you find yourself writing a fifth sentence, you are drifting into the dev-notes audience, not the internal-digest audience. Scale is handled by adding thread replies, never by inflating the root or posting a second top-level message.

## When upstream is empty

If the input file has zero usable blocks (every block dropped, or the upstream collected nothing), write no root or reply files and return exactly:

```
NO_INTERNAL_NOTES — nothing customer-visible shipped this deploy
```

The orchestrator will detect this and skip the Slack post.

## Failure handling

If a per-block field is malformed (missing `IDENTIFIER`, missing `TITLE`, missing both `INTERNAL_SUMMARY` and legacy `SUMMARY`, or `CATEGORY` is not one of the allowed values), drop that block from the output and append a single line at the very bottom of the root post:

```
_Dropped {N} malformed upstream block(s) — orchestrator should re-run the analyzer for: {LIST_OF_IDENTIFIERS_IF_KNOWN}_
```

Never silently invent a category, title, or developer.

## What this skill does not do

- Does not post to Slack. That is `scripts/slack_upload.py` invoked by the calling command (`/release-slack` or `/oracle-release`) — root first, then each reply with `--thread-ts`.
- Does not run the lint gate. The calling command runs `scripts/slack_lint.py` over the root and every reply after composition.
- Does not call the IP guard. The internal post is meant to carry internal vocabulary; the guard would refuse it.
- Does not produce any artifact beyond the root and reply text files. Internal notes are Slack text only.
- Does not look up Slack user IDs. The calling command resolves `DEVELOPER_SLACK_ID` (optional Mem developer directory via the `dev-directory` skill, then `scripts/slack_resolve_users.py` using the bot token's `users.lookupByEmail` + name-match fallbacks) before invoking this skill; if it is empty, this skill falls back to the plain developer name.
- Does not write or rewrite client copy. The `*_For clients:_*` paragraph and the `Clients` value are final when they arrive — the calling command ran the `engineering-to-client` skill over every block before composition.
