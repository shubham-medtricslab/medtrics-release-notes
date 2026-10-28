---
description: One-time setup for this plugin in the current folder — installs what the commands need and checks the outside pieces (today, the KB articles part: Python packages, OCR/PDF tools and the Claude in Chrome extension). Run it once per folder; the other commands then just run.
---

# Release Notes Setup

Install everything once, so the other commands don't. Run it in the folder you
run the commands from (in Cowork, the selected folder). Run it again only when a
command says so — e.g. after the Cowork sandbox was reset.

## Usage

```
/release-note-setup             # every part below
/release-note-setup kb          # only the KB articles part
/release-note-setup --force     # re-check and retry everything, even what is already recorded
```

## Parts

| Part | Needed by | What it sets up |
|---|---|---|
| `kb` | `/kb-articles`, `/release-slack --kb` | Python packages in `.kb-env/`, tesseract / LibreOffice / Carlito font, the Claude in Chrome extension |

Each part is its own section below, run in table order. A part that fails does
not stop the next one. To add a part later: add a row here and a `## Part — <name>`
section with its steps and its line for the final table.

## Required permissions

```json
"Bash(python3:*)",
"mcp__claude-in-chrome__tabs_context_mcp"
```

## Part — `kb` (KB articles)

`K=${CLAUDE_PLUGIN_ROOT}/scripts/kb`.

1. **Install** — `python3 $K/kb_doctor.py --setup` (add `--force` when the user
   passed it). It creates `.kb-env/` in this folder, installs python-docx,
   Pillow, numpy, pypdf and pytesseract into it, tries tesseract, LibreOffice
   and the Carlito font with apt-get or Homebrew, and records the result in
   `.kb-env/kb-setup.json`. If that file already says everything is in place it
   prints `KB setup: already done` and returns at once. It never asks. Never
   pass `--with-playwright` (capture uses Chrome, not Playwright).
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
3. **Tell the user**, in one short list, the two things only they can do in
   that Chrome (Claude never signs in or changes settings for them):
   - sign in to https://medtrics.medtricslab.com;
   - Settings → Downloads → turn off "Ask where to save each file" (the screen
     captures are saved there as GIFs).

   And: in Cowork, `/kb-articles` asks for the Downloads folder on each run —
   approve it.

## Finish

One table, one row per part run:

| Part | Status | To do |
|---|---|---|
| kb | ready / ready, with gaps / to do / failed | e.g. "connect Claude in Chrome", "no PDF: LibreOffice missing" |

Then one line: what to run next (e.g. `/kb-articles M1-1520`).

## Things this command does NOT do

- Does not read or write `.env`, and does not touch Mem, Optimus, GitLab or Slack.
- Does not sign in anywhere or change Chrome settings.
- Does not write outside this folder's `.kb-env/` (and the system tools it
  installs, when it is allowed to).
