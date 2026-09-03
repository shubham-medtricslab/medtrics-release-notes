# medtrics-release-notes — Refactor Plan (v0.8.0 → 0.9.0)

**Date:** 2026-08-25
**Scope decision (owner):** PDF generation is removed entirely. The product is Slack
message generation and delivery only. `/client-brief` and `/covista-weekly` do not
exist in this checkout and are out of scope.
**Status:** Plan reviewed by an adversarial subagent pass; all review findings folded in.

---

## Background

- This checkout is **v0.8.0** (5 commands, 5 scripts, 6 skills, ~1,600 lines of Python).
  The Aug-21 handoff PDF audits a newer v0.15.0 tree that is not on this machine; only
  the findings verified against *this* code are actioned here.
- Verified-real defects in this tree: build blocker (description > 256 chars, and the
  failed build leaves the artifact on disk), `.env` not excluded from the bundle,
  infinite Optimus pagination loop, missing HTTP timeouts, exit-on-first-error on
  Optimus, Slack delivery fragility (no retry, broken channel heuristic, non-atomic
  result reporting).
- Product end state: **gather tickets → write narrative → guard it → post one linted
  Slack message (root post + thread replies). Nothing else.**

---

# Phase 1 — Buildable, PDF-free, correct data

## Step 1 — Make the bundle buildable and safe

### 1a. `.claude-plugin/plugin.json`
- Rewrite `description` to ≤ 256 characters (currently 425 — the build fails on its own
  cap; reproduced by running `scripts/build_plugin.py`). New text must reflect the
  message-only scope. Count with Python `len()` before committing (the cap is
  characters, not bytes — `build_plugin.py:141`).
- Drop `"pdf"` from `keywords`.
- **Do not bump the version yet** — the bump to `0.9.0` lands together with the
  CHANGELOG entry in Step 5b (review finding: version and changelog land as one change).

### 1b. `scripts/build_plugin.py` — bundle hygiene
- Add to `EXCLUDE_FILES` (line 37): `.env`, `.env.local`, plus a rule skipping any
  filename that starts with `.env`.
- Generalize exclusions (review finding — the walk currently bundles anything in the
  root): add `*.pdf`, `*.tmp`, `*.zip`, `*.log`, `PLAN.md` to suffix/file excludes and
  `dist`, `build`, `node_modules`, `.cowork`, `.claude` to `EXCLUDE_DIRS`. Today the
  build would ship the 713 KB internal handoff PDF sitting in the repo root.
- Hard assertion in `_verify_bundle`: fail if any zip entry's basename starts with
  `.env` or ends with `.pdf`.
- Atomic output: write to `<name>.plugin.tmp`, verify, then rename; delete the tmp on
  failure. Note: `_verify_bundle` currently fails via `sys.exit` (`:127-144`) — refactor
  it to raise an exception so the caller can clean up (review finding 9). `.tmp` must be
  in the excludes or an orphaned tmp gets bundled into the *next* build.
- **Test in the same change** (`tests/test_build_plugin.py`): planted dummy `.env`
  never appears in the zip; description > 256 fails *and leaves no artifact*; the
  handoff PDF is absent from the bundle.

## Step 2 — Strip the PDF track (full sweep)

The review found PDF references far beyond `/release-slack`. All of the following:

| File | Action |
|---|---|
| `skills/release-brief-pdf/` | **Delete directory** |
| `skills/integration-brief-pdf/` | **Delete directory** |
| `commands/release-slack.md` | Remove PDF render steps and `--pdf`/`--title` args from the `slack_upload.py` invocation; pipeline becomes gather → analyzers → storyteller → copy-audit → ip-guard → compose message → lint → preview → post. Update frontmatter description. Update the result-block parsing (`:230-232`) for the new JSON shape. |
| `scripts/slack_upload.py` | Delete `upload_pdf()` and the `--pdf`/`--title` args (~60 lines). Keep `slack_get` — still used by `resolve_channel` (`:103`). Update the module docstring (`:2-24`), exit-code doc (`:21` says "all PDFs uploaded"), and drop the `"uploaded"` key from the result JSON (`:240`). |
| `skills/ip-guard/SKILL.md` | Its documented pipeline says "5. invoke release-brief-pdf" (`:87-97`; also `:8`, `:31`, `:105`, `:142`) — remove all PDF steps/references. |
| `skills/storyteller/SKILL.md` | "The release-brief PDF skill renders this markdown" (`:92`, `:116`, `:185`, `:202`) — reword to markdown/Slack-message consumers. |
| `commands/deploy-notes.md` | PDF prose at `:195`, `:203`, `:233` — remove. |
| `commands/integration-notes.md` | Instructions written *for* the PDF renderer (`:208`, `:217`) — remove or reword. |
| `commands/internal-notes.md` | Two "run with no `--pdf` flags" lines (`:241`, `:291`) become nonsense once the flags are deleted — remove. |

Keep `/deploy-notes`, `/integration-notes`, `/notes` as markdown-only building blocks
(they feed the message pipeline and are already PDF-free at runtime).
CHANGELOG history entries mentioning PDFs stay — they are history.

**Bot scope note for docs:** only `files:write` becomes droppable. `channels:read`
(and `groups:read` for private channels) must stay — `resolve_channel` calls
`conversations.list` (`slack_upload.py:103`) whenever a `#name` is configured.
(Review blocker 2: the original plan's "reduce to chat:write" would have broken
channel-name resolution.)

## Step 3 — Fix the verified pipeline bugs in `scripts/gather_notes_data_optimus.py`

| Bug | Location | Fix |
|---|---|---|
| **H1** — infinite loop when Optimus returns an empty page while `meta.count > offset` | `:173`, `:217` | New terminator, robust to a missing/zero `meta.count` on a non-empty page (review finding 5 — the naive fix silently truncates after page 1): break if `not tasks`; else if `count` present, break when `offset + len(tasks) >= count`; else continue only while `len(tasks) == limit`, and **log** any truncation. |
| **H2** — no socket timeout; half-open TCP hangs forever | `:89` (Optimus), `:119` (GitLab) | `timeout=30` on both `urlopen` calls. |
| **H3** — one Optimus HTTP error → `sys.exit(1)`, all work discarded | `:91-94` | Mirror the GitLab retry already in the same file (`:116-151`): retry 429/5xx with backoff + `Retry-After`; raise a typed exception instead of exiting; `main()` catches and exits non-zero with a clear message. |
| **Timeout exception coverage** (review finding 6) | `:88-94`, `:133` | Both request helpers catch only `HTTPError`; a timeout raises `URLError`/`TimeoutError` and would crash the gather. Catch `URLError` + timeout in **both** the Optimus and GitLab paths and route into the retry logic. |

**Tests in the same change** (`tests/test_gather.py`): pagination terminator against
fake page sequences (empty page, short page, count-exact, missing-count); retry path
against mocked 429 → success; `mr_matches_identifier` boundary cases (`M1-59` must not
match `M1-597`; must match title `"M1 808 change"`; case variants).

**Phase 1 exit criteria**
- `python3 scripts/build_plugin.py` exits 0; bundle contains no `.env`, no `*.pdf`,
  no PDF skills.
- No file in the repo references the deleted skills or `--pdf` flags.
- Gather can neither hang nor discard a run on a single transient error.
- `python3 -m unittest discover tests` passes.

**Effort:** ~1 day.

---

# Phase 2 — The Slack message as the product

**Order matters (review blocker 3):** delivery/thread support (4c) lands **before** the
format that depends on it (4a).

## Step 4c — `scripts/slack_upload.py` delivery hardening (land first)

- **Retry:** port the GitLab 429-retry loop, honoring `Retry-After`.
  **Do not blind-retry 5xx on `chat.postMessage`** (review finding 7 — if Slack accepted
  the write before erroring, a retry double-posts to the client channel). Policy:
  429 → retry all calls; 5xx/timeout → retry **reads only** (`conversations.list`);
  for posts, fail with a clear "verify in channel before re-running" message.
- **Channel resolution** (`:93`): replace the heuristic with
  `re.fullmatch(r"[CG][A-Z0-9]{7,}", channel)` — fixes the real bug (G-prefixed private
  channel IDs are currently shunted into a name lookup that can never match). Leading
  `#` always means a name. Length left unbounded (Slack has lengthened IDs before).
- **`--thread-ts` flag:** post a message as a reply into an existing thread. This is
  what the index format's detail replies and resume-after-partial-failure both use.
- **Result contract:** emit the `---SLACK_UPLOAD_RESULT---` JSON block on **every**
  exit path — `{ok, channel, ts, error}` — not only on success, so the command can
  always report faithfully and capture `ts` for thread replies.
- Message length pre-check: fail before calling the API if the text exceeds the cap.

## Step 4a — Two audience-specific message formats (review blocker 1)

The original plan's single format would have leaked internal data into the client
channel. Two formats, both index-style (root ≤ 2,500 chars — Slack collapses ~3,000;
detail in thread replies; **never** a second top-level message):

**`/internal-notes` → `#dream-team` (internal):**
- Root: numbered one-line entries `ID · title · <@U…>` under `*Enhancements & New
  Features*` / `*Bug Fixes*` headers (blank line before each header), plus a
  `Clients to notify:` line.
- Thread: one long-form reply per section; escalations as their own reply.
- Rewrite composition rules in `skills/internal-storyteller/SKILL.md` and
  `commands/internal-notes.md`.

**`/release-slack` → client channel (customer-facing):**
- Root: numbered one-line entries `ID · title` only. Ticket IDs stay (v0.8.0
  deliberately made them client-facing — clients quote them to support). **No dev
  mentions, no client names, no internal URLs.**
- Thread: exec-voice per-ticket detail from the storyteller output.
- Rewrite composition rules in `commands/release-slack.md` and
  `skills/storyteller/SKILL.md` (its output contract currently targets the deleted PDF).

**Orchestration:** command posts root via `slack_upload.py`, captures `ts` from the
result block, then posts each thread reply with `--thread-ts <ts>`.

## Step 4b — New `scripts/slack_lint.py` (deterministic, stdlib, fail-closed)

Runs before the human preview in both Slack commands, with a fix-and-relint loop
(same pattern ip-guard already uses). Because it is fail-closed, it needs a **precise
token grammar** (review finding 8), not a keyword checklist:

- Tokenize first: code spans, `<@U…>` / `<@W…>` mentions (W = enterprise grid —
  review finding 17), `<url>` and `<url|label>` links, `<!here>`. Rules apply outside
  tokens only.
- Mentions must be bare `<@U[A-Z0-9]{7,}>` — no trailing display name (root cause of
  the "@Marina Melo Melo Pires" class of bugs).
- Links: `<url|label>` requires a non-empty label and a space before the token.
  **Raw auto-linked URLs stay legal** — the internal-storyteller mandates them for
  ticket links (`SKILL.md:55`, `:106`); the original plan's require-label rule
  contradicted the product and is dropped.
- Reject `**bold**`, `#` markdown headers, fenced code blocks (mrkdwn supports none).
- Escape-check `&`, `<`, `>` outside tokens.
- Blank line required before section headers.
- Char caps: hard-fail root > 2,500; thread reply > 3,900; anything > 40,000 (API max).
- Contract: JSON on stdout, human summary on stderr, exit **0 pass / 1 config error /
  2 fail** (matches ip_guard's three-code contract — review finding 20).
- **Tests in the same change:** one passing + one failing fixture per rule.

## Step 4d — Re-point the confidentiality gate at the message (review finding 19)

With PDFs gone, the composed Slack message is the **only** client deliverable — and
today `ip_guard.py` scans the intermediate markdown, never the message. Changes:
- `/release-slack` runs `ip_guard.py` against the composed root post **and** every
  thread reply before preview.
- `ip_guard.py`: the verbatim-ticket-title exemption keys on markdown `##` headings
  (`TICKET_HEADER_RE`, `:195`), which don't exist in mrkdwn. Add an mrkdwn-aware
  exemption for the numbered `ID · title` entry lines so verbatim titles don't
  hard-fail every post.
- `/internal-notes` output stays un-guarded (internal vocabulary is the point).

## Step 5 — Remaining tests + documentation truth pass

### 5a. Tests (`tests/`, stdlib `unittest`, fixtures in `tests/fixtures/`)
Phase 1 and 4b/4c tests already landed with their changes. Remaining:
- `resolve_channel`: C-id accepted, G-id accepted, `#name` resolved via mocked
  `conversations.list`, garbage rejected.
- `slack_upload` result block emitted on failure paths (mocked API errors).
- `ip_guard`: mrkdwn ticket-line exemption; one block + one warn case per category.
- Runner: `python3 -m unittest discover tests` — zero external dependencies;
  `tests/` is already excluded from the bundle by `build_plugin.py`.

### 5b. Docs
- **README:** version line (`:161` says 0.7.0); delete PDF-skill rows and all PDF
  mentions (`:31-32`, `:97`, install steps); `/release-slack` row → message-only;
  remove the false "shells out to `git`" claim (line **42**); fix the fictional
  repository-layout section (`:165-178` lists `tests/`, `CONTRIBUTING.md`,
  `requirements*.txt`, "pytest suite" — none existed; now describe the real `tests/`);
  Slack scopes → `chat:write` + `channels:read` (+ `groups:read` if private channels),
  `files:write` removed.
- **Commands:** fix "six required keys" (both Slack commands — it's 5 + 1 optional);
  remove the documented-but-never-read `OPTIMUS_BASE_URL` key
  (`deploy-notes.md:58` — the script hardcodes it and `load_env` won't read it);
  fix `notes.md:221` subagent path to `${CLAUDE_PLUGIN_ROOT}/commands/…`.
- **CHANGELOG:** write the `0.9.0` entry (PDF removal is a major design reversal —
  record it explicitly; the audited tree's worst failure was exactly an unrecorded
  reversal); fix the link footer (`:241-249` — missing 0.7.0/0.8.0 compare links,
  `[Unreleased]` still compares against 0.6.0).
- **`plugin.json`:** bump `version` → `0.9.0` **now**, together with the CHANGELOG.
- Delete `PLAN.md` (this file) from the repo before the release build, or rely on the
  Step 1b exclusion.

**Phase 2 exit criteria**
- One command run produces: a linted, IP-guarded, ≤2,500-char root post + threaded
  detail, correct mentions/links, posted with retry protection and honest result
  reporting; re-run after partial failure resumes into the same thread.
- Every fixed bug has a test; `python3 -m unittest discover tests` passes.
- README/CHANGELOG describe exactly what ships.

**Effort:** ~2–3 days.

---

## Sequencing summary

```
Phase 1:  1a → 1b (+tests) → 2 (full PDF sweep) → 3 (+tests)
Phase 2:  4c (+tests) → 4a → 4b (+tests) → 4d → 5a → 5b (version bump last)
```

## Out of scope (explicitly)

- `/client-brief`, `/covista-weekly`, and everything else from the unreleased v0.15.0
  tree (QA GIFs, client scoping, config layer, Gmail delivery, Oracle profile).
- Migrating Slack delivery to the Slack MCP (possible future step; bot token stays).
- Unattended/scheduled operation (requires guardrails work beyond this scope).
- Token rotation — not applicable to this machine (no `.env` ever existed here);
  flagged as a question for the v0.15.0 tree's owner.
