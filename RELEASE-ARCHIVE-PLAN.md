# Release Archive Plan — persist every release to Mem for ticket→release trace

**Status:** built in 0.19.0 as designed below (optional, trace-only, beside a disk transcript) — then **superseded in 0.20.0 (2026-09-03)**: Mem is now the *only* archive and is *required* at Step 1 of both commands; the note also carries the posted root + every reply verbatim under `## Posted message`; resume runs archive once the last reply posts; the `.claude/outputs/notes/{release-slack,oracle}/` disk copies are gone. The text below is the v2 design as built in 0.19.0, kept for history — see `skills/release-archive/SKILL.md` and `CHANGELOG.md` for the current behavior.
**Revision:** v2 (2026-09-02) — incorporates the design-review findings; supersedes v1. See §8 for what changed.

**Owner decisions (2026-09-02):**
- Primary purpose is **ticket→release traceability**.
- Granularity is **one Mem note per release session** — every run that successfully posts writes exactly **one** note, never one per ticket. Duplicates are tolerated and warned, never merged (see §2, "Dedup rule").
- The Mem **collection name is user-configured** via the `.env` key `MEM_RELEASE_ARCHIVE_COLLECTION`; if no collection with that name exists, the command **creates it under that exact name**. That collection is where every release note is stored.

---

## 1. Goal

After every fully successful `/release-slack` or `/oracle-release` post, write **one Mem note per release session** into a user-named archive collection, so that later a person (or a command) can search Mem for a ticket id — `ORC-232`, `M1-1457` — and land on the release that shipped it, with its date, channel, and a clickable Slack permalink to the root thread.

Slack is already the human-readable archive; Mem adds the one thing Slack search is bad at — **given a ticket, which release, when, and where**.

### In scope
- One shared collection whose **name is set in `.env`** (`MEM_RELEASE_ARCHIVE_COLLECTION`); created on first use if absent. One note per release session, written post-success.
- Ticket ids stored prominently so a Mem search hits the release note.
- A Slack **permalink** to the root thread stored on the note — this feature is the **first** thing in the repo to actually compute one (see §3, step 3).
- Optional, Mem-gated, non-blocking — the same **failure posture** as `dev-directory` / `pr-overrides`.

### Non-goals
- Not a per-ticket record (dedup / client-keyed queries were **not** chosen — revisit only if the purpose changes).
- Not a cache of Slack; not a store of the full composed mrkdwn (redundant with Slack).
- Never blocks or gates a post. No new confirmation prompt in the release flow.
- Does not change composition, ordering, caps, or any existing step's behavior. (It does add **one new read** of an existing file — the summaries file — see §2 "Data source".)

---

## 2. Shape

This feature reuses the **skill + deterministic-script + thin command wiring** split and the **never-raises, Mem-optional** failure posture of the two existing Mem skills (`skills/dev-directory`, `skills/pr-overrides`). It **deliberately differs** from both in how the collection is resolved (by user-configured *name*, not a hardcoded title or a pinned UUID) — called out explicitly below rather than claimed as "the same ladder".

### New skill: `skills/release-archive/SKILL.md`
- `name: release-archive`
- Description: "Optional Mem-backed release archive for the release commands — ONE Mem collection (name from the `.env` key `MEM_RELEASE_ARCHIVE_COLLECTION`, default `Release Notes Archive`; created under that exact name if absent), **one note per release session**, recording each posted release's date, product, channel, Slack ts + permalink, and its posted ticket list, so a ticket id can be traced back to the release that shipped it. Written only after a fully successful post in `/release-slack` and `/oracle-release`. Only active when the Mem MCP is connected; never raises a hard error and never blocks the post."
- Sections (structure modeled on `dev-directory/SKILL.md`): **Is Mem connected?** (same tool-name probe; on absence print `Release archive: Mem not connected, skipping archive write.` and skip), **The collection**, **Data source**, **Note schema**, **What goes into Mem**, **Flow (post-success step of either command)**, **Failure handling**, **What this skill does not do**.

### New script: `scripts/release_archive.py`
Deterministic render twin of `scripts/dev_directory.py` / `scripts/pr_overrides.py`. Pure; no network; never hand-composed in the command. Subcommands:
- `pending` — resolve and print the configured `collection_name` (see "Configuration" below) plus `collection_description` / the `COLLECTION_DESCRIPTION` constant. **Prints the resolved name on every run** so the user always sees where notes are going.
- `record` — render the exact note body the command will `create_note`. Inputs (see "Data source" for why):
  `--summaries /tmp/{prefix}_summaries.txt` (primary), `--data /tmp/{prefix}_data.json` (secondary: `url`, `mrs`), `--product {medtrics|oracle}`, `--date YYYY-MM-DD`, `--channel <configured SLACK_CHANNEL_* value>`, `--channel-id <C…>`, `--ts <root ts>`, `--permalink <url>` (optional).
- A parse/validate helper (for tests): ignores notes that don't start with `Release: `.

### Configuration — how the name is read
There is **no generic `.env`→process-env loader** in this repo: every gather script reads a hardcoded allowlist of keys from `.env`, and the commands never export `.env` into the shell. So `release_archive.py` **parses `.env` at CWD itself** for `MEM_RELEASE_ARCHIVE_COLLECTION`, with a **process-environment override** taking precedence — mirroring `pr_overrides._repo_from_env`. Rules:
- Unset or whitespace-only → default `Release Notes Archive`.
- Never writes to `.env`.
- `pending` echoes the resolved name every run (see R1 in §7).

### The collection

| | |
|---|---|
| Name | From `.env` key `MEM_RELEASE_ARCHIVE_COLLECTION` (process env overrides). Default `Release Notes Archive` when unset/blank. Matched exact, case-insensitive |
| Cardinality | Exactly one; one **note per release session** (never per ticket) |
| Description | From `release_archive.py pending` (`collection_description`) or the script's `COLLECTION_DESCRIPTION` |
| Create-if-missing | If no collection matches the configured name, `create_collection({title: <configured name>, description})` — created under the **exact** name the user set |
| Owner | The user running the command (Mem is per-user) |

Resolution, once per run — **deterministic, via paginated `list_collections`** (NOT the relevance-ranked, bounded `search_collections`, which is not guaranteed to return an exact title match for a generic name):

1. Resolve the name (Configuration above).
2. `list_collections`, following `next_page` until exhausted; collect every entry whose title equals the configured name **case-insensitively**.
3. **Zero matches** → `create_collection({title: <name>, description})`; print once: `Release archive: created Mem collection "<name>".`
4. **Exactly one** → use it.
5. **More than one** → prefer the **exact-case** match; if still >1, prefer the **oldest `created_at`**; if still ambiguous, **refuse to write** and warn with every matching id so the user can rename/trash the stray. Never create a duplicate when any match exists. (This tie-break applies identically when the name is the default.)
6. Any failure → treat Mem as not connected for the rest of the run (skip the write, one warning line).

**Do not** use `create_note`'s `collection_titles` shortcut: on an unmatched title it silently creates the note *outside* any collection. Always write by resolved collection UUID.

**Renaming the env var later** creates a fresh collection under the new name; notes already written stay in the old one. Expected and documented — the command never migrates or merges collections.

Shared across both products — `M1-`/`GEN-` vs `ORC-` prefixes disambiguate, and one collection makes ticket→release trace a single search. Each note still carries a `product` field.

### Data source — the summaries file, not the data JSON
The gathered data JSON (`/tmp/{prefix}_data.json`, from `gather_notes_data_optimus.py` `task_to_issue` / the Oracle gatherer) carries **only** `identifier`, the *internal* Optimus `title`, `url`, `state`, assignee fields, `task_type`, `mrs` (+ `clients`, `mr_links`/`pr_links` added by later steps). It does **not** contain:
- `CATEGORY` — invented by the per-ticket analyzer agent;
- `CLIENT_TITLE` — added later still by the `engineering-to-client` pass;
- the **posted set** — the data JSON still lists tickets the analyzer dropped as `NO_VISIBLE_CHANGE`, and `REFACTOR` blocks the storyteller drops.

All three live only in **`/tmp/{prefix}_summaries.txt`** — the `---`-separated per-ticket blocks (`IDENTIFIER`, `TITLE`, `CLIENT_TITLE`, `CATEGORY`, `URL`, `CLIENTS`, `PR_LINKS`/`MR_LINKS`, …) that `internal-storyteller` already parses. Therefore:
- `record` takes the **summaries file as its primary input**. It reuses the same block format/parser and applies the storyteller's drop rule (skip `CATEGORY: REFACTOR`, skip malformed blocks) so the note's ticket list is **exactly what posted**.
- The data JSON is **secondary**, used only to fill `url` / `mrs` when a block lacks them.
- **No re-gather** is needed (both files already exist at post time). The v1 claim of "no new upstream data" is withdrawn: the archive reads one additional existing file.

### Note schema (one per release session)

First line is the Mem title. Rendered by `release_archive.py` — never hand-composed.

```
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
- ORC-240 · Rotation scheduling presets — heads up · TBC · no PR
```

Field rules:
- **`Tickets:`** is the load-bearing line for trace — a flat, comma-separated id list so a Mem search for a single id surfaces this note. `MR-NNNN` identifiers from `/release-slack mr <iid>` mode are listed as-is (not a ticket, still traceable).
- **`Product:`** is one of exactly `medtrics` | `oracle` — the Optimus product slugs, one vocabulary. (`medtrics` is the "classic"/legacy track; do not introduce `legacy`/`classic` as stored tokens.)
- **`Date:`** and the title date come from `--date`, which the command passes as the **same value it used for the Slack header** (`date +%Y-%m-%d`, local time). `record` never computes its own date, so the archive can't disagree with the Slack post around midnight.
- **`Channel:`** stores the configured `SLACK_CHANNEL_INTERNAL` / `SLACK_CHANNEL_ORACLE` value verbatim (it may itself be an id — a human-readable name is not always available and is not required). **`Channel id:`** is the `channel` field from the `slack_upload.py` result block.
- **`Permalink:`** present only when obtained (§3 step 3); the line is **omitted** on failure — never constructed by hand.
- Per-item bullets carry `{id · CLIENT_TITLE · CATEGORY · clients · code links}`. **Code links list ALL of a ticket's PRs/MRs, primary-first, comma-joined, as raw URLs** (from the block's `PR_LINKS`/`MR_LINKS`, falling back to data-JSON `mrs`); `no PR` / `no MR` when empty. URLs, not `PR#n`, because the trace use case wants clickable targets.
- Notes not starting with `Release: ` are ignored by the parser.

### Dedup rule — one note per session, duplicates tolerated
Every fully successful posting session writes **one new note**. The command does **not** read the collection first and does **not** update-in-place: that would require a collection-wide `list_notes` dump every run and would silently turn the rule into "one note per root ts". If the same release is ever re-posted in a fresh session, a second note appears; `record` prints a one-line reminder (`Release archive: wrote note for ts <ts>; if this was a re-post, trash the older note in Mem.`). Nothing is auto-deleted.

### What goes into Mem (and what does not)
`dev-directory` and `pr-overrides` store **no ticket content and no client names**. This skill **deliberately does** — that is the whole point of a trace — so the difference is stated, not implied. Stored: ticket ids, **client-facing** titles (`CLIENT_TITLE`), categories, client institution **codes**, PR/MR URLs, Slack channel/ts/permalink, date, product. **Not** stored: internal summaries, ticket descriptions, developer names/emails/Slack ids, any composed mrkdwn.

---

## 3. Wiring — where it hooks in

Both commands already end with a one-line post confirmation that has the root `ts` in hand, and both already *document* a permalink in the confirmation header that nothing yet produces:
- `/release-slack` — confirmation at `commands/release-slack.md` ~line 397 ("Posted to <#dream-team>. ts=<ts> …"); header spec at ~396.
- `/oracle-release` — confirmation at `commands/oracle-release.md` ~line 484; header spec at ~483.

Add a new **post-success sub-step** to each command ("Archive to Mem"), gated on **full success**:

0. **Gate:** run only when the root **and every thread reply** posted successfully **in this same session**. On the existing partial-failure path (a reply failed → stop → resume with `--thread-ts`), a **resume-only run does not archive** — it may be a fresh session with no summaries state and only a scrollback `ts`. Print `Release archive: skipped (resume run — archive only on a fully successful post).`
1. "Is Mem connected?" probe. If absent → print the skip line, done.
2. Resolve the collection **by name** (paginated `list_collections` → exact/oldest tie-break → create if none; §2).
3. **Permalink:** call Slack `chat.getPermalink` with the `channel` id from the result block and the root `ts`. Implemented as a new mode on `scripts/slack_upload.py` — `--get-permalink --channel <C> --ts <ts>` — so it reuses the existing token resolution (`os.environ` → `.env`) and the `slack_get` read-retry (429/5xx) path. Needs no extra scope beyond the bot token. **On failure, omit the permalink** — a constructed fallback is impossible because the workspace subdomain is not in `.env`. Wrapped so a failure can never surface as a post failure (the confirmation line has already printed). This same helper also fulfils the pre-existing header-spec gap at 396/483.
4. `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/release_archive.py record --summaries /tmp/{prefix}_summaries.txt --data /tmp/{prefix}_data.json --product {medtrics|oracle} --date <same as header> --channel <SLACK_CHANNEL_*> --channel-id <C…> --ts <ts> [--permalink <url>]` → note body.
5. `create_note({collection_id: <resolved uuid>, content})`. On any failure: one warning line, continue. Never raises.

**Prerequisite in `/release-slack`:** capture the `channel` field from the `slack_upload.py` result block (oracle-release already does at ~467; release-slack's ~382 does not) so `--channel-id` is available.

---

## 4. Trace flow this delivers

> "Where did ORC-232 ship?" → open **the configured collection** (whatever `MEM_RELEASE_ARCHIVE_COLLECTION` names — `pending` prints it every run) → search `ORC-232` → the release note → `Date`, `Channel`, `Permalink` → click straight to the thread.

**Before committing the schema, do a one-time manual check** that a bare-id Mem search reliably surfaces a note whose `Tickets:` line holds 10–15 ids (Mem search is semantic/relevance-ranked, not keyword-exact). If it under-performs, also embed the ids in the title line for small releases.

A future `/where-did-it-ship ORC-232` command (out of scope) could wrap this; the manual Mem search works day one.

---

## 5. Failure handling (never-blocking)

- Not a fully successful in-session post → skip with a line (§3 step 0).
- Mem not connected → one line, skip. Never prompt to connect mid-run.
- Collection resolve/create fails, or the name is irreducibly ambiguous → skip the write, one warning line naming the candidate ids.
- Permalink fetch fails → omit the `Permalink:` line, still write the note.
- `create_note` fails → one warning line, continue; the Slack post already succeeded and is authoritative.
- No hard error anywhere; the archive write is strictly after the post, so it can never affect delivery.

---

## 6. Build checklist (when green-lit)

- [ ] `skills/release-archive/SKILL.md` (structure from `dev-directory/SKILL.md`; include the **What goes into Mem** section).
- [ ] `scripts/release_archive.py` — `pending` (parses `.env` at CWD + env override; echoes name) + `record` (`--summaries` primary, `--data` secondary, `--product`, `--date`, `--channel`, `--channel-id`, `--ts`, `--permalink`) + parse helper. Reuse the summaries-block parser format shared with `internal-storyteller`; apply the REFACTOR/malformed drop rule.
- [ ] `scripts/slack_upload.py` — add `--get-permalink --channel --ts` mode via `slack_get("chat.getPermalink")`; emit a machine-parseable result; never raise into the post path.
- [ ] `scripts/build_plugin.py` — add `RELEASE-ARCHIVE-PLAN.md` to `EXCLUDE_FILES` (the other three plans are excluded by name; **without this the plan ships inside the `.plugin`**).
- [ ] `tests/test_release_archive.py` — golden note render from a summaries fixture (both products); REFACTOR + malformed blocks dropped; `MR-NNNN` listed; multi-PR comma-join primary-first; permalink present vs omitted; `.env` name resolution + env override + blank→default; parse round-trip; `Release: ` prefix rejection. unittest, no pytest dependency.
- [ ] `tests/test_slack_upload.py` (or new) — `--get-permalink` success, API-error → omitted, retry path.
- [ ] `commands/release-slack.md` — capture `channel` from the result block (~382); add the "Archive to Mem" post-success step + full-success gate; add a row to the **Failure modes** table (~399); add `MEM_RELEASE_ARCHIVE_COLLECTION` to **Required .env keys** as optional; Coding-Standards bullet that the archive never blocks the post.
- [ ] `commands/oracle-release.md` — same step + gate; Failure-modes row (~486); `.env` keys row (~72 pattern); Coding-Standards bullet.
- [ ] `README.md` — skills-table row; a line in each command's flow; **`.env` keys table** (~220) entry; **Project layout** (~281) entry.
- [ ] `.claude-plugin/plugin.json` — bump **0.18.1 → 0.19.0**; add `release-archive` to keywords; update the description (currently ends "Optional Mem developer directory.").
- [ ] `CHANGELOG.md` — new `## [0.19.0]` "Added" entry.
- [ ] One-time manual Mem searchability check (§4) before freezing the schema.
- [ ] Rebuild: `python3 scripts/build_plugin.py`; verify bundle version + packaged skill + that the plan is **not** in the bundle; run the full suite (existing 254 must stay green + new tests).

---

## 7. Decisions locked / defaults to confirm at build time

**Locked:** ticket→release trace purpose; **one note per release session, never per ticket, duplicates tolerated + warned (no update-in-place)**; one shared collection whose **name comes from `.env` `MEM_RELEASE_ARCHIVE_COLLECTION`** (env override; default `Release Notes Archive`) and is **created under that exact name if missing**; **summaries file is the primary data source**; archive **only on a fully successful in-session post**; auto-write with no new prompt; Mem-optional / never-blocking.

**Default (change only if told):**
- Permalink via one `chat.getPermalink` call (new `slack_upload.py` mode); omit on failure, never construct.
- Collection resolution via paginated `list_collections`; ambiguity → exact-case → oldest `created_at` → refuse + warn.
- Product tokens `medtrics` | `oracle`.
- Code links as raw URLs, all PRs/MRs, primary-first.
- Version bump is **minor (0.19.0)**.

**Risks accepted:**
- **R1** Renaming the env var forks the store (mitigated: `pending` prints the resolved name every run; documented).
- **R2** Re-posting a release in a fresh session yields a second note (mitigated: reminder line; user trashes the stale one).
- **R3** Permalink adds one Slack read post-success (mitigated: read-retry + omit-on-failure; cannot affect the post).
- **R4** Large classic releases (~50 tickets) make long notes — well under Mem limits; fine.

---

## 8. Changes from v1 (design-review fold-in)

- **Data source corrected:** v1 wrongly claimed the note could be built from the data JSON with no new upstream data. `CLIENT_TITLE`, `CATEGORY`, and the actually-posted set exist only in the summaries file → `record` now takes `--summaries` as primary. (Fixes the risk of notes with internal titles, no categories, and never-posted tickets.)
- **Dedup rule settled:** dropped the self-contradictory "update in place on same `Root ts`" / "newest wins" pair → one note per session, duplicates tolerated + warned, no collection read.
- **Config mechanism nailed:** `.env` parsed by the script at CWD with process-env override (no generic loader exists in the repo).
- **Collection resolution made deterministic:** paginated `list_collections` instead of relevance-ranked `search_collections`; explicit exact-case → oldest → refuse tie-break; forbid `create_note` `collection_titles`.
- **Permalink specified:** nothing in the repo produced one; new `slack_upload.py --get-permalink` mode; omit-on-failure, no constructed fallback (workspace subdomain unavailable).
- **Full-success gate + resume path defined.**
- **Multi-PR/MR, date source, channel-id capture, `MR-NNNN`, product vocabulary (`medtrics|oracle`) specified.**
- **"What goes into Mem" section added** — stated the deliberate data-posture difference from the two skills this mirrors.
- **Stopped claiming to "mirror exactly"/"same ladder"** — same failure posture and skill/script split; deliberately different resolution.
- **Checklist completed:** `EXCLUDE_FILES` entry, release-slack channel capture, failure-mode + `.env` rows in both commands, permalink test, `plugin.json` description, README `.env` table + layout, one-time searchability check.
