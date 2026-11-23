# KB capture: self-healing plan (0.26.0)

Status: **built in 0.26.0** (2026-11-23). Decisions below were confirmed the same day.

Differences from the plan as first written:

- `loader_sel` is not learned. The run cannot tell which element is a page's loader,
  so the key is hand-written only. Instead the built-in detection got wider: any
  leaf element whose text is "Loading…" or "Please wait…" counts as a loader.
- Retries cover the main sign-in (admin, faculty). A learner or external screen
  would need the owner to sign in again for each pass, so it is diagnosed and
  reported but not retried.
- An extra frame for a second label on a screen that already has a screenshot is
  taken only on a start page, or in an entry that is being replayed anyway.
- The replay corpus holds run 2158 only; the raw data of run 1952 was not at hand.

## Why

Runs kb-20261123-1952 and kb-20261123-2158 each needed a plugin release for page
quirks: a label below the fold, a loader still showing, button text with a name
appended, a heading scrolled off. The capture follows a fixed script (fixed wait,
one frame, one attempt), so every quirk becomes a code change. The goal is that a
quirk is handled during the run, or by one line in a local file, and a release is
only needed for a new *kind* of failure.

## Decisions

| Question | Decision |
|---|---|
| Automatic re-attempts per failed screen | Two |
| When every remedy fails | Unringed screenshot, flagged on the contact sheet |
| Near-match label text | Use it, flag it on the contact sheet |
| Overrides file | Local: `.medtrics-release-notes/kb_overrides.json` |
| Who writes learned overrides | The run, automatically, listed in the report |
| Live route check | On demand: `/kb-articles check-routes` |
| Getting overrides back into the plugin | Dev tool reads them from a run zip |
| Release shape | One release, 0.26.0 |

## Part 1: smarter first attempt

- **Wait until quiet (`kbw`).** New stored script, awaited by `javascript_tool`.
  It resolves when no loader is visible and the DOM has not changed for 500 ms,
  capped at 10 s. It runs after the existing settle wait, before `kbs`. The fixed
  wait stays as the minimum so the GIF still gets frames.
- **Candidates for a missing label.** For a label it cannot find, the probe
  returns up to three visible texts (buttons, links, tabs, form labels, headings)
  that are closest to it. Nothing is ringed from these in the first attempt; they
  feed the diagnosis and the retry.
- **Nothing stops the run.** Each visit's ingest and each step's ring drawing is
  wrapped: an unexpected error becomes reason `internal_error` on that one shot,
  with the message kept in the run folder.

## Part 2: diagnosis and two retry passes

After ingest, `kb_capture.py diagnose` reads the raw probe results and gives every
wanted label without a verified shot one cause and one remedy:

| Cause (from the probe) | Remedy | Retried |
|---|---|---|
| Found, outside the viewport | Scroll to that label | Yes |
| Loader visible | Wait until quiet, longer cap | Yes |
| Not found, a candidate is close | Use the candidate as an alias | Yes |
| Found, covered by something | Scroll it to the centre | Yes |
| Two wanted labels too far apart for one frame | One frame per label | Yes |
| Not found, no candidate | None | No: unringed fallback |
| Click refused, tenant BLOCK, 404, needs a save, sign-in | None | No: reported as today |

- `kb_capture.py plan-retry` writes `shots/retry-1.md` in the same format as
  `capture.md`. The capturer agent gets no new abilities and the click guard and
  write guard are unchanged.
- A failed screen behind clicks is replayed from its entry's start page, because
  its state depends on the earlier clicks.
- **Pass 1:** quiet cap 10 s, scroll to the specific label, alias when the text
  similarity is at least 0.82 (the existing label floor), one recording per
  missing label (at most three per screen).
- **Pass 2,** only for what pass 1 left: quiet cap 20 s, alias floor 0.70 or all
  of the label's words present, in any order.
- **Limit:** at most 12 retried screens per run; the rest are reported with their
  diagnosis.
- **Unringed fallback.** After pass 2, a step whose screen loaded, is not loading
  and is not tenant BLOCK gets the settled frame without a ring (`anchor: none`).
  The contact sheet marks it "no ring". Gate G3 (ringed label printed in bold)
  skips such a step.
- **Near match.** A shot ringed through an alias carries `label_match: near` and
  the text found; the contact sheet prints "near match: <text>".
- **Plain-word diagnosis.** The report (Step 10) and a `diagnosis.md` in the run
  folder state the cause per ticket, for example "Automatic Alerts was found at
  y=1147, below the 639 px window; scrolled on retry 1; captured".

## Part 3: overrides and learning

`.medtrics-release-notes/kb_overrides.json`, merged over `kb_routes.json` per
persona and surface. Every entry records its source (`learned` with run id and
date, or `manual`).

| Key | Meaning | Learned automatically |
|---|---|---|
| `wait_s`, `quiet_cap_s` | Page needs longer | Yes |
| `aliases` | Label → text as written on this page | Yes |
| `loader_sel` | Extra loader selector for this page | Yes |
| `clicks` | Click path to the screen | No, by hand |
| `read_posts` | Read-only POST the write guard lets through | No, by hand |

`clicks` and `read_posts` change what the capture is allowed to press or send, so
the run never writes them. A hand-written `read_posts` line still passes the
existing `read_post_ok` check.

- When a retry succeeds because of a remedy, the run writes the override and the
  report lists it ("learned: Benefits > Time Off needs 8 s").
- The file is copied into every run folder, so it is in the run zip.
- A learned line that later fails twice in a row is dropped and reported.

## Part 4: route check and promotion

- **`/kb-articles check-routes [--surface <name>]`.** Walks every capturable
  route on the demo site: page loads, right portal, loader clears within the cap,
  first click of the click path resolves, tenant verdict. No GIFs, no clicks, no
  writes. Prints a table and saves it.
- **`tools/kb_promote_overrides.py <run zip>`.** Shows the learned overrides in a
  zip as a diff against `kb_routes.json`; applies what is accepted. Dev tool, not
  shipped.

## Testing

- Unit tests for the diagnosis table, the retry planner, the overrides merge and
  the learning rules.
- **Replay corpus.** The raw probe files of runs 1952 and 2158 become fixtures:
  `diagnose` must name the known cause for each ticket. Each later run zip can be
  added the same way, so a reported problem becomes a test without a live site.
- Node tests for `kbw` and the candidate finder on the fake DOM used in 0.25.1.
- Live check on the demo site before hand-over, as done for 0.25.1: the three
  tickets of run 2158, plus one forced failure per remedy.

## What this does not fix

A new kind of failure still needs a code change: a page that loads its data by a
POST not yet listed, a click the guard refuses, tenant data on the first row, a
screen that only exists after a save. The diagnosis will name these plainly.

## Open risks

- `javascript_tool` awaiting `kbw` for up to 20 s inside a `browser_batch` has not
  been tried in Cowork; if it times out, `kbw` falls back to polling in short
  calls.
- Retry passes add capture time: about 20 s per retried screen, more for screens
  behind several clicks.
- The capturer agent reads a second and third instruction file per run; its
  prompt must stay as strict as today.
