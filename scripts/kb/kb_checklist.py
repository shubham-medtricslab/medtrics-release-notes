#!/usr/bin/env python3
"""
kb_checklist.py — attach each ticket's manual QA checklist to the data file.

Charles's QA automation reads a `<branch-slug>-manual-qa-checklist.txt` the
branch's author attaches to the GitLab MR; it names the current route and UI
path, where the ticket description's link may be stale. This finds that file
per ticket (priority: exact branch-slug name, then any
`*-manual-qa-checklist.txt`, then a .txt holding the ticket id; newest note
wins ties), downloads its text (40 KB cap) and sets

    issue["qa_checklist"] = {"text", "name", "mr_iid", "source"}

with source "mr_attachment". With no attachment, a "## QA Checklist" section
of the Optimus description becomes source "optimus". Neither: the key stays
absent. Never fails the run — a network error is one warning line per ticket.

  python3 kb_checklist.py attach --data filtered.json [--out filtered.json] [--env-file .env]

Prints one human line, then:

    ---KB_CHECKLIST_RESULT---
    {"attached": n, "missing": [ids], "errors": [...]}
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import gather_notes_data_optimus as G   # noqa: E402  (load_env, gitlab_api)

CAP = 40 * 1024
SUFFIX = "-manual-qa-checklist.txt"
UPLOAD_RE = re.compile(r"\[[^\]]*\]\(\s*/uploads/([0-9a-f]{32})/([^)\s]+?)\s*\)")
KIND_RANK = {"exact_branch_slug": 0, "suffix": 1, "ticket_id": 2}
QA_HEAD_RE = re.compile(r"^#{1,6}\s*QA Checklist.*$", re.M | re.I)


def emit(payload):
    print("---KB_CHECKLIST_RESULT---")
    print(json.dumps(payload))


def branch_slug(branch):
    """fix/m1-1190/Foo -> fix-m1-1190-foo."""
    s = re.sub(r"[/\\]+", "-", (branch or "").strip().lower())
    return re.sub(r"-{2,}", "-", s).strip("-")


def find_candidates(notes, branch, ticket):
    """Upload references in MR notes whose filename qualifies, tagged by kind."""
    exact = branch_slug(branch) + SUFFIX if branch else None
    tid = (ticket or "").lower()
    out = []
    for note in notes or []:
        for secret, raw in UPLOAD_RE.findall(note.get("body") or ""):
            name = urllib.parse.unquote(raw)
            low = name.lower()
            if exact and low == exact:
                kind = "exact_branch_slug"
            elif low.endswith(SUFFIX):
                kind = "suffix"
            elif tid and tid in low and low.endswith(".txt"):
                kind = "ticket_id"
            else:
                continue
            out.append({"secret": secret, "name": name, "kind": kind,
                        "created_at": note.get("created_at") or ""})
    return out


def pick_best(cands):
    """Best kind first; within a kind the newest note."""
    if not cands:
        return None
    newest = sorted(cands, key=lambda c: c["created_at"], reverse=True)
    return min(newest, key=lambda c: KIND_RANK[c["kind"]])


def mr_notes(env, iid):
    """Every note of the MR (paginated)."""
    notes, page = [], 1
    while page:
        data, headers = G.gitlab_api(env, f"merge_requests/{iid}/notes",
                                     {"per_page": 100, "page": page, "sort": "asc"})
        if not isinstance(data, list):
            if data is None and page == 1:
                raise RuntimeError(f"could not read notes of !{iid}")
            break
        notes += data
        nxt = (headers or {}).get("x-next-page", "")
        page = int(nxt) if nxt.isdigit() else 0
    return notes


def fetch_upload(env, secret, name):
    """Text of a project upload (GET /uploads/:secret/:filename), capped."""
    url = (f"{env['GITLAB_URL'].rstrip('/')}/api/v4/projects/{env['GITLAB_PROJECT_ID']}"
           f"/uploads/{secret}/{urllib.parse.quote(name)}")
    req = urllib.request.Request(url, headers={"PRIVATE-TOKEN": env["GITLAB_TOKEN"]})
    with urllib.request.urlopen(req, timeout=G.HTTP_TIMEOUT) as resp:
        return resp.read(CAP).decode("utf-8", errors="replace")


def optimus_section(description):
    """Text of a '## QA Checklist' section in the Optimus description, or ''."""
    m = QA_HEAD_RE.search(description or "")
    if not m:
        return ""
    rest = description[m.end():]
    end = re.search(r"^#{1,6}\s", rest, re.M)
    return (rest[:end.start()] if end else rest).strip()[:CAP]


def from_mr(env, issue):
    """qa_checklist dict from the issue's MR attachments, or None."""
    best = None
    for mr in issue.get("mrs") or []:
        iid = mr.get("iid")
        if not iid:
            continue
        branch = mr.get("source_branch")
        if not branch:
            data, _ = G.gitlab_api(env, f"merge_requests/{iid}")
            branch = (data or {}).get("source_branch", "") if isinstance(data, dict) else ""
        cand = pick_best(find_candidates(mr_notes(env, iid), branch, issue.get("identifier")))
        if cand and (best is None or KIND_RANK[cand["kind"]] < KIND_RANK[best[0]["kind"]]):
            best = (cand, iid)
    if not best:
        return None
    cand, iid = best
    text = fetch_upload(env, cand["secret"], cand["name"])
    return {"text": text, "name": cand["name"], "mr_iid": iid, "source": "mr_attachment"}


def attach(data, env):
    """Set qa_checklist on each issue in place. -> (attached, missing, errors)."""
    attached, missing, errors = 0, [], []
    for issue in data.get("issues") or []:
        tid = issue.get("identifier", "?")
        found = None
        try:
            if env:
                found = from_mr(env, issue)
        except Exception as e:   # never fail the run on one ticket
            errors.append(f"{tid}: {e}")
            print(f"warning: {tid}: checklist lookup failed ({e})", file=sys.stderr)
        if not found:
            text = optimus_section(issue.get("description"))
            if text:
                found = {"text": text, "name": "Optimus description", "mr_iid": None,
                         "source": "optimus"}
        if found:
            issue["qa_checklist"] = found
            attached += 1
        else:
            missing.append(tid)
    return attached, missing, errors


def cmd_attach(a):
    try:
        data = json.loads(Path(a.data).read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"checklist: cannot read {a.data}: {e}")
        emit({"attached": 0, "missing": [], "errors": [str(e)]})
        return 0
    try:
        env = G.load_env(a.env_file)
    except SystemExit:
        env = None
        print("warning: GitLab credentials missing; using Optimus checklists only", file=sys.stderr)
    attached, missing, errors = attach(data, env)
    Path(a.out or a.data).write_text(json.dumps(data, indent=1, ensure_ascii=False))
    mr_n = sum(1 for i in data.get("issues") or []
               if (i.get("qa_checklist") or {}).get("source") == "mr_attachment")
    print(f"checklists: {attached} attached ({mr_n} from MR attachments) · "
          f"{len(missing)} without one")
    emit({"attached": attached, "missing": missing, "errors": errors})
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("attach")
    s.add_argument("--data", required=True)
    s.add_argument("--out")
    s.add_argument("--env-file", default=".env")
    s.set_defaults(fn=cmd_attach)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
