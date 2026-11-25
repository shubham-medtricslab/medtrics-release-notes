#!/usr/bin/env python3
"""Self-healing for the KB capture (0.26.0): diagnose every screenshot that was not
taken, retry the fixable ones (two passes), fall back to an unringed screenshot, learn
overrides, and say in plain words what happened. Also the on-demand route check.

  kb_heal.py plan-retry   --pass 1|2 --calls shots/calls.json --raw-dir shots/raw --out-dir shots
        -> shots/retry-<n>.json + shots/retry-<n>.md (same format as capture.md, for the
           kb-capturer agent). Exit 0 = something to retry, 4 = nothing to retry.
  kb_heal.py ingest-retry --pass 1|2 --calls shots/calls.json --raw-dir shots/raw --out-dir shots
                          [--downloads-dir D] [--no-ocr]
        -> the retry screens' shots appended to shots/shots.json.
  kb_heal.py finish       --calls shots/calls.json --raw-dir shots/raw --out-dir shots [--no-ocr]
        -> unringed fallback shots, learned overrides, shots/diagnosis.md + diagnosis.json.
  kb_heal.py plan-check   --out-dir <dir> [--surface NAME]...
        -> <dir>/routes-check.json + routes-check.md for /kb-articles check-routes.
  kb_heal.py check-report --check <dir>/routes-check.json --raw-dir <dir>/raw
        -> <dir>/routes-check-report.md (one row per route).

What a retry may do is what the capture may do: the retry files hold the same kinds of
action (navigate, wait, the stored page scripts, one kbclick click), the write guard and
the click guard are unchanged, and nothing here presses or sends anything new.

Causes (CAUSES) and what is done about them:
  other_frame   found, outside the frame, while another label was framed -> own frame (pass 1)
  offscreen     found, outside the frame, nothing framed                 -> scroll to it (pass 1)
  covered       found, something lies over it                            -> centre it (pass 1)
  loading       a loader was on screen                                   -> wait longer (pass 1: 10 s, pass 2: 20 s)
  near_match    not found, a close text is on the page                   -> ring that text, flagged (floor 0.82, then 0.70)
  not_found     not found, nothing close                                 -> unringed screenshot
  anything else (click refused, tenant block, 404, needs a save, not reached) -> reported as before
"""

import argparse
import datetime as _dt
import difflib
import json
import os
import re
import shutil
import sys
from pathlib import Path

import kb_capture as C
import kb_format as F
import kb_overrides as O

Q = C.Q
MAX_RETRY_SCREENS = 12        # per run; the rest are reported with their diagnosis
MAX_TARGETS = 3               # frames per screen in one retry pass
NEAR_FLOOR = {1: 0.82, 2: 0.70}
RETRYABLE = {1: ("other_frame", "offscreen", "covered", "loading", "under_recorded", "near_match"),
             2: ("loading", "under_recorded", "near_match")}
WHOLE_SCREEN = ("loading", "under_recorded")      # retried as the whole screen, all its labels
CAUSES = {
    "other_frame": "was on the page but outside the screenshot",
    "offscreen": "was on the page but outside the visible part",
    "covered": "was on the page but something lay over it",
    "loading": "the page was still loading",
    "under_recorded": "the recording missed the final state of the screen",
    "unreadable": "was on screen, but the text check could not read it there",
    "near_match": "was not on the page under that exact text",
    "not_found": "was not found on the page",
}
REMEDY = {"other_frame": "took a separate screenshot of it", "offscreen": "scrolled to it",
          "covered": "moved it to the middle of the screen", "loading": "waited longer",
          "under_recorded": "recorded the screen again",
          "near_match": "used the closest text on the page"}


def emit(payload):
    print("---KB_HEAL_RESULT---")
    print(json.dumps(payload))


def _words(s):
    return set(re.findall(r"[a-z0-9]+", str(s).lower()))


def near_pick(label, cands, pass_n, exclude=()):
    """The candidate text that may stand in for `label` on this pass, or None.
    Pass 1: similarity >= 0.82 (the label floor). Pass 2: >= 0.70, or every word of a
    2+ word label present in the candidate."""
    best = None
    lw = _words(label)
    for c in cands or []:
        if not isinstance(c, str) or c in exclude or c.strip().lower() == label.strip().lower():
            continue
        r = difflib.SequenceMatcher(None, label.lower(), c.lower()).ratio()
        ok = r >= NEAR_FLOOR[min(pass_n, 2)] or (pass_n >= 2 and len(lw) >= 2 and lw <= _words(c))
        if ok and (best is None or r > best[1]):
            best = (c, r)
    return best[0] if best else None


def _probe(items, vid):
    it = items.get(vid) or {}
    return C.parse_probe(it.get("probe")) if it.get("probe") is not None else None


def _latest(calls, items, vid):
    """(visit id, probe) of the newest look at a screen: its last whole-screen retry
    (cause loading / under_recorded) that came back, else the first capture."""
    for rv in reversed(calls.get("retry_visits") or []):
        if rv.get("of") == vid and rv.get("cause") in WHOLE_SCREEN:
            pr = _probe(items, rv["visit_id"])
            if pr is not None:
                return rv["visit_id"], pr
    return vid, _probe(items, vid)


def diagnose(calls, items, manifest, pass_n=1, tried=(), out_dir=None):
    """One row per (visit, ticket, label) that has no screenshot, plus one row per
    captured (visit, ticket). Row: {visit_id, ticket, persona, surface, entry, step, label,
    cause, retry (this pass can help), fallback (an unringed shot is allowed), target,
    wanted, detail}. `tried` = {(visit_id, ticket, target)} of earlier passes."""
    shots = manifest.get("shots") or []
    screens = {x.get("visit_id"): x for x in manifest.get("screens") or []}
    by_id = {x["visit_id"]: x for x in (calls.get("visits") or []) + (calls.get("retry_visits") or [])}
    rows = []
    for v in calls.get("visits") or []:
        vid = v["visit_id"]
        used, pr = _latest(calls, items, vid)
        sc = screens.get(vid)
        under = bool(out_dir and pr is not None and C.frame_under_recorded(by_id.get(used, v), pr, out_dir))
        for t in v.get("tickets") or []:
            labels = list((v.get("labels") or {}).get(t) or [])
            if not labels:
                continue
            base = {"visit_id": vid, "ticket": t, "persona": v["persona"], "surface": v["surface"],
                    "entry": v.get("entry", vid), "step": v.get("step", 0)}
            mine = [s for s in shots if s.get("ticket") == t and vid in (s.get("visit_id"), s.get("of"))]
            ringed = [s for s in mine if s.get("verified") and s.get("fallback") not in ("heading", "unringed")]

            def row(cause, label="", retry=False, fallback=False, **kw):
                rows.append({**base, "label": label, "cause": cause, "retry": retry,
                             "fallback": fallback, **kw})

            if sc is None or (sc.get("reason") and sc["reason"] != "needs_write"):
                row((sc or {}).get("reason") or "not_reached", detail=(sc or {}).get("detail", ""))
                continue
            if out_dir:
                # A retry frame aimed at one label that was itself under-recorded (these
                # retries always scroll, so they are the likeliest to be): the label is
                # recorded again in the next pass instead of being written off as tried.
                for rv in calls.get("retry_visits") or []:
                    if (rv.get("of") != vid or t not in (rv.get("tickets") or [])
                            or rv.get("cause") in WHOLE_SCREEN or int(rv.get("retry") or 0) >= pass_n):
                        continue
                    rp = _probe(items, rv["visit_id"])
                    tg = ((rv.get("labels") or {}).get(t) or [""])[0]
                    if (rp is not None and tg and C.frame_under_recorded(rv, rp, out_dir)
                            and not any(tg in (s.get("label"), s.get("label_wanted")) for s in ringed)
                            and not any(x.get("of") == vid and int(x.get("retry") or 0) > int(rv["retry"])
                                        and tg in ((x.get("labels") or {}).get(t) or [])
                                        for x in calls.get("retry_visits") or [])):
                        wanted = (rv.get("near") or {}).get(tg)
                        row("under_recorded", wanted or tg, retry="under_recorded" in RETRYABLE.get(pass_n, ()),
                            fallback=True, target=tg, detail="the retry recording",
                            **({"wanted": wanted} if wanted else {}))
            if pr is None:
                row("no_probe")
                continue
            if (pr.get("tn") or {}).get("v") == "BLOCK":
                row("tenant_block", detail=", ".join((pr.get("tn") or {}).get("k") or []))
                continue
            by = {l.get("l"): l for l in pr.get("labels") or [] if isinstance(l, dict)}
            found = [l for l in labels if (by.get(l) or {}).get("f") and by[l].get("b")
                     and not C.label_hidden(by[l])]
            can = lambda cause, target: (cause in RETRYABLE.get(pass_n, ())           # noqa: E731
                                         and (vid, t, target) not in tried)
            if ringed:
                got = {s.get("label") for s in ringed} | {s.get("label_wanted") for s in ringed}
                rows.append({**base, "label": "", "cause": "captured", "retry": False, "fallback": False,
                             "labels": sorted(x for x in got if x)})
                for l in found:
                    if l not in got and (not by[l].get("iv") or by[l].get("oc")):
                        row("other_frame", l, retry=can("other_frame", l), target=l,
                            detail=_where(by[l], pr))
                continue
            if pr.get("ld"):
                row("loading", retry="loading" in RETRYABLE.get(pass_n, ()), fallback=False, target="",
                    detail=str(pr["ld"])[:60])
                continue
            if under:
                row("under_recorded", retry="under_recorded" in RETRYABLE.get(pass_n, ()),
                    fallback=True, target="")
                continue
            if found:
                l = found[0]
                if by[l].get("iv") and not by[l].get("oc"):
                    # in the frame and uncovered, yet no ring: the text check failed on it.
                    # Scrolling or waiting again cannot help, so it is not retried.
                    row("unreadable", l, fallback=True, target=l, detail=_where(by[l], pr))
                    continue
                cause = "covered" if by[l].get("iv") else "offscreen"
                row(cause, l, retry=can(cause, l), fallback=True, target=l, detail=_where(by[l], pr))
                continue
            used = {x[2] for x in tried if x[0] == vid and x[1] == t}
            n = 0
            for l in labels:
                c = near_pick(l, (by.get(l) or {}).get("c"), pass_n, exclude=set(labels) | used)
                if c and n < MAX_TARGETS:
                    n += 1
                    row("near_match", l, retry=can("near_match", c), fallback=True, target=c, wanted=l)
            if not n:
                row("not_found", labels[0], fallback=True,
                    detail=", ".join(labels[:4]))
    return rows


def _where(pl, pr):
    b = pl.get("b") or [0, 0, 0, 0]
    return f"at y={round(b[1])} in a {pr.get('ih')} px window"


# ── retry plan ───────────────────────────────────────────────────────────────
def _tried(out_dir, upto):
    tried = set()
    for n in range(1, upto):
        p = Path(out_dir) / f"retry-{n}.json"
        if p.exists():
            for r in json.loads(p.read_text()).get("rows") or []:
                tried.add((r["visit_id"], r["ticket"], r.get("target", "")))
    return tried


def build_retry(calls, demo, rows, pass_n):
    """rows (diagnose, retry=True) -> a calls-like dict for capture_md: for every entry
    with a failed screen, open its start page, replay its clicks without recording, and
    record each failed screen once per target (one frame per target, MAX_TARGETS)."""
    base, gif_dir = calls["base_url"], calls["gif_dir"]
    by_pos = {(v.get("entry", v["visit_id"]), v.get("step", 0)): v for v in calls["visits"]}
    cap = C.QUIET_CAP_S if pass_n == 1 else C.QUIET_CAP_MAX_S
    # screen -> targets, in visit order; the per-run limit counts screens
    targets, skipped = {}, []
    for r in rows:
        v = by_pos[(r["entry"], r["step"])]
        if C.account_of(v["persona"]) != "main" or v.get("private"):
            skipped.append({**r, "retry": False, "skipped": "needs_signin"})
            continue
        if r["visit_id"] not in targets and len(targets) >= MAX_RETRY_SCREENS:
            skipped.append({**r, "retry": False, "skipped": "limit"})
            continue
        tl = targets.setdefault(r["visit_id"], [])
        key = r.get("target", "")
        hit = next((x for x in tl if x["key"] == key), None)
        if hit is None:
            if len(tl) >= MAX_TARGETS:
                skipped.append({**r, "retry": False, "skipped": "limit"})
                continue
            hit = {"key": key, "cause": r["cause"], "labels": {}, "near": {}, "rows": []}
            tl.append(hit)
        if key:
            hit["labels"].setdefault(r["ticket"], []).append(key)
            if r.get("wanted"):
                hit["near"][key] = r["wanted"]
        else:                                   # loading: the screen again, all its labels
            hit["labels"][r["ticket"]] = list((v.get("labels") or {}).get(r["ticket"]) or [])
            hit["near"].update(v.get("near") or {})
        hit["rows"].append(r)
    entries = {}
    for vid, tl in targets.items():
        v = next(x for x in calls["visits"] if x["visit_id"] == vid)
        entries.setdefault(v.get("entry", vid), {})[v.get("step", 0)] = tl
    batches, rvisits, done_rows, seen_p = [], [], [], set()
    portal = (demo.get("portal_entry") or {})

    def bid():
        return f"r{pass_n}b{len(batches) + 1:02d}"

    def rv_of(v, tgt, i):
        flat = []
        for ls in tgt["labels"].values():
            flat += [l for l in ls if l not in flat]
        rid = f"{v['visit_id']}-r{pass_n}{'abc'[i]}"       # letters, digits, hyphens only: Chrome refused "~"
        rv = {"visit_id": rid, "of": v["visit_id"], "entry": v.get("entry", v["visit_id"]),
              "step": v.get("step", 0), "kind": "retry", "retry": pass_n, "cause": tgt["cause"],
              "persona": v["persona"], "surface": v["surface"], "url": v["url"],
              "tickets": list(tgt["labels"]), "labels": {t: list(ls) for t, ls in tgt["labels"].items()},
              "gif": f"{gif_dir}/{rid}.gif", "args": {**v["args"], "labels": flat},
              "quiet": {**(v.get("quiet") or {}), "cap_s": cap}}
        if tgt["near"]:
            rv["near"] = dict(tgt["near"])
        if i == 0 and not rv["step"]:
            rv["nav"] = True                    # this frame's batch navigates: it starts at the top
        for k in ("wait_s", "click", "click_args"):
            if k in v:
                rv[k] = v[k]
        rvisits.append(rv)
        for r in tgt["rows"]:
            done_rows.append({**r, "retry_visit": rid})
        return rv

    def frames(rv):
        return [C._act("gif_creator", action="start_recording"), C._wait(1), C._scroll(rv),
                C._act("computer", action="hover", coordinate=list(C._park(demo))),
                C._act("javascript_tool", action="javascript_exec", text=C.stub(rv["args"])),
                C._act("gif_creator", action="stop_recording"), C._export(rv["gif"])]

    for eid, steps in entries.items():
        for step in range(max(steps) + 1):
            v = by_pos[(eid, step)]
            tl = steps.get(step) or []
            pre = []
            if step == 0 and v["persona"] not in seen_p and portal.get(v["persona"]):
                pre = [C._act("navigate", url=base + portal[v["persona"]]), C._wait()]
            seen_p.add(v["persona"])
            common = {"persona": v["persona"], "account": "main", "entry": eid, "step": step}
            if step == 0 and not tl:
                batches.append({"batch_id": bid(), "kind": "open", "visit_ids": [], **common,
                                "actions": pre + [C._act("navigate", url=base + v["url"]),
                                                  C._wait(C.settle_seconds(demo, v.get("wait_s"))),
                                                  C._quiet({"quiet": {"cap_s": cap}})]})
                continue
            if step and not tl:
                k = v.get("click") or {}
                what = "the first row" if k.get("row") else k.get("check") or k.get("text") or ""
                batches.append({"batch_id": bid(), "kind": "replay", "visit_ids": [], **common, "what": what,
                                "pick": [C._act("javascript_tool", action="javascript_exec",
                                                text=C.stub(v["click_args"], C.KBC_KEY))],
                                "act": [C._act("computer", action="left_click", coordinate=["$X", "$Y"]),
                                        C._wait(C.settle_seconds(demo, v.get("wait_s"))),
                                        C._quiet({"quiet": {"cap_s": cap}})]})
                continue
            first = rv_of(v, tl[0], 0)
            if step == 0:
                batches.append({"batch_id": bid(), "kind": "visits", "visit_ids": [first["visit_id"]],
                                **common, "actions": pre + C.visit_actions(base, first, demo)})
            else:
                pick, act = C.click_visit_actions(first, demo)
                batches.append({"batch_id": bid(), "kind": "click", "visit_ids": [first["visit_id"]],
                                **common, "pick": pick, "act": act})
            for i, tgt in enumerate(tl[1:], 1):
                rv = rv_of(v, tgt, i)
                batches.append({"batch_id": bid(), "kind": "frames", "visit_ids": [rv["visit_id"]],
                                **common, "actions": frames(rv)})
    personas = list(dict.fromkeys(b["persona"] for b in batches))
    return {"title": f"KB capture, retry pass {pass_n}", "run_id": calls.get("run_id"),
            "pass": pass_n, "base_url": base, "gif_dir": gif_dir, "visits": rvisits,
            "batches": batches, "rows": done_rows, "skipped": skipped,
            "accounts": ([{"account": "main", "personas": personas,
                           "batches": [b["batch_id"] for b in batches]}] if batches else []),
            "install": [], "abort": calls.get("abort") or [],
            "restore": calls.get("restore") or [], "entries": calls.get("entries") or {},
            "screens": [], "write_visits": []}


def worth_retrying(rows):
    """The rows a pass acts on. A screen with no screenshot is always retried; an extra
    frame for a second label (other_frame) only where it is cheap: on a start page, or in
    an entry that is being replayed anyway."""
    rows = [r for r in rows if r["retry"]]
    must = {r["entry"] for r in rows if r["cause"] != "other_frame"}
    return [r for r in rows if r["cause"] != "other_frame" or r["step"] == 0 or r["entry"] in must]


def _load(calls_path, raw_dir, out_dir, extra_calls=()):
    calls = json.loads(Path(calls_path).read_text())
    planned = {b["batch_id"]: b["visit_ids"] for b in calls["batches"]}
    for c in extra_calls:
        planned.update({b["batch_id"]: b["visit_ids"] for b in c["batches"]})
    items, _ = C._load_raw(raw_dir, planned)
    mp = Path(out_dir) / "shots.json"
    manifest = json.loads(mp.read_text()) if mp.exists() else {"shots": []}
    return calls, items, manifest, mp


def cmd_plan_retry(a):
    out_dir = Path(a.out_dir)
    C.use_run_token(out_dir)
    done = out_dir / f"retry-{a.pass_n}.json"
    if done.exists() and (json.loads(done.read_text()).get("results") is not None):
        # planning it again would overwrite its results and reuse its screen names
        print(f"retry pass {a.pass_n}: already captured and ingested; not planned again")
        emit({"ok": True, "action": "plan-retry", "pass": a.pass_n, "screens": 0, "batches": 0,
              "skipped": 0, "already_done": True})
        return 4
    calls, items, manifest, _ = _load(a.calls, a.raw_dir, out_dir)
    demo = O.load_routes(a.routes)["demo"]
    rows = worth_retrying(diagnose(calls, items, manifest, a.pass_n, _tried(out_dir, a.pass_n), out_dir))
    plan = build_retry(calls, demo, rows, a.pass_n)
    bad = [v["gif"] for v in plan["visits"] if not C.gif_name_ok(v["gif"])]
    if bad:                                     # never hand Chrome a name it will refuse
        raise KeyError(f"unsafe download name(s): {', '.join(bad)}")
    (out_dir / f"retry-{a.pass_n}.json").write_text(json.dumps(plan, indent=1))
    if not plan["batches"]:
        print(f"retry pass {a.pass_n}: nothing to retry")
        emit({"ok": True, "action": "plan-retry", "pass": a.pass_n, "screens": 0, "batches": 0,
              "skipped": len(plan["skipped"])})
        return 4
    (out_dir / f"retry-{a.pass_n}.md").write_text(C.capture_md(plan))
    calls["retry_visits"] = [v for v in calls.get("retry_visits") or []
                             if v.get("retry") != a.pass_n] + plan["visits"]
    Path(a.calls).write_text(json.dumps(calls, indent=1))          # cleanup removes their GIFs too
    screens = len({r["visit_id"] for r in plan["rows"]})
    print(f"retry pass {a.pass_n}: {screens} screen(s), {len(plan['visits'])} frame(s) in "
          f"{len(plan['batches'])} batches -> {out_dir / f'retry-{a.pass_n}.md'}")
    emit({"ok": True, "action": "plan-retry", "pass": a.pass_n, "screens": screens,
          "frames": len(plan["visits"]), "batches": len(plan["batches"]),
          "skipped": len(plan["skipped"]), "capture_md": str(out_dir / f"retry-{a.pass_n}.md"),
          "first_batch": plan["batches"][0]["batch_id"]})
    return 0


def cmd_ingest_retry(a):
    out_dir = Path(a.out_dir)
    pp = out_dir / f"retry-{a.pass_n}.json"
    plan = json.loads(pp.read_text())
    calls, items, manifest, mp = _load(a.calls, a.raw_dir, out_dir, [plan])
    demo = O.load_routes(a.routes)["demo"]
    use_ocr = C.ocr_available() and not a.no_ocr
    now = _dt.datetime.now().isoformat(timespec="minutes")
    mine = {v["visit_id"] for v in plan["visits"]}
    kept = [s for s in manifest.get("shots") or [] if s.get("visit_id") not in mine]
    n = max([int(re.sub(r"\D", "", s.get("shot_id", "")) or 0) for s in kept] + [0])
    results, fetched = {}, 0
    for rv in plan["visits"]:
        it = items.get(rv["visit_id"])
        if not it or it.get("click_error") or it.get("probe") is None:
            results[rv["visit_id"]] = {"ok": False, "reason": C.click_reason(it.get("click_error"))[0]
                                       if it and it.get("click_error") else "not_reached"}
            continue
        timeout = C.FIRST_FILE_TIMEOUT_S if not fetched else C.NEXT_FILE_TIMEOUT_S
        fetched += 1
        entries, reason = C.safe_ingest_visit(rv, it, calls, demo, out_dir, use_ocr, now,
                                              a.downloads_dir, timeout)
        pr = C.parse_probe(it.get("probe")) or {}
        ok = any(e["verified"] for e in entries)
        results[rv["visit_id"]] = {"ok": ok, "reason": reason or ("" if ok else
                                   "still_loading" if pr.get("ld") else
                                   "under_recorded" if C.frame_under_recorded(rv, pr, out_dir)
                                   else "label_not_found")}
        for e in entries:
            n += 1
            kept.append({"shot_id": f"s{n}", **e})
    manifest["shots"] = kept
    manifest["missing"] = C._screens_missing(calls, manifest.get("screens") or [], kept)
    mp.write_text(json.dumps(manifest, indent=1))
    plan["results"] = results
    pp.write_text(json.dumps(plan, indent=1))
    good = sum(1 for r in results.values() if r["ok"])
    print(f"retry pass {a.pass_n}: {good} of {len(results)} frame(s) gave a screenshot")
    emit({"ok": True, "action": "ingest-retry", "pass": a.pass_n, "frames": len(results), "captured": good,
          "missing": {t: m["reason"] for t, m in manifest["missing"].items()}})
    return 0


# ── finish: unringed fallback, learning, diagnosis ───────────────────────────
def _frame_of(out_dir, vid):
    for ext in ("png", "jpg"):
        p = Path(out_dir) / "frames" / f"{vid}.{ext}"
        if p.is_file():
            return p
    return None


def unringed(calls, items, manifest, rows, demo, out_dir, use_ocr, now):
    """One unringed shot (anchor "screen") per (visit, ticket) that has labels wanted, no
    screenshot at all, a loaded screen and a clean frame. The owner decides on the contact
    sheet whether it ships; a still-loading or tenant-BLOCK screen never gets one."""
    from PIL import Image
    shots, out = manifest.get("shots") or [], []
    for vid, t in dict.fromkeys((r["visit_id"], r["ticket"]) for r in rows if r.get("fallback")):
        if any(s.get("verified") and s.get("ticket") == t and vid in (s.get("visit_id"), s.get("of"))
               for s in shots):
            continue
        v = next(x for x in calls["visits"] if x["visit_id"] == vid)
        used, pr = _latest(calls, items, vid)
        frame = _frame_of(out_dir, used)
        if pr is None or frame is None or pr.get("ld"):
            continue
        with Image.open(frame) as im:
            if C.frame_problem(im.width, im.height, pr):
                continue
        tn = pr.get("tn") or {}
        dom = tn.get("v") if tn.get("v") in ("CLEAR", "REVIEW", "BLOCK") else None
        ocr = None
        if use_ocr:
            try:
                ocr = Q.screen_frame(str(frame), demo["domain"],
                                     allow_strings=demo.get("allow_strings") or [])["verdict"]
            except Exception:                      # noqa: BLE001 - an OCR failure leaves it unscreened
                ocr = None
        verdicts = [x for x in (dom, ocr) if x]
        tenant = Q.worst(*verdicts) if verdicts else "REVIEW"
        if tenant == "BLOCK":
            continue
        host = calls["base_url"].split("//")[-1]
        h = pr.get("h") if isinstance(pr.get("h"), dict) else {}
        out.append({"ticket": t, "persona": v["persona"], "surface": v["surface"], "frame": str(frame),
                    "verified": True, "visit_id": vid, "sidebar": False,
                    "source": (f"chrome:{host} external-link {now}" if v.get("private")
                               else f"chrome:{host}{pr.get('u', v['url'])} {now}"),
                    "frame_source": "gif" if frame.suffix == ".png" else "disk",
                    "fallback": "unringed", "anchor": F.SCREEN_ANCHOR,
                    "label": str(h.get("t") or v["surface"])[:80], "label_score": 1.0,
                    "tenant": tenant, "tenant_dom": dom, "tenant_ocr": ocr})
    return out


def learn(calls, items, manifest, plans, home, run_id, today):
    """Write what the retries proved into kb_overrides.json and count the outcome of the
    learned entries this run used. -> {"learned": [lines], "dropped": [lines], "path"}."""
    data = O.load(home)
    learned, dropped = [], []
    shots = manifest.get("shots") or []
    for plan in plans:
        res = plan.get("results") or {}
        for r in plan.get("rows") or []:
            if not (res.get(r.get("retry_visit")) or {}).get("ok"):
                continue
            if r["cause"] == "near_match" and r.get("wanted"):
                ok = any(s.get("verified") and s.get("visit_id") == r["retry_visit"]
                         and s.get("label") == r["target"] for s in shots)
                e = ok and O.add_learned(data, r["persona"], r["surface"], "alias", r["target"],
                                         run_id, today, label=r["wanted"])
            elif r["cause"] == "loading" and plan.get("pass") == 2:
                e = O.add_learned(data, r["persona"], r["surface"], "quiet_cap_s",
                                  C.QUIET_CAP_MAX_S, run_id, today)
            else:
                e = None
            if e and O.line(e) not in learned:
                learned.append(O.line(e))
    for v in calls.get("visits") or []:           # did the learned aliases this run used still hold?
        pr = _probe(items, v["visit_id"])
        if pr is None:
            continue
        by = {l.get("l"): l for l in pr.get("labels") or [] if isinstance(l, dict)}
        for alias, label in (v.get("near") or {}).items():
            for e in list(data["entries"]):
                if (e.get("key") == "alias" and e.get("surface") == v["surface"]
                        and e.get("persona") == v["persona"] and e.get("label") == label
                        and e.get("value") == alias and e.get("source") == "learned"
                        and e.get("run") != run_id):
                    if O.note_result(data, e, bool((by.get(alias) or {}).get("f"))):
                        dropped.append(O.line(e))
    p = None
    if learned or dropped or data["entries"]:
        p = O.save(data, home)
    return {"learned": learned, "dropped": dropped, "path": str(p) if p else ""}


def diagnosis_lines(rows, plans, manifest):
    """Plain words, one line per label that needed attention."""
    shots = manifest.get("shots") or []

    def subject(r):
        if r["cause"] in WHOLE_SCREEN and not r.get("target"):
            return CAUSES[r["cause"]]                      # a clause of its own: "the page was still loading"
        what = f"\"{r.get('wanted') or r.get('label') or r.get('target')}\""
        if r["cause"] in WHOLE_SCREEN:
            return f"{what}: {CAUSES[r['cause']]}"
        return f"{what} {CAUSES.get(r['cause'], r['cause'])}"

    def unringed_kept(r):
        return any(s.get("fallback") == "unringed" and s.get("verified") and s.get("ticket") == r["ticket"]
                   and r["visit_id"] in (s.get("visit_id"), s.get("of")) for s in shots)

    head = lambda r: f"{r['ticket']} · {r['surface']} (screen {r['visit_id']}): "       # noqa: E731
    key = lambda r: (r["visit_id"], r["ticket"], r.get("wanted") or r.get("label") or "")   # noqa: E731
    L, seen, told = [], set(), set()
    last = {}
    for plan in plans:
        for r in plan.get("rows") or []:
            last[key(r)] = plan.get("pass")
    for plan in plans:
        res = plan.get("results") or {}
        for r in plan.get("rows") or []:
            ok = (res.get(r.get("retry_visit")) or {}).get("ok")
            told.add(key(r))
            if ok:
                end = "; captured."
            elif last[key(r)] != plan.get("pass"):
                end = "; still no screenshot."
            else:
                end = ("; no ring, so an unringed screenshot was kept for you to judge." if unringed_kept(r)
                       else "; still no screenshot.")
            line = (head(r) + subject(r) + (f" ({r['detail']})" if r.get("detail") else "")
                    + f"; retry {plan.get('pass')} {REMEDY.get(r['cause'], 'tried again')}"
                    + (f" (\"{r['target']}\")" if r["cause"] == "near_match" else "") + end)
            if line not in seen:
                seen.add(line)
                L.append(line)
    for r in rows:
        if r["cause"] == "captured" or key(r) in told:
            continue
        got = [s for s in shots if s.get("verified") and s.get("ticket") == r["ticket"]
               and r["visit_id"] in (s.get("visit_id"), s.get("of"))]
        if r["cause"] in CAUSES:
            tail = ("an unringed screenshot was kept for you to judge."
                    if any(s.get("fallback") == "unringed" for s in got)
                    else "the page heading was ringed instead." if any(s.get("fallback") == "heading" for s in got)
                    else "another item on this screen was captured." if got else "no screenshot.")
            extra = {"needs_signin": " Not retried: it needs another sign-in.",
                     "limit": f" Not retried: over the limit of {MAX_RETRY_SCREENS} screens per run "
                              f"or {MAX_TARGETS} items per screen."}.get(r.get("skipped"), "")
            line = head(r) + subject(r) + (f" ({r['detail']})" if r.get("detail") else "") + f"; {tail}{extra}"
        else:
            line = (head(r) + f"{F.missing_reason_text(r['cause'], r.get('detail', ''))}; "
                    "not something a retry can fix.")
        if line not in seen:
            seen.add(line)
            L.append(line)
    return L


def cmd_finish(a):
    out_dir = Path(a.out_dir)
    plans = [json.loads(p.read_text()) for p in sorted(out_dir.glob("retry-[0-9].json"))]
    calls, items, manifest, mp = _load(a.calls, a.raw_dir, out_dir, plans)
    demo = O.load_routes(a.routes)["demo"]
    now = _dt.datetime.now().isoformat(timespec="minutes")
    rows = diagnose(calls, items, manifest, pass_n=3, out_dir=out_dir)   # pass 3: nothing is retryable any more
    skipped = {(r["visit_id"], r["ticket"], r.get("wanted") or r.get("label") or ""): r.get("skipped")
               for p in plans for r in p.get("skipped") or []}
    for r in rows:
        r["skipped"] = skipped.get((r["visit_id"], r["ticket"], r.get("wanted") or r.get("label") or ""))
    added = []
    try:
        added = unringed(calls, items, manifest, rows, demo, out_dir,
                         C.ocr_available() and not a.no_ocr, now)
    except ImportError:
        pass                                       # no Pillow: no fallback shots
    n = max([int(re.sub(r"\D", "", s.get("shot_id", "")) or 0) for s in manifest.get("shots") or []] + [0])
    for e in added:
        n += 1
        manifest.setdefault("shots", []).append({"shot_id": f"s{n}", **e})
    manifest["missing"] = C._screens_missing(calls, manifest.get("screens") or [], manifest.get("shots") or [])
    mp.write_text(json.dumps(manifest, indent=1))
    today = _dt.date.today().isoformat()
    lrn = {"learned": [], "dropped": [], "path": ""}
    if not a.no_learn:
        lrn = learn(calls, items, manifest, plans, a.home, calls.get("run_id") or "", today)
    src = O.path(a.home)
    if src.is_file():
        shutil.copyfile(src, out_dir / O.NAME)     # travels in the run zip
    lines = diagnosis_lines(rows, plans, manifest)
    healed = sum(1 for p in plans for r in (p.get("results") or {}).values() if r.get("ok"))
    md = ["# Capture diagnosis", ""]
    md += [f"- {x}" for x in lines] or ["Every wanted screenshot was taken on the first attempt."]
    if lrn["learned"]:
        md += ["", "## Learned (written to kb_overrides.json)", ""] + [f"- {x}" for x in lrn["learned"]]
    if lrn["dropped"]:
        md += ["", "## Dropped (failed twice in a row)", ""] + [f"- {x}" for x in lrn["dropped"]]
    (out_dir / "diagnosis.md").write_text("\n".join(md) + "\n")
    (out_dir / "diagnosis.json").write_text(json.dumps(
        {"rows": rows, "lines": lines, "healed": healed, "unringed": len(added), **lrn}, indent=1))
    print("\n".join(md))
    emit({"ok": True, "action": "finish", "healed": healed, "unringed": len(added),
          "learned": lrn["learned"], "dropped": lrn["dropped"], "lines": len(lines),
          "diagnosis": str(out_dir / "diagnosis.md"),
          "missing": {t: m["reason"] for t, m in manifest["missing"].items()}})
    return 0


# ── check-routes ─────────────────────────────────────────────────────────────
CHECK_PER_BATCH = 5


def build_check(routes, only=()):
    """A probe-only walk of every capturable route of the main sign-in (admin, faculty):
    navigate, settle, quiet wait, probe (labels: the first click's text). No recording,
    no click, nothing written."""
    demo = routes["demo"]
    base = demo["base_url"]
    plan = {"capture_plan": []}
    visits = []
    for s in routes.get("surfaces") or []:
        if not s.get("url") or s.get("demo_status") in (routes.get("uncapturable_statuses") or []):
            continue
        if only and s.get("surface") not in only:
            continue
        if C.account_of(s.get("persona")) != "main":
            continue
        plan["capture_plan"].append({"persona": s["persona"], "surface": s["surface"]})
        k = (s.get("clicks") or [{}])[0]
        first = k.get("text") or k.get("check") or ""
        args = {"v": C.PROBE_VERSION, "login": demo["login_path"],
                "portal": (demo.get("portal_label") or {}).get(s["persona"]),
                "side": demo.get("sidebar_selector", ""), "allow": list(demo.get("allow_strings", [])),
                "domain": demo["domain"], "labels": [first] if first else []}
        quiet = {"cap_s": min(float(s.get("quiet_cap_s") or C.QUIET_CAP_S), C.QUIET_CAP_MAX_S)}
        if s.get("loader_sel"):
            quiet["ld"] = args["ld"] = ",".join(s["loader_sel"])
        visits.append({"visit_id": f"c{len(visits) + 1:02d}", "persona": s["persona"],
                       "surface": s["surface"], "url": s["url"], "first_click": first,
                       "wait_s": s.get("wait_s"), "args": args, "quiet": quiet})
    order = C.persona_order(list(dict.fromkeys(v["persona"] for v in visits)))
    batches, seen = [], set()
    for p in order:
        mine = [v for v in visits if v["persona"] == p]
        for i in range(0, len(mine), CHECK_PER_BATCH):
            acts = []
            if p not in seen and (demo.get("portal_entry") or {}).get(p):
                acts += [C._act("navigate", url=base + demo["portal_entry"][p]), C._wait()]
            seen.add(p)
            chunk = mine[i:i + CHECK_PER_BATCH]
            for v in chunk:
                acts += [C._act("navigate", url=base + v["url"]),
                         C._wait(C.settle_seconds(demo, v.get("wait_s"))), C._quiet(v),
                         C._act("javascript_tool", action="javascript_exec", text=C.stub(v["args"]))]
            batches.append({"batch_id": f"c{len(batches) + 1:02d}", "persona": p,
                            "visit_ids": [v["visit_id"] for v in chunk], "actions": acts})
    home = (demo.get("portal_entry") or {}).get(demo.get("home_portal", "admin"))
    return {"base_url": base, "visits": visits, "batches": batches,
            "install": C.install_actions(demo, C.read_posts_for(plan, routes)),
            "restore": [C._act("navigate", url=base + home), C._wait()] if home else []}


def check_md(chk):
    L = ["# KB route check — instructions for the capturer", "",
         "You only drive the user's Chrome. Never type, never press keys, never click. Never close "
         "a tab. Replace the string `\"$TAB\"` with the numeric tab id (no quotes) in every action.", "",
         "There is no recording and no export in this file: each route is navigate, wait, a quiet "
         "wait (a `javascript_tool` call that can take up to 20 seconds to answer) and the probe "
         "(the LAST `javascript_tool` call of that route). The orchestrator stored the page scripts "
         "already; an error containing `KB_NOPROBE` means they are gone: write the raw file with "
         "status `needs_install` and stop.", "",
         "For each batch, call `browser_batch` with exactly its `actions`. A batch stops at the "
         "first error; the routes before it are done. Then write `raw/<batch_id>.json` next to "
         "this file:", "",
         C._j({"batch_id": "c01", "status": "ok", "error": None,
               "items": [{"visit_id": "c01", "probe": "<the probe result exactly as returned>"}]}), "",
         "One item per route whose probe came back, in order; `visit_id`s are in the batch heading. "
         "`status`: `ok`, or `expired` (an error containing `KB_LOGIN`: stop), `wrong_persona` "
         "(`KB_PERSONA`: stop), `needs_install`, `bridge_down` (tool unreachable or a hook denied a "
         "call: stop). Any other error: status `ok`, put its text in `error`, and carry on with the "
         "next batch.", ""]
    for b in chk["batches"]:
        L += [f"### {b['batch_id']} — {b['persona']} — routes {', '.join(b['visit_ids'])}", "",
              C._j(b["actions"]), ""]
    L += ["## Restore", "", "After the last batch (or a stop), send once:", "", C._j(chk["restore"]), "",
          "Reply `KB_CHECK_DONE ok=<batches written with status ok> stop=<first non-ok status|none>`.", ""]
    return "\n".join(L)


def check_rows(chk, items):
    rows = []
    for v in chk["visits"]:
        it = items.get(v["visit_id"])
        pr = C.parse_probe(it.get("probe")) if it else None
        if pr is None:
            res = "not checked (no probe result)"
        elif pr.get("nf"):
            res = "PAGE NOT FOUND"
        elif pr.get("ld"):
            res = f"still loading after the wait ({pr['ld']})"
        elif (pr.get("tn") or {}).get("v") == "BLOCK":
            res = "tenant data on the page (" + ", ".join((pr.get("tn") or {}).get("k") or []) + ")"
        elif int(pr.get("wb") or 0) >= 1 and pr.get("wl"):
            res = f"a request was blocked by the write guard ({pr['wl']})"
        elif v["first_click"] and not any(l.get("l") == v["first_click"] and l.get("f")
                                          for l in pr.get("labels") or []):
            res = f"first click \"{v['first_click']}\" is not on the page"
        else:
            res = "ok"
        rows.append({"visit_id": v["visit_id"], "persona": v["persona"], "surface": v["surface"],
                     "url": v["url"], "result": res})
    return rows


def cmd_plan_check(a):
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    C.use_run_token(out)
    chk = build_check(O.load_routes(a.routes), a.surface or ())
    (out / "routes-check.json").write_text(json.dumps(chk, indent=1))
    (out / "routes-check.md").write_text(check_md(chk))
    print(f"route check: {len(chk['visits'])} route(s) in {len(chk['batches'])} batches -> {out / 'routes-check.md'}")
    emit({"ok": True, "action": "plan-check", "routes": len(chk["visits"]), "batches": len(chk["batches"]),
          "check_md": str(out / "routes-check.md")})
    return 0


def cmd_check_report(a):
    chk = json.loads(Path(a.check).read_text())
    items, _ = C._load_raw(a.raw_dir, {b["batch_id"]: b["visit_ids"] for b in chk["batches"]})
    rows = check_rows(chk, items)
    bad = [r for r in rows if r["result"] != "ok"]
    md = ["# KB route check", "", f"{len(rows) - len(bad)} of {len(rows)} routes ok.", "",
          "| Route | Persona | Result |", "|---|---|---|"]
    md += [f"| {r['surface']} | {r['persona']} | {r['result']} |" for r in bad + [r for r in rows if r not in bad]]
    out = Path(a.check).with_name("routes-check-report.md")
    out.write_text("\n".join(md) + "\n")
    print("\n".join(md))
    emit({"ok": not bad, "action": "check-report", "routes": len(rows), "problems": len(bad),
          "report": str(out)})
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan-retry", "ingest-retry", "finish"):
        s = sub.add_parser(name)
        s.add_argument("--calls", required=True); s.add_argument("--raw-dir", required=True)
        s.add_argument("--out-dir", required=True); s.add_argument("--routes", default=str(C.DEFAULT_ROUTES))
        if name != "finish":
            s.add_argument("--pass", dest="pass_n", type=int, choices=(1, 2), required=True)
        if name != "plan-retry":
            s.add_argument("--no-ocr", action="store_true")
        if name == "ingest-retry":
            s.add_argument("--downloads-dir")
        if name == "finish":
            s.add_argument("--home"); s.add_argument("--no-learn", action="store_true")
    s = sub.add_parser("plan-check"); s.add_argument("--out-dir", required=True)
    s.add_argument("--routes", default=str(C.DEFAULT_ROUTES)); s.add_argument("--surface", action="append")
    s = sub.add_parser("check-report"); s.add_argument("--check", required=True)
    s.add_argument("--raw-dir", required=True)
    a = ap.parse_args(argv)
    try:
        return {"plan-retry": cmd_plan_retry, "ingest-retry": cmd_ingest_retry, "finish": cmd_finish,
                "plan-check": cmd_plan_check, "check-report": cmd_check_report}[a.cmd](a)
    except (OSError, json.JSONDecodeError, KeyError) as e:
        emit({"ok": False, "action": a.cmd, "error": f"{type(e).__name__}: {e}"})
        return 1


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import utf8_mode
    utf8_mode.ensure()          # Windows: rerun in UTF-8 mode
    sys.exit(main())
