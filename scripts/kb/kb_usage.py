#!/usr/bin/env python3
"""
kb_usage.py — token and cost estimates per run and per article.

After each subagent returns, the orchestrator appends what the Task result
reported. Task usage is a single total (no input/output/cache split) and the
orchestrator's own tokens are not visible, so every figure here is an estimate
and is labelled as one.

  add     --run-dir D --agent kb-writer --scope admin-01 --model sonnet
          --tokens 41230 [--duration-ms 52000] [--tool-uses 4]
  report  --run-dir D [--groups groups.json] -> usage_report.json + one line

Per article: a writer group's tokens are split by each ticket's share of the
group's input characters (writer_<group>.md), plus that ticket's own retries.
The classifier call is spread evenly over the tickets it placed.

    ---KB_USAGE_RESULT---
    {"ok", "action", "tokens", "est_cost_usd", "error"}
"""

import argparse
import json
import re
import sys
from pathlib import Path

# USD per million tokens, blended (Task usage has no input/output split, so a
# blended rate is the honest choice). Fill from current Anthropic pricing when
# building; a model not listed is reported with no cost.
PRICE_PER_MTOK = {
    "haiku": None,
    "sonnet": None,
    "opus": None,
}


def emit(payload):
    print("---KB_USAGE_RESULT---")
    print(json.dumps(payload))


def _rows(run_dir):
    p = Path(run_dir) / "usage.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def ticket_shares(run_dir, group):
    """{ticket: share} from each ticket's section length in its writer bundle."""
    path = Path(run_dir) / f"writer_{group['group_id']}.md"
    tickets = group["tickets"]
    if not path.exists():
        return {t: 1 / len(tickets) for t in tickets}
    text = path.read_text()
    sizes = {}
    for t in tickets:
        m = re.search(rf"=== TICKET {re.escape(t)} ===(.*?)(?==== TICKET |\Z)", text, re.S)
        sizes[t] = len(m.group(1)) if m else 1
    total = sum(sizes.values()) or 1
    return {t: n / total for t, n in sizes.items()}


def apportion(rows, groups, run_dir):
    per = {}
    by_group = {g["group_id"]: g for g in (groups or {}).get("writer_groups", [])}
    placed = [t for g in by_group.values() for t in g["tickets"]]
    for r in rows:
        scope, tok = r.get("scope", ""), int(r.get("total_tokens", 0))
        if r.get("agent") == "kb-classifier" and placed:
            for t in placed:
                per[t] = per.get(t, 0) + tok / len(placed)
        elif scope in by_group:
            for t, share in ticket_shares(run_dir, by_group[scope]).items():
                per[t] = per.get(t, 0) + tok * share
        elif scope.startswith("retry:"):
            t = scope.split(":", 1)[1]
            per[t] = per.get(t, 0) + tok
    return {t: round(v) for t, v in per.items()}


def cost(rows):
    total, priced = 0.0, True
    for r in rows:
        rate = PRICE_PER_MTOK.get((r.get("model") or "").lower())
        if rate is None:
            priced = False
            continue
        total += rate * int(r.get("total_tokens", 0)) / 1e6
    return (round(total, 4) if priced else None)


def cmd_add(a):
    row = {"agent": a.agent, "scope": a.scope, "model": a.model,
           "total_tokens": a.tokens, "duration_ms": a.duration_ms, "tool_uses": a.tool_uses}
    Path(a.run_dir).mkdir(parents=True, exist_ok=True)
    with open(Path(a.run_dir) / "usage.jsonl", "a") as fh:
        fh.write(json.dumps(row) + "\n")
    emit({"ok": True, "action": "add", "tokens": a.tokens})
    return 0


def cmd_report(a):
    rows = _rows(a.run_dir)
    groups = json.loads(Path(a.groups).read_text()) if a.groups else None
    per = apportion(rows, groups, a.run_dir)
    total = sum(int(r.get("total_tokens", 0)) for r in rows)
    est = cost(rows)
    out = {"total_tokens": total, "est_cost_usd": est, "per_ticket": per, "rows": rows}
    Path(a.run_dir, "usage_report.json").write_text(json.dumps(out, indent=1))
    print(f"tokens ~{total:,} across {len(rows)} agent calls · est. cost "
          f"{'$' + format(est, '.2f') if est is not None else 'n/a (fill PRICE_PER_MTOK)'}")
    emit({"ok": True, "action": "report", "tokens": total, "est_cost_usd": est})
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("add"); s.add_argument("--run-dir", required=True)
    s.add_argument("--agent", required=True); s.add_argument("--scope", required=True)
    s.add_argument("--model", required=True); s.add_argument("--tokens", type=int, required=True)
    s.add_argument("--duration-ms", type=int, default=0); s.add_argument("--tool-uses", type=int, default=0)
    s = sub.add_parser("report"); s.add_argument("--run-dir", required=True); s.add_argument("--groups")
    a = ap.parse_args(argv)
    return {"add": cmd_add, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
