# ORACLE-PLAN — `/oracle-release`: combined client + internal release post for medtrics-oracle

**Date:** 2026-08-26 · **Target version:** 0.10.0 · **Status:** planned, reviewed (all review findings folded in — see §9)

## What this adds

A new command **`/oracle-release`** for the **medtrics-oracle** product (React + Supabase app, currently
no release-notes track):

- Tickets from **Optimus** (Oracle product, `ORC-` prefix) — confirmed by owner.
- Code from **GitHub** (`medtrics/medtrics-oracle`) — PRs and their diffs, replacing GitLab.
- Output: **ONE combined Slack message** to a new **internal-only** channel (`SLACK_CHANNEL_ORACLE`):
  - a delimited, **ip-guarded client-communication block** (exec voice, safe to copy-paste onward), then
  - an **internal reference block** — per ticket: `n. ORC-id · verbatim title · <@dev> · <PR-url|PR#n>` —
  - plus thread replies carrying per-ticket technical detail (dev, PR links, Optimus link, tech notes).
- Same preview-then-confirm discipline; never posts without an explicit yes.
- Classic Medtrics track untouched (`gather_notes_data_optimus.py` unmodified; `ip_guard.py` changes additive only).

## Architecture decisions (verified against code)

- **New sibling gatherer** `scripts/gather_notes_data_oracle.py` importing the classic module
  (`import gather_notes_data_optimus as base`) — reuses `optimus_api`, `fetch_tasks_paginated`,
  `task_to_issue`, `mr_matches_identifier`, `truncate_diffs` unchanged. `ORC-` ids verified to pass
  every existing regex (matcher, ip-guard exemptions, prompt-file naming).
  **Import fix (review I5):** the oracle script inserts its own directory into `sys.path` before the
  import, and tests must mock via `oracle.base.<fn>` (two module objects exist under the test loader).
- **Mechanical root assembler** (review B1 — replaces the unsound byte-verify): a new tiny stdlib script
  `scripts/assemble_oracle_root.py` builds the root from `--client-file` + `--internal-file`
  deterministically (markers, overflow partitioning, "…and N more" line). Because assembly is code,
  the guarded client text is **verbatim by construction** — no grep/cmp verification step, no drift
  window. Guard-rewrite loop or preview edits simply re-run the assembler (fixes review I6). Overflow
  partitioning is computed here, testably (fixes review M3).
- **Guard scope:** `ip_guard.py` runs on the client-section file **and** on any client-continuation
  reply file (both are inputs to the assembler). The assembled root and internal replies are not
  re-guarded — internal attributes are the point; the channel is internal-only.
- **No `--allow` file** (review I1/I2): the `Medtrics-Oracle` pattern is warn-severity (never blocks)
  and doesn't even match the space form — the allow file added a guard-weakening line-level mask to
  suppress a violation that can't fail the run. Dropped.
- **Message format passes `slack_lint` as-is** (reviewer-verified): `<@U…>` mentions, `<url|PR#42>`
  labels, emoji shortcodes, `_(italics)_`, mid-line `#` all legal; no lint changes needed.
- Index lines carry **no KIND suffix** (review M6/M3): the section headers already group by kind, and
  dropping it buys back root budget.

## Phase 0 — Live-API verification — ✅ COMPLETED 2026-08-26

All four unknowns resolved against the live systems (real `.env`):

1. **Optimus product selector: `product=oracle`** — verified: `scope=product&product=oracle&product_status=code_review`
   returns exactly the ORC ticket; `product_slug` is ignored (returns mixed M1+ORC — do not use).
   Plain `search=ORC-232` also finds the ticket directly, so **tickets mode needs no new param at all**.
   Oracle uses the same lane names (`code_review`, `dev_todo`, `dev_in_progress`, `triage` observed).
   Task payload carries `product: "oracle"`, `product_status`, `product_type`.
2. **GitHub**: token is a classic PAT (`ghp_`), canary `GET /repos/medtrics/medtrics-oracle` → 200 on the
   private repo. **`GITHUB_REPO` in the live .env is a full URL** — `load_env` must normalize
   (`strip https://github.com/ prefix and .git suffix`).
3. **PR discovery strategy CHANGED (verified empirically):** GitHub `/search/issues` returned **zero** hits
   for ORC-232 even though PR #456 exists — the ID lives only in the **branch name**
   (`fix/orc-232/…`), and GitHub search does not index branches. Across the 30 most-recent PRs: 8 reference
   an ORC id, **8/8 in the branch**, only 7/8 in title/body. Therefore the **primary discovery is one
   PR-list sweep per run** — `GET /repos/{r}/pulls?state=all&sort=updated&per_page=100`, up to ~5 pages,
   fetched once and matched locally against ALL requested tickets via `base.mr_matches_identifier`
   (which checks branch + title, case-insensitive — verified it matches `fix/orc-232/…`). `/search/issues`
   is demoted to an optional secondary for old PRs beyond the sweep window.
4. **Assignee: raw-API Optimus tasks return `assignee: None`** (the MCP shows assignees, the raw API
   does not). **Dev attribution therefore comes primarily from the GitHub PR author**
   (`user.login` via `_pr_entry`), with the Optimus assignee used when present. The
   `slack_resolve_users.py` step (bot token) maps the PR author's name to `<@U…>` by unique
   full-name match (Oracle has no author email, so name match is the only path).

Original verification checklist (retained for reference):

1. **Optimus product selector**: read `medtrics-optimus-workflow:oracle-config` for the pinned product
   slug; test whether plain `GET /tasks?scope=product&search=ORC-<known-id>` already returns ORC tasks
   (if yes, `tickets` mode needs no new param); test lane-mode product param candidates
   (`product=oracle` / `product_slug` / `product_id`). Record whether Oracle uses the same
   `product_status` lanes. If lane mode is unverifiable, ship `tickets` mode only.
2. **Optimus URL space** for ORC tasks (`https://optimus.medtricslab.com/tasks/ORC-…`) — override the
   URL template in the oracle gatherer if different.
3. **GitHub**: with the real token on the private repo, verify `GET /search/issues?q=repo:… is:pr "ORC-…"`
   response shape; **pin the token type** (classic PAT vs fine-grained — fine-grained PATs have had
   incomplete `/search/*` support; review M5) and confirm scopes cover `/repos/{r}/pulls/{n}` + `/files`.
4. **Assignee payload shape** of Oracle tasks vs `task_to_issue`'s fallback chain.

## Phase 1 — `scripts/gather_notes_data_oracle.py` (new, stdlib-only)

CLI mirrors the classic gatherer (`tickets ORC-101,ORC-115`, optional `status`, `--output`,
`--include-description`, `--dry-run`).

- **`load_env()`**: requires `OPTIMUS_API_KEY`, `GITHUB_TOKEN`, `GITHUB_REPO` (validated
  `owner/repo`); optional `GITHUB_API_URL` (default `https://api.github.com`).
- **Startup canary (review I4)**: `GET /repos/{owner}/{repo}` once before gathering; hard-exit with a
  clear auth/visibility message on failure. Prevents the silent-empty-release failure mode where a bad
  token makes every ticket "find no PRs" (private-repo auth failures surface as 404/422, not 403).
- **`github_api()`** modeled on the classic `gitlab_api`: Bearer auth, `Accept: application/vnd.github+json`,
  `timeout=30`, lowercased headers, proactive throttle on `x-ratelimit-remaining` low (sleep to
  `x-ratelimit-reset`+1, capped ~120s).
  **Retry classification (review I3):** retryable iff status is 429, **or** `x-ratelimit-remaining: 0`,
  **or** a `Retry-After` header is present (GitHub secondary limits send 403 + Retry-After with nonzero
  remaining) — wait = Retry-After, else reset−now+1, else exponential backoff; max 5 attempts.
  5xx / `URLError` / `TimeoutError` retry with backoff. Other 4xx: warn + `(None, {})`, never exit
  mid-gather.
- **Pagination** via the `Link: <…>; rel="next"` header (GitHub has no `x-next-page`); fallback
  continue-while-page-is-full.
- **PR discovery (Phase-0-verified design)**: **one PR-list sweep per run** —
  `GET /repos/{r}/pulls?state=all&sort=updated&per_page=100`, Link-paginated up to ~5 pages (~500 PRs),
  fetched ONCE and matched locally against ALL requested tickets with `base.mr_matches_identifier`
  (branch + title). This is required, not an optimization: ORC ids live primarily in branch names,
  which GitHub search does not index (verified: search returns 0 for a ticket whose PR exists).
  The list response already carries `head.ref`, `title`, `html_url`, `user`, `number` — no per-PR
  detail fetch needed. Optional secondary: `/search/issues` per unmatched ticket, for PRs older than
  the sweep window.
- **`fetch_pr_diffs()`**: `/pulls/{n}/files?per_page=100`, Link-paginated; normalize to the existing
  diff schema (`previous_filename`→`old_path`, missing `patch` → empty diff) so
  `prepare_note_prompts.py` needs zero changes; reuse `base.truncate_diffs`.
- **`_pr_entry()`**: `{iid: number, title, web_url: html_url, author_name: user.name or login,
  author_username: login, diffs}` — matches every key the prompt builder reads.
- `main()` mirrors the classic flow (dry-run table, shared-PR dedupe, same output JSON shape).

## Phase 2 — `scripts/ip_guard.py` additive patterns + `scripts/assemble_oracle_root.py`

- `internal_system` += `\bGitHub\b`, `\bgithub\.com\b`, `\bVite\b` (block).
- PR-reference blocks: `PR\s*#?\d+` (case-sensitive bucket — must match `PR#42`, the plan's own label
  format; review M1) and `pull\s+request\s+#?\d+` in the **IGNORECASE** bucket (review N1).
- No exemption-regex changes (ORC- already covered).
- **New `assemble_oracle_root.py`** (~80 lines): inputs `--client-file`, `--internal-file`,
  `--date`; outputs the root file and, when overflow occurs, continuation fragments with deterministic
  partitioning (whole lines only; client block keeps priority in the root; internal index spills first).
  Root marker text states plainly when client copy continues in the thread (review M7):
  `_(client-safe text — full version continues in the first thread reply)_` on overflow, else
  `_(everything between the markers is client-safe)_`.

## Phase 3 — `skills/oracle-storyteller/SKILL.md` (new skill)

**Self-contained contract** — states its complete rule set explicitly rather than inheriting by
reference, and explicitly overrides conflicting parent rules (e.g., three header emoji allowed:
`:rocket:` `:clipboard:` `:lock:`; no `Clients to notify:` line) (review M4).

- **Input** `/tmp/oracle_notes_summaries.txt`: per-ticket dual-register blocks —
  `IDENTIFIER / TITLE (verbatim) / KIND / OPTIMUS_URL / DEVELOPER_NAME / DEVELOPER_SLACK_ID /
  PR_LINKS / CLIENT_OUTCOME (15–40 words, exec voice, zero internal vocabulary) /
  TECH_NOTES (2–4 sentences, internal register)`.
- **Outputs**:
  - `/tmp/oracle_release_client_section.txt` (≤1,200 chars) and, on overflow,
    `/tmp/oracle_release_client_cont.txt` — **both ip-guarded**.
  - `/tmp/oracle_release_internal_section.txt` — the internal index lines + section headers.
  - Reply files `/tmp/oracle_release_reply_NN.txt` (≤3,900 each): client continuation first (if any),
    then internal index overflow, then per-section technical cards
    (`*1. ORC-101 · title*` / Developer / PRs (raw URLs, lint-legal) / Optimus link / Notes),
    split at card boundaries; SECURITY items get their own escalation reply.
  - The **root itself is produced by the assembler**, not by the skill.
- mrkdwn rules: single-asterisk bold, `&amp;` escaping, blank line before `*…*` section lines, bare
  `<@U…>` mentions, `<pr_url|PR#n>` labeled links on index lines. Empty-input sentinel
  `NO_ORACLE_NOTES`.

## Phase 4 — `commands/oracle-release.md` (new command)

Cloned from `internal-notes.md` orchestration with the guard loop from `release-slack.md`:

1. Env validation: `OPTIMUS_API_KEY`, `GITHUB_TOKEN`, `GITHUB_REPO`, `SLACK_CHANNEL_ORACLE`,
   `SLACK_BOT_TOKEN` (+optional `GITHUB_API_URL`); abort before any call if missing.
2. Gather via the oracle script (zero tickets → stop cleanly).
3. Dev Slack-ID resolution via `slack_resolve_users.py` (bot token, name match), best-effort.
4. Analyzer instructions heredoc (dual-register rubric: client-outcome ban list from deploy-notes +
   GitHub/PR/React/Supabase/Vite; tech-notes rules from internal-notes) →
   `prepare_note_prompts.py --prefix orc`. Note: prompt headers will say "MR !42" — instruct analyzers
   that these are GitHub PRs and PR references must never appear in `CLIENT_OUTCOME` (review M1 note).
5. Spawn all analyzers in one message; collect summaries.
6. copy-audit over the CLIENT_OUTCOME text only.
7. Invoke `oracle-storyteller` → section + reply files.
8. **ip_guard** over `client_section.txt` AND `client_cont.txt` (when present), with `--customer-list`
   when available; 3-iteration rewrite loop; **after any rewrite, re-run the assembler** (Phase 2) so
   the root always embeds the current guarded text.
9. Run the assembler → root; `slack_lint --kind root` on it and `--kind reply` on every reply;
   fix-and-relint ≤3 (a lint fix to client text re-enters step 8).
10. Preview `[y]/[n]/[e]`; any edit to client text re-enters 8; any edit re-runs 9.
11. Post: root via `slack_upload.py --channel "$SLACK_CHANNEL_ORACLE"`; parse the result block; post
    replies with `--thread-ts <ts>` **passing the resolved channel ID from the result block** (avoids
    re-resolving `#name` per reply; review N2); partial-failure resume into the same thread; transcript
    to `.claude/outputs/notes/oracle/oracle-release-<date>.md`.
12. Failure-modes table, does-NOT-do list ("does not query GitLab", "does not run the classic
    pipelines"), coding standards — house pattern.

## Phase 5 — Tests (hermetic, stdlib unittest; all 98 existing stay green)

- `tests/test_gather_oracle.py` (~22): `github_api` success/headers; 429 + Retry-After; **403 with
  remaining:0**; **403 with Retry-After but nonzero remaining (secondary limit) retries** (review I3);
  plain 403 no-retry; persistent 500; network retry; proactive throttle; Link pagination + fallback;
  `fetch_pr_diffs` pagination / missing patch / rename; `find_prs` ORC-59 vs ORC-597 + branch match +
  dedupe; **single-search-per-identifier** call-count assertion; `_pr_entry` schema; `load_env`
  validation; **canary hard-fails on 404** (review I4). Mocks target `oracle.base` where relevant
  (review I5).
- `tests/test_assemble_oracle_root.py` (~8): verbatim client inclusion; client overflow partitioning
  (whole lines, priority in root, marker text switches); internal overflow "…and N more" (review M3 —
  this is the common case, not the edge); determinism (same inputs → identical bytes).
- `tests/test_ip_guard.py` (+6): ORC header/index exemptions; GitHub / github.com / `PR#42` / `PR #42`
  / `Pull Request #42` block; realistic client-section fixture **with internal vocabulary planted**
  fails, clean fixture passes (no allow file involved; review I2 fixed the vacuous-test risk).
- `tests/test_slack_lint.py` (+3): good combined root passes `--kind root`; bad variants fail on
  exactly the right rules; >2,500 fails length.

## Phase 6 — Docs, manifest, build

- `plugin.json`: version 0.10.0; description rebalanced ≤256 chars (currently 248 — must be rewritten,
  not appended); keywords += `oracle`, `github`.
- `README.md`: command row, "Oracle track" section (env keys, combined-message format, guard scope,
  invite the bot to the Oracle channel), output path.
- `CHANGELOG.md`: `[0.10.0]` entry + compare links.
- `build_plugin.py`: no changes; verify build passes.

## Sequencing & effort

```
Phase 0 (live verify) → Phase 1 (gatherer) → Phase 2 (guard patterns + assembler)
  → Phase 3 (skill) → Phase 4 (command) → Phase 5 tests land WITH phases 1/2/3 → Phase 6 last
```
Phase 2 must precede Phase 4 (the command invokes the assembler and the new guard patterns).
Estimate: **2–3 working days** (Phase 0 carries two schedule-altering unknowns).
End-to-end rehearsal before real use: `dry-run` on real ORC ids, then a full run into a **test channel**
before pointing `SLACK_CHANNEL_ORACLE` at the real one.

## Open risks — updated after Phase 0

1. ~~Optimus product param~~ **RESOLVED**: `product=oracle` (never `product_slug`).
2. ~~GitHub search behavior~~ **RESOLVED**: search demoted; list-sweep is primary (branch-only refs).
3. Missing `patch` on large/binary PR files → sparse analyzer input (acceptable, documented).
4. ~~Assignee shape~~ **RESOLVED**: raw API returns None — PR author is the attribution source.
5. ~~Token type~~ **RESOLVED**: classic PAT confirmed working on the private repo.
6. NEW: tickets whose PR is older than the ~500-PR sweep window need the search secondary or a
   manual PR number; log unmatched tickets loudly rather than silently omitting them.

## Out of scope

No Oracle markdown deliverables; no classic-track changes beyond additive ip_guard patterns; no
provider-abstraction refactor; no GitHub MCP / GraphQL / third-party deps; no lane mode if unverified;
no caching, scheduling, per-client channels, or external delivery of the Oracle post.

## §9 Review findings folded in (traceability)

| Finding | Resolution |
|---|---|
| B1 grep/cmp byte-verify unsound | Replaced by mechanical `assemble_oracle_root.py` — verbatim by construction, tested |
| I1/I2 `--allow` file weakens guard & pattern mismatch | Allow file dropped entirely (warn-only pattern can't block) |
| I3 secondary-rate-limit 403+Retry-After misclassified | Retry rule: 429 OR remaining==0 OR Retry-After present |
| I4 silent empty release on bad token (404/422) | Startup canary `GET /repos/{owner}/{repo}`, hard-exit |
| I5 import breaks under test loader | Script self-inserts its dir into sys.path; mocks target `oracle.base` |
| I6 root reassembly after guard rewrite unspecified | Assembler re-runs after every rewrite/edit (steps 8–10) |
| M1 `PR #?\d+` misses `PR#42` | `PR\s*#?\d+`; analyzers warned about "MR !n" prompt wording |
| M2 3× redundant search variants | One search per identifier (+linear id); call-count test |
| M3 root overflow is the common case | Assembler owns partitioning; overflow test mandatory; KIND dropped from index lines |
| M4 conflicting inherited skill rules | oracle-storyteller is self-contained, explicitly overrides |
| M5 fine-grained PAT search support | Token type pinned in Phase 0 |
| M6 KIND undefined/redundant | Removed from index line; sections group by kind |
| M7 spilled client copy contradicts marker | Marker text switches on overflow to say so |
| N1 case-sensitive phrase patterns | Phrase forms go in IGNORECASE bucket |
| N2 N+1 channel resolution | Replies reuse the resolved channel ID from the result block |
