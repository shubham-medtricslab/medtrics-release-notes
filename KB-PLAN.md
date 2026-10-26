# KB Article Plan — one draft KB article per shipped legacy feature, delivered from Cowork

**Status:** proposed, not built. Supersedes the hand-run pipeline in `extra/kb-pipeline/` (18–19 Oct 2026) and productizes the format standard in `extra/kb_*.py`.
**Revision:** v1 (2026-10-26).
**Target versions:** v1 → **0.21.0**, v2 → **0.22.0**, v3 → **0.23.0**.

**Owner decisions (locked):**
- Legacy (M1, Optimus + GitLab) only. **Features only** — Optimus `task_type ∈ {feature, critical_feature}`, filtered by field before any AI call.
- **One article per ticket**, .docx + PDF; never combined. A feature that changes an existing feature gets a new article, and the run lists the prior ticket/article found in Mem.
- Audience: the Intercom KB CX uses to answer clients — client-facing copy, so G6 applies.
- Runs in **Cowork**; output is files in the Cowork outputs folder. Mem is the system of record for history; the .docx/PDF are deliverables, not archives (same posture as `/deploy-notes` markdown).
- Phasing: **v1** manual `/kb-articles` (`tickets <ids>` / `since <date>` / `pending`); **v2** `--kb` on `/release-slack` and `/notes` **only enqueues** to Mem; **v3** scheduled weekly drain delivering a package + summary.
- Efficiency first. Grouped by persona × surface, ~6–8 tickets/group, groups in parallel; one writer per group, one structured spec per ticket, per-article retries only.
- Models pinned per subagent in a new `agents/` folder: Haiku classifies what fields can't place, Sonnet writes; tools restricted.
- Scripts do all deterministic work; AI writes prose fields only.
- Screenshots: live capture on `demo.medtrics.sg` with scripted Playwright, using a saved storage-state session that **a human** signs in to once per persona (the agent never types a password). Element box from the DOM (no vision, no grid). Session expiry stops cleanly and reports what's left.

---

## 1. Context & goals

### What exists today
| Piece | Where | State |
|---|---|---|
| Format as data | `extra/kb_format.py` | Calibri, navy/orange/red palette, anatomy, `LABEL_CUTOFF = 0.82`, `ANCHORS`, banned terms. Single source of truth. |
| Gates G1–G7 | `extra/kb_validate.py` | Fail-closed. **Stdlib only** (`os`, `re`, `kb_format`) — testable without dependencies. |
| Renderer | `extra/build_kb_doc.py` | `validate` / `draft` / `publish` → .docx + optional Intercom markdown (`--md`). **No PDF.** |
| QA GIF → shots | `extra/qa_gif_to_shots.py` | Segments states, OCR-verifies labels (verify only, never transcribe), screens tenant data CLEAR/REVIEW/BLOCK. |
| Annotator | `extra/qa_annotate.py` | `find_label_box` (OCR word boxes), click-transient fallback, orange ring + badge, **caption bar**, red watermark. |
| Hand pipeline | `extra/kb-pipeline/` | `tickets.json` (79), `match_candidates.json` (28), route docs, `house.py` (**red** rings), `build_docx.py` (different anatomy), 30 drafts in `articles_a/b.json`. |

### What the code says about the first batch
- `tickets.json`: 79 tickets — 74 `feature`, 5 `critical_feature`; the type filter keeps all.
- The 30 drafts cover ~37 tickets and another 18 have an `article` match, leaving **~25 tickets** as the default first batch.
- **Only ~5 of those 25 are live-capturable** (Captured 2, Partly captured 2, Ready to capture 2 minus overlap). The rest: 11 No demo data, 4–5 Needs admin pass, 4 No UI surface, 1 Needs SME. The first batch mostly exercises the missing-screenshot path — why §7.1 matters most.
- Persona skew: Admin 19 (across 15 surfaces, mostly one ticket each), Admin + Faculty 3, Faculty 1, Admin + Learner 1, token link 1. Strict persona × surface writer groups would mean ~15 one-ticket agents for 19 Admin tickets; §3.2 packs them instead (flagged deviation).
- **All 30 existing draft titles fail G2** ("Adding a Published Article…", "Advancing Trainees…" — no How to / What / Where / Understanding / Using). They can't be reused unchanged as exemplars, and re-rendering them through the new path would be refused.
- Three of the 25 IDs are **UUID prefixes** (`854f000c`, `6ac6a213`, `3aba78d8`); one is `M2-242`, one `GEN-21668`. See §9 Q5.

### Goals
1. `/kb-articles` turns shipped legacy features into G1–G7-valid **draft** articles (.docx + PDF + Intercom .md) in the Cowork outputs folder, with a run summary.
2. Every AI token goes to prose. Filtering, grouping, capture, annotation, evidence, validation, rendering, PDF, ledger and packaging are scripts.
3. Mem holds the ledger, queue and catalogue. Runs are idempotent: a ticket already in the ledger is skipped before any AI call.
4. Cost and tokens logged per run and per article.

### Non-goals
- Publishing to Intercom, or producing the `publish` render unattended (G4 needs `tenant_confirmed_by` — a human).
- Oracle (ORC) tickets, bugs, work types.
- Combining tickets, or updating an existing article in place.
- Drive upload (`extra/kb-pipeline/README.md`: inline base64 costs millions of tokens).

---

## 2. v1 pipeline

```
/kb-articles tickets <ids> | since <date> | pending        [Cowork; orchestrator = main session]
│
├─ 0  preflight   kb_doctor.py (deps, fonts, soffice, tesseract, playwright, storage states,
│                 CLAUDE_CODE_SUBAGENT_MODEL) + Mem MCP probe  ── hard stop if Mem missing
├─ 1  select      [script] ids ← argv | Mem release archive (release_archive.parse_note) | queue + archive
│                 ledger skip (Mem) → gather_notes_data_optimus.py tickets … --include-description
│                 kb_select.py filter: task_type ∈ {feature, critical_feature}
├─ 2  place       [script] kb_select.py place: kb_routes.json (module/route/template rules)
│     (unplaced) → [AI: kb-classifier, Haiku, ONE batched call] → persona, surface ∈ enum, has_ui
│                 kb_select.py group → groups.json (capture plan + writer groups) → plan preview
├─ 3  prior       [script] kb_ledger.py match: catalogue + ledger → top prior articles per ticket
├─ 4  inputs      [script] kb_inputs.py: description, AC, quoted UI strings, added UI strings + routes
├─ 5  capture     [script] kb_capture.py run --plan groups.json   (Playwright, headless, storage state)
│                 persona → surface: goto + recipe → per candidate label: DOM box → viewport PNG
│                 → OCR verify on crop (≥0.82) → tenant screen DOM+OCR → shots.json
│                 session expiry ⇒ exit 3 + resume.json
│                 [optional, §7.1] kb_qa_source.py: QA GIF frames for tickets demo can't show
├─ 6  write       [AI: kb-writer, Sonnet, ALL groups in ONE message] → JSON prose per ticket
├─ 7  assemble    [script] kb_spec.py assemble: prose + evidence → spec.json; annotate
│                 (badge = step number; watermark non-CLEAR); unverifiable shot ⇒ missing_shot
│                 kb_validate (draft) → prose-only failures → [AI retry, that article only, ≤2]
├─ 8  render      [script] build_kb_doc.py draft --md … ; kb_pdf.py convert (ONE soffice batch) + verify
├─ 9  deliver     [script] kb_package.py → <outputs>/kb-articles/<run_id>/… + RUN-SUMMARY.md + .zip
└─ 10 record      [Mem] kb_ledger.py render → ledger note per ticket + one run note
```

---

## 3. Design

### 3.1 Selection (`kb_select.py`)
- **`tickets <ids>`** — as given.
- **`since <date>`** — page `list_notes` on the release archive collection, reuse `release_archive.parse_note` (`scripts/release_archive.py`) to keep `Product: medtrics` notes with `Date ≥ date`, take their `Tickets:` IDs (drop `MR-NNNN`).
- **`pending`** (v1 meaning, flagged) — released legacy features with no ledger note: the `since` scan over the whole archive minus the ledger. After v2 it also unions `Status: pending` queue entries; the archive scan stays as fallback so a lost queue write never loses a ticket.
- Gather with `gather_notes_data_optimus.py tickets <ids> --include-description`; `task_to_issue` already carries `task_type`. `kb_select.py filter` drops non-features **before any agent spawns** and prints `skipped: N (type) · M (ledger)`.
- **Cost order:** the ledger skip runs before the gather when the ID list is known, so done tickets never trigger GitLab diff fetches.

### 3.2 Placement and grouping
- **`scripts/kb/kb_routes.json`** (new data), built once from `docs/ADMIN-URL-MAP.md`, `docs/persona-capture-routes.md` and `tickets.json`. Per surface:
  `{surface, persona, portal, audience, url, recipe:[{click|dblclick|wait_for|select: …}], match:{modules, url_prefixes, template_globs}, demo_status}`.
- Recipes encode the ADMIN-URL-MAP gotchas: tabs needing two clicks, curriculum opening on the 2021 period, date pickers, reports that download instead of rendering.
- **Deterministic placement first:** route prefixes from `urls.py` diffs → template paths → ticket module. Leftovers go to **one** batched `kb-classifier` call that may only return a surface from the enum or `UNKNOWN`.
- **Capture groups** are strict persona × surface (one visit per surface). A multi-persona ticket appears in each persona's capture pass but one writer group.
- **Writer groups (flagged deviation):** pack surfaces of the *same persona* in route order up to **8 tickets**, splitting anything larger. Strict persona × surface would give ~15 one-ticket agents for 19 Admin tickets, wasting the cached prefix; persona, portal and voice stay shared.

### 3.3 Prior-article lookup (`kb_ledger.py match`)
- Candidates: the **catalogue** (seeded from `kb2.csv`) + the **ledger** (earlier KB tickets, incl. the 30 seeded legacy drafts).
- Scoring reuses the `match_candidates.json` approach (title/description token overlap + module), top 4. Optional `search_notes` second signal, off by default.
- Output is *listed* in the writer input (titles only), `meta.prior`, the ledger and the run summary. It never changes one-ticket-one-article.

### 3.4 Trimmed writer inputs (`kb_inputs.py`)
Per ticket, capped ~4,000 chars: title; description with `Acceptance criteria` split out; quoted strings from description/AC (candidate labels); user-visible strings **added** in template/JS/Vue hunks (`_()`/`gettext`, `verbose_name`, `label=`, button/heading text); changed URL patterns; queued `CLIENT_TITLE` / `EXEC_SUMMARY` when it came via `--kb`. Full diffs never reach a model.

### 3.5 Live capture (`kb_capture.py`)
- **`login --persona admin|faculty|learner`** — run **by the human, headed**. Opens Chromium at the demo login, waits for the user to sign in, saves `storage_state` to `$KB_SESSION_DIR/<persona>.json` (default `<workspace>/.kb-sessions/`, gitignored). The command never invokes `login` itself.
- **`check`** — cheap GET of each persona's home route → `valid | expired | missing`. Run at preflight.
- **`run --plan groups.json`**
  - Headless, one context per persona, viewport 1440×900, `deviceScaleFactor: 2` for crisp OCR crops (downscaled to doc width later).
  - Per surface: goto + recipe. **Expiry:** final URL is the login path or a password field is present → `SessionExpired`, write `resume.json` (remaining persona/surfaces/tickets), exit **3**, keep what was captured.
  - Per candidate label: `page.get_by_text(label)` first visible match → `scroll_into_view_if_needed` → `bounding_box()`. **The DOM box is the anchor.**
  - One raw viewport PNG per *state*, shared by all tickets on that surface; record `dom_text`, `dom_box`, URL, timestamp.
- **OCR still runs — on the cropped DOM box** (padded 12 px, 2×) with the existing `verify_labels` logic and 0.82 floor. Evidence is written as `anchor: "label_box"` with a real `label_score`, so **G3 holds unchanged** and `kb_validate` needs no change. DOM text alone can't prove the pixels show the label (covered, clipped, overlaid); the crop is also cheaper and more reliable than full-frame OCR.
- **Tenant screen** on two inputs, worst verdict wins: (1) existing `screen_frame` OCR rules on the full frame; (2) the same regexes on `page.inner_text("body")` — DOM text catches small sidebar text OCR missed on M1-1337 (`kb_validate.py` G4 comment). Needs a **demo allowlist** in `kb_routes.json` (`demo.allow_strings`, `demo.domain`), otherwise every demo frame screens REVIEW ("Medtrics Medical Center" hits `INSTITUTION_RE`, "Medicine" hits `DISCIPLINE_RE`). Current default `medtrics.dev` is the QA tenant's domain.
- **Output:** `/tmp/kb/<run>/shots/<group>/shots.json`: `{shot_id, ticket, persona, surface, frame, dom_box, label, label_score, tenant, tenant_findings, source}`. Failed verifications kept with `verified: false` and never offered to the writer.

### 3.6 Writing, assembly and retries
- **Writer** (`agents/kb-writer.md`, `model: sonnet`) reads `writer_<group>.md`: per ticket the §3.4 data, prior article titles, and **verified shots as text** (`shot_id`, label, state description, tenant verdict) — no images sent.
- Returns one JSON object per ticket between `---KB_SPECS_START---` / `---KB_SPECS_END---` (§5.3). May only reference listed shots and must print a shot's verified label in **bold** in that step.
- **Assembly (`kb_spec.py assemble`)**, deterministic:
  - Merges prose with evidence; maps `shot_id` → `image` + `evidence`.
  - Sets `label_as_written` when bold text differs from the label only in the variable slot (`kb_validate._generalise`).
  - Unknown `shot_id`, or a shot failing G3/G4/G5 → `missing_shot: "<reason>"`, image removed (the article still renders as a draft).
  - Sets `meta` (ticket, audience, portal from `kb_routes`, persona, surface, prior) and `provenance.note`.
  - **Annotates only now** — the badge number is the step index, known only after the writer orders steps.
- **Retries:** `kb_validate.check(spec, "draft")`. Evidence gates (G3 anchor/score, G4, G5) are fixed by script, never AI. Prose gates (G2, G6, G3 "label not bold") send the failure list + that ticket's prose to a single-ticket `kb-writer` retry, max 2. Then `failed` in summary and ledger, no file written.

### 3.7 Annotation — one source of truth
- **`kb_format.py` wins:** **orange** (`ED601A`) ring + numbered orange badge, **no caption bar**, red (`B00020`) reserved for watermark and draft banner. Reasons: it's the format of record measured from M1-1399; `build_kb_doc.py` already prints captions under images; red rings would collide with red's "not for publication" meaning.
- `house.py` (red) is retired; its sidebar-snap badge placement moves into `qa_annotate.annotate`.
- New `kb_format.py` constants: `RING_WIDTH_PX`, `RING_RADIUS_PX`, `RING_PAD_PX`, `BADGE_RADIUS_PX`, `BADGE_SIDEBAR_SNAP = 0.32`, `SHOT_PX_W = 1200`, `SHOT_FORMAT = "JPEG"`, `SHOT_QUALITY = 85` (flagged). `qa_annotate.py` imports them instead of local `ACCENT`/`INK`.
- **Font ladder** (replaces the hardcoded DejaVu path): `KB_FONT` env → DejaVu (Linux/Cowork) → macOS Arial Bold → `ImageFont.load_default(size=…)` (Pillow ≥ 10.1). Today macOS silently falls back to a tiny bitmap font.

### 3.8 Mem
| Collection | Env key | Default | Notes |
|---|---|---|---|
| KB ledger | `MEM_KB_LEDGER_COLLECTION` | `KB Article Ledger` | one per ticket (`KB: …`) + one per run (`KB run: …`) |
| KB queue (v2) | `MEM_KB_QUEUE_COLLECTION` | `KB Queue` | one per enqueued ticket (`KB queue: …`) |
| Catalogue | `MEM_KB_CATALOGUE_COLLECTION` | `KB Catalogue` | one per published Intercom article (`KB article: …`) |

- Resolved by name with the paginated `list_collections` ladder from `skills/release-archive/SKILL.md`.
- **Mem is required** for `/kb-articles` (hard stop at Step 0) — the ledger is the record.
- **Idempotency:** ledger dumped once per run. `Status: drafted | published | legacy-draft` skipped unless `--force`; `--force` does a version-checked `update_note` (one note per ticket — deliberately unlike the archive's never-update rule, since this is per-ticket state). `failed` / `partial` retry next run.
- **One-time seeds:** `kb_ledger.py seed-legacy` (the drafted + article-matched tickets from `tickets.json` / `articles_a|b.json` as `legacy-draft` / `matched`, leaving the ~25 remaining); `kb_ledger.py seed-catalogue --csv kb2.csv` once the export is provided.
- **Never in Mem:** .docx/PDF/PNG bytes, descriptions, diffs, storage states.

### 3.9 PDF (`kb_pdf.py`)
- **Convert:** one batch `soffice --headless --convert-to pdf --outdir <dir> <all .docx>` with a private `-env:UserInstallation=file:///tmp/kb/lo-profile`.
- **Verify** (pypdf): ≥1 page, Letter; embedded image count = non-missing shots; text contains title, `Overview`, `Key considerations`, `Conclusion` and every bolded label; fonts `Calibri`/`Carlito` pass, anything else → `WARN font substituted` in the summary.
- **One-time fidelity check:** a spec rebuilt from M1-1399 rendered side by side with `extra/M1-1399.pdf`, result recorded in the CHANGELOG.

### 3.10 Delivery (`kb_package.py`)
Output root: `$KB_OUTPUT_DIR` → `/mnt/user-data/outputs` if present (Cowork) → `.claude/outputs/kb/`.
```
<root>/kb-articles/<run_id>/
  RUN-SUMMARY.md
  M1-1520/CX-DRAFT_How to Land Migrated Session Schedules as Unpublished.{docx,pdf,md}
  M1-1520/assets/01.jpg …
  _provenance/M1-1520.md            (see §9 Q3)
  kb-articles-<run_id>.zip
```
`RUN-SUMMARY.md`: per ticket — title, persona/surface, shots captured/missing with reasons, tenant tally, gates, retries, prior article(s), files; plus **Uncapturable**, **Left by session expiry** (from `resume.json`), Skipped (type/ledger), and the token/cost table.

### 3.11 Token and cost logging (`kb_usage.py`)
- After each Task, append `{agent, group|ticket, model, total_tokens, duration_ms, tool_uses}` to `/tmp/kb/<run>/usage.jsonl`.
- Per article: group tokens split by each ticket's share of writer-input characters + its own retries; the classifier call spread across the tickets it placed.
- Run total from a price table in `kb_usage.py`, filled from current pricing at build time.
- Limitation stated in the summary: Task usage is total-only (no input/output/cache split) and the orchestrator's own tokens aren't visible — estimates.
- Totals go into each ledger note and the run note.

---

## 4. File-by-file changes (v1)

### Vendored KB code — `scripts/kb/` (flat modules; each CLI inserts its own dir on `sys.path`)
| File | Change |
|---|---|
| `kb_format.py` | Moved from `extra/`. Annotation constants (§3.7). `FORMAT_VERSION` → `1.1`. |
| `kb_validate.py` | Moved. Add **G7b**: `publish` refuses any `missing_shot`. Stays stdlib-only. |
| `build_kb_doc.py` | Moved. Draft renders `missing_shot` as a red `[Screenshot missing — <reason>]` line. `--provenance` (§9 Q3). Lazy `docx` import so `validate` runs without python-docx. |
| `qa_gif_to_shots.py` | Moved. `screen_frame(…, allow_strings=())`, `screen_text(text, …)` for the DOM path, `verify_crop(img, box, labels)`. |
| `qa_annotate.py` | Moved. Imports `kb_format`; no caption bar on the KB path; font ladder; sidebar badge snap; `find_label_box` uses `Output.DICT` not `DATAFRAME` (**drops a hidden pandas dependency**); lazy `scipy` (GIF path only). |
| `kb_routes.json` | **New.** Surfaces, recipes, persona → audience/portal, demo allowlist. |
| `kb_select.py` | **New.** `filter`, `skip`, `since`, `pending`, `place`, `group`, `classifier-bundle`, `classifier-merge`. |
| `kb_inputs.py` | **New.** Trimmed per-ticket inputs. |
| `kb_capture.py` | **New.** `login` (human, headed), `check`, `run`, `resume`. |
| `kb_spec.py` | **New.** `bundle`, `assemble`, `retry-bundle`, `ingest`. |
| `kb_pdf.py` | **New.** `convert`, `verify`. |
| `kb_ledger.py` | **New.** Render/parse ledger, queue, catalogue, run notes; `pending`/`resolve`; `match`; `seed-legacy`; `seed-catalogue`. Mirrors `release_archive.py`. |
| `kb_usage.py` | **New.** `add`, `report`. |
| `kb_package.py` | **New.** Output layout, `RUN-SUMMARY.md`, zip. |
| `kb_doctor.py` | **New.** Dependency/font matrix, `soffice`/`tesseract`/chromium probes, `CLAUDE_CODE_SUBAGENT_MODEL` warning, storage-state check; prints install lines for anything missing. |

`extra/` stays as the unshipped working area; archive it once `scripts/kb/` is green.

### Agents — new `agents/` folder
- **`agents/kb-classifier.md`** — `model: haiku`, `tools: Read`. Body: the stable surface-enum rules. Output: `[{ticket, persona, surface|UNKNOWN, has_ui, reason}]`.
- **`agents/kb-writer.md`** — `model: sonnet`, `tools: Read` (see §9 Q1). **The body is the cached stable prefix:** `kb_format` anatomy, G2 title phrases, `BANNED_SUBSTRINGS`/`BANNED_PATTERNS` verbatim, bold-means-UI-label, client voice (from `skills/engineering-to-client`), output contract, and **one short exemplar per persona** (Admin, Faculty, Learner — rewritten from `articles_a.py` to pass G2; the Learner one is new). All three live in the body so the prefix never varies.
- Invoked as `subagent_type: "medtrics-release-notes:kb-writer"` **plus** an explicit `model` on each Task call as a second pin.
- Verify: `kb_doctor` warns if `CLAUDE_CODE_SUBAGENT_MODEL` is set; confirm org settings allow Haiku; smoke one group and check the model split in Cowork usage.

### Command and skills
- **`commands/kb-articles.md`** (new), shaped like `release-slack.md`: Usage, Pipeline overview, Output, `.env` keys, permissions, Steps 0–10, failure modes, "does NOT do", coding standards. Flags: `dry-run`, `--force`, `--no-capture`, `--yes`, `--unattended` (v3). Step 2 ends with a **plan preview** (groups, tickets, uncapturable, expired sessions, estimated tokens) and waits for `y` unless `--yes`/`--unattended`.
- **`skills/kb-ledger/SKILL.md`** (new) — collections, resolution ladder, note schemas, what goes in Mem, failure handling. Modeled on `skills/release-archive/SKILL.md`.
- **`skills/kb-capture/SKILL.md`** (new) — human-only sign-in per persona, where storage states live (never Mem/outputs), expiry and resume, adding a surface or recipe to `kb_routes.json`.

### Packaging, docs, version
| File | Change |
|---|---|
| `scripts/build_plugin.py` | Add `extra` and `.kb-sessions` to `EXCLUDE_DIRS_COMMON` — **today a build would ship `extra/*.py`** (only .pdf/.zip are excluded by suffix). Add `KB-PLAN.md` to `EXCLUDE_FILES`. `_verify_bundle` asserts `scripts/kb/kb_format.py` and `agents/kb-writer.md` are present and no `extra/` or `.kb-sessions/` entry exists. |
| `.gitignore` | `.kb-sessions/`. |
| `.claude-plugin/plugin.json` | 0.20.0 → **0.21.0**; description ≤ 256 chars mentioning `/kb-articles`; keywords `kb-articles`, `intercom`, `kb-ledger`. |
| `.claude-plugin/marketplace.json` | Same version and description. |
| `README.md` | Commands row, skills rows, `.env` keys (`MEM_KB_*`, `KB_SESSION_DIR`, `KB_OUTPUT_DIR`, `KB_FONT`), a "KB articles" section, output paths, repo layout. |
| `CHANGELOG.md` | `## [0.21.0]` Added. |

### Tests — `tests/` (unittest, hermetic, style of `tests/test_release_archive.py`; dependency tests use `skipUnless`)
| File | Covers |
|---|---|
| `test_kb_validate.py` | Golden spec passes draft, plus **22+ deliberate mutations**, one assertion each — G1 missing keys/meta; G2 title length/phrase, overview words, step count, empty step, uncaptioned image, considerations, bold lead-in, empty conclusion; G3 bad/`none` anchor, no label/score, score 0.81, bad `label_as_written`, label not bold; G4 unscreened, publish REVIEW, publish CLEAR without confirmer, draft BLOCK unwatermarked; G5 missing file; G6 each banned class/pattern; G7 draft without provenance, publish `banner_override`, **G7b**. Plus G1 short-circuit and `_generalise`. Stdlib only. |
| `test_kb_format.py` | Palette hex, anatomy order, ANCHORS/TENANT tuples, `summary()`. |
| `test_kb_build_doc.py` | Draft banner/captions/missing-shot line; publish refuses non-CLEAR; markdown; Calibri and sizes. |
| `test_kb_select.py` | Type filter, ledger skip, `since` from archive fixtures, placement by route/template/module, UNKNOWN → classifier bundle, cap 8 with split and same-persona packing, multi-persona. |
| `test_kb_inputs.py` | AC extraction, added strings from diff fixtures, caps. |
| `test_kb_capture.py` | Playwright mocked: recipe parsing, expiry → exit 3 + `resume.json`, DOM box → evidence, shared state per surface. |
| `test_kb_screen.py` | Demo allowlist → CLEAR; DOM-text screen; worst-of merge. |
| `test_kb_annotate.py` | Orange ring pixels, badge number, red watermark on non-CLEAR, font fallback never bitmap when a TTF exists. |
| `test_kb_spec.py` | Assemble; unknown `shot_id`/failing evidence → `missing_shot`; `label_as_written` inference; retry bundle has only the failing ticket. |
| `test_kb_ledger.py` | Golden render/parse of all note types; non-prefixed notes ignored; `seed-legacy` remaining count; `kb2.csv` import; match ranking. |
| `test_kb_pdf.py` | Verify logic on a tiny fixture PDF; font-substitution warning. |
| `test_kb_usage.py` | Apportioning, retry attribution, totals. |
| `test_build_plugin.py` | Extend: `extra/`, `KB-PLAN.md`, `.kb-sessions/` excluded; `scripts/kb/` and `agents/` shipped. |

The golden spec should be the **M1-1399 reference `spec.json`** (not provided — §9 Q6); a synthetic golden spec works meanwhile.

---

## 5. Data contracts

### 5.1 Group manifest — `/tmp/kb/<run>/groups.json`
```json
{
  "run_id": "kb-20261027-0930", "date": "2026-10-27", "mode": "tickets",
  "capture_plan": [
    {"persona": "admin", "surface": "Curriculum > Setup > Migrate",
     "url": "/administration/curriculum/", "recipe": [], "tickets": ["M1-1520"], "session": "valid"}
  ],
  "writer_groups": [
    {"group_id": "admin-01", "persona": "admin", "portal": "Administration portal",
     "audience": "Program administrators",
     "surfaces": ["Curriculum > Setup > Migrate", "Curriculum > Map"],
     "tickets": ["M1-1520", "M1-1242"]}
  ],
  "uncapturable": [{"ticket": "M1-1224", "reason": "no UI surface"}],
  "unplaced": [],
  "skipped": [{"ticket": "M1-1413", "reason": "ledger: legacy-draft"},
              {"ticket": "M1-1600", "reason": "type: bug"}]
}
```

### 5.2 Writer input — `/tmp/kb/<run>/writer_<group_id>.md` (variable data only)
```
GROUP admin-01 · Persona: Admin · Portal: Administration portal · Audience: Program administrators

=== TICKET M1-1520 ===
Title: Curriculum migration — option to land migrated session schedules as Unpublished
Client title (from release): …            (only when queued via --kb)
Client summary (from release): …
Description (trimmed): …
Acceptance criteria: …
UI strings: "Migrate", "Land as Unpublished", …
Routes: /administration/curriculum/
Prior articles (list only, do not merge): CX-1782 How to View Option Values … (0.82)
Shots you may use:
  s1 · label "Land as Unpublished" · Curriculum > Setup > Migrate, migrate dialog open · CLEAR
  s2 · label "Migrate" · Curriculum > Setup · REVIEW
Missing: none
```

### 5.3 Writer output — per ticket, inside `---KB_SPECS_START---` / `---KB_SPECS_END---`
```json
{"ticket": "M1-1520",
 "title": "How to Land Migrated Session Schedules as Unpublished",
 "steps_heading": "Steps to migrate curriculum as unpublished",
 "overview": "… ≥40 words …",
 "steps": [{"text": "Open **Migrate** …", "shot": "s2", "caption": "…"},
           {"text": "Tick **Land as Unpublished** …", "shot": "s1", "caption": "…"},
           {"text": "…", "shot": null}],
 "considerations": ["**Lead-in.** …"],
 "conclusion": "…",
 "notes": ["reviewer note, optional"]}
```

### 5.4 Spec JSON — `/tmp/kb/<run>/specs/<ticket>.json` (existing `kb_validate` schema, additive keys only)
```json
{"title": "…", "overview": "…", "steps_heading": "…",
 "steps": [{"text": "…", "caption": "…", "image": "assets/M1-1520/02.jpg",
            "evidence": {"anchor": "label_box", "label": "Land as Unpublished", "label_score": 0.97,
                         "label_as_written": null, "tenant": "CLEAR", "watermarked": false,
                         "frame": "shots/admin/curriculum-setup-migrate/state02.png",
                         "source": "live:demo.medtrics.sg/administration/curriculum/ 2026-10-27T09:41",
                         "dom_box": [612, 388, 214, 32], "tenant_dom": "CLEAR", "tenant_ocr": "CLEAR"}},
           {"text": "…", "missing_shot": "no demo data on demo.medtrics.sg"}],
 "considerations": ["**…**"], "conclusion": "…",
 "meta": {"ticket": "M1-1520", "audience": "Program administrators", "portal": "Administration portal",
          "persona": "Admin", "surface": "Curriculum > Setup > Migrate",
          "prior": [{"id": "CX-1782", "title": "…", "score": 0.82, "source": "catalogue"}]},
 "provenance": {"note": "Captured on demo.medtrics.sg … annotated per kb_format 1.1"},
 "notes": []}
```

### 5.5 Ledger note (one per ticket; first line is the Mem title; rendered by `kb_ledger.py`)
```
KB: M1-1520 · How to Land Migrated Session Schedules as Unpublished

**Ticket:** M1-1520
**Optimus type:** feature
**Status:** drafted            (drafted | partial | failed | published | legacy-draft | matched)
**Article title:** How to Land Migrated Session Schedules as Unpublished
**Generated:** 2026-10-27
**Run:** kb-20261027-0930
**Render:** draft
**Persona:** Admin
**Surface:** Curriculum > Setup > Migrate
**Screenshots:** 2 of 3 (missing: 1 — no demo data)
**Tenant:** CLEAR 2 · REVIEW 0 · BLOCK 0
**Prior article:** CX-1782 How to View Option Values in Evaluation Template Questions (catalogue, 0.82)
**Files:** CX-DRAFT_How to Land Migrated Session Schedules as Unpublished.docx, .pdf, .md
**Tokens:** ~6,900 (writer, apportioned) + 0 (retries) · est. cost <from kb_usage>
**Published as:**              (filled by a human later: CX-NNNN)
```
Run note: `KB run: 2026-10-27 · kb-20261027-0930` — mode, counts, per-agent usage, uncapturable, left-by-expiry, output path.

---

## 6. Phases and acceptance criteria

### v1 (0.21.0) — `/kb-articles`
**Build order:** move code to `scripts/kb/` + safe build exclusions (gate tests green first) → ledger, catalogue, seeds → selection and placement → inputs → capture → agents → assemble/retry/render → PDF → package → command, docs, tests.

**Acceptance:**
- Existing tests plus new suites pass; `kb_validate` covered by the 22+ mutations.
- `kb_doctor` passes in Cowork; dependency matrix recorded (§7.3).
- `/kb-articles tickets M1-1520 dry-run` prints groups, capture plan, skips and token estimate, and spawns **no** agents.
- First batch (after `seed-legacy`): every ticket gets a draft (.docx + PDF + .md) **or** a named failure; uncapturable tickets render with `missing_shot` lines; `RUN-SUMMARY.md` lists uncapturable, skipped, expired groups and the token table; writer agents ≤ ⌈tickets/8⌉ per persona; ≤ 1 classifier call; retries single-article only.
- Re-running skips every drafted ticket before the gather and spawns zero agents.
- An expired storage state mid-run → capture exits 3; already-captured articles finish; the summary names the persona and surfaces left and the `kb_capture.py login` line to run.
- PDF verify passes (or only warns on font substitution); the M1-1399 visual check is recorded.
- Model pins verified.

### v2 (0.22.0) — `--kb` enqueues, then offers to draft in the same chat

**Revised by the owner on 2026-10-26** (supersedes the enqueue-only design below):
`--kb` is on `/release-slack` only (`/notes` is unchanged). After the post and its
archive note, Step 9c enqueues the release's feature tickets and hands exactly
those to `/kb-articles tickets <ids> --from-release` in the same chat, which reuses
`/tmp/release_slack_data.json` + `/tmp/release_slack_summaries.txt`, shows its plan
and asks y/n. "n" leaves them queued for `/kb-articles pending`. Drafted tickets'
queue notes are marked drained.

Original v2 text, kept for history:
- **`/release-slack --kb`:** new **Step 9c**, after the 9b archive once the post is complete. `kb_queue enqueue` with `--summaries /tmp/release_slack_summaries.txt` (CLIENT_TITLE, EXEC_SUMMARY, INTERNAL_SUMMARY), `--data` (for `task_type`), `--source release-slack --date … --ts …`. Filters to features, skips ledger/queue duplicates, creates `KB queue:` notes. Never blocks, never generates; on failure prints one line (the `pending` archive scan catches it anyway).
- **`/notes --kb`:** after Step 4, enqueue from `/tmp/release_notes_data.json` (+ summaries when present) with `Released: no` — status lanes are pre-release. Mem stays optional for `/notes`: no Mem → warn and skip. **Default (flagged):** v3 drains a `Released: no` entry only once its ID appears in a release archive note.
- **Acceptance:** enqueue spawns zero agents and zero model calls; duplicate enqueue is a no-op; non-features never enqueued; `/kb-articles pending` drains the queue using the queued summaries, with the input-token drop measured against v1 on the same tickets.

### v3 (0.23.0) — scheduled weekly drain
- A Cowork scheduled task runs `/kb-articles pending --unattended` weekly.
- Unattended rules: no prompts; **draft only**; `kb_capture.py check` first — groups whose persona session expired are **not** drafted and stay `pending` ("sign in as <persona> and re-run"); uncapturable tickets still draft with missing shots; package + `RUN-SUMMARY.md` delivered; run note written; drained queue notes get `Status: drained` (never trashed).
- **Acceptance:** two weekly runs with no new releases produce an empty run and a "nothing pending" summary with zero agents; expiry leaves those queue entries untouched; cost per run recorded.

---

## 7. Resolutions

### 7.1 QA GIFs as a capture source (recommendation, not a decision)
- **For:** no sign-in; follows the ticket's own QA checklist so it reaches post-click states a page-load recipe can't; `qa_gif_to_shots` / `qa_annotate` are proven on real GIFs; the draft banner text was written for this source.
- **Against:** QA tenants show client-shaped data (REVIEW/BLOCK and watermarks will be common); 256-colour palette hurts OCR and quality; QA1 is a pre-release build; per-ticket GIF coverage is **unknown** — the QA automation repo is no longer at `/Volumes/sj/medtrics/medtrics-qa-automation`.
- **Recommendation:** live Playwright first for surfaces marked capturable; **QA GIF first** for tickets the demo can't show (No demo data / Needs admin pass — 16 of the first 25); GIF as fallback when live verification misses. Do a **half-day spike** first (`kb_qa_source.py index`: ticket → GIF coverage for the 25) and build the GIF path only if coverage is meaningful. Evidence shape unchanged (`source: "qa1:<run>/<gif>#stateNN"`).

### 7.2 Annotation style
`kb_format.py` wins — orange ring + badge, no caption bar, red only for watermark/banner (§3.7). `house.py` retired.

### 7.3 Dependencies (verify with `kb_doctor` in Cowork)
| Dependency | Needed by | Expectation / action |
|---|---|---|
| python-docx, Pillow | render, annotate | Very likely present; `pip install` otherwise. |
| numpy | GIF segmentation | Likely present. |
| scipy | click-transient (GIF path only) | Lazy import. |
| pytesseract + **tesseract binary** | G3 OCR verify, tenant screen | **Highest risk** — the binary needs apt/root. Without it G3 can't pass and every shot becomes missing. §9 Q2. |
| pandas | was implicit in `find_label_box` | **Removed** (`Output.DICT`). |
| playwright + chromium | live capture | `pip install playwright && python -m playwright install chromium`; needs CDN egress and `--with-deps` libraries. Verify. |
| LibreOffice (`soffice`) | PDF | Probably present. Verify. |
| `fonts-crosextra-carlito` | PDF fidelity | Install if possible, else `kb_pdf verify` warns. |
| pypdf | PDF verify | `pip install`. |
| Annotation fonts | `qa_annotate._font` | Font ladder (§3.7). |

### 7.4 Where the code lives
`scripts/kb/` and `agents/`, shipped through `build_plugin.py`'s normal walk. New exclusions: `extra/`, `KB-PLAN.md`, `.kb-sessions/`, plus the `_verify_bundle` assertions.

### 7.5 Mem and idempotency
§3.8. Ledger skip before the gather; one ledger note per ticket (version-checked update under `--force`); queue uses `Status:`; drained notes never deleted.

### 7.6 Token and cost logging
§3.11.

---

## 8. Cost and speed levers

| Lever | Where |
|---|---|
| Type filter + ledger skip before gather or any agent | `kb_select.py filter/skip`, Step 1 |
| Deterministic placement; Haiku only for leftovers, one batched call | `kb_routes.json`, `kb_select.py place`, `agents/kb-classifier.md` |
| Same-persona packing into ≤ 8-ticket writer groups | `kb_select.py group` |
| Stable prefix in the agent body — identical every call, so cached | `agents/kb-writer.md` |
| Trimmed inputs, never diffs, capped per ticket | `kb_inputs.py` |
| Reuse release summaries from the queue | v2 `kb_queue enqueue` → `writer_<group>.md` |
| Writer sees shot labels, never images | `kb_spec.py bundle` |
| Evidence failures fixed by script; AI retries only prose gates, one article, ≤ 2 | `kb_spec.py assemble/retry-bundle` |
| One capture visit per surface, frames shared across tickets | `kb_capture.py run` |
| DOM box + crop OCR (no vision, no grid) | `kb_capture.py`, `verify_crop` |
| One `soffice` batch call | `kb_pdf.py convert` |
| Scripts print one-line summaries, not JSON dumps | all `kb_*.py` CLIs |
| No Drive upload | `kb_package.py` |
| Writer writes its own JSON (avoids re-emission, §9 Q1) | Step 6 |
| Fire group 1, then the rest, for a warm cache — measure first; default all parallel | Step 6 (optional) |

---

## 9. Open questions for the owner

**Decided 2026-10-26 (owner):** Q1 — writer gets Write, used only on `/tmp/kb/**`; Q2 — `dom_box` fallback accepted in drafts only when tesseract is missing; Q3 — provenance off by default (`--provenance`, sidecar in the package). v1 built on branch `kb-articles-v1`. Q4–Q6 still open.

**Q1. Writer output costs twice if the writer is Read-only.** It can only *return* its JSON, which the orchestrator (usually a larger model) then re-types with Write. **Recommendation:** give `kb-writer` `Write` scoped to `/tmp/kb/**`, everything else read-only.

**Q2. Tesseract in Cowork.** If the binary can't be installed, G3 can't be met. Fallback: a new `dom_box` anchor (DOM text equals the label exactly, element visible and unoccluded via `elementFromPoint`), accepted in **draft only**, with publish still requiring `label_box`. Weakens the guarantee — needs sign-off.

**Q3. Is provenance printed?** The standard PDF says neither render prints it (`--provenance` on request); `build_kb_doc.py` prints an appendix in every draft. **Default:** follow the standard — off by default, `--provenance` to print, `_provenance/<ticket>.md` sidecar in the package, so CX-forwarded PDFs carry no internal audit table.

**Q4. Where capture physically runs, and session lifetime.** Unverified whether the Cowork sandbox can reach `demo.medtrics.sg` and the Playwright CDN, and use a storage state from a headed sign-in on the owner's Mac. `faculty-capture-MANIFEST.md` says sessions "expire quickly" — if they last hours, the **v3 weekly run will nearly always find them expired**. Options: (1) longer `SESSION_COOKIE_AGE` for capture accounts on demo only; (2) capture interactively before the drain; (3) accept that v3 only drafts what needs no capture.

**Q5. IDs in the first batch.** `M2-242` and `GEN-21668` — legacy M1 scope? `854f000c`, `6ac6a213`, `3aba78d8` are UUID prefixes `query_tasks_by_identifiers` likely won't resolve — supply full IDs, or let `kb_select` prefix-match a lane listing.

**Q6. Inputs still needed.** `kb2.csv` (catalogue seed; until then prior lookup is ledger-only), the M1-1399 reference `spec.json` (golden fixture), the current QA automation repo path (§7.1 spike).

**Accepted risks:**
- **R1.** The first batch is ~80% missing-screenshot drafts until §7.1 or an institution-enabled demo account exists (`extra/kb-pipeline/README.md`: a School of Medicine account would unlock ~14 tickets).
- **R2.** G6 catches known terms, not wrong copy; human review before publish is the backstop.
- **R3.** LibreOffice font substitution can reflow pages; `kb_pdf verify` warns rather than fails.
- **R4.** Token figures are estimates.
- **R5.** `CLAUDE_CODE_SUBAGENT_MODEL` or org model policy can silently override the pins; `kb_doctor` warns and v1 acceptance checks the split.

---

## 10. Build checklist
- [ ] Move `extra/*.py` → `scripts/kb/`; `kb_format` 1.1 constants, G7b, `missing_shot` render, `--provenance` (Q3), `Output.DICT`, font ladder, lazy imports.
- [ ] `build_plugin.py`: exclude `extra`, `KB-PLAN.md`, `.kb-sessions`; `_verify_bundle` assertions; `.gitignore` entry.
- [ ] `test_kb_validate.py` (22+ mutations) and `test_kb_format.py` green **before** any new script.
- [ ] `kb_routes.json` from ADMIN-URL-MAP, persona-capture-routes and `tickets.json`, with recipes and the demo allowlist.
- [ ] `kb_ledger.py` + `skills/kb-ledger`; run `seed-legacy` once; `seed-catalogue` once `kb2.csv` arrives.
- [ ] `kb_select.py`, `kb_inputs.py`, `agents/kb-classifier.md`.
- [ ] `kb_capture.py` + `skills/kb-capture`; owner signs in once per persona.
- [ ] `agents/kb-writer.md` with three G2-valid exemplars (write the Learner one).
- [ ] `kb_spec.py`, `kb_pdf.py`, `kb_package.py`, `kb_usage.py`, `kb_doctor.py`.
- [ ] `commands/kb-articles.md`.
- [ ] README, CHANGELOG, `plugin.json` + `marketplace.json` → 0.21.0.
- [ ] `kb_doctor` in Cowork, dependency matrix recorded; smoke one group, verify the model split.
- [ ] First batch run; M1-1399 PDF fidelity check; rebuild the bundle and confirm `extra/` isn't in it.
- [ ] (Optional) §7.1 QA GIF coverage spike.
- [ ] v2: `--kb` in `release-slack.md` (Step 9c) and `notes.md`, `kb_queue`, tests → 0.22.0.
- [ ] v3: scheduled task, `--unattended`, drained-status updates, tests → 0.23.0.
