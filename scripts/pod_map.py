#!/usr/bin/env python3
"""
pod_map.py — deterministic half of pod-notify: tell each client's pod (a Slack
channel) which tickets in a release concern its clients.

The pod map lives in ONE Mem collection ("Release Notes Pod Map" by default,
`MEM_POD_MAP_COLLECTION` in .env), one hand-edited note per pod. Mem is only
reachable through the Mem MCP (Claude-side), so this script never talks to Mem
or Slack. The orchestrator saves the collection's `list_notes` response to a
file, runs a subcommand, and performs the Mem / Slack calls it prints.

  collection  Print the resolved collection name and description.
  route       Map the posted tickets to pods and render every pod's root post
              and thread replies (or, with --routing-only, the routing alone).
              Writes <out-dir>/plan.json plus the message files: one root
              post and one thread reply per ticket for each pod.
  preview     Re-lint every unposted pod's files, apply the latest channel
              checks (slack_upload.py --check-channel results), and print the
              routing table and one sample pod. Only a pod that passes both
              becomes `ready`.
  focus       /pod-notify: keep only the tickets a routing-only plan sends
              to a pod (so no analyzer runs for a ticket nobody gets).
  drop        Preview edit: drop one pod, or one ticket from one pod (that
              pod's messages are re-rendered and re-linted).
  mark        Record one successful post (root or reply) in plan.json, so a
              partial run resumes exactly where it stopped.
  log         Emit the Mem write for a pod-log note (what was sent where) —
              one per pod, written as soon as that pod is posted — which later
              runs read to skip already-sent tickets.
  names       List map client names that carry no Optimus client id yet, the
              pods' channels, and whether the template note exists.
  fill-ids    Emit note updates that add resolved Optimus client ids.
  template    Print the body of the "Pod template (copy me)" note.

Routing (route):
  - Only tickets present in the summaries file are routed (the posted set);
    MR-NNNN items have no Optimus ticket and are skipped.
  - A ticket's clients are its Optimus links (`optimus_clients`, from the
    gather script). With none, the `## Reported client(s)` section of the
    description is read: full names only, shown as "guessed"; "All clients /
    platform-wide" and "Internal" tickets go to no pod and are reported.
  - A client matches a pod by Optimus client id, else by normalized name.
  - A (channel id, ticket) pair already in a pod-log note is skipped unless
    --force is given.
  - Each card carries only the client title, the client paragraph, that pod's
    own clients and the ticket link — never the internal summary, developer
    mentions or other clients' names.

Pod note (first line = Mem note title):

    Pod: #impact-pod_chris

    **Channel ID:** C06U322U0F4
    **Clients:**
    - King Faisal Specialty Hospital & Resource Center [<optimus client uuid>]
    - Purdue University - College of Veterinary Medicine

Pod-log note:

    Pod notify: 2026-10-29 · /release-slack

    **Date:** 2026-10-29
    **Source:** /release-slack
    **Release post:** <permalink or —>

    - C06U322U0F4 · #impact-pod_chris · ts 1730000000.000100 · M1-1605, M1-1550
      - Pod post: <permalink of the pod's root message>

Every subcommand prints a machine-readable block on every exit path:

    ---POD_MAP_RESULT---
    {"ok", "action", ..., "error"}

Exit codes: 0 success, 1 any error (unreadable input, a bad plan, …); an
argparse usage error exits 2 before any work, without a result block.
A missing or empty notes file is NOT an error — it is an empty map.
"""

import argparse
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import release_archive  # noqa: E402
import slack_lint  # noqa: E402

ENV_KEY = "MEM_POD_MAP_COLLECTION"
DEFAULT_COLLECTION_NAME = "Release Notes Pod Map"
COLLECTION_DESCRIPTION = (
    "medtrics-release-notes: one note per pod (Slack channel) listing the "
    "Optimus clients it looks after, plus one pod-log note per pod-notify run. "
    "Read by /release-slack, /oracle-release and /pod-notify to tell each pod "
    "which released tickets concern its clients. Edit the pod notes by hand."
)
POD_PREFIX = "Pod: "
LOG_PREFIX = "Pod notify: "
TEMPLATE_TITLE = "Pod template (copy me)"

ROOT_CAP = slack_lint.ROOT_CAP
REPLY_CAP = slack_lint.REPLY_CAP
ROOT_CLIENTS_CAP = 1500   # chars of client names in a pod root before "and N more"

CHANNEL_ID_RE = re.compile(r"^[CG][A-Z0-9]{7,}$")
# `**Key:** value`, `**Key**: value` or plain `Key: value`, for the known keys
# only — so a client name containing a colon is never read as a field.
FIELD_RE = re.compile(
    r"^\*{0,2}\s*(?P<key>channel id|clients|notes|date|source|release post)\s*"
    r"(?:\*\*\s*:|:\s*\*\*|:)\s*(?P<value>.*)$", re.I)
BULLET_RE = re.compile(r"^\s*(?:[-*•+]|\d+[.)])\s+(?P<text>.+?)\s*$")
# Mem hands notes back with Markdown escapes added (`\_`, `\[`, `\~`, …) and
# a trailing `\` as its hard line break.
MD_ESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!~|<>])")
CLIENT_ID_RE = re.compile(r"\s*\[(?P<id>[0-9A-Za-z-]{8,})\]\s*$")
LOG_LINE_RE = re.compile(
    r"^\s*[-*•]\s*(?P<channel>[CG][A-Z0-9]{7,})\s*·\s*(?P<name>\S+)\s*·\s*"
    r"ts\s*(?P<ts>\S+)\s*·\s*(?P<tickets>.+?)\s*$")
HEADING_RE = re.compile(r"^\s*#{1,6}\s+(?P<text>.*)$")
MR_ID_RE = re.compile(r"^MR-\d+$", re.I)
AMP_RE = re.compile(r"&(?!amp;|lt;|gt;)")

PLATFORM_WORDS = ("all clients", "platform-wide", "platform wide", "all institutions")
# Matched against a whole reported item, so "… Internal Medicine" stays a name.
INTERNAL_RE = re.compile(r"^(?:internal\b|not client[- ]specific|no client)", re.I)
NONE_WORDS = {"", "n/a", "na", "none", "tbc", "-", "—"}


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

def normalize_name(value):
    """Casefold, `&` → and, strip punctuation and accents, collapse spaces."""
    v = unicodedata.normalize("NFKD", str(value or ""))
    v = "".join(ch for ch in v if not unicodedata.combining(ch))
    v = v.casefold().replace("&amp;", " and ").replace("&", " and ")
    v = re.sub(r"[^\w\s]", " ", v)
    return " ".join(v.split())


def escape(text):
    """Slack mrkdwn escape for free text: bare & < > (existing entities kept)."""
    t = AMP_RE.sub("&amp;", str(text or ""))
    return t.replace("<", "&lt;").replace(">", "&gt;")


def resolve_collection_name(env_path=".env", environ=None):
    """(name, source) — process env, then .env, then the default."""
    environ = os.environ if environ is None else environ
    from_env = (environ.get(ENV_KEY) or "").strip()
    if from_env:
        return from_env, "env"
    from_file = release_archive._read_env_file_key(env_path, ENV_KEY).strip()
    if from_file:
        return from_file, ".env"
    return DEFAULT_COLLECTION_NAME, "default"


def emit_result(payload):
    print()
    print("---POD_MAP_RESULT---")
    print(json.dumps(payload))


def _read_json(path, label):
    p = Path(path)
    if not p.exists():
        raise ValueError(f"{label} not found: {p}")
    return json.loads(p.read_text())


def _raw_notes(path):
    """The orchestrator's saved `list_notes` response as a list of note dicts.
    Accepts {"notes": [...]}, a bare list, or a missing/empty file (→ [])."""
    p = Path(path) if path else None
    if not p or not p.exists() or p.stat().st_size == 0:
        return []
    raw = json.loads(p.read_text())
    if isinstance(raw, dict):
        notes = raw.get("notes") or raw.get("items") or raw.get("results") or []
    elif isinstance(raw, list):
        notes = raw
    else:
        notes = []
    return [n for n in notes if isinstance(n, dict)]


# --------------------------------------------------------------------------- #
# Note parsing / rendering
# --------------------------------------------------------------------------- #

def clean_line(line):
    """One note line as written: Mem's Markdown escapes and trailing hard
    break removed."""
    line = line.rstrip()
    if line.endswith("\\") and not line.endswith("\\\\"):
        line = line[:-1].rstrip()
    return MD_ESCAPE_RE.sub(r"\1", line)


def _lines(note):
    return [clean_line(x) for x in (note.get("content") or "").splitlines()]


def _title(note):
    """The note title: the first non-blank line, without a `#` heading mark."""
    for line in _lines(note):
        if line.strip():
            return re.sub(r"^\s*#{1,6}\s+", "", line).strip()
    return ""


def parse_client_line(text):
    """'Name [uuid]' → {"name", "id"}; a bare name has id ''."""
    text = text.strip()
    m = CLIENT_ID_RE.search(text)
    cid = ""
    if m:
        cid = m.group("id")
        text = text[:m.start()].strip()
    return {"name": text, "id": cid}


def parse_pod_note(note):
    """Parse one `Pod:` note. Returns (record or None, warnings).

    Each client carries `line`: the index of its bullet in the raw content
    (None for a one-line list), so fill-ids can edit that line alone."""
    raw_lines = (note.get("content") or "").splitlines()
    lines = [clean_line(x) for x in raw_lines]
    title = _title(note)
    if not title.startswith(POD_PREFIX):
        return None, []
    name = title[len(POD_PREFIX):].strip()
    rec = {"note_id": note.get("id") or note.get("note_id") or "",
           "version": note.get("version"),
           "channel_name": name if name.startswith("#") or not name else f"#{name}",
           "channel_id": "", "clients": []}
    in_clients = False
    title_seen = False
    for idx, line in enumerate(lines):
        if not title_seen:
            title_seen = bool(line.strip())
            continue
        if in_clients:
            b = BULLET_RE.match(line)
            if b:
                rec["clients"].append({**parse_client_line(b.group("text")), "line": idx})
                continue
        m = FIELD_RE.match(line.strip())
        if m:
            key = m.group("key").strip().lower()
            value = m.group("value").strip()
            in_clients = key == "clients"
            if key == "channel id":
                rec["channel_id"] = value.strip("`* ")
            elif in_clients and value and value.lower() not in NONE_WORDS:
                # Tolerate a one-line list separated by semicolons.
                for part in value.split(";"):
                    if part.strip():
                        rec["clients"].append({**parse_client_line(part), "line": None})
            continue
        if in_clients and line.strip():
            in_clients = False
    warnings = []
    if not CHANNEL_ID_RE.match(rec["channel_id"]):
        warnings.append(f"Pod note \"{title}\" has no valid Channel ID "
                        f"(got {rec['channel_id'] or 'nothing'}) — skipped.")
        return None, warnings
    if not rec["clients"]:
        warnings.append(f"Pod note \"{title}\" lists no clients.")
    return rec, warnings


def render_pod_note(rec):
    out = [f"{POD_PREFIX}{rec['channel_name']}", "",
           f"**Channel ID:** {rec['channel_id']}", "**Clients:**"]
    for c in rec["clients"]:
        out.append(f"- {c['name']} [{c['id']}]" if c.get("id") else f"- {c['name']}")
    return "\n".join(out) + "\n"


def render_template():
    return (f"{TEMPLATE_TITLE}\n\n"
            "Copy this note for each pod and change the title to `Pod: #<channel name>`.\n"
            "List each client exactly as Optimus names it, one per line. The Slack bot\n"
            "must be a member of the channel. This template is never read as a pod.\n\n"
            "**Channel ID:** C0XXXXXXXXX\n"
            "**Clients:**\n"
            "- Exact Optimus client name\n")


def parse_log_note(note):
    """Parse one `Pod notify:` note → list of {channel_id, channel_name, ts,
    ticket, date}. Returns [] for any other note."""
    title = _title(note)
    if not title.startswith(LOG_PREFIX):
        return []
    date = title[len(LOG_PREFIX):].split("·")[0].strip()
    out = []
    for line in _lines(note):
        m = LOG_LINE_RE.match(line)
        if not m:
            continue
        for t in m.group("tickets").split(","):
            t = t.strip()
            if t:
                out.append({"channel_id": m.group("channel"),
                            "channel_name": m.group("name"),
                            "ts": m.group("ts"), "ticket": t.upper(), "date": date})
    return out


class PodMap:
    """The parsed pod notes plus the client → pod indexes."""

    def __init__(self, notes):
        self.pods = []
        self.warnings = []
        self.sent = {}          # (channel_id, TICKET) -> log entry
        by_channel = {}
        for n in notes:
            for entry in parse_log_note(n):
                self.sent.setdefault((entry["channel_id"], entry["ticket"]), entry)
            rec, warns = parse_pod_note(n)
            self.warnings.extend(warns)
            if rec is None:
                continue
            if rec["channel_id"] in by_channel:
                first = by_channel[rec["channel_id"]]
                self.warnings.append(
                    f"Two pod notes use channel {rec['channel_id']} "
                    f"({first['channel_name']}, {rec['channel_name']}) — merged.")
                first["clients"].extend(rec["clients"])
                continue
            by_channel[rec["channel_id"]] = rec
            self.pods.append(rec)
        self.by_id = {}
        self.by_name = {}
        for i, pod in enumerate(self.pods):
            seen = set()
            for c in pod["clients"]:
                key = normalize_name(c["name"])
                if key in seen:
                    continue
                seen.add(key)
                if c.get("id"):
                    self.by_id.setdefault(c["id"], []).append(i)
                if key:
                    self.by_name.setdefault(key, []).append(i)
        for key, idxs in self.by_name.items():
            if len(idxs) > 1:
                chans = ", ".join(self.pods[i]["channel_name"] for i in idxs)
                self.warnings.append(f"Client \"{key}\" is listed in {len(idxs)} pods "
                                     f"({chans}); each of them gets its tickets.")

    def pods_for(self, client):
        """Pod indexes for a client {"id", "name"} — by id, else by name."""
        if client.get("id") and client["id"] in self.by_id:
            return list(self.by_id[client["id"]])
        return list(self.by_name.get(normalize_name(client.get("name")), []))


# --------------------------------------------------------------------------- #
# Ticket → clients
# --------------------------------------------------------------------------- #

def _clean_reported(item):
    item = re.sub(r"\(\s*`[^`]*`\s*\)", "", item)      # (`CODE`)
    item = re.sub(r"`[^`]*`", "", item)
    item = re.sub(r"\((?:unverified|unconfirmed|tbc)\)", "", item, flags=re.I)
    item = re.sub(r"\*+", "", item)
    return " ".join(item.split()).strip(" -–—:;,.")


def reported_clients(description):
    """Read the `## Reported client(s)` section of a ticket description.

    Returns (kind, names): kind is "names", "platform", "internal" or "none".
    Names come back cleaned of code spans and "(unverified)" notes.
    """
    lines = (description or "").splitlines()
    section = None
    for i, line in enumerate(lines):
        h = HEADING_RE.match(line)
        if h and re.match(r"reported\s+client", h.group("text").strip(), re.I):
            section = []
            for nxt in lines[i + 1:]:
                if HEADING_RE.match(nxt):
                    break
                section.append(nxt)
            break
    if section is None:
        return "none", []
    items = []
    bullets = [BULLET_RE.match(line) for line in section]
    if any(bullets):
        items = [b.group("text") for b in bullets if b]
    else:
        items = [line.strip() for line in section if line.strip()]
    names, platform, internal = [], False, False
    for raw in items:
        low = raw.lower()
        if any(w in low for w in PLATFORM_WORDS):
            platform = True
            continue
        name = _clean_reported(raw)
        if INTERNAL_RE.match(name):
            internal = True
            continue
        if name.lower() in NONE_WORDS:
            continue
        names.append(name)
    if names:
        return "names", names
    if platform:
        return "platform", []
    if internal:
        return "internal", []
    return "none", []


def ticket_clients(issue):
    """(via, clients, kind): via is "linked" or "guessed"; clients are
    [{"id", "name"}]; kind mirrors reported_clients for the fallback."""
    linked = issue.get("optimus_clients") or []
    if linked:
        return "linked", [{"id": c.get("id") or "", "name": c.get("name") or ""}
                          for c in linked if c.get("name")], "names"
    kind, names = reported_clients(issue.get("description") or "")
    return "guessed", [{"id": "", "name": n} for n in names], kind


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def _join_names(names, cap):
    out, used = [], 0
    for i, n in enumerate(names):
        piece = escape(n)
        if out and used + len(piece) + 2 > cap:
            out.append(f"and {len(names) - i} more")
            break
        out.append(piece)
        used += len(piece) + 2
    return ", ".join(out)


def render_root(date, tickets, clients, release_link=""):
    n = len(tickets)
    lines = [f":mega: *Shipped for your clients — {date}*", "",
             f"{n} item{'s' if n != 1 else ''} in this release concern"
             f"{'s' if n == 1 else ''} clients in this pod: "
             f"{_join_names(clients, ROOT_CLIENTS_CAP)}.",
             "The cards are in the thread."]
    if release_link:
        lines.append(f"Full release post: <{release_link}|open in Slack>")
    return "\n".join(lines) + "\n"


def clean_text(text):
    """Analyzer text made safe for a card: one line, no mrkdwn markup that
    would fail slack_lint or restyle the card (`**`, fences, `#` headers,
    stray `*`), then escaped."""
    t = " ".join(str(text or "").split())
    t = t.replace("```", "").replace("**", "").replace("*", "")
    t = re.sub(r"^#{1,6}\s*", "", t)
    return escape(t.strip())


def _link_url(url):
    """A URL safe inside <url|label>: `|`, `<`, `>` percent-encoded."""
    return url.replace("|", "%7C").replace("<", "%3C").replace(">", "%3E")


def render_card(n, ticket, cap=REPLY_CAP):
    """One ticket's thread reply. The summary is cut short (…) when the card
    would pass `cap` chars."""
    title = clean_text(ticket.get("client_title")) or ticket["identifier"]
    names = _join_names(ticket["pod_clients"], 1000)
    if ticket.get("via") == "guessed":
        names += " (from the ticket description, not linked in Optimus)"
    tail = [f"• Clients: {names}"]
    if ticket.get("url"):
        tail.append(f"• Ticket: <{_link_url(ticket['url'])}|{ticket['identifier']}>")
    else:
        tail.append(f"• Ticket: {ticket['identifier']}")
    if ticket.get("flagged"):
        tail.append("• Heads-up: client-specific item still in progress — "
                    "send it to that client only, not in a broadcast")
    head = f"*{n}. {title}*"
    summary = clean_text(ticket.get("summary"))
    room = cap - len(head) - len("\n".join(tail)) - 4
    if len(summary) > room:
        summary = summary[:max(room - 1, 0)].rsplit(" ", 1)[0].rstrip("&") + "…"
    body = [head] + ([summary] if summary else []) + tail
    return "\n".join(body) + "\n"


# --------------------------------------------------------------------------- #
# Subcommands
# --------------------------------------------------------------------------- #

def cmd_collection(args):
    name, source = resolve_collection_name(args.env_path)
    print(f"Pod map collection: \"{name}\" (from {source}).", file=sys.stderr)
    emit_result({"ok": True, "action": "collection", "collection_name": name,
                 "source": source, "collection_description": COLLECTION_DESCRIPTION,
                 "error": None})
    return 0


def _load_data(path):
    data = _read_json(path, "data file")
    if not isinstance(data, dict) or not isinstance(data.get("issues"), list):
        raise ValueError("data file is not a JSON object with an 'issues' list")
    return data


def build_plan(data, blocks, pod_map, date, source, release_link="", force=False):
    """Route the posted tickets. `blocks` is None in routing-only mode (every
    gathered ticket is routed by its internal title)."""
    issues = {(i.get("identifier") or "").upper(): i for i in data["issues"]}
    if blocks is None:
        posted = [{"IDENTIFIER": i.get("identifier") or ""} for i in data["issues"]]
    else:
        posted = blocks
    unrouted = {"no_client": [], "platform_wide": [], "internal": [],
                "unmapped_clients": [], "skipped": [], "missing_data": [],
                "missing_text": [], "no_summary": []}
    if blocks is not None:
        in_blocks = {(b.get("IDENTIFIER") or "").strip().upper() for b in blocks}
        unrouted["no_summary"] = [i.get("identifier") for i in data["issues"]
                                  if (i.get("identifier") or "").upper() not in in_blocks]
    already = []
    per_pod = {}   # pod index -> list of ticket dicts
    for block in posted:
        ident = (block.get("IDENTIFIER") or "").strip()
        if not ident:
            continue
        if MR_ID_RE.match(ident):
            unrouted["skipped"].append(ident)
            continue
        issue = issues.get(ident.upper())
        if issue is None:
            unrouted["missing_data"].append(ident)
            continue
        if blocks is not None and not ((block.get("CLIENT_TITLE") or "").strip()
                                       and (block.get("EXEC_SUMMARY") or "").strip()):
            # Never fall back to the internal title or summary in a pod.
            unrouted["missing_text"].append(ident)
            continue
        via, clients, kind = ticket_clients(issue)
        if not clients:
            bucket = {"platform": "platform_wide", "internal": "internal"}.get(kind, "no_client")
            unrouted[bucket].append(ident)
            continue
        hits = {}   # pod index -> client names
        for c in clients:
            idxs = pod_map.pods_for(c)
            if not idxs:
                unrouted["unmapped_clients"].append(
                    {"identifier": ident, "client": c["name"], "via": via})
            for i in idxs:
                hits.setdefault(i, [])
                if c["name"] not in hits[i]:
                    hits[i].append(c["name"])
        for i, names in hits.items():
            pod = pod_map.pods[i]
            prior = pod_map.sent.get((pod["channel_id"], ident.upper()))
            if prior and not force:
                already.append({"identifier": ident, "channel_id": pod["channel_id"],
                                "channel_name": pod["channel_name"],
                                "date": prior["date"]})
                continue
            per_pod.setdefault(i, []).append({
                "identifier": ident,
                "title": issue.get("title") or "",
                "client_title": (block.get("CLIENT_TITLE") or "").strip(),
                "summary": (block.get("EXEC_SUMMARY") or "").strip(),
                "url": (block.get("URL") or issue.get("url") or "").strip(),
                "flagged": bool((block.get("CLIENT_FLAG") or "").strip()),
                "via": via,
                "pod_clients": names,
            })
    pods = []
    for i in sorted(per_pod, key=lambda k: pod_map.pods[k]["channel_name"]):
        pod = pod_map.pods[i]
        clients = _pod_clients(per_pod[i])
        pods.append({"n": len(pods) + 1, "channel_id": pod["channel_id"],
                     "channel_name": pod["channel_name"], "note_id": pod["note_id"],
                     "clients": clients, "tickets": per_pod[i],
                     "status": "routed", "root_file": "", "reply_files": [],
                     "reply_tickets": [], "root_ts": "", "posted_replies": [],
                     "lint": []})
    return {"date": date, "source": source, "release_link": release_link,
            "pods": pods, "unrouted": unrouted, "already_sent": already,
            "warnings": list(pod_map.warnings)}


def render_pod_files(plan, pod, out_dir):
    """(Re)write one pod's root and reply files and lint them. Returns True
    when every file passes slack_lint."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    n = pod["n"]
    for old in out.glob(f"pod_{n:02d}_*.txt"):
        old.unlink()
    root = render_root(plan["date"], pod["tickets"], pod["clients"],
                       plan.get("release_link") or "")
    root_path = out / f"pod_{n:02d}_root.txt"
    root_path.write_text(root)
    pod["root_file"] = str(root_path)
    pod["reply_files"], pod["reply_tickets"] = [], []
    # One thread reply per ticket.
    for k, t in enumerate(pod["tickets"], start=1):
        path = out / f"pod_{n:02d}_reply_{k:02d}.txt"
        path.write_text(render_card(k, t))
        pod["reply_files"].append(str(path))
        pod["reply_tickets"].append([t["identifier"]])
    pod["lint"] = lint_pod(pod)
    # Ready only once preview has checked the channel.
    pod["status"] = "lint_failed" if pod["lint"] else "routed"
    return not pod["lint"]


def lint_pod(pod):
    """slack_lint over the pod's files as they are on disk now (they may have
    been edited at the preview)."""
    lint = []
    for path, kind in [(pod["root_file"], "root")] + [(f, "reply") for f in pod["reply_files"]]:
        p = Path(path)
        if not p.exists():
            lint.append({"file": path, "rule": "missing", "line": 0, "column": 0,
                         "snippet": "", "hint": "the file is gone — route again"})
            continue
        lint.extend({"file": path, **v} for v in slack_lint.lint(p.read_text(), kind))
    return lint


def render_plan_files(plan, out_dir):
    return sum(not render_pod_files(plan, pod, out_dir) for pod in plan["pods"])


def _pod_clients(tickets):
    clients = []
    for t in tickets:
        for n in t["pod_clients"]:
            if n not in clients:
                clients.append(n)
    return clients


def _write_plan(plan, out_dir):
    path = Path(out_dir) / "plan.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, indent=2))
    return str(path)


def _counts(plan):
    u = plan["unrouted"]
    return {"pods": len(plan["pods"]),
            "cards": sum(len(p["tickets"]) for p in plan["pods"]),
            "no_client": len(u["no_client"]), "platform_wide": len(u["platform_wide"]),
            "internal": len(u["internal"]), "unmapped_clients": len(u["unmapped_clients"]),
            "skipped": len(u["skipped"]), "missing_data": len(u["missing_data"]),
            "missing_text": len(u["missing_text"]), "no_summary": len(u["no_summary"]),
            "already_sent": len(plan["already_sent"]),
            "guessed": sum(1 for p in plan["pods"] for t in p["tickets"]
                           if t["via"] == "guessed")}


def cmd_route(args):
    data = _load_data(args.data)
    if args.routing_only:
        blocks = None
    else:
        if not args.summaries:
            raise ValueError("--summaries is required unless --routing-only is given")
        sp = Path(args.summaries)
        if not sp.exists():
            raise ValueError(f"summaries file not found: {sp}")
        blocks = [b for b in release_archive.parse_summaries(sp.read_text())
                  if b.get("IDENTIFIER")]
    pod_map = PodMap(_raw_notes(args.notes))
    plan = build_plan(data, blocks, pod_map, args.date, args.source,
                      args.release_link, args.force)
    plan["routing_only"] = bool(args.routing_only)
    lint_failures = 0 if args.routing_only else render_plan_files(plan, args.out_dir)
    plan_path = _write_plan(plan, args.out_dir)
    counts = _counts(plan)
    print(f"Pod routing: {counts['cards']} card(s) to {counts['pods']} pod(s) from "
          f"{len(pod_map.pods)} pod note(s); {counts['already_sent']} already sent, "
          f"{counts['no_client']} with no client, {counts['platform_wide']} platform-wide, "
          f"{counts['internal']} internal, {counts['unmapped_clients']} unmapped client "
          f"link(s).", file=sys.stderr)
    emit_result({"ok": True, "action": "route", "plan": plan_path,
                 "pod_notes": len(pod_map.pods), "lint_failures": lint_failures,
                 "channels": [{"channel_id": p["channel_id"],
                               "channel_name": p["channel_name"]} for p in plan["pods"]],
                 **counts, "warnings": plan["warnings"], "error": None})
    return 0


FINAL_STATUSES = ("posted", "partial", "dropped")


def channel_block_reason(check):
    """Why a pod may not be posted to, from one --check-channel result; ''
    when it may. Only a channel shared with another organization (Slack
    Connect, or an invite pending) counts as shared: `is_shared` alone is an
    org-wide internal channel on Enterprise Grid."""
    if not check or not check.get("ok"):
        err = (check or {}).get("error") or "not checked"
        if "channel_not_found" in err:
            return ("channel not found — a private channel the bot is not in looks "
                    "like this; invite the bot, then re-run")
        return f"channel check failed: {err}"
    info = check.get("info") or {}
    if info.get("is_archived"):
        return "channel is archived"
    if info.get("is_ext_shared") or info.get("is_pending_ext_shared"):
        return "channel is shared outside Medtrics (Slack Connect)"
    if not info.get("is_member"):
        return "the Slack bot is not a member — invite it, then re-run"
    return ""


def refresh_status(plan, checks=None):
    """Re-derive every not-yet-posted pod's status from its files on disk
    (re-linted, so a fix made at the preview counts) and, when given, the
    latest channel checks. Without checks a clean pod stays `routed`.
    Returns the blocked count."""
    blocked = 0
    seen_warnings = set(plan["warnings"])
    for pod in plan["pods"]:
        if pod["status"] in FINAL_STATUSES:
            continue
        pod.pop("blocked_reason", None)
        if not plan.get("routing_only"):
            pod["lint"] = lint_pod(pod)
            if pod["lint"]:
                pod["status"] = "lint_failed"
                continue
        if checks is None:
            pod["status"] = "routed"
            continue
        check = checks.get(pod["channel_id"])
        reason = channel_block_reason(check)
        name = ((check or {}).get("info") or {}).get("name")
        if name and f"#{name}" != pod["channel_name"]:
            w = (f"{pod['channel_id']} is now #{name} in Slack; the pod note says "
                 f"{pod['channel_name']} (posting by id).")
            if w not in seen_warnings:
                plan["warnings"].append(w)
                seen_warnings.add(w)
        if reason:
            pod["status"] = "blocked"
            pod["blocked_reason"] = reason
            blocked += 1
        else:
            pod["status"] = "ready"
    return blocked


def render_table(plan):
    lines = [f"Pod routing — {plan['date']} ({plan['source']})", ""]
    if not plan["pods"]:
        lines.append("No pod gets anything from this release.")
    for pod in plan["pods"]:
        ids = ", ".join(t["identifier"] + ("*" if t["via"] == "guessed" else "")
                        for t in pod["tickets"])
        status = pod["status"]
        if status == "blocked":
            status = f"BLOCKED — {pod.get('blocked_reason', '')}"
        elif status == "dropped":
            status = "DROPPED at the preview"
        elif status == "lint_failed":
            status = f"LINT FAILED — {len(pod['lint'])} violation(s)"
        elif status == "routed":
            status = "channel not checked"
        replies = len(pod["reply_files"])
        lines.append(f"{pod['n']}. {pod['channel_name']} ({pod['channel_id']}) — "
                     f"{len(pod['tickets'])} ticket(s), {replies} repl{'y' if replies == 1 else 'ies'} "
                     f"— {status}")
        lines.append(f"   {ids}")
    u = plan["unrouted"]
    if any(t["via"] == "guessed" for p in plan["pods"] for t in p["tickets"]):
        lines += ["", "* client guessed from the ticket description (no Optimus link)"]
    lines.append("")
    lines.append("Not sent to any pod:")
    lines.append(f"  No client: {', '.join(u['no_client']) or 'none'}")
    lines.append(f"  Platform-wide: {', '.join(u['platform_wide']) or 'none'}")
    lines.append(f"  Internal: {', '.join(u['internal']) or 'none'}")
    um = [f"{x['identifier']} → {x['client']}" + (" (guessed)" if x["via"] == "guessed" else "")
          for x in u["unmapped_clients"]]
    lines.append(f"  Clients in no pod: {'; '.join(um) or 'none'}")
    if u["skipped"]:
        lines.append(f"  Skipped (no Optimus ticket): {', '.join(u['skipped'])}")
    if u["missing_data"]:
        lines.append(f"  Not in the gathered data: {', '.join(u['missing_data'])}")
    if u.get("missing_text"):
        lines.append(f"  No client title or client paragraph: {', '.join(u['missing_text'])}")
    if u.get("no_summary"):
        lines.append(f"  No summary (no visible change, or not analyzed): "
                     f"{', '.join(u['no_summary'])}")
    if plan["already_sent"]:
        sent = [f"{x['identifier']} → {x['channel_name']} ({x['date']})"
                for x in plan["already_sent"]]
        lines.append(f"  Already sent: {'; '.join(sent)}")
    if plan["warnings"]:
        lines += ["", "Warnings:"] + [f"  - {w}" for w in plan["warnings"]]
    return "\n".join(lines) + "\n"


def cmd_preview(args):
    plan = _read_json(args.plan, "plan file")
    checks = _read_json(args.channels, "channel checks") if args.channels else None
    blocked = refresh_status(plan, checks)
    Path(args.plan).write_text(json.dumps(plan, indent=2))
    print(render_table(plan))
    for pod in plan["pods"]:
        for v in pod.get("lint") or []:
            print(f"  lint {Path(v['file']).name}:{v.get('line')} {v.get('rule')} — {v.get('hint')}")
    sample = next((p for p in plan["pods"] if p["status"] == "ready"), None)
    if sample and not plan.get("routing_only"):
        print(f"Sample — {sample['channel_name']} root:")
        print("```")
        print(Path(sample["root_file"]).read_text().rstrip())
        print("```")
        for f in sample["reply_files"][:3]:
            print(f"{sample['channel_name']} thread reply ({Path(f).name}):")
            print("```")
            print(Path(f).read_text().rstrip())
            print("```")
        more = len(sample["reply_files"]) - 3
        if more > 0:
            print(f"… and {more} more thread repl{'y' if more == 1 else 'ies'} in this pod.")
    ready = [p["channel_id"] for p in plan["pods"] if p["status"] == "ready"]
    emit_result({"ok": True, "action": "preview", "ready": ready, "blocked": blocked,
                 "sample": sample["channel_name"] if sample else None,
                 "warnings": plan["warnings"], "error": None})
    return 0


def cmd_focus(args):
    """/pod-notify: keep only the tickets a routing-only plan sends to a pod,
    and give each the `clients` line its analyzer prompt carries."""
    data = _load_data(args.data)
    plan = _read_json(args.plan, "plan file")
    clients = {}
    for pod in plan["pods"]:
        for t in pod["tickets"]:
            names = clients.setdefault(t["identifier"].upper(), [])
            names.extend(n for n in t["pod_clients"] if n not in names)
    kept = []
    for issue in data["issues"]:
        names = clients.get((issue.get("identifier") or "").upper())
        if names:
            issue["clients"] = "; ".join(names)
            kept.append(issue)
    data["issues"] = kept
    Path(args.out).write_text(json.dumps(data, indent=2))
    ids = [i["identifier"] for i in kept]
    print(", ".join(ids))
    emit_result({"ok": True, "action": "focus", "tickets": ids, "out": args.out,
                 "error": None})
    return 0


def cmd_drop(args):
    """Preview edit: drop one pod, or one ticket from one pod (re-rendered)."""
    plan = _read_json(args.plan, "plan file")
    pod = next((p for p in plan["pods"] if p["channel_id"] == args.channel_id), None)
    if pod is None:
        raise ValueError(f"no pod with channel id {args.channel_id} in the plan")
    if pod["status"] in ("posted", "partial"):
        raise ValueError(f"{pod['channel_name']} already has posts; it cannot be changed")
    if args.ticket:
        keep = [t for t in pod["tickets"] if t["identifier"].upper() != args.ticket.upper()]
        if len(keep) == len(pod["tickets"]):
            raise ValueError(f"{args.ticket} is not in {pod['channel_name']}")
        pod["tickets"] = keep
        pod["clients"] = _pod_clients(keep)
    if not args.ticket or not pod["tickets"]:
        pod["status"] = "dropped"
    else:
        # Back to `routed` (or lint_failed): the next preview re-checks it.
        render_pod_files(plan, pod, Path(args.plan).parent)
    Path(args.plan).write_text(json.dumps(plan, indent=2))
    emit_result({"ok": True, "action": "drop", "channel_id": pod["channel_id"],
                 "status": pod["status"], "tickets": [t["identifier"] for t in pod["tickets"]],
                 "lint": pod.get("lint", []), "error": None})
    return 0


def cmd_mark(args):
    plan = _read_json(args.plan, "plan file")
    pod = next((p for p in plan["pods"] if p["channel_id"] == args.channel_id), None)
    if pod is None:
        raise ValueError(f"no pod with channel id {args.channel_id} in the plan")
    if pod["status"] not in ("ready", "partial", "posted"):
        raise ValueError(f"{pod['channel_name']} is {pod['status']}, not ready to post")
    if args.kind == "root":
        if pod.get("root_ts") and pod["root_ts"] != args.ts:
            raise ValueError(f"{pod['channel_name']} already has root ts {pod['root_ts']}")
        pod["root_ts"] = args.ts
        if args.permalink:
            pod["root_permalink"] = args.permalink
    else:
        if not pod.get("root_ts"):
            raise ValueError("mark the root post before its replies")
        want = Path(args.file).resolve() if args.file else None
        f = next((x for x in pod["reply_files"] if want and Path(x).resolve() == want), None)
        if f is None:
            raise ValueError(f"{args.file} is not one of this pod's reply files")
        if f not in pod["posted_replies"]:
            pod["posted_replies"].append(f)
    done = len(pod["posted_replies"]) == len(pod["reply_files"]) and pod["root_ts"]
    pod["status"] = "posted" if done else "partial"
    Path(args.plan).write_text(json.dumps(plan, indent=2))
    remaining = [f for f in pod["reply_files"] if f not in pod["posted_replies"]]
    emit_result({"ok": True, "action": "mark", "channel_id": pod["channel_id"],
                 "status": pod["status"], "root_ts": pod["root_ts"],
                 "remaining": remaining, "error": None})
    return 0


def render_log_note(plan, channel_id=""):
    """The pod-log note for every posted pod, or for one pod (`channel_id`)."""
    lines = [f"{LOG_PREFIX}{plan['date']} · {plan['source']}", "",
             f"**Date:** {plan['date']}", f"**Source:** {plan['source']}",
             f"**Release post:** {plan.get('release_link') or '—'}", ""]
    entries = 0
    for pod in plan["pods"]:
        if channel_id and pod["channel_id"] != channel_id:
            continue
        if not pod.get("root_ts"):
            continue
        sent = []
        for f, ids in zip(pod["reply_files"], pod["reply_tickets"]):
            if f in pod["posted_replies"]:
                sent.extend(ids)
        if not sent:
            continue
        entries += 1
        lines.append(f"- {pod['channel_id']} · {pod['channel_name']} · ts {pod['root_ts']}"
                     f" · {', '.join(sent)}")
        if pod.get("root_permalink"):
            lines.append(f"  - Pod post: {pod['root_permalink']}")
    return "\n".join(lines) + "\n", entries


def cmd_log(args):
    plan = _read_json(args.plan, "plan file")
    content, entries = render_log_note(plan, args.channel_id)
    name, _src = resolve_collection_name(args.env_path)
    writes = [{"action": "create", "collection_title": name, "content": content}] if entries else []
    out = {"collection_title": name, "collection_description": COLLECTION_DESCRIPTION,
           "writes": writes}
    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=2))
    else:
        print(json.dumps(out, indent=2))
    print(f"Pod log: {entries} pod(s) to record.", file=sys.stderr)
    emit_result({"ok": True, "action": "log", "pods": entries,
                 "out": args.out or None, "error": None})
    return 0


def cmd_names(args):
    missing = []
    pods = []
    for n in _raw_notes(args.notes):
        rec, _w = parse_pod_note(n)
        if rec is None:
            continue
        pods.append({"channel_id": rec["channel_id"], "channel_name": rec["channel_name"]})
        for c in rec["clients"]:
            if not c.get("id") and c["name"] not in missing:
                missing.append(c["name"])
    print("\n".join(missing))
    has_template = any(_title(n) == TEMPLATE_TITLE for n in _raw_notes(args.notes))
    emit_result({"ok": True, "action": "names", "pods": len(pods), "channels": pods,
                 "has_template": has_template, "missing_ids": missing, "error": None})
    return 0


def cmd_fill_ids(args):
    resolved = _read_json(args.resolved, "resolved ids")
    by_name = {normalize_name(k): v for k, v in resolved.items()}
    writes, unresolved = [], []
    for n in _raw_notes(args.notes):
        rec, _w = parse_pod_note(n)
        if rec is None:
            continue
        # Edit only the client bullet lines; everything else in the note —
        # other fields, free text, Mem's own formatting — stays as it is.
        raw_lines = (n.get("content") or "").splitlines()
        one_line = False
        changed = False
        for c in rec["clients"]:
            if c.get("id"):
                continue
            hit = by_name.get(normalize_name(c["name"]))
            if isinstance(hit, dict):
                hit = hit.get("id")
            if not hit:
                if c["name"] not in unresolved:
                    unresolved.append(c["name"])
                continue
            c["id"] = str(hit)
            if c.get("line") is None:
                one_line = True
            else:
                raw_lines[c["line"]] = raw_lines[c["line"]].rstrip().rstrip("\\").rstrip() + f" [{hit}]"
            changed = True
        if changed:
            # A one-line client list can't be edited in place: rewrite the note.
            content = render_pod_note(rec) if one_line else "\n".join(raw_lines) + "\n"
            writes.append({"action": "update", "note_id": rec["note_id"],
                           "version": rec["version"], "content": content})
    out = {"writes": writes}
    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=2))
    else:
        print(json.dumps(out, indent=2))
    emit_result({"ok": True, "action": "fill-ids", "updates": len(writes),
                 "unresolved": unresolved, "error": None})
    return 0


def cmd_template(args):
    print(render_template(), end="")
    emit_result({"ok": True, "action": "template", "title": TEMPLATE_TITLE, "error": None})
    return 0


def main():
    ap = argparse.ArgumentParser(description="Deterministic half of pod-notify")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("collection", help="Print the resolved pod map collection")
    p.add_argument("--env-path", default=".env")

    p = sub.add_parser("route", help="Route posted tickets to pods and render messages")
    p.add_argument("--data", required=True, help="Gathered data JSON (read-only)")
    p.add_argument("--summaries", default="", help="Posted per-ticket summaries file")
    p.add_argument("--notes", default="", help="Saved Mem list_notes response of the pod map")
    p.add_argument("--date", required=True, help="Release date (YYYY-MM-DD)")
    p.add_argument("--source", required=True, help="Calling command, e.g. /release-slack")
    p.add_argument("--release-link", default="", help="Permalink of the main release post")
    p.add_argument("--out-dir", default="/tmp/pod_notify")
    p.add_argument("--routing-only", action="store_true",
                   help="Dry run: route every gathered ticket, render no messages")
    p.add_argument("--force", action="store_true",
                   help="Re-send tickets a pod-log note already records")

    p = sub.add_parser("preview", help="Apply channel checks and print the preview")
    p.add_argument("--plan", required=True)
    p.add_argument("--channels", default="",
                   help="JSON {channel_id: slack_upload.py --check-channel result}")

    p = sub.add_parser("focus", help="Keep only tickets a routing-only plan sends to a pod")
    p.add_argument("--data", required=True)
    p.add_argument("--plan", required=True)
    p.add_argument("--out", required=True)

    p = sub.add_parser("drop", help="Preview edit: drop a pod, or one ticket from a pod")
    p.add_argument("--plan", required=True)
    p.add_argument("--channel-id", required=True)
    p.add_argument("--ticket", default="", help="Drop only this ticket (re-renders the pod)")

    p = sub.add_parser("mark", help="Record one successful post in the plan")
    p.add_argument("--plan", required=True)
    p.add_argument("--channel-id", required=True)
    p.add_argument("--kind", choices=["root", "reply"], required=True)
    p.add_argument("--ts", default="")
    p.add_argument("--file", default="", help="Reply file that was posted")
    p.add_argument("--permalink", default="",
                   help="Root post's Slack permalink (with --kind root), kept in the pod log")

    p = sub.add_parser("log", help="Emit the pod-log note write")
    p.add_argument("--plan", required=True)
    p.add_argument("--channel-id", default="",
                   help="Log only this pod (written as soon as it is posted)")
    p.add_argument("--out", default="")
    p.add_argument("--env-path", default=".env")

    p = sub.add_parser("names", help="List map client names without an Optimus id")
    p.add_argument("--notes", default="")

    p = sub.add_parser("fill-ids", help="Emit updates adding resolved client ids")
    p.add_argument("--notes", default="")
    p.add_argument("--resolved", required=True, help="JSON {client name: uuid or null}")
    p.add_argument("--out", default="")

    sub.add_parser("template", help="Print the pod template note")

    args = ap.parse_args()
    if args.cmd == "mark" and args.kind == "root" and not args.ts:
        ap.error("--kind root requires --ts")
    handlers = {"collection": cmd_collection, "route": cmd_route, "preview": cmd_preview,
                "focus": cmd_focus, "drop": cmd_drop, "mark": cmd_mark, "log": cmd_log, "names": cmd_names,
                "fill-ids": cmd_fill_ids, "template": cmd_template}
    try:
        return handlers[args.cmd](args)
    except Exception as e:   # every failure still ends in a result block
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        emit_result({"ok": False, "action": args.cmd, "error": str(e)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
