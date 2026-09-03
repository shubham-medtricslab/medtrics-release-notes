---
name: oracle-storyteller
description: Compose the /oracle-release Slack sections in mrkdwn — an ip-guarded client block (exec voice, safe to forward), the internal ORC index (ID · title · @dev · PR), and per-ticket technical thread replies. The root itself is built by assemble_oracle_root.py.
---

# Oracle Storyteller

The Oracle Storyteller takes the per-ticket dual-register summary blocks produced upstream by the `/oracle-release` analyzer agents and writes the **section files and thread-reply files** for the combined Oracle release post — one Slack message to the internal-only Oracle channel that carries both a client-safe communication block and an internal reference index.

**This skill is a self-contained contract.** It does not inherit rules from `storyteller` or `internal-storyteller` — every rule it operates under is stated here. Where it deliberately differs from those skills, the difference is called out explicitly:

- **Dual register in one post.** Unlike `storyteller` (client-only) and `internal-storyteller` (internal-only), this skill produces BOTH registers: a guarded client block AND an unguarded internal block, delivered as one message.
- **Three header emoji are allowed** in the composed message: `:rocket:` `:clipboard:` `:lock:`. (`internal-storyteller` allows only a single `:rocket:`.) All three live in the assembled root, placed by `assemble_oracle_root.py` — the files this skill writes contain **no emoji**.
- **No `Clients to notify:` line.** The internal-storyteller root ends with one; the Oracle post never carries it. The upstream blocks have no CLIENTS field and this skill must not invent one.
- **The root is NOT written by this skill.** The orchestrator runs `scripts/assemble_oracle_root.py`, which embeds the guarded client-section file verbatim by construction. This skill writes only the section files and the reply files below.
- **GitHub PRs, not GitLab MRs.** PR links are GitHub `html_url`s. Raw PR URLs in technical replies are deliberate and lint-legal; the labeled `<url|PR#n>` form is used only on internal index lines.

## Audience

One message, two registers:

- **Client block** (`:clipboard:` section of the root): written so an account manager can copy-paste it onward to a client verbatim. Executive voice. Zero internal vocabulary.
- **Internal block + thread replies** (`:lock:` section and the thread): the Medtrics Oracle pod — engineers, CX, leadership. They want attribution, PR links, Optimus links, and mechanism-level notes.

## Input

The orchestrator (`/oracle-release`) passes a single file: `/tmp/oracle_notes_summaries.txt`, containing per-ticket blocks separated by a line consisting of `---`. Each block has this exact shape:

```
IDENTIFIER: ORC-232
TITLE: {verbatim Optimus ticket title — never cleaned, shortened, or rephrased}
KIND: {NEW | IMPROVED | RESOLVED | AWARENESS}
OPTIMUS_URL: https://optimus.medtricslab.com/product-task/ORC-232
DEVELOPER_NAME: {full name of the developer — primarily the GitHub PR author}
DEVELOPER_SLACK_ID: {Slack user id, e.g. U02AB0913 — may be empty}
PR_LINKS: {comma-separated GitHub html_urls — may be empty; tickets whose PR discovery failed (loudly warned upstream) arrive this way}
CLIENT_OUTCOME: {15–40 words, executive voice, zero internal vocabulary}
TECH_NOTES: {2–4 sentences, internal register — mechanism, modules, gotchas}
```

Field rules:

- `TITLE` is verbatim from the source ticket — reproduce it character-for-character everywhere it appears. Never clean it.
- `DEVELOPER_SLACK_ID` may be empty. When it is, render the plain `DEVELOPER_NAME` — **never invent a Slack ID**.
- `PR_LINKS` may be empty. Render `no PR` in that ticket's index line and omit the `• PRs:` bullet from its technical card.
- `CLIENT_OUTCOME` is the only text destined for the guarded client block. If it contains internal vocabulary the analyzer leaked, rewrite it in the same exec voice before use — the IP guard downstream will block the run otherwise.
- `TECH_NOTES` is copied verbatim into the technical card.

A block missing `IDENTIFIER`, `TITLE`, `KIND`, or `CLIENT_OUTCOME` is dropped, and a single line is appended at the very bottom of the internal-section file: `_Dropped {N} malformed upstream block(s): {LIST_OF_IDENTIFIERS_IF_KNOWN}_`. Never silently invent a field.

## Outputs

Write the files byte-for-byte, no surrounding fences, no leading/trailing whitespace beyond a single trailing newline:

| File | Content | Hard cap |
|---|---|---|
| `/tmp/oracle_release_client_section.txt` | Client-communication block (guarded) | 1,200 chars |
| `/tmp/oracle_release_client_cont.txt` | Client-communication overflow (guarded) — created ONLY on overflow | 3,900 chars |
| `/tmp/oracle_release_internal_section.txt` | Internal index: section headers + numbered lines | none (the assembler partitions it) |
| `/tmp/oracle_release_reply_01.txt`, `_02.txt`, … | Thread replies, in posting order | 3,900 chars each |

The orchestrator then runs `assemble_oracle_root.py` (client section + internal section → root), IP-guards the client files, lints everything, and posts. If the assembler spills internal index lines into its `--overflow-output` file, the orchestrator inserts that overflow as its own reply **after the client continuation and before the technical cards**, renumbering the reply files — this skill does not need to leave a gap for it.

### 1. Client section — `/tmp/oracle_release_client_section.txt` (≤ 1,200 chars)

```
{1–2 sentence executive summary of what shipped for Oracle, plain English.}

{One-line outcome, exec voice.} [ORC-232]
{One-line outcome.} [ORC-234]
{...up to ~5 outcome lines...}
```

Rules — this text is IP-guarded and must survive the guard as-is:

- 1–2 sentence summary first, then up to ~5 one-line outcomes, one per ticket, most load-bearing first. Each outcome line MAY end with its `[ORC-ID]` chip (ticket identifiers are client-legal); nothing else internal may appear.
- **Zero internal vocabulary**: no GitHub, no PR/MR references, no React/Supabase/Vite/TypeScript, no file paths, no code artifacts (endpoint, schema, migration, …), no mechanism vocabulary (queue, cache, worker, webhook, race condition, …), no internal URLs, no developer names or mentions, no client institution names.
- Executive voice: active, declarative, user-outcome first. Bug fixes read as the new correct behavior, never as a confession. No marketing voice, no CTAs.
- **Overflow**: if the summary plus all outcome lines exceed 1,200 chars, keep the summary and as many WHOLE outcome lines as fit; write every remaining outcome line to `/tmp/oracle_release_client_cont.txt`, which opens with the line `*Client communication — continued (safe to forward)*` followed by a blank line, then the overflow outcome lines. The continuation file is also IP-guarded — the same zero-internal-vocabulary rules apply. Tell the orchestrator the continuation exists (it passes `--client-continues` to the assembler, which switches the root's marker text).

### 2. Internal section — `/tmp/oracle_release_internal_section.txt`

The internal index: numbered one-line entries grouped under section headers, with a blank line before each `*…*` header line **except the first** — this file starts directly with its first header (no leading blank, per the byte-for-byte rule above). The assembler (`assemble_oracle_root.py`) inserts the blank line between the `:lock:` reference header and this body, so the composed root satisfies slack_lint's `section_header_blank` rule for the first header without this file carrying a leading blank.

```
*Enhancements &amp; New Features*
1. ORC-232 · {verbatim title} · <@U02AB0913> · <https://github.com/medtrics/medtrics-oracle/pull/456|PR#456>
2. ORC-240 · {verbatim title} — heads up · Jane Doe · no PR

*Bug Fixes*
1. ORC-234 · {verbatim title} · <@U03CD4521> · <https://github.com/medtrics/medtrics-oracle/pull/455|PR#455>
```

Rules:

- Line shape: `{n}. {ORC-ID} · {verbatim title} · {<@SLACK_ID> | plain name} · {<pr_url|PR#n> | no PR}` — separator is ` · ` (space, middle dot, space). **The primary PR only** (`pr_discovery.primary`) on the index line; `mrs` is primary-first, so the first entry is correct (the technical card carries them all). No KIND suffix — the sections already group by kind.
- Section mapping: `NEW` / `IMPROVED` → `*Enhancements &amp; New Features*`; `RESOLVED` → `*Bug Fixes*`. An `AWARENESS` item goes in its natural section (Enhancements if it accompanies new/changed capability, Bug Fixes if it accompanies a fix) with the suffix `— heads up` appended after the verbatim title.
- Numbering restarts at `1` in each section. Omit an empty section entirely — header included.
- Mentions are bare `<@U…>` when `DEVELOPER_SLACK_ID` is present — nothing appended after the closing `>`, never the `<@U…|name>` form. Otherwise the plain `DEVELOPER_NAME`. Never invent an ID.
- The PR link is the labeled form `<{first html_url}|PR#{number}>` where the number is taken from the URL's trailing path segment. Empty `PR_LINKS` renders the literal text `no PR`.
- Do not write the root header, the client marker, or the "Details for each item…" line here — the assembler owns all of that. This file is index lines and headers only.

### 3. Thread replies — `/tmp/oracle_release_reply_01.txt`, `_02.txt`, … (≤ 3,900 chars each)

Written in posting order:

1. **Client continuation** (only when `/tmp/oracle_release_client_cont.txt` exists) — that file IS the first reply; do not duplicate its content into a numbered reply file. The orchestrator posts it first.
2. **Per-section technical cards** — one reply per section, Enhancements first, then Bug Fixes.
3. **Security escalation reply** — last, when any security-flavored ticket exists.

Section reply shape:

```
*Enhancements &amp; New Features — technical detail*

*1. ORC-232 · {verbatim title}*
• Developer: {<@SLACK_ID> | plain name}
• PRs: https://github.com/medtrics/medtrics-oracle/pull/456 https://github.com/medtrics/medtrics-oracle/pull/457
• Optimus: https://optimus.medtricslab.com/product-task/ORC-232
• Notes: {TECH_NOTES verbatim}

*2. ORC-240 · {verbatim title}*
• ...
```

Rules:

- Section opener is `*{Section} — technical detail*` (e.g. `*Enhancements &amp; New Features — technical detail*`). Blank line before every standalone `*…*` line — the opener and every card-title line.
- Card title is `*{n}. {ORC-ID} · {verbatim title}*`; the number matches the internal-index number for that section.
- `• PRs:` lists ALL of the ticket's PR links as **raw GitHub URLs, space-separated** — raw URLs are deliberate and lint-legal (Slack auto-links them). Omit this bullet entirely when `PR_LINKS` is empty.
- `• Optimus:` is the raw `OPTIMUS_URL`.
- `• Notes:` is `TECH_NOTES` verbatim — internal register, mechanism vocabulary welcome. Do not pad, do not invent detail that is not in the source.
- A reply exceeding 3,900 chars splits at **card boundaries** into consecutive reply files; each continuation opens with `*{Section} — technical detail (cont.)*`. Deterministic: fill each reply with whole cards in order until the next card would break the cap.
- **Security-flavored tickets** — any ticket whose verbatim title or TECH_NOTES marks it as security work (e.g. `SECURITY`, `Security >`, vulnerability/injection/SSRF/exploit wording, or a security type/severity passed by the orchestrator) — keep their index line in their natural section, but their technical card is EXCLUDED from the section reply and lands in its own dedicated **final** reply, opening with `*Security — technical detail*`, same card format.

## mrkdwn rules (all files)

- Bold: `*text*` — single asterisks only. **Never** `**text**`.
- Italic: `_text_`. Code: backticks.
- Escape literal `&` `<` `>` outside Slack tokens as `&amp;` `&lt;` `&gt;` (Slack renders them back). Section headers are literally `*Enhancements &amp; New Features*` in the file.
- Blank line before every standalone `*…*` line (section headers, card titles, the continuation opener).
- Mentions: exactly `<@U02AB0913>` — bare, nothing after the closing `>`, never `<@U…|name>`. Plain name when no ID is known. Never `@here` or `@channel`.
- Links: labeled `<https://…|label>` on index lines; raw URLs in technical cards (deliberate and lint-legal — Slack auto-links them).
- No markdown headers (`#`), no fenced code blocks, no tables, no images.
- Caps enforced downstream by `slack_lint.py`: root ≤ 2,500 (assembler's problem), client section ≤ 1,200 (this skill's problem), every reply ≤ 3,900 (this skill's problem).

## When upstream is empty

If the input file has zero usable blocks, write exactly this sentinel to `/tmp/oracle_release_client_section.txt` and stop — write no other files:

```
NO_ORACLE_NOTES — nothing shipped for Oracle in this run
```

The orchestrator detects the sentinel and skips the assembler, the guard, and the Slack post.

## What this skill does not do

- Does not write the root post — `scripts/assemble_oracle_root.py` builds it from the client-section and internal-section files, so the guarded client text lands in the message verbatim by construction.
- Does not post to Slack — `scripts/slack_upload.py`, invoked by `/oracle-release`, posts the root first, then each reply with `--thread-ts`.
- Does not run the IP guard or the lint gate — `/oracle-release` runs `ip_guard.py` over the client files and `slack_lint.py` over the root and every reply after composition.
- Does not resolve Slack user IDs — the orchestrator resolves `DEVELOPER_SLACK_ID` before invoking this skill; empty means plain-name fallback.
- Does not call GitHub, Optimus, or any external system, and does not read raw diffs.
