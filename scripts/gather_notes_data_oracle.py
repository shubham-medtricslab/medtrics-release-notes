#!/usr/bin/env python3
"""
Data gathering script for the Oracle release track (/oracle-release).

Reads Optimus tasks (Oracle product, ORC- prefix) and GitHub PR diffs from
the medtrics-oracle repo, outputs structured JSON in the exact shape the
classic gatherer produces, so prepare_note_prompts.py needs zero changes.

Reuses the classic module (gather_notes_data_optimus) for everything
Optimus-side and for the shared matching/truncation helpers. The classic
script is only extended additively (issue `created_at`), never reshaped.

Usage:
    python3 scripts/gather_notes_data_oracle.py tickets ORC-101,ORC-115 [options]
    python3 scripts/gather_notes_data_oracle.py status [options]

Options:
    --output PATH               Output JSON file path (default: /tmp/oracle_notes_data.json)
    --include-description       Include Optimus task descriptions in output
    --dry-run                   Print summary table and exit without writing JSON
    --pr-overrides PATH         Mem `list_notes` dump of the "Oracle PR Overrides"
                                collection (missing/empty file = no overrides)
    --pr-override-inline T=N    Run-only override (ORC-232=415); repeatable,
                                comma-separated pairs allowed; beats a Mem override

PR discovery (Phase-0-verified design): ORC ids live primarily in BRANCH
names (fix/orc-232/...), which GitHub search does not index — so the primary
discovery is ONE PR-list sweep per run (/pulls?state=all&sort=updated, up to
5 pages of 100), matched locally against every requested ticket. /search/issues
is only a secondary for tickets with zero sweep matches (PRs older than the
sweep window).

Every PR that matches a ticket is kept in `mrs` (several PRs per ticket is
normal), ordered PRIMARY-FIRST. The primary rule (choose_primary): one open PR
→ it; zero open → newest merged_at; ≥2 open → the single non-draft if exactly
one, else `flagged_ambiguous`. A human override (Mem note or --pr-override-
inline) always wins. Each issue carries a `pr_discovery` block with the
decision (override | matched | flagged_ambiguous | no_pr_yet |
no_pr_in_window), and the output JSON carries a top-level `pr_discovery` with
sweep stats, per-decision counts and every warning — so the command reads
them from the file, not from stderr scrollback.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gather_notes_data_optimus as base  # noqa: E402
import pr_overrides  # noqa: E402

import argparse  # noqa: E402
import datetime as _dt  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import time  # noqa: E402
import urllib.error  # noqa: E402
import urllib.parse  # noqa: E402
import urllib.request  # noqa: E402

log = base.log

GITHUB_MAX_RETRIES = 5
GITHUB_INITIAL_BACKOFF = 2  # seconds
GITHUB_MIN_REMAINING = 5  # proactively pause when remaining requests drop below this
GITHUB_MAX_WAIT = 120  # cap every computed wait (seconds)
GITHUB_PER_PAGE = 100
SWEEP_MAX_PAGES = 5  # ~500 most-recently-updated PRs

REPO_RE = re.compile(r"^[\w.\-]+/[\w.\-]+$")

DECISIONS = ("override", "matched", "flagged_ambiguous", "no_pr_yet",
             "no_pr_in_window")


def load_env(env_path=".env"):
    """Read .env file and return dict of relevant variables.

    Requires OPTIMUS_API_KEY, GITHUB_TOKEN, GITHUB_REPO; optional
    GITHUB_API_URL (default https://api.github.com) and
    MEM_PR_OVERRIDES_COLLECTION_ID (default "" — the command resolves the
    Mem collection by title when it is empty).

    GITHUB_REPO is normalized: a leading http(s)://github.com/ prefix,
    trailing slashes, and a trailing .git are stripped (the live .env holds a
    full URL). After normalization it must be 'owner/repo'.
    """
    env = {}
    if not os.path.exists(env_path):
        log(f"ERROR: {env_path} not found")
        sys.exit(1)
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip("'\"")
                if key in ("OPTIMUS_API_KEY", "GITHUB_TOKEN", "GITHUB_REPO",
                           "GITHUB_API_URL", "MEM_PR_OVERRIDES_COLLECTION_ID"):
                    env[key] = value
    env.setdefault("GITHUB_API_URL", "https://api.github.com")
    env.setdefault("MEM_PR_OVERRIDES_COLLECTION_ID", "")
    for required in ("OPTIMUS_API_KEY", "GITHUB_TOKEN", "GITHUB_REPO"):
        if required not in env:
            log(f"ERROR: {required} not found in {env_path}")
            sys.exit(1)
    raw_repo = env["GITHUB_REPO"]
    repo = re.sub(r"^https?://github\.com/", "", raw_repo.strip(),
                  flags=re.IGNORECASE)
    repo = repo.rstrip("/")
    if repo.lower().endswith(".git"):
        repo = repo[:-4]
    repo = repo.rstrip("/")
    if not REPO_RE.match(repo):
        log(f"ERROR: GITHUB_REPO must normalize to 'owner/repo' "
            f"(got {raw_repo!r} -> {repo!r})")
        sys.exit(1)
    env["GITHUB_REPO"] = repo
    return env


def github_api(env, path, params=None):
    """Execute a GitHub API GET request with rate-limit handling.

    `path` is a full path from the API root (e.g. "/repos/{repo}/pulls") or a
    complete URL (used when following a Link rel="next" header).

    Returns (parsed_json, lowercased_headers_dict) on success.

    Retry classification (load-bearing — GitHub secondary limits send 403 +
    Retry-After with NONZERO remaining): retryable iff status == 429, OR the
    x-ratelimit-remaining header is "0", OR a Retry-After header is present.
    Wait = Retry-After if present, else x-ratelimit-reset - now + 1 if
    present, else exponential backoff; every computed wait is capped at
    GITHUB_MAX_WAIT. 5xx / URLError / TimeoutError retry with exponential
    backoff. Max 5 attempts, then warn + (None, {}). Any other 4xx: warn +
    (None, {}) immediately — never sys.exit mid-gather.
    """
    if path.startswith(("http://", "https://")):
        url = path
    else:
        url = env.get("GITHUB_API_URL", "https://api.github.com").rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode(params)

    for attempt in range(1, GITHUB_MAX_RETRIES + 1):
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {env['GITHUB_TOKEN']}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        try:
            with urllib.request.urlopen(req, timeout=base.HTTP_TIMEOUT) as resp:
                headers = {k.lower(): v for k, v in resp.getheaders()}
                data = json.loads(resp.read().decode("utf-8"))
                # Proactive throttle: pause when close to the rate limit.
                remaining = (headers.get("x-ratelimit-remaining") or "").strip()
                if remaining.isdigit() and int(remaining) < GITHUB_MIN_REMAINING:
                    reset_at = (headers.get("x-ratelimit-reset") or "").strip()
                    if reset_at.isdigit():
                        wait = max(0, int(reset_at) - int(time.time())) + 1
                    else:
                        wait = GITHUB_INITIAL_BACKOFF
                    wait = min(GITHUB_MAX_WAIT, wait)
                    log(f"  GitHub rate limit low ({remaining} remaining), "
                        f"pausing {wait}s...")
                    time.sleep(wait)
                return data, headers
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            err_headers = e.headers if e.headers is not None else {}
            retry_after = (err_headers.get("Retry-After") or "").strip()
            remaining = (err_headers.get("x-ratelimit-remaining") or "").strip()
            rate_limited = (e.code == 429 or remaining == "0"
                            or bool(retry_after))
            if rate_limited:
                if attempt >= GITHUB_MAX_RETRIES:
                    log(f"WARNING: GitHub rate limited on {path}, exhausted "
                        f"all {GITHUB_MAX_RETRIES} attempts")
                    return None, {}
                if retry_after.isdigit():
                    wait = int(retry_after)
                else:
                    reset_at = (err_headers.get("x-ratelimit-reset") or "").strip()
                    if reset_at.isdigit():
                        wait = max(0, int(reset_at) - int(time.time())) + 1
                    else:
                        wait = GITHUB_INITIAL_BACKOFF * (2 ** (attempt - 1))
                wait = min(GITHUB_MAX_WAIT, wait)
                log(f"  GitHub rate limited ({e.code}) on {path}, retrying "
                    f"in {wait}s (attempt {attempt}/{GITHUB_MAX_RETRIES})...")
                time.sleep(wait)
                continue
            if 500 <= e.code <= 599:
                if attempt >= GITHUB_MAX_RETRIES:
                    log(f"WARNING: GitHub API {e.code} for {path} after "
                        f"{GITHUB_MAX_RETRIES} attempts: {body[:200]}")
                    return None, {}
                wait = min(GITHUB_MAX_WAIT,
                           GITHUB_INITIAL_BACKOFF * (2 ** (attempt - 1)))
                log(f"  GitHub {e.code} on {path}, retrying in {wait}s "
                    f"(attempt {attempt}/{GITHUB_MAX_RETRIES})...")
                time.sleep(wait)
                continue
            # Any other 4xx: warn and move on — never exit mid-gather.
            log(f"WARNING: GitHub API {e.code} for {path}: {body[:200]}")
            return None, {}
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt >= GITHUB_MAX_RETRIES:
                log(f"WARNING: GitHub network error for {path} after "
                    f"{GITHUB_MAX_RETRIES} attempts: {e}")
                return None, {}
            wait = min(GITHUB_MAX_WAIT,
                       GITHUB_INITIAL_BACKOFF * (2 ** (attempt - 1)))
            log(f"  GitHub network error on {path} ({e}), retrying in {wait}s "
                f"(attempt {attempt}/{GITHUB_MAX_RETRIES})...")
            time.sleep(wait)
            continue
    return None, {}


LINK_NEXT_RE = re.compile(r'<([^<>]+)>\s*;\s*rel="next"')


def _next_page_from_link(headers):
    """Parse the Link header for rel="next". Returns the full URL or None."""
    link = headers.get("link", "") if headers else ""
    if not link:
        return None
    m = LINK_NEXT_RE.search(link)
    return m.group(1) if m else None


def github_paginate(env, path, params=None, per_page=GITHUB_PER_PAGE,
                    max_pages=None, stats=None):
    """Fetch every page of a list endpoint.

    Follows the Link rel="next" header when present. Fallback when the Link
    header is absent: a full page (len == per_page) continues with the next
    page number; a short page stops.

    `stats` (optional out-dict) records `pages` (pages that returned data),
    `last_page_full` (the final page held exactly per_page items — the
    window may be truncated) and `failed` (a page request came back None
    after github_api's retries, so the result is incomplete). Default
    behavior and the return value are unchanged.
    """
    items = []
    page = 1
    next_url = None
    pages_fetched = 0
    pages_ok = 0
    last_page_full = False
    failed = False
    while True:
        if max_pages is not None and pages_fetched >= max_pages:
            break
        if next_url:
            data, headers = github_api(env, next_url)
        else:
            page_params = dict(params or {})
            page_params["per_page"] = str(per_page)
            page_params["page"] = str(page)
            data, headers = github_api(env, path, page_params)
        pages_fetched += 1
        page += 1
        if data is None:
            failed = True
        if not data:
            break
        pages_ok += 1
        last_page_full = len(data) == per_page
        items.extend(data)
        nxt = _next_page_from_link(headers)
        if nxt:
            next_url = nxt
        elif len(data) == per_page:
            next_url = None
        else:
            break
    if stats is not None:
        stats["pages"] = pages_ok
        stats["last_page_full"] = last_page_full
        stats["failed"] = failed
    return items


def verify_github_access(env):
    """Startup canary: GET /repos/{repo} once before any gathering.

    A bad/expired token, missing scopes, or missing SSO authorization on a
    private repo surfaces as 404/422 (not 403) — without this check, every
    ticket would silently 'find no PRs' and the release would go out empty.
    """
    repo = env["GITHUB_REPO"]
    data, _ = github_api(env, f"/repos/{repo}")
    if data is None:
        log(f"ERROR: GitHub canary check failed — GET /repos/{repo} did not "
            f"return 200. Check that GITHUB_TOKEN is valid, has the 'repo' "
            f"scope, and is SSO-authorized for the organization (auth "
            f"failures on private repos surface as 404/422, not 403). "
            f"Aborting before gathering so the release cannot silently "
            f"ship with zero code context.")
        sys.exit(1)
    log(f"  GitHub access verified for {repo}")


# --- PR discovery ---


def _issue_identifiers(issue):
    """Identifiers to match for an issue: primary + distinct linear id."""
    ids = [issue["identifier"]]
    linear_id = issue.get("linear_identifier", "")
    if linear_id and linear_id != issue["identifier"]:
        ids.append(linear_id)
    return ids


def _pr_branch(pr):
    return ((pr.get("head") or {}).get("ref") or "")


def match_field(pr, identifiers):
    """Which PR field carries an identifier: "branch", "title", or None.

    Branch is checked first (for every identifier), then title — both via
    the shared exact-id matcher (`(?!\\d)` guard, hyphen/space tolerant).
    """
    branch = _pr_branch(pr)
    title = pr.get("title", "") or ""
    for ident in identifiers:
        if base.mr_matches_identifier({"source_branch": branch, "title": ""},
                                      ident):
            return "branch"
    for ident in identifiers:
        if base.mr_matches_identifier({"source_branch": "", "title": title},
                                      ident):
            return "title"
    return None


def _pr_matches(pr, identifiers):
    """Local match of a PR against identifiers via the shared matcher."""
    return match_field(pr, identifiers) is not None


def _parse_ts(value):
    """Parse an ISO-8601 timestamp (GitHub `Z`, Optimus `+00:00` or naive)
    to an aware UTC datetime; None when absent or unparseable."""
    if not value or not isinstance(value, str):
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = _dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed


def _utc_now_iso():
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sweep_pulls(env):
    """ONE PR-list sweep per run — the primary discovery mechanism.

    ORC ids live primarily in branch names, which GitHub search does NOT
    index (Phase-0-verified), so per-ticket search misses real PRs.

    Returns (pulls, sweep_stats) where sweep_stats is the top-level
    `pr_discovery.sweep` block: `pr_count`, `pages`, `window_full` (all
    SWEEP_MAX_PAGES pages came back full — older PRs exist beyond the
    window), `oldest_updated_at` (min `updated_at` over the sweep) and
    `failed` (a page request failed after retries — the sweep is incomplete
    and "no PRs" must not be read as "no PRs exist").
    """
    stats = {}
    pulls = github_paginate(
        env, f"/repos/{env['GITHUB_REPO']}/pulls",
        {"state": "all", "sort": "updated", "direction": "desc"},
        per_page=GITHUB_PER_PAGE, max_pages=SWEEP_MAX_PAGES, stats=stats)
    oldest = None
    oldest_raw = None
    for pr in pulls:
        ts = _parse_ts(pr.get("updated_at"))
        if ts is not None and (oldest is None or ts < oldest):
            oldest, oldest_raw = ts, pr.get("updated_at")
    sweep_stats = {
        "pr_count": len(pulls),
        "pages": stats.get("pages", 0),
        "window_full": (stats.get("pages", 0) >= SWEEP_MAX_PAGES
                        and bool(stats.get("last_page_full"))),
        "oldest_updated_at": oldest_raw,
        "failed": bool(stats.get("failed")),
    }
    return pulls, sweep_stats


def match_prs_to_issue(issue, pulls):
    """Match swept PRs to one issue locally. Dedupes by PR number."""
    identifiers = _issue_identifiers(issue)
    matched = {}
    for pr in pulls:
        number = pr.get("number")
        if number is None or number in matched:
            continue
        if _pr_matches(pr, identifiers):
            matched[number] = pr
    return list(matched.values())


def _warn(warnings, msg):
    """Log a WARNING to stderr AND record it (the JSON carries it too)."""
    log(f"WARNING: {msg}")
    if warnings is not None:
        warnings.append(msg)


def search_prs_for_issue(env, issue, warnings=None):
    """Secondary discovery via /search/issues for tickets with zero sweep
    matches (PRs older than the sweep window).

    Search items are issue-shaped (no head.ref), so each hit is confirmed by
    fetching the real PR and re-matching. A failed search call is recorded in
    `warnings` (the ticket then falls through to no_pr_yet).
    """
    repo = env["GITHUB_REPO"]
    identifiers = _issue_identifiers(issue)
    matched = {}
    for identifier in identifiers:
        q = f'repo:{repo} is:pr "{identifier}"'
        data, _ = github_api(env, "/search/issues",
                             {"q": q, "per_page": "20"})
        if data is None:
            _warn(warnings, f"/search/issues failed for {identifier} — "
                            f"search secondary skipped for "
                            f"{issue['identifier']}")
            continue
        items = data.get("items", []) if isinstance(data, dict) else []
        for item in items:
            number = item.get("number")
            if number is None or number in matched:
                continue
            pr, _ = github_api(env, f"/repos/{repo}/pulls/{number}")
            if not pr:
                continue
            if _pr_matches(pr, identifiers):
                matched[number] = pr
    return list(matched.values())


def _pr_entry(pr, matched_via=""):
    """Build the normalized MR-shaped dict for a PR — the classic _mr_entry
    keys (so downstream consumers need zero changes) plus additive PR facts
    the primary rule and the preview need: `number`, `branch`, `state`
    (GitHub open|closed — "merged" is derived from `merged_at`), `draft`,
    `merged_at`, `updated_at`, and `matched_via` (branch | title | manual)."""
    user = pr.get("user") or {}
    if not isinstance(user, dict):
        user = {}
    return {
        "iid": pr["number"],
        "title": pr.get("title", "") or "",
        "web_url": pr.get("html_url", "") or "",
        "author_name": user.get("name") or user.get("login") or "",
        "author_username": user.get("login") or "",
        "author_email": "",   # filled in by enrich_author_emails (best-effort)
        "number": pr["number"],
        "branch": _pr_branch(pr),
        "state": pr.get("state") or "",
        "draft": bool(pr.get("draft")),
        "merged_at": pr.get("merged_at") or None,
        "updated_at": pr.get("updated_at") or None,
        "matched_via": matched_via or "",
    }


def choose_primary(mrs):
    """The primary rule over a ticket's PR entries — pure.

    Returns (decision, primary, candidates):
      one open PR            -> ("matched", it, [])
      zero open, any merged  -> ("matched", newest merged_at, [])
      zero open, none merged -> ("matched", None, [])   (only closed-unmerged)
      >=2 open, one non-draft-> ("matched", the non-draft, [])
      >=2 open otherwise     -> ("flagged_ambiguous", None, [open numbers])
      no PRs                 -> ("no_pr_yet", None, [])  (caller may refine)
    """
    if not mrs:
        return "no_pr_yet", None, []
    open_prs = [m for m in mrs if (m.get("state") or "") == "open"]
    if len(open_prs) == 1:
        return "matched", open_prs[0]["iid"], []
    if not open_prs:
        merged = [m for m in mrs if m.get("merged_at")]
        if not merged:
            return "matched", None, []
        newest = max(merged, key=lambda m: (_parse_ts(m["merged_at"])
                                            or _dt.datetime.min.replace(
                                                tzinfo=_dt.timezone.utc)))
        return "matched", newest["iid"], []
    non_draft = [m for m in open_prs if not m.get("draft")]
    if len(non_draft) == 1:
        return "matched", non_draft[0]["iid"], []
    return "flagged_ambiguous", None, [m["iid"] for m in open_prs]


def _override_record(ov):
    """The per-issue `pr_discovery.override` block from a loaded override."""
    return {
        "pr": ov["pr"],
        "resolved_by": ov.get("resolved_by") or "",
        "resolved_at": ov.get("resolved_at") or "",
        "reason": ov.get("reason") or "",
        "note_id": ov.get("note_id") or "",
    }


def apply_overrides(env, issue, overrides, pulls_by_number, warnings,
                    notes=None):
    """Apply a human override (ticket -> PR number) to one issue.

    Ensures the override PR is in `issue["mrs"]` — from the sweep when it
    was swept, else ONE `GET /pulls/{n}` — marks it `matched_via: manual`
    and returns the override record (decision `override`, primary = it).
    Returns None when there is no override for this ticket or its PR could
    not be fetched (404 -> warning, override ignored, sweep result stands).
    """
    ov = overrides.get(issue["identifier"]) if overrides else None
    if not ov:
        return None
    number = ov["pr"]
    ident = issue["identifier"]
    entry = next((m for m in issue["mrs"] if m["iid"] == number), None)
    if entry is None:
        pr = pulls_by_number.get(number)
        if pr is not None:
            if notes is not None:
                notes.append(f"override PR {number} was swept but did not "
                             f"match {ident}; added by override")
        else:
            pr, _ = github_api(env, f"/repos/{env['GITHUB_REPO']}/pulls/{number}")
            if not pr:
                _warn(warnings, f"override for {ident} names PR #{number} "
                                f"but GET /pulls/{number} failed (404 / auth "
                                f"/ rate limit) — override ignored, sweep "
                                f"result kept")
                return None
            if notes is not None:
                notes.append(f"override PR {number} not in sweep; fetched "
                             f"by number")
        entry = _pr_entry(pr, "manual")
        issue["mrs"].append(entry)
    entry["matched_via"] = "manual"
    return _override_record(ov)


def _is_noreply(email):
    return email.endswith("@users.noreply.github.com") or email == "noreply@github.com"


def fetch_pr_author_email(env, number, author_login, cache):
    """Best-effort real email for a PR's author, for exact Slack matching.

    The PR object and the user profile usually carry no usable email, so the
    email comes from the PR's commits — specifically `commit.author.email`
    (NOT `commit.committer.email`, which is whoever pushed/rebased and is
    frequently a different person). Only a commit whose top-level `author.login`
    matches the PR author is trusted, so a multi-author PR never yields a
    co-author's or committer's address. GitHub privacy masks (`…@users.noreply.
    github.com`) are skipped. Falls back to a public profile email if set.
    Returns "" when nothing usable is found. Cached per login.
    """
    if not author_login:
        return ""
    if author_login in cache:
        return cache[author_login]

    repo = env["GITHUB_REPO"]
    email = ""
    commits = github_paginate(
        env, f"/repos/{repo}/pulls/{number}/commits",
        params={}, per_page=GITHUB_PER_PAGE, max_pages=2) or []
    for c in commits:
        top = c.get("author") or {}
        if not isinstance(top, dict) or (top.get("login") or "") != author_login:
            continue
        cand = (((c.get("commit") or {}).get("author") or {}).get("email") or "").strip()
        if cand and not _is_noreply(cand):
            email = cand
            break

    if not email:
        user, _ = github_api(env, f"/users/{author_login}")
        if isinstance(user, dict):
            cand = (user.get("email") or "").strip()
            if cand and not _is_noreply(cand):
                email = cand

    cache[author_login] = email
    return email


def enrich_author_emails(env, issues):
    """Populate `author_email` on every PR entry (cached per author login)."""
    cache = {}
    for issue in issues:
        for entry in issue.get("mrs", []):
            entry["author_email"] = fetch_pr_author_email(
                env, entry["iid"], entry.get("author_username", ""), cache)


def discover_prs(env, issues, overrides=None):
    """Sweep once, match locally, fall back to search, apply overrides, pick
    a primary, warn loudly on zero.

    Tickets with no PRs stay in the output with empty mrs (classic
    behavior) — never silently omitted. Every issue gets a `pr_discovery`
    block; `mrs` is ordered primary-first. Returns (issues, run_stats) where
    run_stats is the top-level `pr_discovery` block for the output JSON.
    """
    overrides = overrides or {}
    warnings = []
    log("Sweeping GitHub PRs (one list sweep per run)...")
    swept_at = _utc_now_iso()
    pulls, sweep_stats = sweep_pulls(env)
    log(f"  Swept {len(pulls)} PRs")
    if sweep_stats["failed"]:
        _warn(warnings, "GitHub PR sweep failed after retries — the scan is "
                        "incomplete; tickets showing no PRs may still have "
                        "them. Re-run the gather.")
    pulls_by_number = {pr.get("number"): pr for pr in pulls
                       if pr.get("number") is not None}
    oldest_updated = _parse_ts(sweep_stats.get("oldest_updated_at"))
    summary = {d: 0 for d in DECISIONS}

    for issue in issues:
        ident = issue["identifier"]
        identifiers = _issue_identifiers(issue)
        notes = []
        matched = match_prs_to_issue(issue, pulls)
        if not matched and ident in overrides:
            # The answer is already known — no search call for this ticket.
            log(f"  {ident}: no sweep match; override present, search "
                f"secondary skipped")
        elif not matched:
            log(f"  {ident}: no sweep match, trying search secondary...")
            matched = search_prs_for_issue(env, issue, warnings)
        issue["mrs"] = [_pr_entry(pr, match_field(pr, identifiers))
                        for pr in matched]

        override = apply_overrides(env, issue, overrides, pulls_by_number,
                                   warnings, notes)
        if override is not None:
            decision, primary, candidates = "override", override["pr"], []
        else:
            decision, primary, candidates = choose_primary(issue["mrs"])
            if decision == "no_pr_yet":
                created = _parse_ts(issue.get("created_at"))
                if (sweep_stats["window_full"] and created is not None
                        and oldest_updated is not None
                        and created < oldest_updated):
                    decision = "no_pr_in_window"
                    notes.append(f"ticket created {issue['created_at']}, "
                                 f"before the oldest swept PR "
                                 f"({sweep_stats['oldest_updated_at']})")
        if not issue["mrs"]:
            _warn(warnings, f"no PRs found for {ident} — it will appear "
                            f"without code context")
        if primary is not None:
            issue["mrs"].sort(key=lambda m: m["iid"] != primary)  # stable
        issue["pr_discovery"] = {
            "decision": decision,
            "primary": primary,
            "candidates": candidates,
            "override": override,
            "notes": notes,
        }
        summary[decision] += 1
        pr_desc = ", ".join(f"PR#{m['iid']}" for m in issue["mrs"]) or "none"
        log(f"  {ident}: {pr_desc} ({decision}"
            + (f", primary PR#{primary}" if primary is not None else "") + ")")

    run_stats = {
        "swept_at": swept_at,
        "sweep": sweep_stats,
        "overrides_loaded": len(overrides),
        "summary": summary,
        "warnings": warnings,
    }
    return issues, run_stats


def parse_inline_overrides(values):
    """`--pr-override-inline ORC-232=415` (repeatable, comma-separated pairs
    allowed) -> {ticket: override}. Raises ValueError on a malformed pair."""
    result = {}
    for raw in values or []:
        for pair in raw.split(","):
            pair = pair.strip()
            if not pair:
                continue
            ticket, sep, number = pair.partition("=")
            ticket = ticket.strip().upper()
            if not sep or not pr_overrides.TICKET_RE.match(ticket):
                raise ValueError(f"bad --pr-override-inline {pair!r}: "
                                 f"expected ORC-NNN=<pr number>")
            pr = pr_overrides.parse_pr_number(number)
            if pr is None:
                raise ValueError(f"bad --pr-override-inline {pair!r}: PR "
                                 f"must be a positive integer")
            result[ticket] = {"ticket": ticket, "pr": pr, "pr_url": "",
                              "resolved_by": "inline", "resolved_at": "",
                              "reason": "inline override for this run only",
                              "note_id": "", "version": None}
    return result


def collect_overrides(notes_path, inline_values):
    """Mem overrides (from the saved dump) ∪ inline pairs — inline wins for
    the same ticket. Returns (overrides, warnings). A missing/empty dump is
    no overrides, never an error; a malformed inline pair raises ValueError."""
    overrides, warnings = pr_overrides.load_overrides(notes_path)
    inline = parse_inline_overrides(inline_values)
    overrides.update(inline)  # the human is typing it right now
    return overrides, warnings


def fetch_pr_diffs(env, repo, number):
    """Fetch all changed files for a PR, normalized to the classic diff
    schema ({old_path, new_path, diff}) and truncated."""
    files = github_paginate(env, f"/repos/{repo}/pulls/{number}/files",
                            params={}, per_page=GITHUB_PER_PAGE)
    diffs = [
        {
            "old_path": f.get("previous_filename") or f.get("filename", ""),
            "new_path": f.get("filename", ""),
            "diff": f.get("patch", "") or "",
        }
        for f in files
    ]
    return base.truncate_diffs(diffs)


# --- Optimus side ---


def query_tasks_status_oracle(env):
    """Query Optimus for Oracle tasks in the same five notes lanes as the
    classic gatherer, filtered with product=oracle (verified; product_slug is
    broken/ignored — never use it)."""
    all_tasks = []
    for status in base.NOTES_STATUSES:
        log(f"  Querying {status}...")
        all_tasks.extend(base.fetch_tasks_paginated(env, {
            "scope": "product",
            "product": "oracle",
            "product_status": status,
        }))
    return all_tasks


# --- Main ---


def main():
    parser = argparse.ArgumentParser(
        description="Gather data from Optimus and GitHub for Oracle release "
                    "notes generation")
    parser.add_argument("mode", choices=["tickets", "status"],
                        help="Query mode")
    parser.add_argument("parameter", nargs="?",
                        help="Comma-separated ORC ticket IDs (tickets mode)")
    parser.add_argument("--output", default="/tmp/oracle_notes_data.json",
                        help="Output JSON path")
    parser.add_argument("--include-description", action="store_true",
                        help="Include Optimus task descriptions")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print summary and exit")
    parser.add_argument("--pr-overrides", default="",
                        help="Saved Mem list_notes dump of the 'Oracle PR "
                             "Overrides' collection (missing/empty file = "
                             "no overrides, never an error)")
    parser.add_argument("--pr-override-inline", action="append", default=[],
                        metavar="ORC-NNN=PR",
                        help="Run-only override, e.g. ORC-232=415 (repeat or "
                             "comma-separate); beats a Mem override for the "
                             "same ticket; nothing is persisted")
    args = parser.parse_args()

    if args.mode == "tickets" and not args.parameter:
        log("ERROR: 'tickets' mode requires a comma-separated ORC id list")
        sys.exit(1)

    env = load_env()
    log(f"Mode: {args.mode}, Parameter: {args.parameter or 'N/A'}")

    # --- Human PR overrides: Mem dump (persisted) + inline pairs (run-only) ---
    try:
        overrides, override_warnings = collect_overrides(
            args.pr_overrides, args.pr_override_inline)
    except ValueError as e:
        log(f"ERROR: {e}")
        sys.exit(1)
    for w in override_warnings:
        log(f"WARNING: {w}")
    if overrides:
        inline_n = sum(1 for o in overrides.values()
                       if o.get("resolved_by") == "inline")
        log(f"  PR overrides loaded: {len(overrides)} ({inline_n} inline)")

    # Startup canary — before any gathering.
    verify_github_access(env)

    # --- Gather issues from Optimus ---
    issues = []
    try:
        if args.mode == "tickets":
            ticket_ids = [t.strip() for t in args.parameter.split(",")
                          if t.strip()]
            log(f"Querying Optimus for {len(ticket_ids)} tickets...")
            tasks = base.query_tasks_by_identifiers(env, ticket_ids)
        else:
            log("Querying Optimus for Oracle tasks in QA1, QA2, Code Review, "
                "Needs Changes, Pipeline Issues...")
            tasks = query_tasks_status_oracle(env)
        log(f"  Found {len(tasks)} tasks")
        for t in tasks:
            issues.append(base.task_to_issue(t, args.include_description))
    except base.OptimusError as e:
        log(f"ERROR: Optimus gather failed: {e}")
        sys.exit(1)

    # --- PR discovery: one sweep, local matching, search secondary,
    #     overrides, primary rule ---
    issues, run_stats = discover_prs(env, issues, overrides)
    # Override-note problems (malformed note, duplicate notes) belong in the
    # same list the command reads from the JSON.
    run_stats["warnings"] = list(override_warnings) + run_stats["warnings"]

    # --- Dry run: print summary and exit ---
    if args.dry_run:
        print(f"\n{'='*80}")
        print(f"DRY RUN — {len(issues)} issues found")
        print(f"{'='*80}")
        print(f"{'Identifier':<15} {'PRs':<20} {'Decision':<18} {'Title'}")
        print(f"{'-'*15} {'-'*20} {'-'*18} {'-'*40}")
        for issue in issues:
            pr_str = ", ".join(f"PR#{mr['iid']}" for mr in issue["mrs"]) or "none"
            disc = issue.get("pr_discovery") or {}
            decision = disc.get("decision", "")
            if disc.get("primary") is not None and len(issue["mrs"]) > 1:
                decision += f" (#{disc['primary']})"
            title = (issue["title"][:50] + "..."
                     if len(issue["title"]) > 50 else issue["title"])
            print(f"{issue['identifier']:<15} {pr_str:<20} {decision:<18} "
                  f"{title}")
        print(f"\nTotal issues: {len(issues)}")
        total_prs = sum(len(i["mrs"]) for i in issues)
        no_pr = sum(1 for i in issues if not i["mrs"])
        print(f"Total PRs: {total_prs}")
        print(f"Issues without PRs: {no_pr}")
        sweep = run_stats["sweep"]
        print(f"Sweep: {sweep['pr_count']} PRs over {sweep['pages']} page(s); "
              f"window_full={sweep['window_full']}; failed={sweep['failed']}; "
              f"oldest_updated_at={sweep['oldest_updated_at'] or 'n/a'}")
        print("Decisions: " + ", ".join(
            f"{k}={v}" for k, v in run_stats["summary"].items()))
        print(f"Overrides loaded: {run_stats['overrides_loaded']}")
        for w in run_stats["warnings"]:
            print(f"WARNING: {w}")
        sys.exit(0)

    # --- Resolve PR-author emails for Slack @-mention matching ---
    log("Resolving PR-author emails (commit author, non-noreply)...")
    enrich_author_emails(env, issues)

    # --- Fetch diffs for all PRs (shared-PR dedupe) ---
    log("Fetching PR diffs...")
    seen_numbers = set()
    diffs_cache = {}
    for issue in issues:
        for mr in issue["mrs"]:
            number = mr["iid"]
            if number in seen_numbers:
                continue
            seen_numbers.add(number)
            log(f"  Fetching diffs for PR#{number}...")
            diffs_cache[number] = fetch_pr_diffs(env, env["GITHUB_REPO"],
                                                 number)
    for issue in issues:
        for mr in issue["mrs"]:
            mr["diffs"] = diffs_cache.get(mr["iid"], [])

    # --- Write output (classic shape + additive top-level pr_discovery) ---
    output = {
        "mode": args.mode,
        "parameter": args.parameter or "",
        "issues": issues,
        "pr_discovery": run_stats,
    }
    with open(args.output, "w") as f:
        json.dump(output, f, indent=2)
    log(f"\nData written to {args.output}")
    log(f"  {len(issues)} issues, {sum(len(i['mrs']) for i in issues)} PRs "
        f"with diffs")
    summary = run_stats["summary"]
    log("  PR discovery: " + ", ".join(f"{k}={v}" for k, v in summary.items()
                                        if v))
    if run_stats["warnings"]:
        log(f"  {len(run_stats['warnings'])} warning(s) recorded in "
            f"pr_discovery.warnings")


if __name__ == "__main__":
    main()
