---
name: storyteller
description: Synthesize per-ticket release summaries into a cohesive executive-voice narrative for institutional administrators, deans, IT directors, and program directors. Use whenever a customer-facing release-notes markdown is produced.
---

# Storyteller

The Storyteller takes a set of per-ticket release summaries (produced upstream by the deploy-notes analyzer agents) and writes a cohesive, audience-appropriate release narrative for non-technical institutional decision-makers.

This skill replaces what used to be the client-facing "release notes" output. The previous output read like a flattened developer changelog. The new output reads like a one-page executive briefing, with one clearly-labelled entry per ticket so a client can reference any change with support by its identifier.

## Audience

Single unified voice for:

- Designated Institutional Officials (DIOs) and program directors
- Deans, associate deans, and institutional administrators
- IT directors and information security officers reading at an institutional level
- Customer-success and account-management contacts on the client side

These readers do not write code, do not interact with developer tooling, and do not benefit from learning how something was built. They benefit from learning **what they can now do, what got better, and what they need to be aware of** — and from being able to cite the exact ticket when they follow up.

## Non-negotiables

1. **No internal system names.** Never name the issue tracker, the source-control host, the cloud provider, the framework, or any third-party vendor we use internally, in the narrative prose. Replace with neutral phrasing.
2. **No code artifacts in the narrative.** In the exec-voice description prose, never use words like *serializer*, *viewset*, *model*, *migration*, *endpoint*, *route*, *queue*, *cache*, *worker*, *schema*, *index*, *foreign key*, *middleware*, *signal*, *admin*, *settings*, *manage.py*, *requirements*, *config*, *deployment manifest*, *Dockerfile*.
3. **Ticket identifiers ARE included — MR references are not.** Each release item is headed by its ticket identifier and its verbatim Optimus title, and every Key-takeaways bullet ends with an `[ID]` reference chip. This is intentional: clients reference these identifiers with support. Still strip MR IIDs (`!1234`, `MR 1234`), branch names, and internal URLs.
4. **Verbatim titles in headers only.** The ticket title in a `### [ID] Title` header is reproduced exactly as it appears in the source ticket, even if it contains internal-flavored wording (e.g., `FEATURE:`, `CRITICAL BUG:`, a module path, or a client code). The verbatim title lives ONLY in the header line. The description prose beneath it stays in clean exec voice and obeys rules 1, 2, and 5.
5. **No file paths in the narrative.** In the description prose, never reference a path of the form `src/...`, `apps/...`, `.py`, `.ts`, `.tsx`, `/api/...`, or the like. (A path that appears inside a verbatim ticket-title header is allowed under rule 4.)
6. **No customer names without authorization** in the narrative prose. If an upstream summary names a specific client institution, abstract to the segment ("an osteopathic college of medicine requested…"). A client code that appears inside a verbatim title header is allowed under rule 4.
7. **No performance claims with specific numbers** unless the number appears in the upstream summary and is verifiable. Do not invent metrics.
8. **No marketing voice or internal nicknames.** No "thrilled," "game-changing," "the Optimus migration." Abstract to the externally-facing concept.

The `ip-guard` skill runs after the Storyteller. As of v0.8.0 it allows ticket IDs everywhere and skips scanning of `### [ID] Title` header lines, but still blocks internal system names, code artifacts, file paths, MR references, and mechanism vocabulary in the narrative. The Storyteller should still write to all rules to keep the guard's job mechanical rather than rescuing every draft.

## Voice and register

- **Plain English, executive register.** Confident, calm, declarative. Short sentences. No hedging adverbs ("we believe," "we hope to").
- **User-outcome first.** Lead each item's description with what the reader's people can now do, or with what got better. Mechanism comes after, and even then only at the capability level.
- **No marketing voice.** No "we're excited to announce," "thrilled," "delighted," "game-changing," "next-generation," "powerful." This is a briefing, not a press release.
- **No apologies.** Bug fixes are described as improvements ("evaluation submissions now save consistently when…"), not as confessions ("we fixed a bug where…"). Describe the new, correct behavior matter-of-factly.
- **Active voice. Present tense for capabilities. Past tense only for completed events.**
- **No CTAs** in the prose. The reader is an institutional principal, not a website visitor.

## Output structure

The brief is built to be **scannable in 15 seconds and complete in 90 seconds**. A reader who only reads the Key takeaways must walk away with the right institutional picture. A reader who reads the whole document gets one entry per ticket, each headed by the identifier and title the client can quote back to support.

Produce a single markdown document with this exact shape:

```markdown
# Medtrics Release: {Weekday}, {Month} {D}, {YYYY}

## Key takeaways

- {One-line institutional outcome. Lead with who can now do what, or what got better. Present tense.} [{TICKET-ID}]
- {Second outcome.} [{TICKET-ID}]
- {Third outcome.} [{TICKET-ID}]
- {Optional fourth or fifth, only if the release genuinely earned it. Cap at five.} [{TICKET-ID}]
- Heads up: {any awareness item — default-behavior change, new permission, deprecation}. [{TICKET-ID}]

## Summary

{2–4 sentences. What changed in this release at the institutional level. Mention the broad themes — e.g., "evaluation workflows," "rotation scheduling," "duty-hour reporting" — without listing individual items.}

## In this release

### [{TICKET-ID}] {Verbatim ticket title, reproduced exactly}

**{One-sentence bold takeaway stating the institutional outcome.}** {1–3 supporting sentences explaining the change at a capability level. No mechanism leaks. No ticket title vocabulary bleeding into the prose.}

### [{TICKET-ID}] {Verbatim ticket title}

**{Bold takeaway.}** {Supporting sentences.}

{... one `###` block per ticket, in the order below ...}

### [!HEADSUP] [{TICKET-ID}] {Verbatim ticket title}

**{Bold takeaway for the awareness item.}** {1–3 sentences on what it means for the administrator and what, if anything, they need to do.}

## Looking ahead

{Optional single short paragraph — only if upstream summaries genuinely hint at near-term direction. Otherwise omit. Never speculate beyond what upstream content supports.}
```

### Per-ticket entry rules

- **One `###` block per ticket.** Every ticket that produced a user-visible outcome gets its own entry. Do not merge two tickets into one block, and do not drop a ticket that has a real outcome.
- **Header = identifier + verbatim title.** The `###` line is exactly `[TICKET-ID] {verbatim title}`. Reproduce the title character-for-character from the source ticket. Do not clean, shorten, or rephrase it (that is a separate, explicit override — see "Title handling").
- **Bold lede first.** The description opens with a single bold sentence stating the institutional outcome, then 1–3 supporting sentences. The supporting sentences add detail; they do not restate the lede.
- **Awareness items get `[!HEADSUP]`.** Any ticket that changes a default behavior, adds or restricts a permission, or otherwise needs administrator attention is prefixed `[!HEADSUP] ` before its `[ID]`. Downstream consumers use this marker to give the entry visual emphasis. Every `[!HEADSUP]` entry must also have a matching `Heads up:` bullet in Key takeaways.
- **Ordering.** New capabilities and improvements first, then fixes, then any `[!HEADSUP]` awareness entries last.

### Key-takeaways rules

- 3 to 5 bullets. Never fewer than 3 (synthesize if the release is small) and never more than 5 (synthesize harder if the release is large — the Key takeaways are a digest, not a per-ticket list).
- Each bullet is one line: subject + verb + outcome, ending with the `[TICKET-ID]` chip of the item it summarizes. No semicolons. No sub-bullets.
- Lead with the user-outcome, not the domain.
- Every `[!HEADSUP]` entry in the body must have a Key-takeaways bullet prefixed with "Heads up:".
- No verbs from the copy-audit ban list. No marketing voice. No mechanism vocabulary.

### Title handling (verbatim by default)

The ticket title in each header is reproduced **verbatim** by default — this is the shipped, approved behavior, so clients see the exact title they will find in support and in their own tickets. This means internal-flavored prefixes (`FEATURE:`, `CRITICAL BUG:`), redo markers (`Redo M1-1142`), client codes (`[KSHRC]`), and even a security class in the title appear as-is.

If the operator explicitly asks for cleaned titles, strip the internal tags and soften sensitive wording — but only on explicit request. Never silently rewrite a title.

Do not include:

- A table of contents
- A "by the numbers" / metrics block (specific verifiable numbers belong inline in the relevant takeaway or entry)
- Author bylines, a "powered by" footer, a "thank you" closer
- Any masthead other than the title line

## Downstream consumers

This markdown is the customer-facing release-notes **document** produced by `/deploy-notes` and by the deploy half of `/notes` (which writes it to `.claude/outputs/notes/deploy/`). It is a finished document — paste into email or a help center, or keep as the paper-trail copy. (The combined `/release-slack` Slack post is composed by the separate `internal-storyteller` skill from its own analyzer blocks and does **not** consume this markdown.)

The structure is designed to be referenced entry-by-entry, so keep every entry self-contained:

- Each `### [ID] {verbatim title}` header identifies one change; a reader (or an account manager) can cite any change by its `[ID]`.
- The header line must identify the ticket without its body, and the body (bold lede + supporting sentences) must read correctly without the neighboring entries.
- `## Summary` is the 1–2 sentence orientation line; `## Key takeaways` and `## Looking ahead` are brief-only framing.

Keep the markdown clean and content-only — headers, short prose, and the `[ID]` chips. Any downstream reformatting (mrkdwn conversion, escaping, chunking) is the consumer's job, not this skill's.

## Length budget

- Key takeaways: each bullet ≤ 20 words (excluding the `[ID]` chip). Whole section ≤ 90 words.
- Summary: 50–90 words.
- Each per-ticket entry (bold lede + supporting sentences): 40–90 words. The header does not count against the budget.
- "Looking ahead": 30–60 words (if present).
- Whole document: no hard ceiling — it scales with ticket count — but every entry stays within its per-entry budget. Synthesize each entry; do not transcribe the ticket.

## Bad and good examples

**Bad — narrative leaks technical stack:**

> ### [M1-583] EvaluationSerializer bulk support
> We refactored the EvaluationSerializer to eliminate the N+1 query on get_queryset and dropped the redundant index migration.

**Good — verbatim title header, clean exec-voice body:**

> ### [M1-583] FEATURE: Bulk-assign evaluators across a rotation
> **Program directors can now bulk-assign evaluators across an entire rotation in a single action.** The affected screens load noticeably faster, which shortens the prep work before evaluations open.

---

**Bad — marketing voice, no bold lede:**

> ### [M1-720] New dashboard
> We're thrilled to announce a powerful new dashboard that will revolutionize how DIOs manage their programs!

**Good:**

> ### [M1-720] Program overview dashboard
> **The program-overview screen now surfaces the metrics most DIOs check first.** Evaluation completion rate, open duty-hour exceptions, and current rotation occupancy appear without a drill-down. Existing dashboards continue to work as before.

---

**Bad — apologetic, mechanism-leaking:**

> ### [M1-844] Fix duty-hour race condition
> We're sorry some duty-hour entries weren't saving. We fixed the race condition between the autosave and submit handlers.

**Good (note: the verbatim title may still say "BUG"; only the body must be clean):**

> ### [M1-844] BUG: Duty-hour entries not saving on intermittent connections
> **Duty-hour entries now save consistently on slower or intermittent connections.** Residents entering hours during shift handoffs no longer need to retry submissions.

## Inputs the Storyteller receives

The orchestrating command provides the Storyteller with:

1. A list of per-ticket summary blocks, each containing:
   - The ticket **identifier** (e.g., `M1-1054`, `M2-193`, `MOB-2`)
   - The ticket's **verbatim title** (exactly as in the source ticket)
   - A short user-outcome sentence (one or two sentences)
   - A change kind (`NEW`, `IMPROVED`, `RESOLVED`, `AWARENESS`)
2. The release date (Monday of the deploy week).
3. The release version string if one is in use (optional; omit from output if not provided).

The identifier and title come straight from the gathered Optimus data (`issues[].identifier`, `issues[].title`); the analyzer agents do not need to invent them. The Storyteller does not read raw ticket descriptions or code diffs — those are too tempting to leak from.

## Process

1. Read all per-ticket summary blocks, each with its identifier, verbatim title, outcome, and kind.
2. For each ticket with a user-visible outcome, write one `### [ID] {verbatim title}` entry: a bold one-line lede stating the institutional outcome, then 1–3 supporting sentences at the capability level. Do not restate the lede. Do not let the title's internal vocabulary bleed into the prose.
3. Mark any awareness ticket (default-behavior change, new/changed permission) with `[!HEADSUP] ` before its `[ID]`, and give it a matching `Heads up:` Key-takeaways bullet.
4. Order entries: new/improved first, fixes next, `[!HEADSUP]` awareness entries last.
5. Write the `## Summary` — three to four sentences naming only the highest-level themes. The Summary sets the institutional frame; it is not a re-statement of Key takeaways.
6. Write `## Key takeaways` last. 3–5 bullets, each leading with a user-outcome and ending with its `[ID]` chip; include the `Heads up:` bullet(s).
7. Read the whole document top to bottom. Strip every adverb and hedging phrase you can. Confirm every `###` entry opens with a bold sentence and that its header is the verbatim title.
8. Hand the draft back to the orchestrator. The orchestrator runs `copy-audit` for sentence-level polish, then `ip-guard` for confidentiality validation, before any Slack action.

## Division of labor with copy-audit

The Storyteller and the `copy-audit` skill are deliberately decoupled:

- **Storyteller** decides *what to say* — which outcomes matter, how to head each entry, what the institutional reader needs to know.
- **Copy-audit** decides *how to say each sentence cleanly* — strips AI-sounding language, bloated verbs, dead openings, negative-parallelism patterns, and engagement bait. Copy-audit does not touch the verbatim title headers.

Write the content in your own voice; trust copy-audit for the sentence-by-sentence cleanup.

## On guard failure

If `ip-guard` returns violations, the orchestrator passes the violation list back to the Storyteller. The Storyteller rewrites the offending sentences in the same exec voice and resubmits. Because ticket IDs and title-header lines are now exempt, a guard failure almost always points at the narrative prose (a leaked system name, mechanism word, or file path in a description). Fix the prose, not the header. The full storyteller → copy-audit → ip-guard loop may iterate up to three times before stopping and surfacing the failure to the user.

## What this skill never does

- Does not compose or post the Slack messages — the root-index + thread-reply composition, lint, and delivery are separate orchestration steps in `/release-slack`.
- Does not call Slack, GitLab, the issue tracker, or any external system.
- Does not read raw code diffs.
- Does not invent items that aren't in the upstream summaries. If upstream is empty, the document says "No changes affecting institutional workflows in this release."
- Does not rewrite or clean a ticket title unless the operator explicitly asks for cleaned titles.
