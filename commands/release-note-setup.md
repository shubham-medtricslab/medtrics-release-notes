---
description: Light setup for this plugin in the current folder — installs what the commands need, checks the outside pieces (today, the KB articles part - Python packages, OCR/PDF tools, Claude in Chrome, the .env keys, the three Mem collections and the Downloads folder) and remembers it in one config file. Run it once per folder; run it again and it only redoes what is missing.
argument-hint: "[kb] [--force]"
---

# Release Notes Setup

Set everything up once, so the other commands don't have to. Run it in the folder
you run the commands from (in Cowork, the selected folder) — everything it
creates is in that folder and ignored by git. Run it again any time: it reads
what is already done, says so, and only redoes what is missing.

## Usage

```
/release-note-setup             # every part below
/release-note-setup kb          # only the KB articles part
/release-note-setup --force     # redo every step, even what the config says is done
```

## Parts

| Part | Needed by | What it sets up |
|---|---|---|
| `kb` | `/kb-articles`, `/release-slack --kb` | Python packages in `.kb-env/`, tesseract / LibreOffice / Carlito font, the Claude in Chrome extension, the `.env` keys, the KB Ledger / Queue / Catalogue collections in Mem, the Downloads folder |

Each part is its own section below, run in table order. A part that fails does
not stop the next one. To add a part later: add a row here and a `## Part — <name>`
section with its steps and its line for the final table.

## Required permissions

```json
"Bash(python3:*)",
"mcp__claude-in-chrome__tabs_context_mcp",
"mcp__cowork__request_cowork_directory"
```

plus the Mem collection tools (`list_collections`, `create_collection`).

## Start

`K=${CLAUDE_PLUGIN_ROOT}/scripts/kb`. Work from the folder you were started in.

1. `python3 $K/kb_paths.py ensure` — creates `.kb-runs/` and
   `.medtrics-release-notes/` (each with its own `.gitignore` of `*`) and prints
   `KB_PY=`, `KB_RUNS=` and `KB_CONFIG=`. Keep `KB_CONFIG`.
2. `python3 $K/kb_paths.py config` — the config this folder already has (`{}` when
   none). Print what it says is done, in plain words, e.g. "Already set up here
   (2026-10-28): packages, Mem collections. Missing: the Ledger collection id,
   the .env key GITLAB_TOKEN." Nothing is asked: a step the config marks done is
   skipped (but still verified where the step below says it is cheap); with
   `--force` every step runs again.

## Part — `kb` (KB articles)

1. **Install** — `python3 $K/kb_doctor.py --setup` (add `--force` when the user
   passed it). It creates `.kb-env/` in this folder, installs python-docx,
   Pillow, numpy, pypdf and pytesseract into it, installs tesseract, LibreOffice
   and the Carlito font with apt-get or Homebrew where it is allowed to (the
   owner wants this automatic — no question), and records the result in the
   config (`kb.setup`, `kb.items`). If everything is already in place it prints
   `KB setup: already done` and returns at once. Never pass `--with-playwright`
   (capture uses Chrome, not Playwright). It writes no absolute path anywhere.
   - Exit 1 (python-docx, Pillow or numpy could not be installed) → the part
     **failed**; show its output.
   - Otherwise the part is installed. Note what is missing, in plain words: no
     tesseract → label checks are weaker and articles stay drafts; no LibreOffice
     → no PDF; no Carlito → PDFs use a fallback font.
2. **Claude in Chrome** — ToolSearch `select:mcp__claude-in-chrome__tabs_context_mcp`,
   then call it. If the tool is missing or the call fails, say, once: "Install
   the Claude in Chrome extension from the Chrome Web Store and connect it to
   this Claude account." It is not something this command can install; record
   it as **to do** and carry on.
3. **`.env` keys** — `python3 $K/kb_paths.py env-check` reads `.env` in this folder
   (and the environment) and prints `OPTIMUS_API_KEY`, `GITLAB_TOKEN` and
   `GITLAB_PROJECT_ID` as `set` or `MISSING`, recording only the names in the
   config — never the values. Exit 1 → list the missing keys and tell the user
   to add them to `.env` (this command never writes `.env`); record **to do**.
   This is a presence check only; no key is tried against its service here (the
   first `/kb-articles` run does that).
4. **Mem** — the KB collections live in Mem and `/kb-articles` will not run
   without it.
   - Probe: the Mem tools (`list_collections`, `create_collection`) are
     available? If not, print `Mem MCP not connected — connect the Mem
     connector, then run /release-note-setup again.` and record **to do** (skip
     the rest of this step).
   - Names: `python3 $K/kb_ledger.py resolve` prints the three names (`.env` or
     the defaults `KB Article Ledger`, `KB Queue`, `KB Catalogue`). The release
     archive is named by `MEM_RELEASE_ARCHIVE_COLLECTION` (default `Release
     Notes Archive`).
   - Resolve each exactly as the `kb-ledger` skill says (and `release-archive`
     for the archive): paginated `list_collections`, exact title match
     case-insensitively, prefer exact case then the oldest `created_at`, create
     under the exact name when none matches (description from the `resolve`
     output). Create only Ledger, Queue and Catalogue; the archive is only looked
     up (the release commands create it) — save its id if it exists.
   - Skip a collection whose id the config already holds, unless `--force`; then
     do it once more cheaply with a `get_collection` and re-resolve if that fails.
   - Save the ids: `python3 $K/kb_paths.py merge '{"mem": {"kb_ledger": "<id>",
     "kb_queue": "<id>", "kb_catalogue": "<id>", "release_archive": "<id>"}}'`
     (leave out one that does not exist). Ids are Mem's own, not paths.
5. **Downloads folder** — screen captures are exported as GIFs into the owner's
   Downloads folder.
   - In **Cowork** (ToolSearch `select:mcp__cowork__request_cowork_directory`
     finds the tool): call it with `path: "~/Downloads"` so it is approved for
     this chat. Tell the user: "Each new Cowork chat asks for the Downloads folder
     again — approve it when asked." Never store the path it returns.
   - In **Claude Code**: nothing to do; `~/Downloads` is read directly.
6. **Tell the user**, in one short list, the two things only they can do in
   that Chrome (Claude never signs in or changes settings for them):
   - sign in to https://medtrics.medtricslab.com;
   - Settings → Downloads → turn off "Ask where to save each file".

## Finish

One table, one row per part run, then one row per step that needs the user:

| Part | Status | To do |
|---|---|---|
| kb | ready / ready, with gaps / to do / failed | e.g. "connect Claude in Chrome", "add GITLAB_TOKEN to .env", "no PDF: LibreOffice missing" |

Then **what's next**, in two or three lines: run `/kb-articles M1-1520` (a ticket
id) or `/kb-articles M1-1520 dry-run` first; the first run after setup is the one that
asks Chrome for site access; each new Cowork chat re-approves Downloads. Say that
the config is `KB_CONFIG` (in `.medtrics-release-notes/`), is not committed, and
can be deleted to start over.

## Things this command does NOT do

- Does not write `.env` and does not print or store a key's value.
- Does not create Mem notes, and touches no Optimus, GitLab or Slack data.
- Does not sign in anywhere or change Chrome settings.
- Does not write outside this folder (`.kb-env/`, `.kb-runs/`,
  `.medtrics-release-notes/`) apart from the system tools it installs when it is
  allowed to; the home folder is used for the config only when this folder is
  read-only.
