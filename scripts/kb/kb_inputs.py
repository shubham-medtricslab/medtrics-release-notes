#!/usr/bin/env python3
"""
kb_inputs.py — the trimmed per-ticket input the KB writer sees.

A KB article explains what a user sees and does, so full diffs are mostly
wasted tokens. This keeps: the title, the description with its acceptance
criteria split out, strings quoted in either (candidate UI labels), the
user-visible strings ADDED in template / JS / Vue / Django hunks, changed URL
patterns, and — when the ticket came through `--kb` — the client title and
summary the release already wrote. Each ticket is capped (default 4,000 chars).

The candidate labels it emits are also what kb_capture.py looks for on screen.

  python3 kb_inputs.py --data filtered.json [--summaries release_summaries.txt] \
      --out inputs.json [--cap 4000]

Prints one human line, then:

    ---KB_INPUTS_RESULT---
    {"ok", "tickets", "chars", "error"}
"""

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import release_archive as RA            # noqa: E402  (summaries block parser)

CAP = 4000
AC_RE = re.compile(r"^\s*(?:#+\s*)?(?:\*\*)?acceptance criteria(?:\*\*)?\s*:?\s*$", re.I | re.M)
QUOTED_RE = re.compile(r"[\"“'‘`]([A-Z][^\"”'’`\n]{1,48})[\"”'’`]")
UI_FILE_RE = re.compile(r"\.(html|js|jsx|ts|tsx|vue|py)$")
ADDED_STRING_RES = [
    re.compile(r"""(?:_|gettext|gettext_lazy|ugettext_lazy|trans)\(\s*['"]([^'"\n]{2,60})['"]"""),
    re.compile(r"""\{%\s*trans\s+['"]([^'"\n]{2,60})['"]"""),
    re.compile(r"""(?:verbose_name|label|placeholder|title|help_text)\s*=\s*['"]([^'"\n]{2,60})['"]"""),
    re.compile(r""">\s*([A-Z][A-Za-z0-9 &/'-]{1,40}?)\s*</(?:button|a|h[1-6]|label|th|option|span)>"""),
]
URL_RE = re.compile(r"""(?:path|re_path|url)\(\s*r?['"]\^?([^'"\n]{2,80})['"]""")


def split_ac(description):
    """(body, acceptance_criteria)."""
    m = AC_RE.search(description or "")
    if not m:
        return (description or "").strip(), ""
    return description[:m.start()].strip(), description[m.end():].strip()


def added_lines(issue):
    for mr in issue.get("mrs") or []:
        for d in mr.get("diffs") or []:
            path = d.get("new_path") or ""
            if not UI_FILE_RE.search(path) or "/tests/" in path or "/migrations/" in path:
                continue
            for line in (d.get("diff") or "").splitlines():
                if line.startswith("+") and not line.startswith("+++"):
                    yield path, line[1:]


def is_label(s):
    """True for a string a user could read on screen. Drops code identifiers
    (CamelCase, UPPER_SNAKE, snake_case), placeholders and email copy."""
    s = (s or "").strip()
    if not s or re.search(r"[{}\[\]<>=]|%\(|\\", s):
        return False
    if " " not in s and (re.search(r"[a-z][A-Z]", s) or "_" in s or re.fullmatch(r"[A-Z0-9]{2,}", s)):
        return False
    if re.fullmatch(r"[A-Z0-9_ ]+", s) and "_" in s:
        return False
    return len(s.split()) <= 6 and not re.search(r"[.!?:]$", s)


# An email's subject, body or greeting is copy, not a screen label.
EMAIL_LINE_RE = re.compile(r"\b(?:subject|body|greeting|salutation)\b|^\W*(?:hi|hello|dear)\b", re.I)


def ui_strings(issue):
    """User-visible strings ADDED in templates/JS (any pattern) and in gettext
    calls of .py files — never verbose_name/label kwargs of Python code."""
    seen = []
    for path, line in added_lines(issue):
        rxs = ADDED_STRING_RES[:2] if path.endswith(".py") else ADDED_STRING_RES
        for rx in rxs:
            for s in rx.findall(line):
                s = s.strip()
                if s and s not in seen and is_label(s):
                    seen.append(s)
    return seen


def routes(issue):
    out = []
    for path, line in added_lines(issue):
        if path.endswith("urls.py"):
            for r in URL_RE.findall(line):
                if r not in out:
                    out.append(r)
    return out


def quoted(text):
    """Quoted capitalised strings that read like labels; lines about an email's
    subject/body/greeting are skipped."""
    out = []
    for line in (text or "").splitlines():
        if EMAIL_LINE_RE.search(line):
            continue
        out += [q.strip() for q in QUOTED_RE.findall(line) if is_label(q)]
    return list(dict.fromkeys(out))


def _clip(s, n):
    s = re.sub(r"[ \t]+", " ", s or "").strip()
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


def build_input(issue, summary=None, cap=CAP):
    body, ac = split_ac(issue.get("description") or "")
    ui = ui_strings(issue)
    labels = list(dict.fromkeys(quoted(body) + quoted(ac) + ui))
    rec = {"ticket": issue["identifier"], "title": issue.get("title", ""),
           "module": issue.get("module", ""),
           "client_title": (summary or {}).get("CLIENT_TITLE", ""),
           "client_summary": (summary or {}).get("EXEC_SUMMARY", ""),
           "description": "", "acceptance_criteria": "",
           "ui_strings": ui[:25], "routes": routes(issue)[:8], "labels": labels[:30]}
    fixed = sum(len(str(rec[k])) for k in ("title", "client_title", "client_summary")) \
        + sum(len(s) + 4 for s in rec["ui_strings"] + rec["routes"])
    room = max(400, cap - fixed)
    # The client summary already says what changed; when it is there the raw
    # description gets less room.
    share = 0.35 if rec["client_summary"] else 0.6
    rec["description"] = _clip(body, int(room * share))
    rec["acceptance_criteria"] = _clip(ac, int(room * (1 - share)))
    return rec


def load_summaries(path):
    if not path or not Path(path).exists():
        return {}
    out = {}
    for block in RA.parse_summaries(Path(path).read_text()):
        tid = block.get("IDENTIFIER")
        if tid:
            out[tid] = block
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--data", required=True)
    ap.add_argument("--summaries")
    ap.add_argument("--out", required=True)
    ap.add_argument("--cap", type=int, default=CAP)
    a = ap.parse_args(argv)
    try:
        data = json.loads(Path(a.data).read_text())
        sums = load_summaries(a.summaries)
    except (OSError, json.JSONDecodeError) as e:
        print("---KB_INPUTS_RESULT---")
        print(json.dumps({"ok": False, "error": str(e)}))
        return 1
    recs = {i["identifier"]: build_input(i, sums.get(i["identifier"]), a.cap)
            for i in data.get("issues") or []}
    Path(a.out).write_text(json.dumps(recs, indent=1, ensure_ascii=False))
    chars = sum(len(json.dumps(r)) for r in recs.values())
    print(f"inputs: {len(recs)} tickets · {chars:,} chars total · "
          f"{sum(1 for r in recs.values() if r['client_summary'])} reuse a release summary")
    print("---KB_INPUTS_RESULT---")
    print(json.dumps({"ok": True, "tickets": len(recs), "chars": chars}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
