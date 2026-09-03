#!/usr/bin/env python3
"""
release_archive.py — deterministic half of the Mem release archive.

After every `/release-slack` or `/oracle-release` post (including a resume run
that finishes posting the remaining replies), the command writes ONE Mem note
per release session into a user-named archive collection. Mem is the ONLY
archive — nothing is written to disk. The note carries a trace header (date,
channel, root ts, permalink, the posted ticket list) so a later Mem search for
a ticket id (`ORC-232`, `M1-1457`) lands on the release that shipped it, AND
the posted root + every thread reply verbatim, so the full message text is
preserved outside Slack. Mem is only reachable through the Mem MCP
(Claude-side), so this script never talks to Mem itself. It does the
deterministic, testable halves around the orchestrator's Mem calls, like
dev_directory.py / pr_overrides.py:

  pending   Resolve and print the configured collection name (process env
            `MEM_RELEASE_ARCHIVE_COLLECTION` > the same key in `.env` at CWD >
            default "Release Notes Archive") plus the collection description.
            Prints the resolved name on EVERY run so the user always sees
            where notes are going. Never writes to `.env`.

  record    Render the exact note body the command passes to `create_note`.
            PRIMARY input is the summaries file (`---`-separated per-ticket
            blocks, the same format internal-storyteller parses) because only
            it carries CLIENT_TITLE, CATEGORY and the actually-posted set. The
            gathered data JSON is SECONDARY — used only to fill `url` / code
            links when a block lacks them. Applies the storyteller drop rule:
            skip `CATEGORY: REFACTOR`, skip malformed blocks (no IDENTIFIER or
            no TITLE). Never raises on a bad block — drops it and proceeds.
            The posted message files (`--root-file`, `--reply-files` in post
            order) are appended verbatim under a `## Posted message` heading.

Output conventions:

  pending   stdout: JSON {collection_name, collection_description, source}
            followed by the machine-readable result block (below).
  record    stdout: the note body ONLY (so the command can pass it straight to
            create_note), unless --out is given (then the body goes to the file
            and stdout is empty). The advisory line and the result block go to
            stderr so stdout stays clean.

    ---RELEASE_ARCHIVE_RESULT---
    {"ok", "action", ...counts..., "error"}

Exit codes: 0 success, 1 configuration error (unreadable summaries / root /
reply file, bad --date, nothing left to record). A missing --data file is NOT
an error — the summaries file alone is enough for the trace header.

Usage:
    python3 release_archive.py pending [--env-file .env]
    python3 release_archive.py record \
        --summaries /tmp/oracle_notes_summaries.txt \
        --data /tmp/oracle_notes_data.json \
        --root-file /tmp/oracle_release_root.txt \
        --reply-files /tmp/oracle_release_reply_01.txt /tmp/oracle_release_reply_02.txt \
        --product oracle --date 2026-09-02 \
        --channel '#pod-oracle' --channel-id C0XXXXXXX \
        --ts 1725300000.123456 \
        [--permalink https://medtrics.slack.com/archives/C0XXXXXXX/p1725300000123456] \
        [--out /tmp/oracle_release_archive_note.txt]

Note schema (first line = Mem note title):

    Release: 2026-09-02 · oracle · #pod-oracle

    **Date:** 2026-09-02
    **Product:** oracle
    **Channel:** #pod-oracle
    **Channel id:** C0XXXXXXX
    **Root ts:** 1725300000.123456
    **Permalink:** https://medtrics.slack.com/archives/C0XXXXXXX/p1725300000123456
    **Item count:** 3
    **Tickets:** ORC-232, ORC-234, ORC-240
    **Clients:** KFSHRC, CVM-NCSU

    - ORC-232 · Average score shown on completed evaluations · ENHANCEMENT · KFSHRC · https://github.com/medtrics/medtrics-oracle/pull/456, https://github.com/medtrics/medtrics-oracle/pull/457
    - ORC-234 · Export timeout on large cohorts · BUG FIX · TBC · https://github.com/medtrics/medtrics-oracle/pull/455
    - ORC-240 · Rotation scheduling presets · FEATURE · TBC · no PR

    ## Posted message

    ### Root

    ~~~
    :rocket: *Release Notes — Wednesday, September 2, 2026*
    ...
    ~~~

    ### Reply 1

    ~~~
    *1. ORC-232 · Average score shown on completed evaluations*
    ...
    ~~~

The `**Permalink:**` line is present only when --permalink is given — never
constructed by hand. Code links list ALL of a ticket's PR/MR URLs, primary
first (PR_LINKS order, then MR_LINKS, then data-JSON `mrs`), comma-joined;
`no PR` (oracle) / `no MR` (medtrics) when empty. `MR-NNNN` identifiers from
`/release-slack mr <iid>` mode are listed as-is. The transcript uses `~~~`
fences so Slack's own ``` code blocks survive inside it. The parser reads the
trace header and item bullets only up to the `## Posted message` heading, then
the fenced sections, so bullet-like lines inside the message text can never be
mistaken for items. Stored: ticket ids, client-facing titles, categories,
client codes, code URLs, Slack coordinates, and the posted message text
(which itself carries developer mentions and internal summaries — it is the
same content that is already in the internal Slack channel).
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

ENV_KEY = "MEM_RELEASE_ARCHIVE_COLLECTION"
DEFAULT_COLLECTION_NAME = "Release Notes Archive"
COLLECTION_DESCRIPTION = (
    "medtrics-release-notes: one note per posted release session (date, "
    "product, Slack channel, root ts, permalink, the posted ticket list with "
    "client-facing titles, categories, client codes and PR/MR URLs, and the "
    "posted root + thread replies verbatim). The only archive of each release "
    "outside Slack — nothing is written to disk. Written by /release-slack and "
    "/oracle-release after every completed post. Safe to trash a duplicate "
    "note by hand."
)
TITLE_PREFIX = "Release: "
TRANSCRIPT_HEADING = "## Posted message"
ROOT_HEADING = "### Root"
REPLY_HEADING_PREFIX = "### Reply "
FENCE = "~~~"
PRODUCTS = ("medtrics", "oracle")
NO_LINKS = {"oracle": "no PR", "medtrics": "no MR"}
SEP = " · "
TBC = "TBC"

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FIELD_RE = re.compile(r"^\*\*(?P<key>[^*]+?):\*\*\s*(?P<value>.*)$")
BLOCK_KEY_RE = re.compile(r"^(?P<key>[A-Z][A-Z0-9_]*):\s?(?P<value>.*)$")

# Note field labels (exact rendering) and their internal keys, in order.
FIELDS = [
    ("Date", "date"),
    ("Product", "product"),
    ("Channel", "channel"),
    ("Channel id", "channel_id"),
    ("Root ts", "root_ts"),
    ("Permalink", "permalink"),
    ("Item count", "item_count"),
    ("Tickets", "tickets"),
    ("Clients", "clients"),
]


def _split_list(value):
    """Comma- or whitespace-separated list -> ordered, deduped, non-empty."""
    out = []
    for part in re.split(r"[,\s]+", value or ""):
        part = part.strip()
        if part and part not in out:
            out.append(part)
    return out


def _split_clients(value):
    """CLIENTS value -> ordered, deduped codes; the literal TBC stays TBC.
    Client codes may contain spaces (rare), so split on commas only."""
    out = []
    for part in (value or "").split(","):
        part = part.strip()
        if part and part not in out:
            out.append(part)
    return out


# --------------------------------------------------------------------------- #
# Configuration — collection name from process env > .env at CWD > default
# --------------------------------------------------------------------------- #

def _read_env_file_key(env_path, key):
    """Best-effort value of `key` from a .env file; '' if absent/unreadable.
    Mirrors pr_overrides._repo_from_env: last assignment wins, quotes stripped,
    comments and blank lines ignored. Never writes."""
    if not env_path or not os.path.exists(env_path):
        return ""
    value = ""
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                if k.startswith("export "):
                    k = k[len("export "):].strip()
                if k == key:
                    value = v.strip().strip("'\"")
    except OSError:
        return ""
    return value


def resolve_collection_name(env_path=".env", environ=None):
    """(name, source) — source is 'env', '.env' or 'default'.

    Process environment wins when set and non-blank; then the .env file at
    `env_path`; unset or whitespace-only everywhere -> the default name."""
    environ = os.environ if environ is None else environ
    from_env = (environ.get(ENV_KEY) or "").strip()
    if from_env:
        return from_env, "env"
    from_file = _read_env_file_key(env_path, ENV_KEY).strip()
    if from_file:
        return from_file, ".env"
    return DEFAULT_COLLECTION_NAME, "default"


# --------------------------------------------------------------------------- #
# Summaries file — the same block format internal-storyteller parses
# --------------------------------------------------------------------------- #

def parse_summaries(text):
    """Split the summaries text into raw blocks: list of {KEY: value} dicts.

    Blocks are separated by a line that is exactly `---`. Each field is
    `KEY: value` on one line; a line that does not start a new field is
    appended to the previous field's value (tolerates wrapped summaries).
    Empty blocks are skipped. Never raises."""
    blocks = []
    current = {}
    last_key = None

    def flush():
        nonlocal current, last_key
        if current:
            blocks.append(current)
        current, last_key = {}, None

    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if line.strip() == "---":
            flush()
            continue
        m = BLOCK_KEY_RE.match(line)
        if m:
            last_key = m.group("key")
            current[last_key] = m.group("value").strip()
        elif last_key is not None and line.strip():
            current[last_key] = (current[last_key] + " " + line.strip()).strip()
    flush()
    return blocks


def load_data_index(path):
    """{IDENTIFIER (upper) -> issue dict} from the gathered data JSON.
    Missing/unreadable/malformed file -> {} (the data JSON is secondary)."""
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return {}
    issues = data.get("issues") if isinstance(data, dict) else None
    index = {}
    for issue in issues or []:
        if not isinstance(issue, dict):
            continue
        ident = (issue.get("identifier") or "").strip().upper()
        if ident and ident not in index:
            index[ident] = issue
    return index


def _links_from_issue(issue):
    """All PR/MR URLs an issue's `mrs` carry, in order (web_url or html_url)."""
    out = []
    for mr in (issue or {}).get("mrs") or []:
        if not isinstance(mr, dict):
            continue
        url = (mr.get("web_url") or mr.get("html_url") or "").strip()
        if url and url not in out:
            out.append(url)
    return out


def build_items(blocks, product, data_index=None):
    """Apply the storyteller drop rule and shape each posted block into an item.

    Drops: `CATEGORY: REFACTOR`; malformed blocks (no IDENTIFIER or no
    TITLE). Title = CLIENT_TITLE, falling back to TITLE when it is missing. Code links = PR_LINKS then MR_LINKS (deduped, primary first),
    falling back to the data JSON `mrs`; `url` falls back to the data JSON
    too. Returns (items, dropped) where dropped lists a reason per block."""
    data_index = data_index or {}
    items, dropped = [], []
    for block in blocks:
        ident = (block.get("IDENTIFIER") or "").strip()
        internal_title = (block.get("TITLE") or "").strip()
        title = (block.get("CLIENT_TITLE") or "").strip() or internal_title
        if not ident:
            dropped.append("(no IDENTIFIER): malformed")
            continue
        if not internal_title:
            dropped.append(f"{ident}: malformed (no TITLE)")
            continue
        category = (block.get("CATEGORY") or "").strip().upper()
        if category == "REFACTOR":
            dropped.append(f"{ident}: REFACTOR")
            continue
        issue = data_index.get(ident.upper()) or {}
        links = list(dict.fromkeys(_split_list(block.get("PR_LINKS"))
                                   + _split_list(block.get("MR_LINKS"))))
        if not links:
            links = _links_from_issue(issue)
        url = (block.get("URL") or "").strip() or (issue.get("url") or "").strip()
        clients = _split_clients(block.get("CLIENTS"))
        items.append({
            "id": ident,
            "title": title,
            "category": category or "—",
            "clients": clients or [TBC],
            "links": links,
            "url": url,
        })
    return items, dropped


# --------------------------------------------------------------------------- #
# Note rendering / parsing
# --------------------------------------------------------------------------- #

def _client_codes(items):
    """Deduped union of non-TBC client codes across items, first-seen order."""
    out = []
    for it in items:
        for c in it.get("clients") or []:
            if c.upper() != TBC and c not in out:
                out.append(c)
    return out


def render_transcript(root_text, reply_texts=()):
    """Render the `## Posted message` section: the root and every reply, in
    post order, each inside a `~~~` fence. Empty when there is no root text.
    Pure."""
    root_text = (root_text or "").rstrip("\n")
    if not root_text and not reply_texts:
        return []
    out = [TRANSCRIPT_HEADING, ""]
    out += [ROOT_HEADING, "", FENCE, root_text, FENCE, ""]
    for i, reply in enumerate(reply_texts or (), start=1):
        out += [f"{REPLY_HEADING_PREFIX}{i}", "", FENCE,
                (reply or "").rstrip("\n"), FENCE, ""]
    return out


def render_note(items, product, date, channel, channel_id, ts, permalink="",
                root_text="", reply_texts=()):
    """Render the archive note (first line = Mem title): trace header, item
    bullets, then the posted message transcript. Pure."""
    no_links = NO_LINKS.get(product, "no PR")
    tickets = [it["id"] for it in items]
    codes = _client_codes(items)
    values = {
        "date": date,
        "product": product,
        "channel": channel,
        "channel_id": channel_id,
        "root_ts": ts,
        "permalink": (permalink or "").strip(),
        "item_count": str(len(items)),
        "tickets": ", ".join(tickets),
        "clients": ", ".join(codes) if codes else TBC,
    }
    out = [f"{TITLE_PREFIX}{date}{SEP}{product}{SEP}{channel}", ""]
    for label, key in FIELDS:
        if key == "permalink" and not values["permalink"]:
            continue  # omitted entirely when not obtained
        out.append(f"**{label}:** {values[key]}")
    out.append("")
    for it in items:
        links = ", ".join(it["links"]) if it["links"] else no_links
        out.append(f"- {it['id']}{SEP}{it['title']}{SEP}{it['category']}"
                   f"{SEP}{', '.join(it['clients'])}{SEP}{links}")
    transcript = render_transcript(root_text, reply_texts)
    if transcript:
        out.append("")
        out += transcript
        while out and out[-1] == "":
            out.pop()
    return "\n".join(out) + "\n"


def is_archive_note(note):
    """True when the title line carries the `Release: ` prefix."""
    content = note if isinstance(note, str) else \
        ((note.get("content") or "") if isinstance(note, dict) else "")
    lines = content.splitlines()
    return bool(lines) and lines[0].strip().startswith(TITLE_PREFIX)


def parse_note(note):
    """Parse one archive note (a content string, or a Mem note dict with
    content/id/version) into a record; None when it is not an archive note
    (title does not start with `Release: `) so stray notes are ignored.

    Record: {note_id, version, title, date, product, channel, channel_id,
    root_ts, permalink, item_count (int|None), tickets [..], clients [..],
    items [{id, title, category, clients [..], links [..]}],
    root_text (str), reply_texts [str, ..]}.

    Header fields and item bullets are read only up to the `## Posted message`
    heading; after it, only the fenced `### Root` / `### Reply n` sections are
    read, so bullet-like lines inside the message text are never items."""
    if isinstance(note, str):
        content, note = note, {}
    elif isinstance(note, dict):
        content = note.get("content") or ""
    else:
        return None
    lines = content.splitlines()
    if not lines:
        return None
    title = lines[0].strip()
    if not title.startswith(TITLE_PREFIX):
        return None
    rec = {
        "note_id": note.get("id") or note.get("note_id") or "",
        "version": note.get("version"),
        "title": title[len(TITLE_PREFIX):].strip(),
        "item_count": None, "tickets": [], "clients": [], "items": [],
        "root_text": "", "reply_texts": [],
    }
    for _label, key in FIELDS:
        if key not in ("item_count", "tickets", "clients"):
            rec[key] = ""
    body, transcript = lines[1:], []
    for i, line in enumerate(body):
        if line.strip() == TRANSCRIPT_HEADING:
            body, transcript = body[:i], body[i + 1:]
            break
    _parse_transcript(transcript, rec)
    for line in body:
        s = line.strip()
        if not s:
            continue
        m = FIELD_RE.match(s)
        if m:
            label = m.group("key").strip().lower()
            value = m.group("value").strip()
            for flabel, key in FIELDS:
                if flabel.lower() != label:
                    continue
                if key == "item_count":
                    rec[key] = int(value) if value.isdigit() else None
                elif key == "tickets":
                    rec[key] = _split_list(value)
                elif key == "clients":
                    rec[key] = [] if value.upper() == TBC else _split_clients(value)
                else:
                    rec[key] = value
            continue
        if s.startswith("- "):
            item = _parse_bullet(s[2:])
            if item:
                rec["items"].append(item)
    return rec


def _parse_transcript(lines, rec):
    """Fill rec['root_text'] / rec['reply_texts'] from the lines after the
    `## Posted message` heading: `### Root` / `### Reply n` headings, each
    followed by one `~~~`-fenced block. Text outside a fence is ignored."""
    section, in_fence, buf = None, False, []

    def close():
        nonlocal buf
        text = "\n".join(buf)
        if section == "root":
            rec["root_text"] = text
        elif section == "reply":
            rec["reply_texts"].append(text)
        buf = []

    for line in lines:
        s = line.rstrip()
        if in_fence:
            if s.strip() == FENCE:
                in_fence = False
                close()
            else:
                buf.append(s)
            continue
        if s.strip() == ROOT_HEADING:
            section = "root"
        elif s.strip().startswith(REPLY_HEADING_PREFIX):
            section = "reply"
        elif s.strip() == FENCE and section:
            in_fence = True
    if in_fence:          # unterminated fence: keep what was read
        close()


def _read_text_file(path, label):
    """Read a posted-message file; ValueError (exit 1) when missing."""
    p = Path(path)
    if not p.exists():
        raise ValueError(f"{label} file not found: {p}")
    return p.read_text()


def _parse_bullet(text):
    """`id · title · CATEGORY · clients · links` -> item dict, or None.
    The title may itself contain ` · `, so split from both ends."""
    parts = text.split(SEP)
    if len(parts) < 5:
        return None
    ident = parts[0].strip()
    links_raw = parts[-1].strip()
    clients_raw = parts[-2].strip()
    category = parts[-3].strip()
    title = SEP.join(parts[1:-3]).strip()
    if not ident or not title:
        return None
    links = [] if links_raw in NO_LINKS.values() else _split_list(links_raw)
    return {"id": ident, "title": title, "category": category,
            "clients": _split_clients(clients_raw) or [TBC], "links": links}


# --------------------------------------------------------------------------- #
# Subcommands
# --------------------------------------------------------------------------- #

def emit_result(payload, stream=None):
    stream = stream or sys.stdout
    print(file=stream)
    print("---RELEASE_ARCHIVE_RESULT---", file=stream)
    print(json.dumps(payload), file=stream)


def cmd_pending(args):
    name, source = resolve_collection_name(args.env_file)
    where = {"env": f"process env {ENV_KEY}",
             ".env": f"{ENV_KEY} in {args.env_file}",
             "default": "default — set MEM_RELEASE_ARCHIVE_COLLECTION in .env "
                        "to change"}[source]
    print(json.dumps({"collection_name": name,
                      "collection_description": COLLECTION_DESCRIPTION,
                      "source": source}, indent=2))
    print(f'Release archive: notes go to Mem collection "{name}" ({where}).',
          file=sys.stderr)
    emit_result({"ok": True, "action": "pending", "collection_name": name,
                 "source": source, "error": None})
    return 0


def cmd_record(args):
    p = Path(args.summaries)
    if not p.exists():
        raise ValueError(f"summaries file not found: {p}")
    date = (args.date or "").strip()
    if not DATE_RE.match(date):
        raise ValueError(f"--date must be YYYY-MM-DD (got {args.date!r})")
    ts = (args.ts or "").strip()
    if not ts:
        raise ValueError("--ts is required (root message ts)")
    root_text = _read_text_file(args.root_file, "root message")
    reply_texts = [_read_text_file(f, f"reply message {i}")
                   for i, f in enumerate(args.reply_files or [], start=1)]
    blocks = parse_summaries(p.read_text())
    items, dropped = build_items(blocks, args.product, load_data_index(args.data))
    if not items:
        raise ValueError(f"nothing to record: {len(blocks)} block(s), all "
                         f"dropped ({'; '.join(dropped) or 'file empty'})")
    body = render_note(items, args.product, date, (args.channel or "").strip(),
                       (args.channel_id or "").strip(), ts, args.permalink,
                       root_text, reply_texts)
    if args.out:
        Path(args.out).write_text(body)
    else:
        sys.stdout.write(body)
    for d in dropped:
        print(f"Release archive: dropped block {d}", file=sys.stderr)
    print(f"Release archive: rendered note for ts {ts} ({len(items)} item(s), "
          f"root + {len(reply_texts)} repl{'y' if len(reply_texts) == 1 else 'ies'}); "
          f"if this was a re-post, trash the older note in Mem.", file=sys.stderr)
    emit_result({"ok": True, "action": "record", "product": args.product,
                 "date": date, "ts": ts, "items": len(items),
                 "dropped": len(dropped), "tickets": [it["id"] for it in items],
                 "permalink": bool((args.permalink or "").strip()),
                 "replies": len(reply_texts),
                 "out": args.out or None, "error": None}, stream=sys.stderr)
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="Deterministic half of the Mem release archive")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("pending", help="Print the configured collection name "
                        "and description")
    p1.add_argument("--env-file", default=".env",
                    help=f"Read {ENV_KEY} here (process env overrides; "
                         f"default name {DEFAULT_COLLECTION_NAME!r})")

    p2 = sub.add_parser("record", help="Render the release note body")
    p2.add_argument("--summaries", required=True,
                    help="Per-ticket summaries file (PRIMARY input)")
    p2.add_argument("--data", default="", help="Gathered data JSON (secondary: "
                    "fills url / code links when a block lacks them)")
    p2.add_argument("--root-file", required=True,
                    help="The posted root message file (e.g. "
                         "/tmp/oracle_release_root.txt); appended verbatim")
    p2.add_argument("--reply-files", nargs="*", default=[],
                    help="Every posted thread-reply file, in post order; "
                         "each appended verbatim")
    p2.add_argument("--product", required=True, choices=PRODUCTS)
    p2.add_argument("--date", required=True,
                    help="YYYY-MM-DD, the same value used for the Slack header")
    p2.add_argument("--channel", required=True,
                    help="Configured SLACK_CHANNEL_* value, verbatim")
    p2.add_argument("--channel-id", required=True,
                    help="`channel` field from the slack_upload.py result block")
    p2.add_argument("--ts", required=True, help="Root message ts")
    p2.add_argument("--permalink", default="",
                    help="Slack permalink to the root; line omitted when absent")
    p2.add_argument("--out", default="", help="Write the note body here "
                    "(default: stdout)")

    args = ap.parse_args()
    try:
        if args.cmd == "pending":
            return cmd_pending(args)
        return cmd_record(args)
    except (ValueError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        emit_result({"ok": False, "action": args.cmd, "error": str(e)},
                    stream=sys.stderr if args.cmd == "record" else sys.stdout)
        return 1


if __name__ == "__main__":
    sys.exit(main())
