# Plan — PR discovery v2 for `/oracle-release` (multi-PR, primary rule, Mem overrides)

**Status:** v3 — retargeted to this plugin, approved for build
**Date:** 2026-09-02
**Owner:** Shubham
**Target repo:** `medtrics-release-notes` (this checkout, v0.17.0 → **v0.18.0**)
**Entry point:** `/oracle-release` only. No standalone command. The matcher is internal to the release flow.

> v1/v2 of this plan targeted `medtrics-qa-automation`. That was the wrong repo. v3 keeps the v2 design decisions (set of PRs + primary rule, Mem = human overrides only, sweep-window awareness) and re-expresses them as changes to the code that already does PR discovery here.

---

## 0. What exists today — verified

| Fact | Where |
|---|---|
| PR discovery is one sweep per run (`/pulls?state=all&sort=updated`, ≤5×100) + per-ticket `/search/issues` secondary, in `discover_prs`. | `scripts/gather_notes_data_oracle.py:418-438`, `SWEEP_MAX_PAGES` `:53` |
| Match = exact-ID regex on `head.ref` + `title` with `(?!\d)` guard. | `_pr_matches` `:285`, `match_prs_to_issue` `:307`, `base.mr_matches_identifier` (`gather_notes_data_optimus.py:383`) |
| All matching PRs are kept in `issue["mrs"]`, deduped by number, in sweep order. Nothing picks a primary. | `:307-317` |
| The per-PR record keeps only `iid, title, web_url, author_name, author_username, author_email, diffs`. **`head.ref`, `state`, `draft`, `merged_at`, `updated_at` are discarded.** | `_pr_entry` `:347-360` |
| Zero-PR tickets: `WARNING: no PRs found …` to **stderr only**; issue kept with `mrs: []`. No `warnings` key, no per-issue status in the JSON. | `:428-436`; `log = base.log` `:46` |
| `github_paginate` returns a bare list; page count and window fullness are not tracked. | `:216-250` |
| `load_env()` reads a **fixed `./.env`** with a whitelist: `OPTIMUS_API_KEY, GITHUB_TOKEN, GITHUB_REPO, GITHUB_API_URL`. No `--env-file`. | `:58-101`, called at `:498` |
| Output JSON: `{"mode", "parameter", "issues": [...]}`. | `:567-573` |
| `/oracle-release` Step 2 runs the gather; Step 4 builds `pr_links` by joining `mrs[].web_url`; Step 10 preview must list "Tickets without PRs" by scraping stderr; on a missing PR the command tells the user "give me the PR number and I'll re-run the gather" — **but nothing can consume that number today.** | `commands/oracle-release.md:91-116, 178-212, 381-399, 111-112` |
| Hard rule: no github.com fetches outside the gather script; all code context comes from `/tmp/oracle_notes_data.json`. | `commands/oracle-release.md:135-137, 466` |
| Mem precedent: `dev-directory`. Script parses a `list_notes` dump (`--notes`), emits `writes[]` (`create`/`update` with `version`), the command applies them. Notes are `Title line` + `**Label:** value` lines. Never blocks. | `scripts/dev_directory.py:64-90, 142-213, 381-393`; `skills/dev-directory/SKILL.md:20-25, 55, 99-110`; invocations `commands/oracle-release.md:146-166` |
| Downstream: `prepare_note_prompts.py` uses `iid, title, author_*, diffs` (never `web_url`); `internal-storyteller` renders **all** PR URLs space-separated; parked `oracle-storyteller` renders **first PR only** on the index line. | `prepare_note_prompts.py:84-143`; `skills/internal-storyteller/SKILL.md:142,180`; `skills/oracle-storyteller/SKILL.md:96-100` |
| Tests: `tests/test_gather_oracle.py` (`_pr`/`_issue` fixtures `:273-284` carry no state/draft/dates; `DiscoveryOrchestrationTests` `:318`), `tests/test_dev_directory.py` (fake dumps as dicts), `tests/test_build_plugin.py`. | — |
| `build_plugin.py` excludes `PLAN.md`/`ORACLE-PLAN.md` by exact name; `PR-MATCHER-PLAN.md` would currently ship. | `scripts/build_plugin.py:45-52` |
| Oracle repo reality: several PRs per ticket is normal (ORC-53 ×2 merged, ORC-5 ×5, ORC-92/-49/-47/-98 ×2 branches). | Oracle `git log --merges` |

---

## 1. Goal

Inside `/oracle-release`, for every ORC ticket in the run:

1. Return **all** PRs that belong to it (already true) **plus** a deterministic **primary** PR and an explicit **decision**.
2. Honor a **human override** stored in Mem (ticket → PR number) when the sweep is wrong or empty.
3. Tell the user, in the preview, exactly which tickets need attention and why, and let them **record an override without leaving the command**.

```
Step 2  gather  ──▶  canary → ONE sweep → match every ticket (branch+title) → search secondary for misses
                     apply Mem overrides (from --pr-overrides dump) → primary rule → per-ticket decision
                     fetch diffs for every PR in mrs (incl. override PRs not in the sweep)
Step 10 preview ──▶  "PR discovery" block: flagged / no PR / older than window / overrides used
                     user: "ORC-232 → 415"  ──▶  pr_overrides.py pending → Mem create/update → re-run Step 2
```

Decisions per ticket: `override | matched | flagged_ambiguous | no_pr_yet | no_pr_in_window`.

**Why not one PR:** see §0 last row. **Why Mem is overrides-only:** the sweep already runs once per release and costs ≤5 requests; a cache would add staleness (PR A closed unmerged → PR B opened) for no saving. Mem stores only what a sweep cannot produce: a person's decision.

---

## 2. Locked decisions

| Decision | Choice |
|---|---|
| Where | Extend `gather_notes_data_oracle.py`; add `scripts/pr_overrides.py` + `skills/pr-overrides/SKILL.md` modeled on dev-directory. |
| Caller | `/oracle-release` only (Step 2 and Step 10). No new slash command. |
| Cardinality | All PRs kept in `mrs`, **ordered primary-first**; `pr_discovery.primary` names it explicitly. |
| Primary rule | One open PR → it. Zero open → newest `merged_at`. ≥2 open → the single non-draft if exactly one, else `flagged_ambiguous`. Override always wins. |
| Mem role | **Overrides only.** One note per ticket, written only when a human decides. Never a cache. |
| Collection id | **`.env` key `MEM_PR_OVERRIDES_COLLECTION_ID`** (owner's decision). If absent/empty: bootstrap by title `Oracle PR Overrides` (dev-directory pattern), print the id, tell the user to pin it in `.env`. Never blocks. |
| Network | GitHub HTTP stays inside the gather script (existing rule). Mem via MCP from the command, driven by `pr_overrides.py` payloads (dev-directory rule). |
| Backward compatibility | `mrs` entry keeps every existing key; new keys are additive. `prepare_note_prompts.py`, `internal-storyteller`, Step 4 `pr_links` keep working unchanged. |

---

## 3. Data changes

### 3.1 `mrs[]` entry (additive)
```json
{
  "iid": 415, "title": "ORC-232 consent banner fix",
  "web_url": "https://github.com/medtrics/medtrics-oracle/pull/415",
  "author_name": "…", "author_username": "…", "author_email": "…",
  "diffs": [],
  "number": 415,
  "branch": "fix/orc-232/consent-banner",
  "state": "open",
  "draft": false,
  "merged_at": null,
  "updated_at": "2026-09-01T18:22:10Z",
  "matched_via": "branch"
}
```
`matched_via ∈ {branch, title, manual}` (a wrapper around `mr_matches_identifier` reports which field hit; search-secondary hits are re-matched so they report `branch`/`title`; override PRs report `manual`). `state` is GitHub's `open|closed`; consumers derive "merged" from `merged_at`.

### 3.2 Per-issue `pr_discovery` (new)
```json
{
  "decision": "matched",
  "primary": 415,
  "candidates": [],
  "override": null,
  "notes": []
}
```
- `primary`: PR number or `null`.
- `candidates`: open PR numbers when `flagged_ambiguous`.
- `override`: `{"pr": 415, "resolved_by": "…", "resolved_at": "…", "reason": "…", "note_id": "…"}` when an override was applied (decision `override`).
- `notes`: human-readable strings (e.g. `"override PR 415 not in sweep; fetched by number"`, `"2 override notes for this ticket; newest used"`).

### 3.3 Top-level `pr_discovery` (new)
```json
{
  "swept_at": "2026-09-02T12:00:00Z",
  "sweep": {"pr_count": 487, "pages": 5, "window_full": false, "oldest_updated_at": "2026-05-11T09:14:00Z"},
  "overrides_loaded": 3,
  "summary": {"override": 1, "matched": 9, "flagged_ambiguous": 1, "no_pr_yet": 2, "no_pr_in_window": 0},
  "warnings": ["no PRs found for ORC-240 — it will appear without code context", "…"]
}
```
Warnings continue to go to stderr **and** are now in the JSON so the command reads them from the file, not from scrollback.

### 3.4 Mem note — collection `Oracle PR Overrides`, one note per ticket
Title line doubles as the Mem title, dev-directory style:
```
PR Override: ORC-232
**Ticket:** ORC-232
**PR:** 415
**PR URL:** https://github.com/medtrics/medtrics-oracle/pull/415
**Resolved by:** shubham@medtricslab.com
**Resolved at:** 2026-09-02T12:00:00Z
**Reason:** two open PRs; 416 is a spike branch
```
Parsed with the same `FIELD_RE` approach as `dev_directory.py:73,142-176`; non-conforming notes are ignored. `PR` must be a positive integer. Duplicate notes for one ticket: newest `Resolved at` wins, plus a warning. A human may edit the `PR` line in Mem directly; the next run picks it up.

---

## 4. Code changes

### 4.1 `scripts/gather_notes_data_oracle.py`
| Change | Detail |
|---|---|
| `load_env` | Add `MEM_PR_OVERRIDES_COLLECTION_ID` to the whitelist as **optional** (`setdefault("")`). Not required. |
| `github_paginate(..., stats=None)` | Optional out-dict recording `pages`, `last_page_full`, `failed`. Default behavior unchanged. |
| `sweep_pulls` | Pass `stats`; compute `oldest_updated_at` = min `updated_at` over the sweep; `window_full = pages == SWEEP_MAX_PAGES and last_page_full`. |
| `match_field(pr, identifiers)` | New: returns `"branch"`, `"title"`, or `None` (branch checked first). `_pr_matches` becomes `match_field(...) is not None`. |
| `_pr_entry(pr, matched_via)` | Add `number, branch, state, draft, merged_at, updated_at, matched_via`. |
| `search_prs_for_issue(..., warnings=None)` | Append a warning string on a failed `/search/issues` call instead of only logging. |
| `choose_primary(mrs) -> (decision, primary, candidates)` | New pure function implementing §2 primary rule. |
| `apply_overrides(env, issue, overrides, pulls_by_number, warnings)` | New: if an override exists for the ticket, ensure its PR is in `mrs` (from the sweep, else one `GET /pulls/{n}`; on 404 → warning, override ignored), mark it `matched_via: manual`, set decision `override`, primary = it. |
| `discover_prs(env, issues, overrides=None)` | Orchestrate: sweep → match → search secondary → overrides → `choose_primary` → reorder `mrs` primary-first → set `issue["pr_discovery"]` → `no_pr_in_window` when `window_full` and `issue.created_at < oldest_updated_at` and no PR found (needs `created_at` on the issue — see next row). Return `(issues, run_stats)`. |
| `base.task_to_issue` | Expose the Optimus task's creation timestamp as `issue["created_at"]` (field name to be confirmed from the raw task payload; tolerate `createdAt`/`created_at`; `None` if absent → `no_pr_in_window` never fires). |
| `main()` | New flag `--pr-overrides <path>` (Mem `list_notes` dump; missing/empty file → no overrides, never an error, matching `dev_directory.load_notes`). Import `pr_overrides` via the existing sibling-import pattern (`:33-36`). Write top-level `pr_discovery`. `--dry-run` table gains a `Decision` column and prints the summary. |
| Exit codes | Unchanged: canary failure and missing env still `sys.exit(1)`. Sweep failure after retries → existing behavior (empty list) **plus** `warnings` entry and `sweep.failed: true`, so the command can say "scan failed" instead of "no PRs". |

### 4.2 `scripts/pr_overrides.py` (new, mirrors `dev_directory.py`)
- Constants: `COLLECTION_TITLE = "Oracle PR Overrides"`, `COLLECTION_DESCRIPTION`, `TITLE_PREFIX = "PR Override: "`, `FIELDS`.
- `parse_note(note) -> dict|None`, `render_note(d) -> str`, `load_notes(path) -> list` (accepts `{"notes"}`/`{"items"}`/`{"results"}`/list/missing).
- `load_overrides(path) -> (dict[ticket -> override], warnings)` — applies newest-wins. **Imported by the gather script.**
- CLI `pending --ticket ORC-232 --pr 415 --notes <dump> --out <path> --resolved-by <email> [--reason "…"] [--data /tmp/oracle_notes_data.json]` → emits `{"collection_title", "collection_description", "writes": [ {action: create|update, ticket, pr, content, note_id?, version?} ]}`. Uses `--data` only to fill `PR URL` from a matching `mrs` entry when present; otherwise builds the URL from `GITHUB_REPO` in `.env` (read best-effort, dev-directory style). Exit 0 / 1 on bad input (non-integer PR, ticket not matching `^ORC-\d+$`).
- CLI `show --notes <dump>` → prints the parsed overrides table (used in the preview).
- `---PR_OVERRIDES_RESULT---` block on every path, like `---DEV_DIRECTORY_RESULT---`.
- No network. No MCP.

### 4.3 `skills/pr-overrides/SKILL.md` (new, modeled on `skills/dev-directory/SKILL.md`)
Documents: is Mem connected; collection resolution (`.env` id → else search by title → else create → print id + hint); the `list_notes` dump procedure (`include_note_content: true, limit: 100`, follow `next_page`, save verbatim with `id, version, content`); applying `writes` (`create_note({content, collection_ids:[id]})`, `update_note({note_id, content, version})`); never-block table; how to hand-edit a note.

### 4.4 `commands/oracle-release.md`
| Step | Edit |
|---|---|
| Env table `:59-69` | Add `MEM_PR_OVERRIDES_COLLECTION_ID` (optional). |
| **New Step 2-pre** | If Mem connected: resolve the overrides collection (§4.3), `list_notes` → `/tmp/oracle_pr_overrides.json`. If not: print `PR overrides: Mem not connected, sweep only.` and continue. |
| Step 2 `:91-116` | Add `--pr-overrides /tmp/oracle_pr_overrides.json` to the gather invocation. Replace the stderr-scraping sentence at `:111` with: read `pr_discovery` from the JSON. Replace the dead-end text at `:112` with the override procedure (Step 10b). |
| Step 4 `:204-206` | `pr_links` unchanged in shape; note that `mrs` is already primary-first. |
| Step 10 preview `:381-399` | Replace `Tickets without PRs:` with a **PR discovery** block: `Overrides applied: …`, `Multiple open PRs (pick one or post as-is): ORC-… [415, 416]`, `No PR found: …`, `No PR within the ~500-PR window (older ticket): …`, `Sweep failed: yes/no`. Then: *"To record a PR for a ticket, reply `ORC-232 → 415` (repeat per ticket). Otherwise reply `y` to post."* |
| **New Step 10b** | On `ORC-NNN → N` replies: run `pr_overrides.py pending …` per pair, apply `writes` via Mem, then **re-run Step 2** (gather with the refreshed dump) and Steps 4–9, then show the preview again. If Mem is not connected: apply the pick for this run only by passing `--pr-override-inline ORC-232=415` to the gather (same code path, nothing persisted) and say so. |
| Failure table `:441` | Update the zero-PR row; add rows for `flagged_ambiguous`, `no_pr_in_window`, override-PR-404, Mem-not-connected. |
| HARD RULES | Unchanged. Overrides never fetch github.com outside the gather script. |

### 4.5 Housekeeping
- `scripts/build_plugin.py`: add `PR-MATCHER-PLAN.md` to `EXCLUDE_FILES`. Delete the stale `medtrics-release-notes-0.17.0.plugin` from the repo root before building 0.18.0 (it is gitignored output, not source).
- `.claude-plugin/plugin.json` → `0.18.0`; add keyword `pr-overrides`.
- `CHANGELOG.md`: `## [0.18.0] - 2026-09-02` with Added/Changed, same style as 0.17.0.
- `README.md`: Oracle `.env` table row; rewrite `### PR discovery` (`:202-210`) for multi-PR + primary + overrides; new `## Mem PR overrides (optional)` section after the dev-directory one; repo-layout entries for the new script/skill.
- `skills/oracle-storyteller/SKILL.md:96-100` (parked): change "First PR only" to "the primary PR (`pr_discovery.primary`); `mrs` is primary-first so the first entry is correct". One-line edit; skill stays parked.

---

## 5. Flow (Step 2 internals, per run)

```
1. load_env (.env in cwd); canary GET /repos/{repo} → exit 1 on failure (unchanged)
2. overrides = pr_overrides.load_overrides(--pr-overrides) ∪ --pr-override-inline pairs   (inline wins)
3. issues = Optimus query (unchanged); each issue gets created_at
4. pulls, stats = sweep_pulls(env)   # one sweep; window_full, oldest_updated_at
5. for each issue:
     mrs = matches from sweep (match_field → matched_via)
     if not mrs: mrs = search secondary (confirmed by /pulls/{n} + re-match)     # unchanged
     apply_overrides(...)            # may add one PR by number; sets decision=override
     decision, primary, candidates = choose_primary(mrs) unless override
     if not mrs:
        decision = no_pr_in_window if window_full and created_at and created_at < oldest_updated_at else no_pr_yet
        warning "no PRs found for {id} — it will appear without code context"   # unchanged text
     reorder mrs primary-first; set issue["pr_discovery"]
6. enrich_author_emails, fetch diffs for every PR in mrs (unchanged; override PRs included)
7. write JSON with top-level pr_discovery
```

---

## 6. Failure handling

| Failure | Behavior |
|---|---|
| Mem not connected / `list_notes` fails | Gather runs with no overrides. Preview says so. Step 10b falls back to `--pr-override-inline` (run-only). Never blocks. |
| `.env` has no collection id | Bootstrap by title; print id; suggest pinning it. If that fails → treat as not connected. |
| Override note malformed / PR not an integer | Ignored with a warning naming the note id. |
| Two override notes for one ticket | Newest `Resolved at` wins; warning; user can trash the stale one in Mem. |
| Override PR returns 404 | Override ignored; warning; decision falls back to sweep result. |
| Override PR not in sweep | One `GET /pulls/{n}`; included with diffs like any other PR. |
| Sweep fails after retries | `sweep.failed: true`, warning, every ticket without an override gets `no_pr_yet`; preview shows **"Sweep failed"** so the user does not read it as "no PRs". |
| `/search/issues` fails for one ticket | That ticket → `no_pr_yet`; warning; others unaffected. |
| ≥2 open non-draft PRs | `flagged_ambiguous`; **posting is not blocked** (all PRs still listed); preview invites an override. |
| Ticket older than the window, nothing found | `no_pr_in_window`; preview labels it distinctly; override is the remedy. |
| Mem write fails after a pick | Pick still applied for this run via inline override; user told the note was not saved. |

---

## 7. Tests

**`tests/test_gather_oracle.py`** — extend `_pr` fixture with `state="open", draft=False, merged_at=None, updated_at=…`; extend `_issue` with `created_at=None`.
- `SweepMatchingTests`: `match_field` returns `branch` before `title`; unchanged regex cases still pass.
- New `PrimaryRuleTests`: one open → it; one merged + one closed-unmerged → merged; two merged (ORC-53 shape) → newest `merged_at`; two open non-draft → `flagged_ambiguous` with both candidates; two open, one draft → the non-draft; only closed-unmerged → `matched`, primary `null`.
- New `OverrideTests`: override wins over sweep; override PR not in sweep → exactly one `/pulls/{n}` call; 404 → warning + fallback; inline override beats Mem override; `matched_via: manual` on the override entry; `mrs` reordered primary-first.
- New `WindowTests`: `window_full` + older ticket + no hits → `no_pr_in_window`; not full → `no_pr_yet`; no `created_at` → `no_pr_yet`; `oldest_updated_at` computed from the sweep.
- `DiscoveryOrchestrationTests`: existing three still pass (one sweep, warning text, search confirm); add: top-level `pr_discovery.summary` counts; warnings present in JSON; sweep failure → `sweep.failed` and warning.
- `LoadEnvTests`: new key optional; absent → `""`.
- `PrEntryTests`: new keys present; old keys unchanged.

**`tests/test_pr_overrides.py`** (new, modeled on `test_dev_directory.py`): parse/render round-trip; stray note ignored; `load_notes` accepts object/list/missing; newest-wins on duplicates with warning; `pending` create; `pending` update carries `note_id` + `version`; bad ticket / bad PR → exit 1; URL filled from `--data` when present, else from `GITHUB_REPO`.

**`tests/test_build_plugin.py`**: `PR-MATCHER-PLAN.md` excluded.

**Unchanged suites** (`test_prepare_note_prompts.py`, `test_ip_guard.py`, storyteller-related) must still pass — they prove the additive-only contract.

---

## 8. Decisions made without asking (flag for the owner)

1. **Override recording lives in the preview step** (10b) as `ORC-232 → 415` replies, then a gather re-run. This is the existing remedy the command already promises ("give me the PR number and I'll re-run the gather") made real. Alternative would be a separate `/oracle-pr-override` command; rejected per "internal to `/oracle-release`".
2. **`.env` id with title-search fallback.** Pure `.env`-only would make first-time setup manual; the fallback reuses the dev-directory bootstrap and prints the id to pin. If you want strict `.env`-only, delete the fallback row in §2/§6.
3. **Note format is dev-directory field lines, not a JSON block**, to match this repo's Mem convention and parser.
4. **`flagged_ambiguous` does not block posting.** The release still lists every PR; the flag is advice in the preview.
5. **`--pr-override-inline`** exists so a pick works even without Mem. Small, and it is the same code path.
6. **Inline override beats Mem override** for the same ticket in the same run (the human is typing it right now).
7. **Optimus `created_at`** is exposed on the issue solely for `no_pr_in_window`; if the raw task payload has no creation timestamp, that decision simply never fires and the code stays.
8. **`oracle-storyteller` one-line edit** even though the skill is parked, so it does not drift from the new contract.

---

## 9. Out of scope (v1 of this feature)
- Fuzzy/semantic matching when no ID exists anywhere.
- Writing PR links back to Optimus (field not API-writable/readable).
- A standalone matcher command or use outside `/oracle-release`.
- Porting anything to `medtrics-qa-automation` (the uncommitted work there is not part of this plan).
- Detecting unrelated work that shares a ticket ID — the override is the remedy.

---

## 10. Build & verify
1. Implement §4 in this checkout. No commit (not a git repo).
2. `python3 -m pytest tests -q` (or `python3 -m unittest discover -s tests`) — all green, no new warnings.
3. `python3 scripts/build_plugin.py` → `medtrics-release-notes-0.18.0.plugin`; `tests/test_build_plugin.py` green.
4. Manual smoke (owner, needs live `.env`): `python3 scripts/gather_notes_data_oracle.py tickets ORC-53 --dry-run` should show two PRs with a primary and decision `matched`.
