#!/usr/bin/env python3
"""
kb_usage.py — token and cost estimates per run and per article.

After each subagent returns, the orchestrator appends what the Task result
reported. Task usage is a single total (no input/output/cache split) and the
orchestrator's own tokens are not visible, so every figure here is an estimate
and is labelled as one.

  add     --run-dir D --agent kb-writer --scope admin-01 --model sonnet
          --tokens 41230 [--duration-ms 52000] [--tool-uses 4]
  report  --run-dir D [--groups groups.json] [--calls calls.json] [--data-plan data_plan.json]
          -> usage_report.json + one line

Cost is an estimate in dollars: Task usage has no input/output split, so each
call's total tokens are priced at a fixed 85% input / 15% output split (INPUT_SHARE)
at the model's per-million rates (PRICE_PER_MTOK). Main-chat tokens are not visible
and not included; COST_NOTE says so wherever a figure is shown.

Per article: a writer group's tokens are split by each ticket's share of the
group's input characters (writer_<group>.md), plus that ticket's own retries.
The classifier call is spread evenly over the tickets it placed; the capture call
(kb-capturer) by each ticket's share of the capture visits in <run>/shots/calls.json
(evenly over the placed tickets without it); the form-fill call (kb-data-maker) over
the tickets of <run>/data_plan.json by record count. The per-ticket figures then add
up to the run total. A ticket any of whose calls used an unpriced model has no cost
(null, shown n/a), never a partial number.

    ---KB_USAGE_RESULT---
    {"ok", "action", "tokens", "est_cost_usd", "error"}
"""

import argparse
import json
import re
import sys
from pathlib import Path

# USD per million tokens, (input, output). Source: claude-api skill pricing,
# cached 2026-10-06. A model is matched by substring ("haiku", "sonnet", "opus");
# one that matches none is reported with no cost.
PRICE_PER_MTOK = {
    "haiku": (0.10, 0.50),
    "sonnet": (2.00, 10.00),
    "opus": (4.00, 20.00),
}
INPUT_SHARE = 0.85        # share of a call's total tokens priced as input; the rest as output
COST_NOTE = "estimate; main-chat tokens not included"


def price_for(model):
    """(input, output) USD per million tokens for a model name, or None."""
    m = (model or "").lower()
    for key, rate in PRICE_PER_MTOK.items():
        if key in m:
            return rate
    return None


def row_cost(row):
    """Estimated USD for one agent call, or None for an unpriced model."""
    rate = price_for(row.get("model"))
    if rate is None:
        return None
    tok = int(row.get("total_tokens", 0))
    return (tok * INPUT_SHARE * rate[0] + tok * (1 - INPUT_SHARE) * rate[1]) / 1e6


def money(v):
    return "n/a" if v is None else f"${v:,.2f}"


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


def _load_json(path):
    try:
        v = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    return v if isinstance(v, dict) else None


def _even(tickets):
    tickets = list(dict.fromkeys(tickets))
    return {t: 1 / len(tickets) for t in tickets}


def capture_shares(calls, placed):
    """{ticket: share} of the capture agent's call: each ticket's share of the capture visits
    in calls.json (a visit that serves several tickets counts for each), else evenly over the
    tickets the run placed."""
    n = {}
    for v in (calls or {}).get("visits") or []:
        for t in dict.fromkeys(v.get("tickets") or []):
            n[t] = n.get(t, 0) + 1
    total = sum(n.values())
    return {t: c / total for t, c in n.items()} if total else _even(placed)


def fill_shares(data_plan, placed):
    """{ticket: share} of the form-fill agent's call: the tickets in data_plan.json by their
    record count (evenly when none has a record), else evenly over the placed tickets."""
    n = {t: len((b or {}).get("records") or []) for t, b in ((data_plan or {}).get("tickets") or {}).items()}
    total = sum(n.values())
    if total:
        return {t: c / total for t, c in n.items() if c}
    return _even(n) if n else _even(placed)


def row_shares(r, groups, run_dir, calls, data_plan):
    """{ticket: share} (shares add up to 1, or {} when nothing can be attributed): a writer
    group by each ticket's share, the classifier evenly over the tickets it placed, a retry onto
    its own ticket, the capturer by capture visits, the form-fill agent over the data plan."""
    by_group = {g["group_id"]: g for g in (groups or {}).get("writer_groups", [])}
    placed = [t for g in by_group.values() for t in g["tickets"]]
    scope, agent = r.get("scope", ""), r.get("agent")
    if agent == "kb-classifier" and placed:
        return _even(placed)
    if agent == "kb-capturer" and (calls or placed):
        return capture_shares(calls, placed)
    if agent == "kb-data-maker" and (data_plan or placed):
        return fill_shares(data_plan, placed)
    if scope in by_group:
        return ticket_shares(run_dir, by_group[scope])
    if scope.startswith("retry:"):
        return {scope.split(":", 1)[1]: 1.0}
    return {}


def _spread(rows, groups, run_dir, value, calls=None, data_plan=None):
    """-> ({ticket: sum of value(row) * share}, {tickets with an unvalued (None) row}). calls /
    data_plan default to <run>/shots/calls.json and <run>/data_plan.json when they exist."""
    if calls is None:
        calls = _load_json(Path(run_dir) / "shots" / "calls.json")
    if data_plan is None:
        data_plan = _load_json(Path(run_dir) / "data_plan.json")
    per, unvalued = {}, set()
    for r in rows:
        v = value(r)
        for t, share in row_shares(r, groups, run_dir, calls, data_plan).items():
            if v is None:
                unvalued.add(t)
            else:
                per[t] = per.get(t, 0) + v * share
    return per, unvalued


def apportion(rows, groups, run_dir, calls=None, data_plan=None):
    per, _ = _spread(rows, groups, run_dir, lambda r: int(r.get("total_tokens", 0)), calls, data_plan)
    return {t: round(v) for t, v in per.items()}


def apportion_cost(rows, groups, run_dir, calls=None, data_plan=None):
    """{ticket: est USD}; None (shown n/a) for a ticket any of whose calls used an unpriced
    model - never a partial number."""
    per, unpriced = _spread(rows, groups, run_dir, row_cost, calls, data_plan)
    out = {t: round(v, 4) for t, v in per.items()}
    out.update({t: None for t in unpriced})
    return out


def cost(rows):
    """Run total in USD, or None if any call used an unpriced model."""
    total = 0.0
    for r in rows:
        c = row_cost(r)
        if c is None:
            return None
        total += c
    return round(total, 4)


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
    calls = _load_json(a.calls) if a.calls else None
    plan = _load_json(a.data_plan) if a.data_plan else None
    per = apportion(rows, groups, a.run_dir, calls, plan)
    total = sum(int(r.get("total_tokens", 0)) for r in rows)
    est = cost(rows)
    priced = [dict(r, est_cost_usd=(None if row_cost(r) is None else round(row_cost(r), 4)))
              for r in rows]
    out = {"total_tokens": total, "est_cost_usd": est, "cost_note": COST_NOTE,
           "input_share": INPUT_SHARE, "per_ticket": per,
           "per_ticket_cost": apportion_cost(rows, groups, a.run_dir, calls, plan),
           "unpriced_models": sorted({r.get("model") or "?" for r in rows if row_cost(r) is None}),
           "rows": priced}
    Path(a.run_dir, "usage_report.json").write_text(json.dumps(out, indent=1))
    print(f"tokens ~{total:,} across {len(rows)} agent calls · est. cost {money(est)} ({COST_NOTE})")
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
    s.add_argument("--calls"); s.add_argument("--data-plan", dest="data_plan")
    a = ap.parse_args(argv)
    return {"add": cmd_add, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
