---
name: kb-capture
description: Live screen capture for /kb-articles on the capture site (medtrics.medtricslab.com) through the Claude in Chrome extension, driving the owner's own Chrome where they have already signed in by hand — one account covers admin and faculty (via the Personal portal), and the run returns to Admin at the end. Each screen is recorded and exported as a GIF into Downloads, so it works in Cowork. Covers the flow (plan-calls, canary, the Haiku kb-capturer, ingest), the Downloads folder, sweep, the live-test facts the design depends on, raw files and resume, and how to map a surface in kb_routes.json (a URL only). Claude never types a password.
---

# KB Capture (Claude in Chrome on the demo tenant)

Screenshots are the slow, expensive part of a KB article. Scripts plan the
capture, a Haiku agent carries it out in the owner's Chrome without looking at
any page (it never receives an image), and scripts verify every frame and place
every ring.

## Why GIFs

In Cowork, `computer screenshot` with `save_to_disk` leaves no file the sandbox
can read. The route that works is the one Charles proved in QA: record the visit
with `gif_creator`, export it with `download: true` into the user's
`~/Downloads`, and read it through the Cowork-mounted Downloads folder. The
design (record, export to Downloads, poll until the file is stable, the
`wait_for_file` function) is adopted from Charles's medtrics-qa-automation
`qa-screenshot-capture` skill; this skill takes only the mechanism, since his is
bound to QA steps, GitLab and his plugin config. The screenshot for a visit is
the **last frame** of its GIF, taken by script at ingest.

## Sign-in

The owner signs in to `https://medtrics.medtricslab.com` **by hand** in the
Chrome that has the Claude in Chrome extension connected to their Claude
account. Claude never types a password and there is no credentials file. One
account covers both personas: the admin screens use the Administration portal
and the faculty screens use the **Personal** portal, switched by URL
(`/?portal_change=administration|personal|institution`). There are no learner
screens in `kb_routes.json`. The run ends on `/?portal_change=administration`,
so the Chrome is left on Admin.

`/kb-articles` Step 0 checks the extension on every run (ToolSearch
`select:mcp__claude-in-chrome__tabs_context_mcp`, then `tabs_context_mcp`) and
asks the user to install, connect and sign in only when that fails. It then
gets the Downloads folder (below).

## Downloads folder

Step 0 (every run, except `--no-capture` and `--kb-queue`):

- **Cowork:** ToolSearch `select:mcp__cowork__request_cowork_directory`, call it
  with `path: "~/Downloads"`, and use the **returned** path as `DL`. Never cache
  it across sessions. If the user declines, ask once: approve, or continue with
  `--no-capture`.
- **Claude Code:** `DL=~/Downloads`.

Chrome must save downloads there without asking (Settings → Downloads → "Ask
where to save each file" off). GIFs land in `Downloads/kb-capture/<run_id>/`
and are copied out by ingest; `cleanup` deletes them afterwards (below).

**Cleanup.** Ingest never moves or deletes anything in Downloads: it copies each
GIF to `shots/gifs/` and reports `downloads_left`. After the drafts are in, and
only once the owner approves, `kb_capture.py cleanup --downloads-dir $DL
--names-from $RUN/shots/calls.json` deletes this run's GIFs (and the canary)
and the emptied `kb-capture/<run_id>` folders; it never touches a path outside
`Downloads/kb-capture/`.

**Sweep.** `kb_capture.py sweep --downloads-dir $DL --into $RUN/shots/stale`
moves any leftover `Downloads/kb-capture/*` folders (a crashed earlier run; they
may hold tenant data) into the run's `shots/stale/` and prints what moved. It
never deletes.

## Flow

```bash
$KB_PY $K/kb_capture.py sweep --downloads-dir $DL --into $RUN/shots/stale            # Step 0
$KB_PY $K/kb_capture.py plan-calls --plan $RUN/groups.json --inputs $RUN/inputs.json --out-dir $RUN/shots
# canary: you send calls.json["canary"] in one browser_batch, then
$KB_PY $K/kb_capture.py pickup --downloads-dir $DL --name kb-capture/$RUN_ID/canary.gif --timeout 20
# Task: medtrics-release-notes:kb-capturer (Haiku)  →  $RUN/shots/raw/<batch_id>.json
$KB_PY $K/kb_capture.py ingest --calls $RUN/shots/calls.json --raw-dir $RUN/shots/raw --out-dir $RUN/shots --downloads-dir $DL
```

1. **plan-calls** (no model, no browser) writes `shots/calls.json` (the visits,
   each batch's exact `browser_batch` actions, the probe install, the `canary`,
   the `abort` actions, `gif_dir` and a `gif` filename per visit) and
   `shots/capture.md`, the only file the capturer reads. Per visit, no
   screenshot:
   `gif_creator start_recording` → `navigate` → `computer wait W` →
   `computer hover` at the cursor park → `javascript_tool` probe stub →
   `gif_creator stop_recording` → `gif_creator export` with `download: true` and
   `filename: kb-capture/<run_id>/<visit_id>.gif`. The first batch of a persona
   opens with that persona's portal entry URL, outside any recording. Visits
   with an empty `url` are not planned; they stay uncapturable.
2. **Settle wait.** `W` is the surface's `wait_s` in `kb_routes.json` if it has
   one, else `demo.settle_s` (default **4**), at most 10. Slow pages (Users >
   Groups) need 4 to 5 seconds; 2 showed "Loading…". The last frame, after the
   hover, is the settled page with no cursor.
3. **Export options:** `{showWatermark: false, showProgressBar: false,
   showActionLabels: false, showClickIndicators: false, showDragPaths: false,
   quality: 1}`. Frames are the viewport in CSS px (1470×802 in the test),
   130 to 600 KB per GIF.
4. **Canary** (run by the orchestrator before the capturer, owner present): one
   throwaway recording of the first persona's portal entry exported as
   `kb-capture/<run_id>/canary.gif`, then `pickup` waits for it. Exit 0 = ok
   (prints the frame size, deletes the canary); 1 `download_missing`; 2
   `empty_export`; 3 `bad_gif`. A failure skips capture with that reason (Chrome
   download settings, a privacy or download prompt) and the drafts carry
   missing-screenshot markers.
5. **kb-capturer** loads the Chrome tools (including `gif_creator`), uses the
   first tab, installs the probe once, sends each batch verbatim and writes one
   raw file per batch. It never types, clicks or closes tabs, never sees an
   image and does not read page contents. After any stop rule it sends the
   `abort` actions (a `gif_creator` clear) so a half recording is never exported
   later, except when the bridge is down.
   `KB_CAPTURE NO_CHROME_TOOLS` means the orchestrator follows `capture.md`
   itself.
6. **ingest** pairs each probe result with its GIF: `D/<gif>` is awaited until
   its size is stable over two polls (20 s for the first file, 5 s for the rest),
   **copied** (Downloads untouched) to `shots/gifs/<visit_id>.gif`, and its last composited frame
   written to `shots/frames/<visit_id>.png`. Then, as before: map the probe's
   element boxes onto the frame, verify the label (OCR when tesseract is
   present, else a DOM match, which a draft accepts and a publish does not), run
   the tenant screen, write `shots/shots.json`. Entries carry
   `frame_source: gif|disk`. `shots.json` also carries `screens` (one record per
   screen, with its `reason` when it failed) and `missing` (`{ticket: {reason,
   detail}}`), which `kb_spec assemble` turns into the missing-screenshot line;
   the result carries `downloads_left`. A 0.23.0 raw file with a readable `screenshot_path`
   still ingests the old way. Exit 3 means some visits were left;
   `shots/resume.json` holds the rest of the plan and the reason per
   `persona/surface`: `expired`, `wrong_persona`, `bridge_down`, `no_probe`,
   `retry`, `not_reached` (its batch never ran), `download_missing` (no GIF in
   Downloads), `empty_export` (a zero-byte GIF) or `bad_gif` (not readable as an
   image).

## Click visits (0.26.0)

A capture_plan entry may carry `"clicks": [{"text": "View"}, {"row": true}, …]`
(from the ticket's route) and `"write_gated"` (a step that only appears after a
save). Visit `<entry>-s0` is the start page; each click is one more visit
`<entry>-s1`, `-s2`, … that continues from the previous screen (an entry with
no clicks keeps the plain id `v01`). A click visit is a **click batch** with two
`browser_batch` calls, because the click point comes out of the first:

1. `pick`: `gif_creator start_recording`, then `javascript_tool` running
   `kbclick` (stored in `sessionStorage.kbc`, installed with the probe at the
   start of the first batch of every run). It returns `{ok, x, y, label}`
   (element centre in CSS px, after `scrollIntoView` with `behavior: instant`)
   or throws `KB_CLICK_REFUSED:<why>`, `KB_CLICK_NOTFOUND:<text>` or
   `KB_TENANT_ROW`.
2. `act`: `computer left_click` at `"$X","$Y"` (the capturer substitutes the
   numbers from the `pick` result), settle wait, hover at the cursor park, probe,
   `stop_recording`, export.

`kbclick` refuses (in this order): not visible; sidebar element on a `{row}`
click; `type=submit`; inside `form[method=post]` unless a plain GET `<a href>`;
`data-href`; inline `onclick`; class `btn-danger|text-danger|delete|remove`;
`href` or any `data-*` value containing
`delete|remove|destroy|toggle|archive|portal_change|logout|signout`; title,
aria-label or text starting with a write verb (save, submit, send, confirm,
delete, remove, archive, publish, approve, reject, sign); a `<button>` (or
`role=button`) without `data-bs-toggle`/`data-toggle` of `modal|tab|collapse|dropdown`;
an `<a>` with no real target (`#`, `javascript:`, none) and no toggle (**both
dropped on a Vue page**, see below); and a point covered by another element. `{row: true}` (0.27.0) takes the first body row of the
largest visible table that holds a GET `<a href>` or is a clickable row (class
`clickable`, or `cursor: pointer` on the row or its first cell, the session list
being one: `<tr class="clickable">`, no anchors, Vue click handler). A link wins;
otherwise the click point is the centre of the row's first non-empty text
`<span>` (else cell), never a button, icon (`triangle`, `expand`, caret) or
anything the rules above refuse. It runs only when the page's tenant check is
not BLOCK (else `KB_TENANT_ROW`; REVIEW does not stop it). **Leaf rows first:**
a row is a group when it holds an `.expand`, `.triangle` or `[aria-expanded]`
element or its class is `group` / `expandable` / `parent` (optionally `-row`,
`-header`, `-head`); a link row is always a leaf. The first leaf row wins; when
only group rows are visible the result is `{ok:true,x,y,label,"group":true}` for the
first group row. The capturer then clicks it, waits 1 second and sends the same
`kbclick` once more in the same visit (one retry) to reach a leaf row; a second
`group` or any error counts as a refused click. The rules live in `kb_capture.py`
(`CLICK_RX`, `click_refusal()`, which tests pin) and the JS is generated from the
same constants.

A refused or missing click does not stop the run: the capturer sends the abort
(`gif_creator clear`), writes `click_error` for that visit and skips the rest
of that entry's clicks. Ingest records the screen with its reason
(`click_refused`, `click_not_found`, `tenant_row`), never leaves it for resume,
and `write_gated` becomes a `needs_write` screen without a visit. Reasons reach
the article as: `needs_write` → "This step happens after you save; no
screenshot"; `no_screen` → no marker, a one-line note; `click_*`, `tenant_row`,
`label_not_found` → a marker with that reason.

**Vue pages (0.27.0).** The page is Vue when `document.querySelector('[data-v-app]')`
matches, any element has `__vue_app__`, or `window.__VUE__` is set. There the
`button_no_toggle` and `anchor_no_target` refusals are dropped (the network write
guard below makes them unnecessary: "Add schedule" is clickable); every `danger_*`,
`write_verb`, `submit`, `post_form`, `data_href`, `onclick`, `ajax_link` and
`occluded` refusal stays. On a non-Vue page nothing changes.

**Network write guard (0.27.0).** The probe, `kbclick` and `kbopt` each install it
first, on every call (idempotent: every wrapper carries a `__kb` mark, so a
second install, an SPA route change or a page that swaps its own `fetch` in
never double-wraps; a full navigation gives a fresh window the next call patches).
It wraps `fetch`, `XMLHttpRequest.prototype.open/send`, `navigator.sendBeacon`
and `HTMLFormElement.prototype.submit/requestSubmit`, and adds a capture-phase
`submit` listener on `document`. Only `GET` passes (HEAD, OPTIONS and every other
method are blocked too): `fetch` returns a rejected Promise (`KB_WRITE_BLOCKED`),
`XHR.send` throws, `sendBeacon` returns `false`, a non-GET form's `submit()` /
`requestSubmit()` does nothing (the submitter's `formmethod` wins over the form's
method) and its `submit` event is cancelled. Each block adds 1 to
`window.__kbBlocked` (last `"METHOD /path"` in `window.__kbLast`); the probe
reports them as `wb` and `wl`. The guard has no off switch in 0.27.0, not even
in write mode, and the Python mirror (`request_blocked`, `form_blocked`) is
pinned against the JS by `tests/test_kb_write_guard.py`. Because the guard stays
on, a page that POSTs to load its data (a search API, a GraphQL call) loads
without that data during capture; that shows as a missing label, never a write.

**Heading fallback (0.27.0).** The probe also returns `h`: the title of the
top-most visible open dialog (`.modal-title`, `[role=dialog] h1-h3`,
`.swal2-title`; the last one in the document), else the first visible `h1`/`h2`,
as `{t, b}`. When a click or write visit found none of its labels, ingest anchors
the shot on `h` as a `dom_box` entry (`"fallback": "heading"`), so the screen is kept
for the draft. `dom_box` is draft-only: gate G3 already refuses it on publish.

**`kbopt` (0.27.0).** The third stored function (`sessionStorage.kbo`, installed with
the probe and `kbclick`) takes `{"option": "<exact text>"}` and returns `{ok,x,y,label}`
of the visible option in an open listbox or dropdown (`[role=listbox] [role=option]`,
`.multiselect__content li`, `.dropdown-menu .dropdown-item`, `.vs__dropdown-option`,
`ul[role=listbox] li`). The text match is exact after whitespace collapsing
(case-insensitive); an option with a `write_danger` word (send, notify, invite,
publish, approve, delete, remove, archive, destroy; "No, …" is fine) is
refused; none found is `KB_CLICK_NOTFOUND:<text>`, two is `ambiguous`. Dates are
typed (data agent), not picked.

**Hook.** `hooks/hooks.json` + `hooks/kb_guard.py` (PreToolUse on
`computer|form_input`) deny `type`, `key`, `left_click_drag` and `form_input`
only when the caller's `agent_type` contains `kb-capturer`; everyone else and
any hook error is allowed, and it writes no files. The Cowork spike showed
plugin hooks fire for subagents and main chat and that a deny is honoured.

**Write mode (0.27.0, the data agent only).** `kbclick` args
`{"text": "Save", "write": ["Save", "No, don't send notifications"]}` lift the
`write_verb`, `submit`, `post_form` and `button_no_toggle` refusals for an element
whose visible text equals one of the `write` texts exactly (case-insensitive,
trimmed). `danger_class`, `danger_attr`, `data-href` and `onclick` still refuse,
and so does `write_danger`: text, title or aria-label with
delete/remove/archive/destroy/send/notify/invite/publish/approve, unless the text
starts with "No" ("No, don't send notifications" is allowed). With `write`, only
the top-most open dialog is searched (`.modal.show`, `[role=dialog]`,
`.swal2-popup`, `dialog[open]`) and more than one visible match for the text is
`KB_CLICK_REFUSED:ambiguous`. A write-mode click can still never persist anything in
0.27.0: the network write guard blocks the request. The capturer never passes
`write`; `plan-calls` never emits it.

## Per-step labels (0.27.0)

A click entry's screens look for different labels. Start screen: the page's
menu label (the last non-numeric path segment, title-cased, e.g. `Sessions`) plus
the first click's text when it is a text click. A click screen: the **next**
click's text (the button the reader presses next, ringed). The last screen of
the entry: the ticket's own inputs labels. Entries without clicks keep the
ticket's labels.

## Accounts and sign-in (0.27.0)

`calls.json` carries `accounts`: `[{"account": "main", "personas": ["admin",
"faculty"], "batches": [...]}, {"account": "learner", "personas": ["learner"],
"signin": "learner", "batches": [...]}, {"account": "external", "personas":
["external"], "signin": "external_link", "batches": [...]}]`. Batches are ordered
by account, then persona (admin before faculty). The first batch of an account
with a `signin` carries `needs_signin`. The capturer stops before it and replies
`KB_CAPTURE_DONE ok=<n> stop=needs_signin:<account>`; the orchestrator has the
owner sign in by hand (learner; Claude never types a password) or paste the
emailed link (external), then starts the capturer again with `--from-batch
<batch_id>` (it sends the install again and carries on). `plan-calls
--external-link <url>` makes external entries start at that URL's path and query
(kept in `calls.json` / `capture.md` inside the run folder, which is git-ignored and never copied to
outputs or Mem: ingest records `external-link`, never the URL, in shots.json, and
`resume.json` rows for external entries carry an empty `url`). Without it,
external entries are uncapturable (reason `external_link_missing`, shown as
"external link not given").

After the last learner or external batch, `plan-calls` appends a final **return
batch** (`kind: "restore"`, `account: "main"`, `needs_signin: "main"`, the
navigation back to the home portal) and an `accounts` row `{"account": "main",
"signin": "main", "return": true}`. The capturer stops before it with
`stop=needs_signin:main`; the orchestrator has the owner sign back in as admin and
starts it again at that batch id (`--from-batch`), which sends the restore. Runs
that end on the main account get no return batch.

## Write visits (0.27.0)

`plan-calls --data-plan $RUN/data_plan.json` (the owner-approved plan; one with
`"approved": false` is ignored) adds, for each ticket record with an `after`
block, a visit `{"visit_id": "<entry>-w<k>", "kind": "write", "ticket",
"record_id", "persona", "labels": after.labels, "gif", "args"}` after the entry's
click visits, in `calls.json["write_visits"]`. They are in no capture batch: the
data agent drives them and writes `raw/data-<ticket>.json` items (`probe` + `gif`
+ `blocked`, or `write_error`). Ingest reads their items from any raw file (a
`data-` file is never a batch; batch ids never start with `data-`); a
`write_error` or a visit never run is the reason `write_failed` ("the form step
could not be completed"). A write visit whose `blocked` (or probe `wb`) is 1 or
more gets `note: "write_blocked_ok"` on its screen: informational, the save was
blocked as planned and nothing was stored.
An entry with a write visit no longer gets the `needs_write` screen.

## Limits

The write guard is installed by the page scripts, so it covers what runs in the
page after the install:

- Covered: `fetch` (rejected), XMLHttpRequest (`send` throws; an XHR opened before
  the install has no recorded method, so it is blocked too, failing closed),
  `navigator.sendBeacon`, `form.submit()` / `requestSubmit()`, a capture-phase
  `submit` listener, `WebSocket.prototype.send` (throws `KB_WRITE_BLOCKED`) and
  same-origin iframes (installed into `window.frames` on every install, again on
  each frame's `load`, and by a `MutationObserver` for frames added later; a
  cross-origin frame cannot be reached).
- Not covered: a `fetch` function reference that page code saved before the install
  (`const f = window.fetch` early in the page). The legacy app uses axios / jQuery
  over XMLHttpRequest, which is covered. EventSource is read-only and is not
  patched.
- The hook (`hooks/kb_guard.py`) covers the spawned agents only; it does not apply
  when the orchestrator follows `capture.md` itself.

## Live-test facts the design depends on

- `gif_creator` works inside `browser_batch`. `start_recording` clears previous
  frames and `export` clears the recording, so no `clear` is needed between
  visits; back-to-back visits in one batch export cleanly.
- Exported frames are the viewport in CSS px, even with no screenshot taken, so
  frame px = page px (1:1), at least the 1200 px width the gates need.
  `innerHeight` changes between visits (802, 746), so every probe reads
  `iw`/`ih` itself.
- No screenshot is needed: `navigate`, `wait` and `hover` produce frames.
  `javascript_tool` produces none, and a thrown error stops the batch before the
  export.
- `filename: kb-capture/<run>/<vid>.gif` creates the subfolder under Downloads.
  Four or five exports in a row raise no "multiple downloads" prompt or save
  dialog.
- The mouse pointer would be drawn into frames. A `hover` at the cursor park
  (`demo.cursor_park`, `[1465, 740]`) before the export removes it.
- The first `javascript_tool` right after `navigate` can run on the old page, so
  every visit waits after navigating (the settle wait above).
- The probe is stored once per tab in `sessionStorage.kbp` (with `kbc`, the click guard; both
  reinstalled at the start of every run) and called with a
  short stub, `(0,eval)(sessionStorage.getItem('kbp'))(<args>)`. The page
  reloads on navigation but `sessionStorage` survives. A new tab has no probe
  and the stub throws `KB_NOPROBE`.
- A JS `throw` stops `browser_batch`, so `KB_LOGIN` (sign-in page or a password
  field) and `KB_PERSONA` (the active portal button is not the one asked for)
  end a batch at once. The probe returns plain JSON objects, because the
  extension can replace a result with `[BLOCKED: Base64 encoded data]`; ingest
  leaves such a visit as `retry`.
- Label matching is an exact trimmed `innerText`, visible, first match outside
  the sidebar (`demo.sidebar_selector`), else the first match anywhere.
- Demo data includes `@gmail.com` / `@sacredheart.com` addresses. Those frames
  screen BLOCK and are watermarked. The demo domain is `medtricslab.com`.
- Not testable from Claude Code: the Cowork mount call itself. The canary is the
  first-run check that the whole route works.

## Raw files and resume

`shots/raw/<batch_id>.json`:

```json
{"batch_id": "b01", "status": "ok|expired|wrong_persona|bridge_down|no_probe", "error": null,
 "items": [{"visit_id": "v01", "probe": {…as returned…}, "gif": "kb-capture/<run>/v01.gif"}]}
```

`gif` is the filename from the export result's `Downloaded "…"` text. Items are
in visit order and a visit the batch did not reach is absent. Ingest replaces
entries by `visit_id`, so it can be re-run on the same raw folder (a GIF already
moved to `shots/gifs/` is not looked for in Downloads again). After a stop the
command carries on with what it has: tickets whose surfaces were not reached are
drafted with missing-screenshot markers and recorded as `partial`. Sign in again
in Chrome if it was `expired`, then re-run `/kb-articles … --retry-partial`.

## Mapping a surface (`scripts/kb/kb_routes.json`)

A surface is a **URL**, nothing more. There are no recipes: capture is the page
each URL lands on. Anything the screen needs (a tab, an academic period, a
filter) must be expressible as a URL; if it isn't, the surface is not
capturable yet and its tickets are drafted without screenshots.

```json
{"surface": "Curriculum > Setup > Migrate", "persona": "admin", "also_personas": [],
 "url": "/administration/curriculum/",
 "match": {"modules": ["Curriculum & Sessions"], "url_prefixes": ["/administration/curriculum/"],
           "template_globs": ["src/templates/curriculum/setup/*.html"]},
 "known_tickets": {"M1-1520": "Captured"}, "demo_status": "capturable"}
```

- `url: ""` → not mapped; the surface is never planned.
- `persona` is `admin` or `faculty`; faculty visits go through the Personal portal.
- `match` places NEW tickets: a URL prefix the ticket's diff mentions, a template
  path it changes, or a module only this surface carries.
- `wait_s` (optional, per surface): seconds to wait after navigate before the
  frame, for slow pages; otherwise `demo.settle_s` (default 4), at most 10.
- `demo` keys: `allow_strings` (the demo tenant's synthetic names, ignored by the
  tenant screen; keep it short), `domain` (`medtricslab.com`), `cursor_park`,
  `portal_entry` (per persona), `portal_label` (the button text, `Admin` /
  `Personal`), `home_portal` (`admin`, where the run ends) and
  `sidebar_selector`.

## Legacy: Playwright

`kb_capture.py login`, `check` and `run` (headless Playwright with saved
sessions) still exist in the script but `/kb-articles` no longer uses them, and
`kb_doctor.py --setup` (run by `/release-note-setup`) installs Playwright only with `--with-playwright`.
