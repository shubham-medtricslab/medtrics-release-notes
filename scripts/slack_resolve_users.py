#!/usr/bin/env python3
"""
slack_resolve_users.py — resolve developer Slack user IDs for release notes.

Writes `assignee_slack_id` onto each issue in a gathered data JSON, using the
same SLACK_BOT_TOKEN that posts the message. Scopes required on that bot token:
`users:read.email` (email match) and `users:read` (name-match fallback). No
Slack MCP is involved — this replaces the former `slack_search_users` step.

Resolution, per issue, in three tiers (stop at the first hit):
  1. Email match — `users.lookupByEmail`. Exact; always safe to tag. The email
     is the issue's `assignee_email` (classic/Optimus) or, when that is absent,
     the first `mrs[].author_email` (Oracle — the gather fills it from the PR's
     commit author, non-noreply).
  2. Exact name match — a UNIQUE, case-insensitive, multi-token full-name match
     against the Slack directory (`users.list`). Only a unique full-name tags;
     ambiguous or single-token (first-name/login) names do not. Deactivated
     members and bots are skipped.
  3. Fuzzy name match — a difflib similarity match (default threshold 0.72,
     `--fuzzy-threshold`) that tags the single best directory name clearly
     ahead of the runner-up. Lower-confidence and can mis-tag; the command's
     human preview (which shows `@Name (Author Name)`) is the safety gate.
     Disable with `--no-fuzzy`; the developer's name for tiers 2/3 comes from
     `assignee_name` or `mrs[].author_name`.

Best-effort: a lookup failure never aborts the pipeline; an unresolved developer
is left with an empty `assignee_slack_id` and renders as a plain name.

Pre-resolved issues are skipped. An issue that already carries a non-empty
`assignee_slack_id` (e.g. filled from the optional Mem developer directory by
`dev_directory.py prefill`) is left untouched unless `--force` is given, so a
cached id is never overwritten by a lower-confidence name match. Every issue
this script resolves is stamped `assignee_slack_resolved_via` = "email" |
"name" | "fuzzy" ("" when unresolved) so the directory write-back knows which
developers are new.

Exit codes: 0 success (even if some developers were left unresolved),
            1 configuration error (missing/invalid token, unreadable data file).

A machine-readable summary is printed on stdout on every exit path:

    ---SLACK_RESOLVE_RESULT---
    {"ok", "resolved", "total", "by_email", "by_name", "by_fuzzy",
     "preresolved", "unresolved", "resolutions": [{identifier, slack_id, via}]}

Usage:
    python3 slack_resolve_users.py --data /tmp/release_slack_data.json
    python3 slack_resolve_users.py --data /tmp/oracle_notes_data.json
    python3 slack_resolve_users.py --data <path> --no-name-fallback   # email only
"""

import argparse
import difflib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SLACK_API = "https://slack.com/api"
HTTP_TIMEOUT = 30
MAX_RETRIES = 5
INITIAL_BACKOFF = 2
USERS_LIST_PAGE = 200


class SlackError(Exception):
    """A Slack API call failed definitively (bad response, exhausted retries)."""


def load_env(path=".env"):
    """Best-effort .env loader. Mirrors the other plugin scripts."""
    env = {}
    p = Path(path)
    if not p.exists():
        return env
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _retry_wait(headers, attempt):
    retry_after = ""
    if headers is not None:
        retry_after = headers.get("Retry-After", "") or ""
    if retry_after.strip().isdigit():
        return int(retry_after.strip())
    return INITIAL_BACKOFF * (2 ** (attempt - 1))


def slack_get(method, token, params=None):
    """GET-style Slack read. 429 / 5xx / network errors are retried."""
    qs = urllib.parse.urlencode(params or {})
    url = f"{SLACK_API}/{method}?{qs}"
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {token}"}, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if e.code == 429 or 500 <= e.code <= 599:
                last_error = f"Slack HTTP {e.code}: {body[:200]}"
                if attempt < MAX_RETRIES:
                    wait = _retry_wait(e.headers, attempt)
                    print(f"  Slack {e.code}, retrying in {wait}s "
                          f"(attempt {attempt}/{MAX_RETRIES})...", file=sys.stderr)
                    time.sleep(wait)
                    continue
            else:
                raise SlackError(f"Slack HTTP {e.code}: {body[:200]}")
        except (urllib.error.URLError, TimeoutError) as e:
            last_error = f"Slack network error: {e}"
            if attempt < MAX_RETRIES:
                wait = INITIAL_BACKOFF * (2 ** (attempt - 1))
                print(f"  Slack network error ({e}), retrying in {wait}s "
                      f"(attempt {attempt}/{MAX_RETRIES})...", file=sys.stderr)
                time.sleep(wait)
                continue
    raise SlackError(f"{last_error} (exhausted {MAX_RETRIES} attempts)")


def normalize_name(value):
    """Lowercase, strip punctuation to spaces, collapse whitespace."""
    if not value:
        return ""
    s = re.sub(r"[^\w\s]", " ", str(value).lower())
    return re.sub(r"\s+", " ", s).strip()


def issue_identity(issue):
    """Return (email, name) for an issue across both gather shapes.

    Classic (Optimus): assignee_email / assignee_name.
    Oracle (GitHub):   no assignee — the developer is the first PR author, so
                       the email comes from mrs[].author_email (the gather fills
                       it from the PR's commit author, non-noreply) and the name
                       from mrs[].author_name (or username).
    """
    email = (issue.get("assignee_email") or "").strip()
    name = (issue.get("assignee_name") or "").strip()
    if not email:
        for mr in issue.get("mrs", []):
            cand = (mr.get("author_email") or "").strip()
            if cand:
                email = cand
                break
    if not name:
        for mr in issue.get("mrs", []):
            cand = (mr.get("author_name") or mr.get("author_username") or "").strip()
            if cand:
                name = cand
                break
    return email, name


def lookup_by_email(email, token, cache):
    """users.lookupByEmail → Slack user id, or None. Cached per email."""
    if email in cache:
        return cache[email]
    result = slack_get("users.lookupByEmail", token, {"email": email})
    uid = None
    if result.get("ok") and result.get("user"):
        uid = result["user"].get("id")
    cache[email] = uid
    return uid


def build_member_index(token):
    """Return {normalized_name: set(user_ids)} from the Slack directory.

    Skips deactivated members and bots. Indexes real_name and display_name
    (plus Slack's own *_normalized variants) so a full-name match can be found.
    A normalized name that maps to more than one id is ambiguous and will not
    be used to tag anyone.
    """
    index = {}
    cursor = ""
    while True:
        params = {"limit": str(USERS_LIST_PAGE)}
        if cursor:
            params["cursor"] = cursor
        result = slack_get("users.list", token, params)
        if not result.get("ok"):
            raise SlackError(f"users.list failed: {result.get('error')}")
        for m in result.get("members", []):
            if m.get("deleted") or m.get("is_bot") or m.get("id") == "USLACKBOT":
                continue
            uid = m.get("id")
            if not uid:
                continue
            profile = m.get("profile", {}) or {}
            for raw in (
                m.get("real_name"),
                profile.get("real_name"),
                profile.get("real_name_normalized"),
                profile.get("display_name"),
                profile.get("display_name_normalized"),
            ):
                n = normalize_name(raw)
                if n:
                    index.setdefault(n, set()).add(uid)
        cursor = (result.get("response_metadata", {}) or {}).get("next_cursor", "")
        if not cursor:
            break
    return index


def match_name(name, index):
    """Return a Slack id ONLY on a unique, multi-token full-name match.

    Requires the normalized name to contain a space — a single token (a first
    name, or a GitHub login used as the author fallback) is never enough to
    tag, so a coincidental one-word collision can't mis-tag the wrong person.
    """
    n = normalize_name(name)
    if not n or " " not in n:
        return None
    ids = index.get(n)
    if ids and len(ids) == 1:
        return next(iter(ids))
    return None


def fuzzy_match_name(name, index, threshold, margin=0.05):
    """Return a Slack id for a fuzzy full-name match, or None.

    Scores the normalized name against every multi-token directory name with
    difflib's similarity ratio, keeping the best score per Slack member. Tags
    the top member ONLY when its score clears `threshold` AND beats the
    runner-up (a different member) by at least `margin` — a near-tie between
    two people is left unresolved rather than risk the wrong tag. Single-token
    names never fuzzy-match. This tier is lower-confidence; the command's
    human preview (which shows `@Name (Author Name)`) is the safety gate.
    """
    n = normalize_name(name)
    if not n or " " not in n:
        return None
    best_by_uid = {}
    for key, uids in index.items():
        if " " not in key:
            continue
        score = difflib.SequenceMatcher(None, n, key).ratio()
        for uid in uids:
            if score > best_by_uid.get(uid, 0.0):
                best_by_uid[uid] = score
    if not best_by_uid:
        return None
    ranked = sorted(best_by_uid.items(), key=lambda kv: kv[1], reverse=True)
    top_uid, top_score = ranked[0]
    if top_score < threshold:
        return None
    if len(ranked) > 1 and (top_score - ranked[1][1]) < margin:
        return None
    return top_uid


def emit_result(ok, resolved=0, total=0, by_email=0, by_name=0, by_fuzzy=0,
                preresolved=0, unresolved=None, resolutions=None, error=None):
    print()
    print("---SLACK_RESOLVE_RESULT---")
    print(json.dumps({
        "ok": ok, "resolved": resolved, "total": total,
        "by_email": by_email, "by_name": by_name, "by_fuzzy": by_fuzzy,
        "preresolved": preresolved,
        "unresolved": unresolved or [], "resolutions": resolutions or [],
        "error": error,
    }))


def main():
    ap = argparse.ArgumentParser(
        description="Resolve developer Slack user IDs into a gathered data JSON")
    ap.add_argument("--data", required=True,
                    help="Path to the gathered data JSON (issues[] with "
                         "assignee_email/assignee_name or mrs[].author_*).")
    ap.add_argument("--no-name-fallback", action="store_true",
                    help="Email match only; skip both name tiers (exact + fuzzy).")
    ap.add_argument("--no-fuzzy", action="store_true",
                    help="Keep the exact name match but skip the fuzzy tier.")
    ap.add_argument("--fuzzy-threshold", type=float, default=0.72,
                    help="Minimum difflib similarity for a fuzzy name match "
                         "(default 0.72). Higher = stricter/safer.")
    ap.add_argument("--force", action="store_true",
                    help="Re-resolve issues that already carry an "
                         "assignee_slack_id (default: skip them — they were "
                         "pre-filled, e.g. from the Mem developer directory).")
    ap.add_argument("--env-path", default=".env",
                    help="Path to .env (default: .env in CWD).")
    args = ap.parse_args()

    def fail(code, error):
        print(f"ERROR: {error}", file=sys.stderr)
        emit_result(False, error=error)
        return code

    env = load_env(args.env_path)
    token = os.environ.get("SLACK_BOT_TOKEN") or env.get("SLACK_BOT_TOKEN")
    if not token:
        return fail(1, "SLACK_BOT_TOKEN not set in environment or .env")
    if not token.startswith("xoxb-"):
        return fail(1, "SLACK_BOT_TOKEN must start with xoxb- (bot token, not user)")

    data_path = Path(args.data)
    if not data_path.exists():
        return fail(1, f"data file not found: {data_path}")
    try:
        data = json.loads(data_path.read_text())
    except (ValueError, OSError) as e:
        return fail(1, f"could not read data file: {e}")
    if not isinstance(data, dict):
        return fail(1, "data file is not a JSON object with an 'issues' list")

    issues = data.get("issues", [])
    email_cache = {}
    member_index = None          # built lazily, only when name fallback is needed
    resolved = by_email = by_name = by_fuzzy = preresolved = 0
    unresolved = []
    resolutions = []

    for issue in issues:
        ident = issue.get("identifier", "?")

        # 0) Pre-resolved (e.g. Mem developer directory) — leave untouched.
        if issue.get("assignee_slack_id") and not args.force:
            preresolved += 1
            continue

        email, name = issue_identity(issue)
        uid = None
        via = ""

        # 1) Email match (exact).
        if email:
            try:
                uid = lookup_by_email(email, token, email_cache)
            except SlackError as e:
                print(f"  {ident}: email lookup failed ({e}); "
                      f"falling back to name", file=sys.stderr)
            if uid:
                by_email += 1
                via = "email"

        # 2/3) Name-based fallback: exact unique full-name, then fuzzy.
        if not uid and name and not args.no_name_fallback:
            if member_index is None:
                try:
                    member_index = build_member_index(token)
                except SlackError as e:
                    print(f"  name fallback unavailable ({e})", file=sys.stderr)
                    member_index = {}
            uid = match_name(name, member_index)
            if uid:
                by_name += 1
                via = "name"
            elif not args.no_fuzzy:
                uid = fuzzy_match_name(name, member_index, args.fuzzy_threshold)
                if uid:
                    by_fuzzy += 1
                    via = "fuzzy"
                    print(f"  {ident}: fuzzy-matched '{name}' → {uid} "
                          f"(verify at preview)", file=sys.stderr)

        if uid:
            issue["assignee_slack_id"] = uid
            issue["assignee_slack_resolved_via"] = via
            resolved += 1
            resolutions.append({"identifier": ident, "slack_id": uid, "via": via})
        else:
            issue["assignee_slack_id"] = ""     # explicit: plain-name fallback
            issue["assignee_slack_resolved_via"] = ""
            unresolved.append(ident)

    try:
        data_path.write_text(json.dumps(data, indent=2))
    except OSError as e:
        return fail(1, f"could not write data file: {e}")

    total = len(issues)
    print(f"Resolved {resolved}/{total} developer(s): "
          f"{by_email} by email, {by_name} by exact name, {by_fuzzy} by fuzzy name"
          + (f"; {preresolved} pre-resolved (skipped)" if preresolved else "")
          + "."
          + (f" Unresolved (plain name): {', '.join(unresolved)}"
             if unresolved else ""), file=sys.stderr)
    emit_result(True, resolved=resolved, total=total, by_email=by_email,
                by_name=by_name, by_fuzzy=by_fuzzy, preresolved=preresolved,
                unresolved=unresolved, resolutions=resolutions)
    return 0


if __name__ == "__main__":
    sys.exit(main())
