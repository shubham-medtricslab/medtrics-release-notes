#!/usr/bin/env python3
"""
kb_route.py — read the start page and click path a ticket's own text gives.

Optimus descriptions of legacy features usually say where the feature lives:
a "**URL/Page:**" field (often with a "User Opens a Session > hits View
Button" line under it), the appended Release Impact Checklist answer to
"Which portal and URL does this live on?", or a "## QA Checklist" whose first
step is "Navigate to ...". `extract(issue)` turns the first of those it finds
into a route; kb_select uses it before any diff-based guess.

    {"start": "/administration/curriculum/sessions/",   # host dropped, no id segment
     "clicks": [{"row": true}, {"text": "View"}, {"text": "Add Schedule"}],
     "write_gated": "",      # what only appears after a save/submit, else ""
     "source": "url_page" | "release_checklist" | "qa_checklist"}

Only medtricslab.com and medtrics.sg hosts count (demo, client tenants);
loom/lovable/atlassian/slack/github links are ignored. A path with a numeric or uuid segment is a record, not a page, and is
rejected. A click path ends at the first write verb; that step and the rest go
to write_gated, so the capture never presses Save/Submit/Confirm/Delete.

Stdlib only. `python3 kb_route.py --data filtered.json` prints each ticket's route.
"""

import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

WRITE_RE = re.compile(
    r"\b(?:save\w*|submit\w*|send\w*|confirm\w*|delet\w*|remov\w*|archiv\w*|"
    r"publish\w*|approv\w*|reject\w*|sign\w*)\b", re.I)
URL_RE = re.compile(r"https?://[^\s)\]>\"'<]+")
PATH_RE = re.compile(r"(?<![\w:/.])/[A-Za-z0-9_\-./#<>]+")
UUID_RE = re.compile(r"^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$", re.I)
FIELD_RE = re.compile(r"^\s*\*\*[^*\n]+?:\s*\*\*|^\s*\*\*[^*\n]+?\*\*\s*:")
URL_FIELD_RE = re.compile(
    r"^\s*(?:\*\*|__)?\s*(?:URL\s*/\s*Page|URL|Page)\s*(?:\*\*|__)?\s*:\s*(?:\*\*|__)?(.*)$", re.I)
SEP_RE = re.compile(r"\s*(?:>|→|->|»)\s*")
LEAD = {"user", "open", "opens", "navigate", "navigates", "go", "goes", "to", "select",
        "selects", "choose", "chooses", "hits", "clicks", "click", "on", "the", "then", "taps"}
HOST_SUFFIXES = ("medtricslab.com", "medtrics.sg")
TRAIL = {"button", "btn", "tab", "link"}
GENERIC = {"session", "record", "row", "item", "entry", "result", "one", "first", "list"}


def _path_of(url):
    """Path (+ #hash) of a medtrics URL, or None when the host is not ours or
    the path points at one record."""
    u = urlparse(url.rstrip(".,;:)"))
    host = (u.hostname or "").lower()
    if not any(host == d or host.endswith("." + d) for d in HOST_SUFFIXES):
        return None
    # A query stays (it can pick the page) unless a value is a record id.
    q = parse_qsl(u.query, keep_blank_values=True)
    keep = u.query if q and not any(v.isdigit() or UUID_RE.match(v) for _, v in q) else ""
    return _clean_path(u.path + (f"?{keep}" if keep else "") + (f"#{u.fragment}" if u.fragment else ""))


def _clean_path(p):
    p = p.rstrip(".,;:)")
    path = p.split("#")[0].split("?")[0]
    if not path.startswith("/") or not path.strip("/") or "<" in p or ">" in p:
        return None
    if any(seg.isdigit() or UUID_RE.match(seg) for seg in path.split("/") if seg):
        return None
    return p


def _first_path(text):
    """First usable path in free text: full medtrics URLs first, then bare /paths."""
    for m in URL_RE.finditer(text):
        p = _path_of(m.group(0))
        if p:
            return p
    scrubbed = URL_RE.sub(" ", text)
    for m in PATH_RE.finditer(scrubbed):
        p = _clean_path(m.group(0))
        if p and len(p.split("#")[0].strip("/")) > 1:
            return p
    return None


def parse_clicks(line):
    """('>'-separated click text) -> (clicks, write_gated). The path stops at
    the first step holding a write verb; that step and the rest are gated."""
    clicks, gated = [], []
    for raw in SEP_RE.split(line.strip().strip("*_ ")):
        toks = raw.strip().strip("*_.").split()
        while toks and toks[0].lower() in LEAD:
            toks.pop(0)
        while toks and toks[-1].lower() in TRAIL:
            toks.pop()
        if not toks:
            continue
        text = " ".join(toks)
        if gated or WRITE_RE.search(text):
            gated.append(raw.strip())
            continue
        if toks[0].lower() in ("a", "an") and all(t.lower() in GENERIC for t in toks[1:]):
            clicks.append({"row": True})
        else:
            if toks[0].lower() in ("a", "an"):
                toks.pop(0)
            clicks.append({"text": " ".join(toks)})
    return clicks, " > ".join(gated)


def _write_gated_hint(desc):
    """A sentence saying a modal / confirmation / notification follows a save."""
    for line in re.split(r"[\n.]", desc):
        if (re.search(r"\bwhen\b.*\b(?:save|submit)\b", line, re.I)
                and re.search(r"modal|dialog|pop-?up|confirm|notif", line, re.I)):
            return re.sub(r"[*_]+", "", line).strip(" :-")[:120]
    return ""


def _url_page(desc):
    lines = desc.splitlines()
    for n, line in enumerate(lines):
        m = URL_FIELD_RE.match(line)
        if not m:
            continue
        section = [m.group(1)]
        for nxt in lines[n + 1:]:
            if FIELD_RE.match(nxt) or re.match(r"^\s*#{1,6}\s", nxt):
                break
            section.append(nxt)
        start = None
        for s in section:
            for u in URL_RE.findall(s):
                p = _path_of(u)
                if p and not start:
                    start = p
        if not start:
            continue
        clicks, gated = [], ""
        for s in section:
            if URL_RE.search(s) or not SEP_RE.search(s) or not s.strip():
                continue
            clicks, gated = parse_clicks(s)
            break
        return start, clicks, gated
    return None


def _release_checklist(desc):
    m = re.search(r"Which portal and URL[^\n?]*\?[*_]*\s*(.*?)(?=\n\s*\d+\.\s+\*\*|\Z)", desc, re.S | re.I)
    if not m:
        return None
    ans = m.group(1)[:800]
    at = re.search(r"\bat\s+(/[A-Za-z0-9_\-./#<>]+)", ans)
    p = _clean_path(at.group(1)) if at else None
    p = p or _first_path(ans)
    return (p, [], "") if p else None


def _qa_checklist(desc):
    m = re.search(r"^#{1,6}\s*QA Checklist.*$", desc, re.M | re.I)
    if not m:
        return None
    rest = desc[m.end():]
    end = re.search(r"^#{1,6}\s", rest, re.M)
    for line in (rest[:end.start()] if end else rest).splitlines():
        nav = re.search(r"\bNavigate to\b(.*)", line, re.I)
        if not nav:
            continue
        tail = nav.group(1)
        p = _first_path(tail)
        if not p:
            return None
        after = tail.split(p.split("#")[0], 1)[-1] if p.split("#")[0] in tail else ""
        clicks, gated = parse_clicks(after) if SEP_RE.search(after) else ([], "")
        return p, clicks, gated
    return None


def extract(issue):
    """Route dict from the untrimmed description, or None."""
    desc = issue.get("description") or ""
    for source, fn in (("url_page", _url_page), ("release_checklist", _release_checklist),
                       ("qa_checklist", _qa_checklist)):
        found = fn(desc)
        if found:
            start, clicks, gated = found
            gated = gated or _write_gated_hint(desc)
            return {"start": start, "clicks": clicks, "write_gated": gated, "source": source}
    return None


def main(argv=None):
    data = json.loads(Path((argv or sys.argv[1:])[-1]).read_text())
    for i in data.get("issues") or []:
        print(i["identifier"], json.dumps(extract(i)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
