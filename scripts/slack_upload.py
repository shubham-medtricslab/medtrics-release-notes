#!/usr/bin/env python3
"""
slack_upload.py — post a mrkdwn message to a Slack channel (stdlib-only).

Used by the /release-slack command after the user
confirms the preview. Does NOT use the Slack MCP. Talks to the Slack Web API
directly via urllib, using SLACK_BOT_TOKEN from .env in the current working
directory (process env overrides .env).

Posts one message per invocation. To build a root-post-plus-thread release
announcement, the orchestrating command calls this script once for the root,
parses `ts` out of the ---SLACK_UPLOAD_RESULT--- block, then calls it again
for each thread reply with --thread-ts <ts>.

Retry policy:
    429                       retried on ALL calls, honoring Retry-After
                              (exponential backoff fallback), max 5 attempts.
    5xx / network / timeout   retried on READ calls only (conversations.list,
                              chat.getPermalink).
                              On chat.postMessage these are NOT retried:
                              Slack may have accepted the write before
                              erroring, and a blind retry would double-post to
                              the channel. The script fails with a
                              "verify in channel before re-running" error.

Required env (in .env at CWD, or process env):
    SLACK_BOT_TOKEN        xoxb-... bot token with chat:write + channels:read
                           scopes (+ groups:read if resolving private
                           channel names)
    (channel)              the destination is passed via --channel; the caller
                           supplies it from .env — SLACK_CHANNEL_INTERNAL for
                           /release-slack, SLACK_CHANNEL_ORACLE for
                           /oracle-release.

Usage:
    python3 slack_upload.py \
        --channel "$SLACK_CHANNEL_INTERNAL" \
        --message-file /tmp/release_slack_root.txt

    # thread reply into an existing thread:
    python3 slack_upload.py \
        --channel "$SLACK_CHANNEL_INTERNAL" \
        --message-file /tmp/release_slack_reply_1.txt \
        --thread-ts 1724650000.123456

    # permalink mode — fetch the permalink of an already-posted message
    # (chat.getPermalink, a READ; nothing is posted). Needs no scope beyond
    # the bot token. Used post-success by the release commands; on failure
    # the caller omits the permalink — it is never constructed by hand.
    python3 slack_upload.py \
        --get-permalink \
        --channel C0123ABCD \
        --ts 1724650000.123456

Exit codes:
    0  success — message posted (or permalink fetched)
    1  configuration error (missing env, bad arg, message over the API cap)
    2  Slack API error (channel resolution, post failure, unverified write,
       permalink lookup failure)

Result contract:
    A ---SLACK_UPLOAD_RESULT--- line followed by one JSON object is printed
    on EVERY exit path, success or failure:
        {"ok": bool, "channel": str|null, "ts": str|null, "error": str|null,
         "permalink": str|null}
    "permalink" is populated only by --get-permalink on success; it is null
    on the post path and on every failure.
"""

import argparse
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

HTTP_TIMEOUT = 30          # seconds, every request
MAX_RETRIES = 5            # attempts per call
INITIAL_BACKOFF = 2        # seconds, doubled each retry when no Retry-After
MAX_MESSAGE_CHARS = 40000  # Slack chat.postMessage hard cap

# Channel IDs: public (C...) and private/group (G...) prefixes. Length is
# left unbounded above the minimum — Slack has lengthened IDs before.
CHANNEL_ID_RE = re.compile(r"[CG][A-Z0-9]{7,}")


class SlackError(Exception):
    """A Slack API call failed definitively (bad response, exhausted retries)."""


class SlackPostUnverifiedError(SlackError):
    """A write call failed in a way that leaves its outcome unknown.

    Raised on 5xx / network error / timeout during chat.postMessage. The
    message may or may not have landed — the caller must verify in the
    channel before re-running, never blind-retry.
    """


def load_env(path=".env"):
    """Best-effort .env loader. Mirrors the pattern used by the other plugin scripts."""
    env = {}
    p = Path(path)
    if not p.exists():
        return env
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _retry_wait(headers, attempt):
    """Wait time from a Retry-After header, else exponential backoff."""
    retry_after = ""
    if headers is not None:
        retry_after = headers.get("Retry-After", "") or ""
    if retry_after.strip().isdigit():
        return int(retry_after.strip())
    return INITIAL_BACKOFF * (2 ** (attempt - 1))


def _execute(build_request, retry_unsafe):
    """Run one Slack HTTP call under the retry policy. Returns parsed JSON.

    build_request: zero-arg callable returning a fresh urllib Request.
    retry_unsafe:  True for idempotent reads (5xx/network/timeout retried);
                   False for writes (those raise SlackPostUnverifiedError).
    429 is retried on every call — Slack rejected the request unprocessed.
    """
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        req = build_request()
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if e.code == 429:
                last_error = f"Slack rate limited (429): {body[:200]}"
                if attempt < MAX_RETRIES:
                    wait = _retry_wait(e.headers, attempt)
                    print(f"  Slack 429, retrying in {wait}s "
                          f"(attempt {attempt}/{MAX_RETRIES})...", file=sys.stderr)
                    time.sleep(wait)
                    continue
            elif 500 <= e.code <= 599:
                if not retry_unsafe:
                    raise SlackPostUnverifiedError(
                        f"Slack returned HTTP {e.code} on a write call. The message "
                        f"may or may not have been posted — verify in the channel "
                        f"before re-running (an automatic retry could double-post).")
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
            if not retry_unsafe:
                raise SlackPostUnverifiedError(
                    f"Network error during a write call ({e}). The message may or "
                    f"may not have been posted — verify in the channel before "
                    f"re-running (an automatic retry could double-post).")
            last_error = f"Slack network error: {e}"
            if attempt < MAX_RETRIES:
                wait = INITIAL_BACKOFF * (2 ** (attempt - 1))
                print(f"  Slack network error ({e}), retrying in {wait}s "
                      f"(attempt {attempt}/{MAX_RETRIES})...", file=sys.stderr)
                time.sleep(wait)
                continue
    raise SlackError(f"{last_error} (exhausted {MAX_RETRIES} attempts)")


def slack_call(method, token, payload=None):
    """POST to the Slack Web API (a write — 5xx/timeout are NOT auto-retried)."""
    url = f"{SLACK_API}/{method}"
    data = json.dumps(payload or {}).encode("utf-8")

    def build_request():
        return urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )

    return _execute(build_request, retry_unsafe=False)


def slack_get(method, token, params=None):
    """GET-style Slack call (a read — 429/5xx/network errors are retried)."""
    qs = urllib.parse.urlencode(params or {})
    url = f"{SLACK_API}/{method}?{qs}"

    def build_request():
        return urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {token}"},
            method="GET",
        )

    return _execute(build_request, retry_unsafe=True)


def resolve_channel(channel, token):
    """Resolve a channel argument to a channel id.

    - A value fully matching [CG][A-Z0-9]{7,} is already an id (C = public,
      G = private/group) and is returned verbatim.
    - A leading '#' always means a name.
    - Anything else falls back to a name lookup via conversations.list.
    """
    if CHANNEL_ID_RE.fullmatch(channel):
        return channel  # already an id
    name = channel.lstrip("#")
    # conversations.list — paginate
    cursor = ""
    while True:
        params = {"limit": "1000", "exclude_archived": "true",
                  "types": "public_channel,private_channel"}
        if cursor:
            params["cursor"] = cursor
        result = slack_get("conversations.list", token, params)
        if not result.get("ok"):
            raise SlackError(f"conversations.list failed: {result.get('error')}")
        for ch in result.get("channels", []):
            if ch.get("name") == name:
                return ch["id"]
        cursor = result.get("response_metadata", {}).get("next_cursor", "")
        if not cursor:
            break
    raise SlackError(f"Channel #{name} not found (or bot is not a member).")


def post_message(channel_id, text, token, thread_ts=None):
    """Post one message. Returns the message ts.

    With thread_ts, the message lands as a reply inside that thread.
    """
    payload = {
        "channel": channel_id,
        "text": text,
        "mrkdwn": True,
        "unfurl_links": False,
        "unfurl_media": False,
    }
    if thread_ts:
        payload["thread_ts"] = thread_ts
    result = slack_call("chat.postMessage", token, payload=payload)
    if not result.get("ok"):
        raise SlackError(f"chat.postMessage failed: {result.get('error')}")
    return result["ts"]


def get_permalink(channel_id, ts, token):
    """Fetch the permalink of an existing message. Returns the URL string.

    chat.getPermalink is a read (GET) — 429/5xx/network errors are retried via
    slack_get. Raises SlackError on an API error or a malformed response; the
    caller must treat that as "no permalink" — never build one by hand.
    """
    result = slack_get("chat.getPermalink", token,
                       {"channel": channel_id, "message_ts": ts})
    if not result.get("ok"):
        raise SlackError(f"chat.getPermalink failed: {result.get('error')}")
    permalink = result.get("permalink")
    if not permalink:
        raise SlackError("chat.getPermalink returned ok without a permalink")
    return permalink


def emit_result(ok, channel=None, ts=None, error=None, permalink=None):
    """Print the machine-parseable result block. Called on EVERY exit path."""
    print()
    print("---SLACK_UPLOAD_RESULT---")
    print(json.dumps({"ok": ok, "channel": channel, "ts": ts, "error": error,
                      "permalink": permalink}))


def main():
    ap = argparse.ArgumentParser(description="Post a Slack mrkdwn message")
    ap.add_argument("--channel", required=True,
                    help="Channel id (C0123ABC / G0123ABC) or name (#dream-team). "
                         "Usually $SLACK_CHANNEL_INTERNAL from .env.")
    ap.add_argument("--message-file", default=None,
                    help="Path to a text file containing the Slack mrkdwn message body. "
                         "Required unless --get-permalink is given.")
    ap.add_argument("--thread-ts", default=None,
                    help="Optional thread root ts. When given, the message is posted "
                         "as a reply inside that thread (chat.postMessage thread_ts).")
    ap.add_argument("--env-path", default=".env",
                    help="Path to .env (default: .env in CWD).")
    ap.add_argument("--get-permalink", action="store_true",
                    help="Permalink mode: fetch the permalink of an existing message "
                         "(chat.getPermalink) instead of posting. Requires --ts. "
                         "--message-file / --thread-ts are not accepted.")
    ap.add_argument("--ts", default=None,
                    help="Message ts whose permalink to fetch (only with --get-permalink).")
    args = ap.parse_args()

    # Mode validation happens before any env/API work so a bad invocation is a
    # plain argparse usage error (exit 2), same as a missing --channel.
    if args.get_permalink:
        if not args.ts:
            ap.error("--get-permalink requires --ts")
        if args.message_file or args.thread_ts:
            ap.error("--get-permalink does not accept --message-file / --thread-ts")
    else:
        if args.ts:
            ap.error("--ts is only valid with --get-permalink")
        if not args.message_file:
            ap.error("the following arguments are required: --message-file")

    def fail(code, error, channel=None):
        print(f"ERROR: {error}", file=sys.stderr)
        emit_result(False, channel=channel, error=error)
        return code

    env = load_env(args.env_path)
    # Allow process env to override .env (standard pattern).
    token = os.environ.get("SLACK_BOT_TOKEN") or env.get("SLACK_BOT_TOKEN")
    if not token:
        return fail(1, "SLACK_BOT_TOKEN not set in environment or .env")
    if not token.startswith("xoxb-"):
        return fail(1, "SLACK_BOT_TOKEN must start with xoxb- (bot token, not user)")

    if args.get_permalink:
        # Read-only path. Every failure lands in fail() -> ok:false + non-zero
        # exit; nothing here can post, and nothing raises out of the process.
        try:
            channel_id = resolve_channel(args.channel, token)
        except Exception as e:
            return fail(2, f"resolving channel: {e}")
        try:
            permalink = get_permalink(channel_id, args.ts, token)
        except Exception as e:
            return fail(2, f"fetching permalink: {e}", channel=channel_id)
        print(f"OK: permalink for ts={args.ts} in {channel_id}: {permalink}")
        emit_result(True, channel=channel_id, ts=args.ts, permalink=permalink)
        return 0

    msg_path = Path(args.message_file)
    if not msg_path.exists():
        return fail(1, f"message file not found: {msg_path}")
    message_text = msg_path.read_text()

    # Pre-check the length before any API call — Slack rejects longer texts,
    # and the composed message should have been chunked upstream.
    if len(message_text) > MAX_MESSAGE_CHARS:
        return fail(1, f"message is {len(message_text)} chars; the Slack API cap "
                       f"is {MAX_MESSAGE_CHARS}. Split it into thread replies.")

    try:
        channel_id = resolve_channel(args.channel, token)
    except Exception as e:
        return fail(2, f"resolving channel: {e}")

    try:
        ts = post_message(channel_id, message_text, token,
                          thread_ts=args.thread_ts)
    except SlackPostUnverifiedError as e:
        return fail(2, str(e), channel=channel_id)
    except Exception as e:
        return fail(2, f"posting message: {e}", channel=channel_id)

    where = f"thread {args.thread_ts} in {channel_id}" if args.thread_ts else channel_id
    print(f"OK: posted message to {where} at ts={ts}")
    emit_result(True, channel=channel_id, ts=ts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
