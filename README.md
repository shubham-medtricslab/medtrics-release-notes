# medtrics-release-notes

Release communications for Medtrics. As of 0.11.0, `/release-slack` posts **one combined message** to the internal `#dream-team` channel per deploy: each ticket card carries a **client-portable exec-voice lede** (what an institutional administrator can now do) plus the **internal detail** (ticket identifiers, developer Slack `@`-mentions, client institution names, Optimus URLs). The internal team ports the lede to clients themselves — there is no separate client-channel post. The product is Slack message generation and delivery: gather tickets → write both voices → lint → post one linted announcement (a compact root-post index plus threaded detail). Nothing else.

## What it delivers

- **`/release-slack`** runs the combined pipeline: pulls upstream task and code-change data → resolves developer Slack IDs and client tags → generates per-ticket summaries via parallel analyzer agents, each emitting **both** a client-safe `EXEC_SUMMARY` and a technical `INTERNAL_SUMMARY` → composes the Slack root-post index + thread replies via the bundled **internal-storyteller** skill (each card leads with the exec lede, then the internal detail) → lints them with `slack_lint.py` → previews to you → posts to `#dream-team` on confirmation (root first, then each reply into the thread). No IP guard: the message is internal by nature, and the exec lede's client-safety is enforced upstream by the analyzer's voice rules.
- The remaining commands (`/notes`, `/deploy-notes`, `/integration-notes`) are building blocks that produce markdown files only. The customer-facing **storyteller**, **copy-audit**, and **ip-guard** skills still back `/deploy-notes` and `/notes` for non-Slack markdown delivery.

### The message format

`/release-slack` posts **one root message that is an index** — numbered one-line entries under `*Enhancements & New Features*` and `*Bug Fixes*` headers — with all long-form detail in **thread replies** (one per section; escalations get their own reply). Never a second top-level message.

- Root header: `:rocket: *Release Notes — <date>*`. Root index entry: `1. M1-986 · <client-facing headline>` (no developer mention in the root), plus a `Clients to notify:` line at the bottom of the root.
- Thread card (bullet order fixed):

  ~~~
  *1. M1-986 · <client-facing headline — written by the engineering-to-client skill>*
  *_For clients:_* <client-portable paragraph — written by the engineering-to-client skill; safe to copy to a client>
  • Ticket: M1-986
  • Developer: <@dev> (Dev Name)
  • Internal: <technical summary — code, tables, RPCs, migrations, endpoints>
  • URL: <Optimus task link>
  • MRs: <links>            (PRs: on the Oracle track; omitted when none)
  • Clients: <client code(s)> | TBC (platform-wide)
  ~~~

- The card title, the `For clients` paragraph, and the `Clients` value are finalized by the **engineering-to-client** skill in one pass over all tickets after the analyzers run (the internal Optimus title stays in the analyzer block for traceability but is not rendered) (`/release-slack` Step 5e, `/oracle-release` Step 6b). A named client's item that is still unresolved is flagged at the preview as "client-specific — send to the named client only" rather than broadcast.
- Developer @-mentions are resolved from email (with name-match fallbacks) via the bot token — see the Slack setup below — and cached in an optional **Mem developer directory** so each developer is looked up once (see below).

Caps enforced by the lint gate: root ≤ 2,500 chars, each reply ≤ 3,900 chars.

## Commands

| Command | What it does | When to run it |
|---|---|---|
| `/release-slack` | End-to-end combined Monday command. ONE message into `#dream-team` (`SLACK_CHANNEL_INTERNAL`): root index + threaded per-ticket cards, each carrying a client-portable exec lede plus the internal detail (ticket IDs, dev @-mentions, client tags, Optimus URLs). The team ports the lede to clients. | After every production deploy. |
| `/oracle-release` | End-to-end **Oracle** command, same shape as `/release-slack`: ONE combined message into `SLACK_CHANNEL_ORACLE` (`#pod-oracle`) — root index + per-ticket cards (exec lede + internal detail incl. PR links). Sources: Optimus (ORC tickets) + **GitHub** PRs. Exec ledes IP-guarded as a safety net. | After every medtrics-oracle deploy. |
| `/notes` | Both note types (deploy / integration) as markdown files in parallel. | Paper-trail backup or when you need both documents at once. |
| `/deploy-notes` | Client-facing release notes markdown only. | Non-Slack delivery (paste into email, help center). |
| `/integration-notes` | Integration-partner markdown only. | Separate partner channel. |
| `/kb-articles` | One Intercom **KB article draft** (.docx + PDF + markdown) per shipped legacy **feature** ticket, in the Medtrics KB format. Grouped by persona × surface; screenshots captured live on the demo tenant and ringed by script; prose by a pinned Sonnet writer per group; every article checked by the format gates G1–G7; history in Mem so no ticket is drafted twice. See [KB articles](#kb-articles). | Manually, e.g. weekly: `/kb-articles since <date>` or `pending`. |

> `/client-copy` was removed in 0.17.0 — the `engineering-to-client` skill now runs inside both release pipelines. Pasting ticket blocks and asking for the client-facing version still triggers the skill on its own.

## Bundled skills

The plugin ships with eleven skills, all packaged inside the plugin so installing it gives you the full pipeline in one shot:

| Skill | Purpose |
|---|---|
| `medtrics-release-notes:storyteller` | Transforms per-ticket release summaries into a cohesive executive-voice narrative for institutional administrators (DIOs, deans, IT directors). Decides *what to say*. Never names internal systems, code artifacts, or implementation mechanisms. Its markdown maps mechanically onto the Slack root-index + thread-reply chunks. Customer-facing. |
| `medtrics-release-notes:internal-storyteller` | Composes the combined announcement for **both** `/release-slack` (`#dream-team`) and `/oracle-release` (`#pod-oracle`) from per-ticket analyzer blocks: a root-post index (`ID · title` lines under `*Enhancements & New Features*` / `*Bug Fixes*`, plus a `Clients to notify:` line) and per-section thread cards in the fixed layout above (`*n. ID · title*`, `For clients:` paragraph, then Ticket / Developer / Internal / URL / PRs-or-MRs / Clients). Renders the `For clients` paragraph and `Clients` value verbatim — it never rewrites client copy. Parameterized by output prefix. No IP guard of its own. |
| `medtrics-release-notes:dev-directory` | **Optional** Mem-backed cache of developer Slack ids — one collection (`Release Notes Developer Directory`), one note per developer (name, emails, GitHub/GitLab login, Optimus assignee id, Slack id, resolving tier). Both release commands read it before the Slack resolver runs and write every newly resolved developer back immediately. Active only when the Mem MCP is connected; every Mem failure degrades to the resolver-only behavior with a one-line notice — never a hard error, never blocks the post. Parsing/matching/rendering is `scripts/dev_directory.py`. |
| `medtrics-release-notes:pr-overrides` | **Optional** Mem-backed store of human PR picks for `/oracle-release` — one collection (`Oracle PR Overrides`), one note per ORC ticket (ticket, PR number + URL, who, when, why). The gather script applies the overrides before the primary rule; the preview lets the user reply `ORC-232 → 415` to record one and re-run. Overrides only, never a cache. Active only when the Mem MCP is connected; without it a pick still applies for the current run (`--pr-override-inline`). Parsing/rendering is `scripts/pr_overrides.py`. |
| `medtrics-release-notes:release-archive` | **The** release archive — Mem-backed, **required**, and the only copy of a posted release outside Slack (nothing is written to disk). One collection whose **name you set** in `.env` (`MEM_RELEASE_ARCHIVE_COLLECTION`, default `Release Notes Archive`; created under that exact name if absent), **one note per release session**: a trace header (date, product, channel, root ts, Slack permalink, the posted ticket list with client-facing titles, categories, client codes, and PR/MR URLs) **plus the posted root and every thread reply verbatim**. Both release commands check that Mem is connected in Step 1 (hard stop otherwise) and write the note once the post is complete — in the session, or on the resume run that finishes it. Duplicates from a re-post are tolerated and warned, never merged. A failed write prints the note in chat for a manual paste, never to disk. Rendering is `scripts/release_archive.py`; the permalink comes from `scripts/slack_upload.py --get-permalink`. |
| `medtrics-release-notes:oracle-storyteller` | **Parked (unused since 0.12.0).** Formerly composed the separated `/oracle-release` design (a "safe to forward" client block + internal index, root assembled by `scripts/assemble_oracle_root.py`). Left in the repo in case that design is wanted again; `/oracle-release` now uses `internal-storyteller`. |
| `medtrics-release-notes:copy-audit` | Sentence-level polish pass that runs between storyteller and ip-guard. Strips AI-sounding language, bloated verbs (`serves as`, `boasts a`, `helps to`), dead openings (`Furthermore`, `Moreover`), negative-parallelism patterns (`Not X. Y.`), decorative metaphor verbs (`mapped`, `surfaced`, `unpacked`), and engagement bait. Customer-facing only. |
| `medtrics-release-notes:ip-guard` | Deterministic safety net that scans customer-facing output — the deploy markdown AND the composed Slack root + replies — for internal-system names, code-artifact jargon, MR identifiers, file paths, and architecture mechanism vocabulary. Blocks delivery on violation. Iterates with the storyteller + copy-audit up to 3 times to remediate. **Not** run against the internal-storyteller output — internal vocabulary is expected there. |
| `medtrics-release-notes:kb-ledger` | Mem history for `/kb-articles`: the `KB Article Ledger` (one note per legacy feature ticket — article title, run, screenshots, tenant tally, prior article, files, tokens; plus one note per run) and the `KB Catalogue` (published Intercom articles, seeded once from the FAQ Content Planner export). Required by `/kb-articles`. Rendering, parsing, matching and the one-time seeds are `scripts/kb/kb_ledger.py`. |
| `medtrics-release-notes:kb-capture` | Live screenshot capture on `demo.medtrics.sg` with Playwright: a **person** signs in once per persona (`kb_capture.py login`), the saved session is reused headless, one visit per surface, rings placed from the page's own element box, OCR-verified when tesseract is present. Covers session expiry (stop, keep shots, `resume.json`) and mapping surfaces in `scripts/kb/kb_routes.json`. |
| `medtrics-release-notes:engineering-to-client` | Owns the client-copy voice rules. **Pipeline mode:** both release commands run it once over the analyzer blocks (after the analyzers, before composition / the Oracle IP guard) to write each card's client-facing title, finalize its `For clients:` paragraph (1–3 sentences, no jargon, no client names), confirm the `Clients:` value verbatim (`TBC` = platform-wide), and add a `CLIENT_FLAG` on any named-client item that is still unresolved. **Standalone mode:** paste engineering ticket blocks and get back only the plain-English client description per item. Never invents a capability, fix, or client code. |

Two deterministic scripts back the pipeline gates: `scripts/ip_guard.py` (confidentiality) and `scripts/slack_lint.py` (mrkdwn correctness — bare `<@U…>` mentions, labeled links, no `**bold**`/`#`/fences, escaped `&` `<` `>`, blank line before section headers, and the root/reply char caps). Both run before the human preview, with a fix-and-relint loop (max 3 iterations).

`/release-slack` invokes the **internal-storyteller** skill and the `slack_lint.py` gate. The storyteller, copy-audit, and ip-guard skills back the customer-facing markdown commands (`/deploy-notes`, `/notes`); each can also be invoked standalone.

## Engineering commands moved out

For code review, plan, rebase, MR-feedback, bug-report, branch context, and the internal dev-notes changelog, install the companion plugin **`medtrics-dev-workflow`**.

## Host-repo requirements

The plugin reads Optimus + GitLab over HTTP (no git access needed). It only works inside a Medtrics monorepo checkout with the conventions in the legacy CLAUDE.md (`src/apps/...`, `develop` base branch, ticket id regex `M1-\d+ | GEN-\d+ | DEV-\d+ | RD-\d+ | do-\d+`).

## .env keys (in the host-repo root, not the plugin folder)

| Key | Required for | Notes |
|---|---|---|
| `OPTIMUS_API_KEY` | all commands | |
| `GITLAB_TOKEN` | all commands | |
| `GITLAB_PROJECT_ID` | all commands | |
| `GITLAB_URL` | optional | defaults to `https://gitlab.com` |
| `SLACK_CHANNEL_INTERNAL` | `/release-slack` | channel id `C1BCUHXT9` (= `#dream-team`) — the combined post's destination |
| `SLACK_CHANNEL_RELEASE` | no longer used | retired in 0.11.0 when the client-channel post was folded into the combined `#dream-team` message; safe to leave in `.env` |
| `SLACK_BOT_TOKEN` | `/release-slack` | `xoxb-...` bot token. **NOT** a user token (`xoxp-`). The bot must be invited to `#dream-team`. |
| `MEM_KB_LEDGER_COLLECTION` / `MEM_KB_CATALOGUE_COLLECTION` | optional | `/kb-articles` Mem collection names (defaults `KB Article Ledger` / `KB Catalogue`) |
| `KB_SESSION_DIR` / `KB_OUTPUT_DIR` / `KB_FONT` | optional | `/kb-articles`: where demo sessions are saved (default `.kb-sessions/`, gitignored), the output root override, and the badge font |
| `MEM_RELEASE_ARCHIVE_COLLECTION` | optional | Name of the Mem collection that receives the archive note for every posted release (see [Mem release archive](#mem-release-archive)). Created under that exact name if absent; defaults to `Release Notes Archive` when unset or blank. Shared by `/release-slack` and `/oracle-release`. The key is optional; the **Mem MCP connection is required** by both release commands |

## Slack bot setup (one-time, ~10 minutes)

**Posting** does NOT use the Slack MCP — `/release-slack` and `/oracle-release` talk to the Slack Web API directly using the bot token in your `.env`, the same way the plugin uses `GITLAB_TOKEN` for GitLab. The bot setup below is what you must do.

**Developer @-mentions** use the **same bot token** — no Slack MCP needed. `scripts/slack_resolve_users.py` resolves each developer's Slack ID via the Slack Web API in three tiers: (1) **`users.lookupByEmail`** — exact, on the Optimus assignee email (classic) or the GitHub PR **commit-author** email that the Oracle gather resolves (non-noreply); (2) a **unique full-name match** against `users.list`; (3) a **fuzzy full-name match** (`difflib`, default threshold 0.72) that tags only a clear single best, leaving near-ties unresolved. Tiers 2–3 skip deactivated members and bots. Best-effort — an unresolved developer just renders as a plain name; nothing blocks. Every mention renders as `@Name (Author Name)` so a human can verify a fuzzy match at the preview. This requires two extra scopes on the bot token:

- `users:read.email` — look a developer up by email (exact match)
- `users:read` — the name-match fallback

Add those under **Bot Token Scopes** (same as the posting scopes below) and reinstall. A mention renders as `@Name (Author Name)` — the Slack mention followed by the ticket's author name in parentheses.

1. Go to https://api.slack.com/apps and click **Create New App** → **From scratch**.
2. Name it something like `Medtrics Release Bot` and pick your Medtrics workspace.
3. Under **OAuth & Permissions** → **Scopes** → **Bot Token Scopes**, add:
   - `chat:write` — to post the root message and its thread replies.
   - `channels:read` — to resolve `#channel-name` to a channel id (skip if you'll use the channel id directly).
   - `groups:read` — only if a destination is a **private** channel configured by name.
   - `users:read.email` — to resolve a developer's `@`-mention by email.
   - `users:read` — the name-match fallback for `@`-mentions.
4. Click **Install to Workspace** at the top of OAuth & Permissions, approve the install.
5. Copy the **Bot User OAuth Token** (starts with `xoxb-`).
6. Invite the bot to `#dream-team`: `/invite @Medtrics Release Bot`.
7. Add to your host-repo `.env`:
   ~~~
   SLACK_BOT_TOKEN=xoxb-12345...
   SLACK_CHANNEL_INTERNAL=C1BCUHXT9     # #dream-team (combined /release-slack destination)
   ~~~

Prefer the channel id over the name: ids never change, names can be renamed. Right-click the channel in Slack → View channel details → channel id at the bottom.

## Typical Monday workflow

~~~
$ cd ~/code/medtrics
$ git checkout develop && git pull
$ # production deploy completes
$ # open Cowork
/release-slack
~~~

`/release-slack` then:
1. Pulls all deploy-ready tickets and their code-change data in parallel — plus Optimus assignee and GitLab MR author info.
2. Resolves each developer's Slack user ID: first from the optional **Mem developer directory** (if Mem is connected), then via `slack_resolve_users.py` for whoever is left (email → exact name → fuzzy name, best-effort; falls back to plain name), and stores the newly resolved developers back into Mem immediately.
3. Extracts client tags (`KFSHRC`, `MSUCVM`, `ADTALEM`, etc.) from the ticket description; defaults to `TBC` for platform-wide work.
4. Runs the per-ticket analyzer agents — each ticket becomes **two** summaries: a draft client-safe `EXEC_SUMMARY` and a technical `INTERNAL_SUMMARY` (mechanism specifics, client motivation).
5. Runs the **engineering-to-client** skill once over all blocks to write the client-facing card title, finalize the `For clients` paragraph, and confirm the `Clients` value, flagging any named-client item that is still unresolved.
6. Invokes the **internal-storyteller** skill to compose the root index (`ID · title` under `*Enhancements & New Features*` / `*Bug Fixes*`, plus `Clients to notify:`) and the per-section thread replies in the card layout above.
7. Lints the root and every reply with `slack_lint.py` (fix-and-relint, max 3).
8. Shows you the preview — root post + every thread reply, plus the developer-directory tally and any client-specific flags.
9. On your `y`, posts the root to `SLACK_CHANNEL_INTERNAL` (`#dream-team` / `C1BCUHXT9`), captures its `ts` and the resolved channel id, and posts each reply into the thread with `--thread-ts`. Never a second top-level message.
10. Once the post is complete (root and every reply — in this session, or on the resume run that finishes it), writes **one Mem note for the release** into the **release archive** collection — date, channel, root `ts`, Slack permalink (via `slack_upload.py --get-permalink`), the posted ticket list, **and the posted root + replies verbatim**. That note is the only archive of the post; nothing is written to disk. Mem must be connected before step 1 runs.

The combined post deliberately bypasses the IP guard and the copy-audit skill — the message is internal, and the `For clients` paragraph's client-safety is enforced upstream by the engineering-to-client pass. To port to clients, the team copies the `_For clients:_` lines from the thread.

## Mem developer directory (optional)

If the **Mem MCP** is connected in your Claude session, both release commands cache developer Slack ids in one Mem collection, **`Release Notes Developer Directory`** — one note per developer:

~~~
Developer: Chris Tokodi

**Slack ID:** U02AB0913
**Emails:** chris@medtricslab.com
**GitHub login:** ctokodi
**GitLab username:** —
**Optimus assignee id:** 42
**Resolved via:** email
**First seen:** 2026-09-02
**Last used:** 2026-09-02
**Source:** medtrics-release-notes /oracle-release
~~~

Before the Slack resolver runs, each ticket's developer is matched against the directory (exact email → GitHub/GitLab login → unique full name) and pre-filled; the resolver only queries Slack for the rest; every newly resolved developer is written back **immediately**. Nothing in `.env` is needed — the collection is created on first use. If Mem is not connected, or any Mem call fails, the run prints one line and continues exactly as before: **no hard errors, never blocks the post**.

To correct a wrong mention, edit that developer's note (`Slack ID`) or trash it in Mem — the next run picks it up. `slack_resolve_users.py --force` bypasses the cache for a single run. Mem is per user, so each teammate builds their own directory; it holds work identifiers only (names, work emails, logins, Slack ids).

For a specific ticket set:
~~~
/release-slack tickets M1-583,GEN-128592,M1-848
~~~

Dry-run (see what would post without posting):
~~~
/release-slack dry-run
~~~

## Mem PR overrides (optional)

`/oracle-release` only. When the automatic PR sweep is wrong for a ticket — two open PRs and only a person knows which one ships, a PR whose branch and title never carried the ticket id, a ticket older than the ~500-PR window — you record the right PR **once** and every later run honors it. If the **Mem MCP** is connected, those picks live in one Mem collection, **`Oracle PR Overrides`** — one note per ticket:

~~~
PR Override: ORC-232

**Ticket:** ORC-232
**PR:** 415
**PR URL:** https://github.com/medtrics/medtrics-oracle/pull/415
**Resolved by:** shubham@medtricslab.com
**Resolved at:** 2026-09-02T12:00:00Z
**Reason:** two open PRs; 416 is a spike branch
~~~

Before the gather, the command dumps the collection and passes it to `gather_notes_data_oracle.py --pr-overrides`; an override always wins over the sweep (the PR is fetched by number if the sweep did not carry it, and marked `matched_via: manual`). At the preview, the **PR discovery** block names every ticket that needs a human — `Multiple open PRs`, `No PR found`, `No PR within the window` — and you reply `ORC-232 → 415`. The command runs `scripts/pr_overrides.py pending` to compute the Mem write (`create`, or a version-checked `update`), performs it, re-runs the gather, and shows the preview again. Mem is **overrides only**, never a cache of sweep results.

Pin the collection id in `.env` as `MEM_PR_OVERRIDES_COLLECTION_ID` to skip the title lookup; when it is absent the command resolves (or creates) the collection by title and prints the id to pin. If Mem is not connected, or any Mem call fails, the run prints one line and continues on the sweep alone — a preview pick still applies for that run through `--pr-override-inline ORC-232=415`, it just is not remembered. **No hard errors, never blocks the post.** To change a decision by hand, edit the note's `PR` line (or trash the note) in Mem; the next run picks it up.

## Mem release archive

Every release that `/release-slack` or `/oracle-release` posts is archived in **one place: Mem**. There is no markdown copy on disk — the `.claude/outputs/notes/release-slack/` and `.claude/outputs/notes/oracle/` files that versions before 0.20.0 wrote are gone. Because Mem is the only archive, both commands **require the Mem MCP**: Step 1 checks it alongside the `.env` keys and stops before any gathering when it is not connected (`dry-run` included). The other two Mem uses — the developer directory and PR overrides — stay best-effort.

Once a post is complete (root and every reply — in the session, or on the resume run that finishes it), the command writes **one note per release session** into a collection whose **name you choose** in `.env`:

~~~
MEM_RELEASE_ARCHIVE_COLLECTION=Release Notes Archive     # default when unset or blank
~~~

If no collection with that name exists, the command creates it under exactly that name. The note has two parts — a **trace header** and the **posted message**:

~~~~
Release: 2026-09-02 · oracle · #pod-oracle

**Date:** 2026-09-02
**Product:** oracle
**Channel:** #pod-oracle
**Channel id:** C0XXXXXXX
**Root ts:** 1725300000.123456
**Permalink:** https://medtrics.slack.com/archives/C0XXXXXXX/p1725300000123456
**Item count:** 3
**Tickets:** ORC-232, ORC-234, ORC-240
**Clients:** KFSHRC, CVM-NCSU

- ORC-232 · Average score shown on completed evaluations · ENHANCEMENT · KFSHRC · https://github.com/medtrics/medtrics-oracle/pull/456, https://github.com/medtrics/medtrics-oracle/pull/457
- ORC-234 · Export timeout on large cohorts · BUG FIX · TBC · https://github.com/medtrics/medtrics-oracle/pull/455
- ORC-240 · Rotation scheduling presets — heads up · FEATURE · TBC · no PR

## Posted message

### Root

~~~
:rocket: *Release Notes — Wednesday, September 2, 2026*
…the root post exactly as posted…
~~~

### Reply 1

~~~
*1. ORC-232 · Average score shown on completed evaluations*
…the thread reply exactly as posted…
~~~
~~~~

To trace a ticket: open the collection in Mem, search the id (`ORC-232`, `M1-1457`), open the release note, click the permalink — or read the posted text right there. The ticket list is read from the per-ticket summaries file — the set that actually posted, with client-facing titles — and the transcript is the exact root and reply files `slack_upload.py` sent, in post order, each in a `~~~` fence so Slack's own code blocks survive. The permalink comes from one `chat.getPermalink` read (`scripts/slack_upload.py --get-permalink`); when that read fails the line is omitted, never constructed. `Product:` is `medtrics` or `oracle`; one shared collection holds both tracks. Every completed session writes one new note — a re-post in a fresh session yields a second note with a reminder to trash the older one; nothing is merged or updated in place. Renaming the key later starts a fresh collection under the new name (old notes stay put); `scripts/release_archive.py pending` prints the resolved name every run. If the Mem write fails after the post, the command prints the whole note in chat so you can paste it into the collection by hand — it never writes it to disk and never re-posts to Slack.

## Oracle track (`/oracle-release`)

The **medtrics-oracle** product (React + Supabase app) gets its own release track. As of 0.12.0 it uses the **same shape as `/release-slack`** — one combined internal post to `#pod-oracle` with a root index plus per-ticket cards — differing only in its data sources: code lives on **GitHub** (`medtrics/medtrics-oracle` PRs) instead of GitLab, and tickets carry the `ORC-` prefix.

~~~
/oracle-release tickets ORC-101,ORC-115     # explicit ticket list (primary mode)
/oracle-release status                      # all Oracle tickets in the five deploy lanes (product=oracle)
/oracle-release tickets ORC-101 dry-run     # discovery preview only
~~~

### Message anatomy

Identical to `/release-slack`, composed by the shared **`internal-storyteller`** skill:

- **Root index**: `:rocket: *Release Notes — <date>*`, a one-sentence theme line, numbered `ORC-id · title` entries under `*Enhancements & New Features*` / `*Bug Fixes*`, plus a `Clients to notify:` line.
- **Thread cards**: one per ticket in the fixed layout — `*n. ORC-id · client-facing headline*`, `*_For clients:_* <paragraph>`, then `• Ticket: <ORC-id>`, `• Developer: <@dev> (Name)`, `• Internal: <technical summary>`, `• URL: <Optimus link>`, `• PRs: <GitHub PR links>`, `• Clients: <codes> | TBC (platform-wide)`. SECURITY items get their own escalation reply.
- **Developer directory**: PR authors are matched against the optional Mem directory by GitHub login as well as email and name, so a developer whose commits carry a privacy-masked email is still tagged after the first run that resolves them.
- **Release archive**: once the post is complete, one Mem note for the release (product `oracle`, `#pod-oracle`, root `ts`, permalink, the posted ORC list, and the posted root + replies verbatim) lands in the same archive collection the classic track uses — see [Mem release archive](#mem-release-archive). It is the only copy outside Slack; Mem is required at Step 1.

The one intentional difference from `/release-slack`: because Oracle is a private, pre-GA product, the `For clients` paragraphs still pass **`ip_guard.py`** as a safety net after the engineering-to-client pass (over the paragraphs only — the internal detail and PR links are internal and unguarded). `copy-audit` is not run; its rules are baked into the engineering-to-client voice contract.

> The former separated design (a "safe to forward" client block assembled by `oracle-storyteller` + `assemble_oracle_root.py`) is retired. Those files stay **parked** in the repo (unused) in case that design is wanted again.

### Oracle .env keys

| Key | Required for | Notes |
|---|---|---|
| `OPTIMUS_API_KEY` | `/oracle-release` | same key as the classic track |
| `GITHUB_TOKEN` | `/oracle-release` | **classic PAT** (`ghp_…`) with the `repo` scope. If the org uses SAML SSO, the token must be **SSO-authorized** for it — auth failures on the private repo surface as 404/422, not 403 (the gather script's startup canary catches this). |
| `GITHUB_REPO` | `/oracle-release` | `owner/repo` **or** a full URL (`https://github.com/owner/repo[.git]`) — the script normalizes either form |
| `GITHUB_API_URL` | optional | defaults to `https://api.github.com` |
| `SLACK_CHANNEL_ORACLE` | `/oracle-release` | channel id preferred, or `#pod-oracle` |
| `ORACLE_REPO_PATH` | optional | absolute path to a local `medtrics-oracle` checkout — lets analyzer agents read related source files for context when a diff is sparse, mirroring the classic track's codebase-context behavior |
| `MEM_PR_OVERRIDES_COLLECTION_ID` | optional | UUID of the Mem collection `Oracle PR Overrides` (see [Mem PR overrides](#mem-pr-overrides-optional)). When empty, the command resolves the collection by title and prints the id to pin here |
| `MEM_RELEASE_ARCHIVE_COLLECTION` | optional | Name of the Mem collection that receives the archive note for every posted release (see [Mem release archive](#mem-release-archive)); the same key and collection as the classic track. Defaults to `Release Notes Archive`. The key is optional; the Mem MCP connection is required |
| `SLACK_BOT_TOKEN` | `/oracle-release` | the same bot token as the other Slack commands — **invite the bot to the Oracle channel** (`/invite @Medtrics Release Bot` in `#pod-oracle`) or the post fails with `not_in_channel` |

### PR discovery

ORC ids live primarily in **branch names** (`fix/orc-232/…`), which GitHub search does not index — so the gatherer's primary discovery is **one PR-list sweep per run** (`/pulls?state=all&sort=updated`, up to ~500 most-recently-updated PRs) matched locally against every requested ticket by branch name + title. A per-ticket `/search/issues` secondary covers PRs older than the sweep window.

**Several PRs per ticket is normal**, so every matching PR is kept in `mrs[]` — ordered **primary-first** — and each ticket gets a `pr_discovery` block with an explicit decision:

| Decision | Meaning |
|---|---|
| `override` | A human pick from the Mem `Oracle PR Overrides` collection (or `--pr-override-inline`) — always wins |
| `matched` | Primary chosen by the rule: one open PR → it; none open → the newest `merged_at`; two or more open → the single non-draft |
| `flagged_ambiguous` | Two or more open non-draft PRs — `candidates` lists them; posting is not blocked, the preview invites a pick |
| `no_pr_yet` | Nothing found by sweep, search, or override; the ticket ships without code context (never silently omitted) |
| `no_pr_in_window` | Nothing found **and** the sweep window was full **and** the ticket predates the oldest swept PR — the PR may exist beyond the window; an override is the remedy |

Each `mrs[]` entry keeps the classic keys and adds `number`, `branch`, `state`, `draft`, `merged_at`, `updated_at`, and `matched_via` (`branch` | `title` | `manual`). The output JSON also carries a top-level `pr_discovery` — sweep stats (`pr_count`, `pages`, `window_full`, `oldest_updated_at`, `failed`), a count per decision, and every warning verbatim — so the command reads results from the file rather than scraping stderr. A failed sweep is reported as "sweep failed", never as "no PRs". One visible consequence: on a multi-PR ticket the developer `@`-mentioned on the card is now the **primary (or override) PR's author** — previously it was the most-recently-updated PR's author.

Two things to know:

- **Optimus's own "PR" field is not readable.** Oracle tickets carry a PR field in the Optimus UI, but the API does not expose it in task payloads, so the pipeline cannot use it — PR links come from the GitHub matching above plus your overrides. When a ticket is flagged, has no PR, or is older than the window, the preview says so and you reply `ORC-232 → 415`; the command records it (in Mem when connected) and re-runs the gather.
- **PR diffs are fetched by the gather script through the authenticated GitHub API** and embedded in the gathered JSON — the same "read the actual code changes" behavior as the classic track. The command is hard-forbidden from fetching `github.com` pages (the repo is private; page fetches come back empty); all code context flows from the gathered data.

### Oracle output path

There is none on disk. The posted root + replies are archived as one Mem note (see [Mem release archive](#mem-release-archive)); the only local files are the `/tmp/oracle_*` working files. The classic track (`gather_notes_data_optimus.py`, GitLab, `/release-slack`) is untouched by all of this.

## KB articles

`/kb-articles` drafts one KB article per shipped legacy feature ticket for the Intercom help centre, in the format defined by `scripts/kb/kb_format.py` (measured from the approved M1-1399 reference) and enforced by the gates in `scripts/kb/kb_validate.py`:

| Gate | Refuses when |
|---|---|
| G1 schema | a required key is missing or the wrong shape |
| G2 anatomy | a section can't be filled, the title isn't a task phrase (How to / What / Where / Using / Understanding, ≤ 90 chars), a screenshot has no caption, a consideration has no bold lead-in |
| G3 evidence | a screenshot is unanchored, its label scored below 0.82, or the ringed label isn't the one the step prints in bold; `dom_box` anchors (no OCR) pass in a draft only |
| G4 tenant | a frame was never screened; on publish, any frame that isn't CLEAR and human-confirmed |
| G5 assets | a referenced image is not on disk |
| G6 voice | client copy carries an internal term, MR reference, HTTP status, file path or lane key |
| G7 mode | a draft has no provenance; a publish carries draft chrome or a missing screenshot |

**How a run stays cheap.** Non-features and tickets the ledger already covers are dropped before any GitLab call. Tickets are placed on a surface by rule (backlog map, URL prefix in the diff, template path, module); only leftovers go to one batched Haiku call. Writers see trimmed inputs (description, acceptance criteria, UI strings added in the diff, routes) and shot *labels*, never images or full diffs; their fixed rules live in the agent body so every call shares the cached prefix. Evidence problems are fixed by script; only prose-gate failures go back to the writer, one article at a time, at most twice. One capture visit per surface, one LibreOffice call for all PDFs.

**Before the first run** (once): sign in per persona (`python3 scripts/kb/kb_capture.py login --persona admin|faculty|learner`, a person at a screen — Claude never types passwords), seed the ledger from the October hand-run backlog (`kb_ledger.py seed-legacy`), and, when the FAQ Content Planner export is available, the catalogue (`kb_ledger.py seed-catalogue --csv kb2.csv`). `python3 scripts/kb/kb_doctor.py` shows what this machine can do: python-docx and Pillow are required; Playwright (screenshots), tesseract (OCR-verified anchors), LibreOffice (PDF) and pypdf (PDF checks) each degrade gracefully when absent.

**Drafts only.** Publishing needs a person to confirm each screenshot is free of tenant data (`tenant_confirmed_by`), then `build_kb_doc.py publish`.

## Output paths

Only the markdown-document commands write to disk, under `.claude/outputs/notes/` in your host repo:

~~~
.claude/outputs/notes/
├── deploy/
│   └── release-notes-2026-05-19.md
└── integration/
    └── integration-notes-2026-05-19.md
~~~

`/kb-articles` writes its drafts to `$KB_OUTPUT_DIR`, else `/mnt/user-data/outputs` (Cowork), else `.claude/outputs/kb/`, under `kb-articles/<run_id>/` (per-ticket folders, `_provenance/`, `RUN-SUMMARY.md`, a zip).

`/release-slack` and `/oracle-release` write **nothing** here. Their archive is the Mem note described in [Mem release archive](#mem-release-archive); their working files (`/tmp/release_slack_*`, `/tmp/oracle_*`) live in `/tmp/` only. (Versions before 0.20.0 also wrote `release-slack/` and `oracle/` transcript copies; those directories are no longer produced and any existing files in them can be deleted.)

## Install (Cowork)

1. Cowork → Settings → Plugins → Upload plugin.
2. Select `medtrics-release-notes.plugin`.
3. Add the `.env` keys above to your host-repo root.
4. Complete the Slack bot setup above.

## Version history

Detailed release history lives in [`CHANGELOG.md`](./CHANGELOG.md). Current version: **0.21.0**.

## Repository layout

```
medtrics-release-notes/
├── .claude-plugin/plugin.json
├── agents/               # kb-writer (Sonnet), kb-classifier (Haiku)
├── commands/             # /release-slack, /oracle-release, /deploy-notes, /integration-notes, /notes, /kb-articles
├── skills/               # storyteller, internal-storyteller, oracle-storyteller, copy-audit, ip-guard, engineering-to-client, dev-directory, pr-overrides, release-archive, kb-ledger, kb-capture
├── scripts/              # gather, prompts, slack resolve users, dev directory (Mem), pr overrides (Mem), release archive (Mem), slack upload (+ permalink), slack lint, ip guard, build
│   └── kb/               # KB format, gates, renderer, capture, selection, assembly, PDF, packaging, ledger
├── tests/                # stdlib unittest suite (not shipped in .plugin)
├── CHANGELOG.md
├── LICENSE
└── README.md
```

The release scripts are stdlib-only. `scripts/kb/` needs python-docx and Pillow, and optionally Playwright, tesseract, LibreOffice and pypdf (`scripts/kb/kb_doctor.py` lists them); tests that need them skip when they are missing. Run the test suite with:

```
python3 -m unittest discover tests
```

Build the shippable bundle with `python3 scripts/build_plugin.py` (it excludes `tests/`, dev files, and secrets, and refuses to ship an invalid manifest).
