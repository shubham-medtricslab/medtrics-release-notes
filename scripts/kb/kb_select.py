#!/usr/bin/env python3
"""
kb_select.py — which tickets get a KB article, where each one lives, and how
the run is grouped. Everything here is deterministic; the only model call it
prepares is ONE batched Haiku classification for tickets the rules can't place.

  filter            Gathered data -> features only, minus tickets the ledger
                    already covers. Runs before any agent is spawned.
  place             Filtered data + kb_routes.json -> persona/surface per ticket.
                    Unplaced tickets are written to a classifier bundle.
  classifier-merge  Fold the kb-classifier's JSON answer back into the placement.
  group             Placement -> groups.json: the capture plan (strict persona ×
                    surface, one visit each) and the writer groups (same-persona
                    surfaces packed up to --max tickets, larger ones split).

Every subcommand prints one human line, then a machine-readable block:

    ---KB_SELECT_RESULT---
    {"ok", "action", ...counts..., "error"}

Exit codes: 0 success, 1 configuration error.
"""

import argparse
import fnmatch
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import kb_ledger as L                    # noqa: E402

DEFAULT_ROUTES = HERE / "kb_routes.json"
MAX_GROUP = 8


def emit(payload):
    print("---KB_SELECT_RESULT---")
    print(json.dumps(payload))


def _load(path):
    return json.loads(Path(path).read_text())


def _write(path, obj):
    Path(path).write_text(json.dumps(obj, indent=1, ensure_ascii=False))


# ── filter ───────────────────────────────────────────────────────────────────
def filter_issues(issues, ledger_index, force=False, retry_partial=False):
    """(kept, skipped). Type first — a bug never costs a ledger lookup."""
    done = set(L.DONE)
    if retry_partial:
        done.discard("partial")
    kept, skipped = [], []
    for i in issues:
        t = (i.get("task_type") or "").strip().lower()
        if t not in L.FEATURE_TYPES:
            skipped.append({"ticket": i["identifier"], "reason": f"type: {t or 'unknown'}"})
            continue
        st = (ledger_index.get(i["identifier"].upper()) or {}).get("status")
        if st in done and not force:
            skipped.append({"ticket": i["identifier"], "reason": f"ledger: {st}"})
            continue
        kept.append(i)
    return kept, skipped


# ── place ────────────────────────────────────────────────────────────────────
def _paths(issue):
    for mr in issue.get("mrs") or []:
        for d in mr.get("diffs") or []:
            yield d.get("new_path") or d.get("old_path") or "", d.get("diff") or ""


def place_issue(issue, routes):
    """(surface_entry, how) or (None, None). Order: the backlog's known
    placement, then a URL prefix the diff touches, then a template path, then a
    unique module match."""
    tid = issue["identifier"]
    surfaces = routes["surfaces"]
    for s in surfaces:
        if tid in s.get("known_tickets", {}):
            return s, "known"
    diffs = list(_paths(issue))
    for s in surfaces:
        for prefix in s["match"].get("url_prefixes") or []:
            core = prefix.strip("/")
            if core and any(core in body for _, body in diffs):
                return s, "url"
    for s in surfaces:
        for glob in s["match"].get("template_globs") or []:
            if any(fnmatch.fnmatch(p, glob) for p, _ in diffs):
                return s, "template"
    module = (issue.get("module") or "").strip().lower()
    if module:
        hits = [s for s in surfaces
                if module in (m.lower() for m in s["match"].get("modules") or [])]
        if len(hits) == 1:
            return hits[0], "module"
    return None, None


def _placement(issue, s, how, routes):
    status = s.get("known_tickets", {}).get(issue["identifier"], "")
    persona = routes["personas"][s["persona"]]
    uncapturable = (not s.get("url") or persona.get("no_session")
                    or status in routes.get("uncapturable_statuses", []))
    reason = ""
    if uncapturable:
        reason = (status.lower() if status in routes.get("uncapturable_statuses", [])
                  else "no sign-in persona" if persona.get("no_session")
                  else "surface not mapped to a demo URL")
    return {"ticket": issue["identifier"], "persona": s["persona"],
            "also_personas": s.get("also_personas", []), "surface": s["surface"],
            "url": s.get("url", ""), "portal": persona["portal"],
            "audience": persona["audience"], "how": how,
            "capturable": not uncapturable, "reason": reason}


def classifier_bundle(unplaced, routes):
    lines = ["Place each ticket on ONE surface from the list, or UNKNOWN. Set has_ui",
             "false when the change has no screen a user sees. Reply with a JSON array",
             "only: [{\"ticket\", \"persona\", \"surface\", \"has_ui\", \"reason\"}].", "",
             "SURFACES (persona · surface · modules):"]
    for s in routes["surfaces"]:
        lines.append(f"- {s['persona']} · {s['surface']} · {', '.join(s['match']['modules'])}")
    lines.append("")
    for i in unplaced:
        desc = re.sub(r"\s+", " ", i.get("description") or "")[:600]
        lines += [f"=== {i['identifier']} ===", f"Title: {i['title']}",
                  f"Module: {i.get('module') or '—'}", f"Description: {desc}", ""]
    return "\n".join(lines)


def merge_classifier(placed, answer, routes, data_by_id):
    by_surface = {(s["persona"], s["surface"]): s for s in routes["surfaces"]}
    by_name = {}
    for s in routes["surfaces"]:
        by_name.setdefault(s["surface"], s)
    still = []
    for row in answer:
        tid = row.get("ticket")
        if tid not in data_by_id:
            continue
        if row.get("has_ui") is False:
            placed["placements"].append({"ticket": tid, "persona": "", "surface": "",
                                         "capturable": False, "how": "classifier",
                                         "reason": "no ui surface"})
            continue
        s = by_surface.get((row.get("persona"), row.get("surface"))) or by_name.get(row.get("surface"))
        if not s:
            still.append(tid)
            continue
        placed["placements"].append(_placement(data_by_id[tid], s, "classifier", routes))
    placed["unplaced"] = still
    return placed


# ── group ────────────────────────────────────────────────────────────────────
def group(placements, routes, max_group=MAX_GROUP, sessions=None):
    """Capture plan: one entry per persona × surface. Writer groups: a
    persona's surfaces packed in route order up to max_group tickets."""
    order = {(s["persona"], s["surface"]): n for n, s in enumerate(routes["surfaces"])}
    sessions = sessions or {}
    capture, by_key = [], {}
    for p in placements:
        if not p.get("capturable"):
            continue
        for persona in [p["persona"]] + list(p.get("also_personas") or []):
            if persona not in routes["personas"] or routes["personas"][persona].get("no_session"):
                continue
            key = (persona, p["surface"])
            if key not in by_key:
                by_key[key] = {"persona": persona, "surface": p["surface"], "url": p["url"],
                               "recipe": next((s.get("recipe", []) for s in routes["surfaces"]
                                               if s["surface"] == p["surface"]), []),
                               "tickets": [], "session": sessions.get(persona, "unknown")}
                capture.append(by_key[key])
            by_key[key]["tickets"].append(p["ticket"])
    capture.sort(key=lambda c: (c["persona"], order.get((c["persona"], c["surface"]), 1e9)))

    writers = []
    personas = []
    for p in placements:
        if p.get("persona") and p["persona"] not in personas:
            personas.append(p["persona"])
    for persona in personas:
        mine = sorted((p for p in placements if p.get("persona") == persona),
                      key=lambda p: (order.get((persona, p["surface"]), 1e9), p["ticket"]))
        for n in range(0, len(mine), max_group):
            chunk = mine[n:n + max_group]
            meta = routes["personas"][persona]
            writers.append({"group_id": f"{persona}-{n // max_group + 1:02d}", "persona": persona,
                            "portal": meta["portal"], "audience": meta["audience"],
                            "surfaces": list(dict.fromkeys(p["surface"] for p in chunk)),
                            "tickets": [p["ticket"] for p in chunk]})
    # A ticket the classifier said has no UI still gets an article (text only),
    # written with the admin group's voice unless it named a persona.
    orphans = [p for p in placements if not p.get("persona")]
    if orphans:
        writers.append({"group_id": "no-ui-01", "persona": "admin",
                        "portal": routes["personas"]["admin"]["portal"],
                        "audience": routes["personas"]["admin"]["audience"],
                        "surfaces": [], "tickets": [p["ticket"] for p in orphans]})
    uncapturable = [{"ticket": p["ticket"], "reason": p["reason"]}
                    for p in placements if not p.get("capturable")]
    return capture, writers, uncapturable


# ── CLI ──────────────────────────────────────────────────────────────────────
def cmd_filter(a):
    data = _load(a.data)
    idx = _load(a.ledger_index) if a.ledger_index and Path(a.ledger_index).exists() else {}
    kept, skipped = filter_issues(data.get("issues") or [], idx, a.force, a.retry_partial)
    _write(a.out, {**data, "issues": kept, "skipped": skipped})
    n_type = sum(1 for s in skipped if s["reason"].startswith("type"))
    print(f"kept {len(kept)} features · skipped: {n_type} (type) · {len(skipped) - n_type} (ledger)")
    emit({"ok": True, "action": "filter", "kept": len(kept), "skipped": len(skipped)})
    return 0


def cmd_place(a):
    data, routes = _load(a.data), _load(a.routes)
    placements, unplaced = [], []
    for i in data.get("issues") or []:
        s, how = place_issue(i, routes)
        if s:
            placements.append(_placement(i, s, how, routes))
        else:
            unplaced.append(i)
    _write(a.out, {"placements": placements, "unplaced": [i["identifier"] for i in unplaced],
                   "skipped": data.get("skipped") or []})
    if unplaced and a.bundle:
        Path(a.bundle).write_text(classifier_bundle(unplaced, routes))
    print(f"placed {len(placements)} by rule · {len(unplaced)} for the classifier")
    emit({"ok": True, "action": "place", "placed": len(placements), "unplaced": len(unplaced)})
    return 0


def cmd_merge(a):
    placed, routes, data = _load(a.placed), _load(a.routes), _load(a.data)
    raw = Path(a.answer).read_text()
    m = re.search(r"\[.*\]", raw, re.S)
    answer = json.loads(m.group(0)) if m else []
    by_id = {i["identifier"]: i for i in data.get("issues") or []}
    placed = merge_classifier(placed, answer, routes, by_id)
    for tid in placed["unplaced"]:
        placed["placements"].append({"ticket": tid, "persona": "", "surface": "",
                                     "capturable": False, "how": "none",
                                     "reason": "could not be placed"})
    _write(a.placed, placed)
    print(f"classifier placed {len(answer) - len(placed['unplaced'])} · unplaced {len(placed['unplaced'])}")
    emit({"ok": True, "action": "classifier-merge", "unplaced": len(placed["unplaced"])})
    return 0


def cmd_group(a):
    placed, routes = _load(a.placed), _load(a.routes)
    sessions = _load(a.sessions) if a.sessions and Path(a.sessions).exists() else {}
    capture, writers, uncap = group(placed["placements"], routes, a.max, sessions)
    out = {"run_id": a.run_id, "date": a.date, "mode": a.mode,
           "capture_plan": capture, "writer_groups": writers, "uncapturable": uncap,
           "unplaced": placed.get("unplaced") or [], "skipped": placed.get("skipped") or []}
    _write(a.out, out)
    by_p = {}
    for w in writers:
        by_p[w["persona"]] = by_p.get(w["persona"], 0) + len(w["tickets"])
    print(" · ".join(f"{k}: {v}" for k, v in by_p.items()) +
          f" · writer groups {len(writers)} · capture visits {len(capture)} · uncapturable {len(uncap)}")
    emit({"ok": True, "action": "group", "writer_groups": len(writers),
          "capture_visits": len(capture), "uncapturable": len(uncap)})
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("filter"); s.add_argument("--data", required=True)
    s.add_argument("--ledger-index"); s.add_argument("--out", required=True)
    s.add_argument("--force", action="store_true"); s.add_argument("--retry-partial", action="store_true")
    s = sub.add_parser("place"); s.add_argument("--data", required=True)
    s.add_argument("--routes", default=str(DEFAULT_ROUTES)); s.add_argument("--out", required=True)
    s.add_argument("--bundle")
    s = sub.add_parser("classifier-merge"); s.add_argument("--placed", required=True)
    s.add_argument("--answer", required=True); s.add_argument("--data", required=True)
    s.add_argument("--routes", default=str(DEFAULT_ROUTES))
    s = sub.add_parser("group"); s.add_argument("--placed", required=True)
    s.add_argument("--routes", default=str(DEFAULT_ROUTES)); s.add_argument("--sessions")
    s.add_argument("--max", type=int, default=MAX_GROUP); s.add_argument("--run-id", required=True)
    s.add_argument("--date", required=True); s.add_argument("--mode", default="tickets")
    s.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    try:
        return {"filter": cmd_filter, "place": cmd_place, "classifier-merge": cmd_merge,
                "group": cmd_group}[a.cmd](a)
    except (OSError, json.JSONDecodeError, KeyError) as e:
        emit({"ok": False, "action": a.cmd, "error": f"{type(e).__name__}: {e}"})
        return 1


if __name__ == "__main__":
    sys.exit(main())
