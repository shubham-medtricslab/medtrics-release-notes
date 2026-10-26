---
name: kb-capture
description: Live screenshot capture for /kb-articles on the demo tenant (demo.medtrics.sg) with a scripted Playwright browser — the human-only sign-in per persona, where the saved sessions live, how a run walks persona × surface, what happens when a session expires mid-run, and how to map a new surface or recipe in kb_routes.json. Claude never types a password and never runs the sign-in itself.
---

# KB Capture (Playwright on the demo tenant)

Screenshots are the slow, expensive part of a KB article. `scripts/kb/kb_capture.py`
takes them without a model: one visit per surface, every ticket on that surface
sharing the frame, and each ring placed from the page's own element box.

## Sign-in — a person, once per persona

The capture accounts' passwords are never given to Claude, and Claude never
types one. A person runs, in a terminal on a machine with a screen:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/kb/kb_capture.py login --persona admin
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/kb/kb_capture.py login --persona faculty
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/kb/kb_capture.py login --persona learner
```

Each opens a visible browser at the demo sign-in page and waits up to ten
minutes for the person to sign in, then saves the session to
`$KB_SESSION_DIR/<persona>.json` (default `.kb-sessions/` in the working folder,
mode 600). When `/kb-articles` finds a persona's session missing or expired, it
**prints the line above and moves on** — it does not run it.

Session files are credentials:
- never put them in Mem, the outputs folder, a zip, chat, or git (`.kb-sessions/`
  is gitignored and excluded from the plugin build);
- delete them when the capture accounts change.

## Check

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/kb/kb_capture.py check --out /tmp/kb/<run>/sessions.json
```

One page load per persona → `valid | expired | missing`. The command runs it
before grouping so the plan preview shows which personas can be captured.

## Run

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/kb/kb_capture.py run \
    --plan /tmp/kb/<run>/groups.json --inputs /tmp/kb/<run>/inputs.json \
    --out-dir /tmp/kb/<run>/shots
```

Headless, 1440×900 at 2× scale. For each persona with a session, for each surface
in the capture plan: open the URL, run its recipe, take one frame, then for each
ticket's candidate labels (from `kb_inputs.py`, at most six per ticket) find the
first visible element showing the label and record its box.

- **With tesseract:** OCR reads the cropped box; ≥ 0.82 → `label_box` evidence.
- **Without tesseract:** an exact, visible, unoccluded DOM match → `dom_box`
  evidence, which the gates accept in a **draft only** (a publish needs OCR).
- **Tenant screen:** the page's DOM text, plus the frame's OCR when available;
  the worse verdict wins. The demo's own synthetic names
  (`demo.allow_strings` in `kb_routes.json`) are ignored so a demo frame is not
  flagged for the data that makes it safe. Anything not CLEAR is watermarked.

Output: `shots/shots.json` (one entry per candidate label, `verified` true or
false) and the frames. Only verified shots are offered to the writer.

## Session expiry mid-run

The run stops at the first sign-in page or password field, keeps every shot
already taken, writes `shots/resume.json` (the rest of the plan) and exits **3**.
The command then carries on with what it has — tickets whose surfaces were not
reached are drafted with missing-screenshot markers and recorded as `partial` —
and the run summary names the persona to sign in again. After signing in:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/kb/kb_capture.py run --plan /tmp/kb/<run>/shots/resume.json …
```

or simply re-run `/kb-articles … --retry-partial` later.

## Mapping a surface (`scripts/kb/kb_routes.json`)

Each surface entry:

```json
{"surface": "Curriculum > Setup > Migrate", "persona": "admin", "also_personas": [],
 "url": "/administration/curriculum/",
 "recipe": [{"click": "Setup"}, {"wait_for": "Migrate"}],
 "match": {"modules": ["Curriculum & Sessions"], "url_prefixes": ["/administration/curriculum/"],
           "template_globs": ["src/templates/curriculum/setup/*.html"]},
 "known_tickets": {"M1-1520": "Captured"}, "demo_status": "capturable"}
```

- `url: ""` → the surface is not mapped yet and its tickets are drafted without
  screenshots. 20 of the 56 surfaces seeded from the October backlog still need
  a URL (mostly faculty and learner screens).
- Recipe steps run after the page loads, in order: `{"click": text}`,
  `{"dblclick": text}`, `{"wait_for": text}`, `{"select": [label, option]}`,
  `{"wait_ms": n}`. Use them for the gotchas in the old ADMIN-URL-MAP: tabs that
  need two clicks, curriculum pages opening on an old academic period, date
  pickers.
- `match` places NEW tickets: a URL prefix the ticket's diff mentions, a template
  path it changes, or a module only this surface carries.
- `demo.allow_strings` lists the demo tenant's synthetic names. Keep it short —
  every entry is text the tenant screen will no longer see.
