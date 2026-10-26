#!/usr/bin/env python3
"""
kb_capture.py — live screenshot capture on the demo tenant with Playwright.

A human signs in ONCE per persona with `login`; the browser's storage state is
saved and every later run reuses it headless. Nothing here ever types a
password, and the command never runs `login` itself.

  login   --persona admin|faculty|learner
          Opens a visible browser at the demo sign-in page and waits for the
          person to sign in, then saves <session-dir>/<persona>.json.
  check   For each persona: valid | expired | missing (one cheap page load).
  run     --plan groups.json --inputs inputs.json
          Walks the capture plan persona by persona, surface by surface. One
          visit per surface; every ticket on it shares the frames. For each
          candidate label: the DOM gives the element box (no vision, no grid);
          OCR on that crop proves the pixels show it (label_box, ≥0.82). With no
          tesseract, an exact, visible, unoccluded DOM match is recorded as a
          draft-only dom_box anchor instead. The tenant screen reads both the
          page's DOM text and (when OCR is available) the frame; the worse
          verdict wins.

Session expiry mid-run: the shots already taken are kept, the rest of the plan
is written to <out-dir>/resume.json (same shape as groups.json), and the
script exits 3. After the person signs in again: `run --plan resume.json`.

Exit codes: 0 done, 1 configuration error, 3 session expired (resume.json written).

    ---KB_CAPTURE_RESULT---
    {"ok", "action", "shots", "verified", "visits", "left", "error"}
"""

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import kb_format as F                   # noqa: E402
import qa_gif_to_shots as Q             # noqa: E402

DEFAULT_ROUTES = HERE / "kb_routes.json"
VIEWPORT = {"width": 1440, "height": 900}
SCALE = 2                 # deviceScaleFactor: crisp OCR crops; shots are downscaled later
MAX_LABELS = 6            # per ticket — the writer rarely needs more shots than steps
LOGIN_WAIT_S = 600


class SessionExpired(Exception):
    pass


def emit(payload):
    print("---KB_CAPTURE_RESULT---")
    print(json.dumps(payload))


def session_dir(arg=None):
    return Path(arg or os.environ.get("KB_SESSION_DIR") or ".kb-sessions")


def ocr_available():
    if not shutil.which("tesseract"):
        return False
    try:
        import pytesseract  # noqa: F401
        return True
    except Exception:
        return False


# ── pure helpers (tested without a browser) ──────────────────────────────────
def is_login_page(url, has_password_field, login_path):
    return bool(has_password_field) or (login_path and login_path in (url or ""))


def to_image_box(css_box, scale=SCALE):
    """Playwright bounding_box (CSS px, dict) -> (x, y, w, h) in screenshot px."""
    return tuple(int(round(css_box[k] * scale)) for k in ("x", "y", "width", "height"))


def resume_plan(plan, done_keys):
    left = [c for c in plan["capture_plan"] if (c["persona"], c["surface"]) not in done_keys]
    return {**plan, "capture_plan": left}


def shot_evidence(label, dom_text, box, crop_result, occluded, tenant_dom, tenant_ocr):
    """Evidence for one candidate label, or None when it can't anchor a ring.

    OCR present: label_box when the crop scores ≥ the floor.
    OCR absent:  dom_box (draft-only) when the DOM text is exactly the label
                 and nothing covers the element."""
    if occluded:
        return None
    if crop_result is not None:
        if crop_result["verdict"] == "MISS":
            return None
        anchor, score = "label_box", crop_result["score"]
    else:
        if (dom_text or "").strip().lower() != label.strip().lower():
            return None
        anchor, score = "dom_box", 1.0
    verdicts = [v for v in (tenant_dom, tenant_ocr) if v]
    return {"anchor": anchor, "label": label, "label_score": score,
            "dom_box": list(box), "tenant": Q.worst(*verdicts) if verdicts else "REVIEW",
            "tenant_dom": tenant_dom, "tenant_ocr": tenant_ocr}


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60]


# ── browser half ─────────────────────────────────────────────────────────────
def _playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("playwright is not installed: "
                         "pip install playwright && python -m playwright install chromium")
    return sync_playwright


def _expired(page, login_path):
    try:
        pw = page.locator("input[type=password]").count() > 0
    except Exception:
        pw = False
    return is_login_page(page.url, pw, login_path)


def _run_recipe(page, recipe):
    """Bring the surface into the state its tickets live in (open a tab, a
    modal, a period). Labels are then looked up on that one state."""
    for step in recipe or []:
        if "click" in step:
            page.get_by_text(step["click"], exact=True).first.click()
        elif "dblclick" in step:
            page.get_by_text(step["dblclick"], exact=True).first.dblclick()
        elif "wait_for" in step:
            page.get_by_text(step["wait_for"]).first.wait_for(timeout=15000)
        elif "select" in step:
            label, option = step["select"]
            page.get_by_label(label).select_option(label=option)
        elif "wait_ms" in step:
            page.wait_for_timeout(int(step["wait_ms"]))
        page.wait_for_load_state("networkidle")


def _snap(page, out_dir, name):
    path = out_dir / f"{name}.png"
    page.screenshot(path=str(path))
    return {"name": name, "path": str(path), "dom_text": page.inner_text("body")}


def _find(page, label):
    """The first visible element showing `label` -> (css_box, text, occluded)."""
    loc = page.get_by_text(label)
    for i in range(min(loc.count(), 8)):
        el = loc.nth(i)
        if not el.is_visible():
            continue
        el.scroll_into_view_if_needed()
        box = el.bounding_box()
        if not box:
            continue
        occluded = not el.evaluate(
            """(e, b) => { const t = document.elementFromPoint(b.x + b.width / 2,
                                                              b.y + b.height / 2);
                          return !!t && (t === e || e.contains(t) || t.contains(e)); }""",
            box)
        return box, el.inner_text().strip(), occluded
    return None, "", False


def cmd_login(a):
    routes = json.loads(Path(a.routes).read_text())
    demo = routes["demo"]
    d = session_dir(a.session_dir)
    d.mkdir(parents=True, exist_ok=True)
    with _playwright()() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(viewport=VIEWPORT)
        page = ctx.new_page()
        page.goto(demo["base_url"] + demo["login_path"])
        print(f"Sign in as the {a.persona} capture account in the browser window. "
              f"Waiting up to {LOGIN_WAIT_S // 60} minutes…")
        t0 = time.time()
        while time.time() - t0 < LOGIN_WAIT_S:
            page.wait_for_timeout(2000)
            if not _expired(page, demo["login_path"]):
                break
        else:
            emit({"ok": False, "action": "login", "error": "timed out waiting for sign-in"})
            return 1
        out = d / f"{a.persona}.json"
        ctx.storage_state(path=str(out))
        os.chmod(out, 0o600)
        browser.close()
    print(f"saved {out}")
    emit({"ok": True, "action": "login", "persona": a.persona})
    return 0


def check_sessions(routes, d):
    demo = routes["demo"]
    status = {}
    personas = [k for k, v in routes["personas"].items() if not v.get("no_session")]
    todo = [k for k in personas if (d / f"{k}.json").exists()]
    for k in personas:
        status[k] = "missing"
    if not todo:
        return status
    with _playwright()() as p:
        browser = p.chromium.launch()
        for k in todo:
            ctx = browser.new_context(storage_state=str(d / f"{k}.json"), viewport=VIEWPORT)
            page = ctx.new_page()
            try:
                page.goto(demo["base_url"] + demo["home"].get(k, "/"), timeout=30000)
                status[k] = "expired" if _expired(page, demo["login_path"]) else "valid"
            except Exception as e:
                status[k] = f"error: {type(e).__name__}"
            ctx.close()
        browser.close()
    return status


def cmd_check(a):
    routes = json.loads(Path(a.routes).read_text())
    status = check_sessions(routes, session_dir(a.session_dir))
    if a.out:
        Path(a.out).write_text(json.dumps(status, indent=1))
    print(" · ".join(f"{k}: {v}" for k, v in status.items()))
    emit({"ok": True, "action": "check", **status})
    return 0


def cmd_run(a):
    plan = json.loads(Path(a.plan).read_text())
    inputs = json.loads(Path(a.inputs).read_text())
    routes = json.loads(Path(a.routes).read_text())
    demo = routes["demo"]
    d = session_dir(a.session_dir)
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "shots.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"shots": []}
    use_ocr = ocr_available() and not a.no_ocr
    if not use_ocr:
        print("OCR unavailable — anchors will be dom_box (draft-only).")
    from PIL import Image

    done, visits, left_reason = set(), 0, None
    n0 = len(manifest["shots"])
    by_persona = {}
    for c in plan["capture_plan"]:
        by_persona.setdefault(c["persona"], []).append(c)

    with _playwright()() as p:
        browser = p.chromium.launch()
        for persona, visits_for in by_persona.items():
            state = d / f"{persona}.json"
            if not state.exists():
                print(f"no session for {persona}: run `kb_capture.py login --persona {persona}`")
                continue
            ctx = browser.new_context(storage_state=str(state), viewport=VIEWPORT,
                                      device_scale_factor=SCALE)
            page = ctx.new_page()
            try:
                for c in visits_for:
                    if not c.get("url"):
                        continue
                    page.goto(demo["base_url"] + c["url"], wait_until="networkidle", timeout=45000)
                    if _expired(page, demo["login_path"]):
                        raise SessionExpired(persona)
                    base = f"{persona}-{_slug(c['surface'])}"
                    states = []
                    try:
                        _run_recipe(page, c.get("recipe"))
                    except Exception as e:
                        print(f"  recipe failed on {c['surface']}: {type(e).__name__}")
                    states.append(_snap(page, out_dir, base))
                    visits += 1
                    for st in states:
                        st["tenant_dom"] = Q.screen_text(
                            st["dom_text"], demo["domain"], allow_strings=demo["allow_strings"])["verdict"]
                        st["tenant_ocr"] = (Q.screen_frame(st["path"], demo["domain"],
                                                           allow_strings=demo["allow_strings"])["verdict"]
                                            if use_ocr else None)
                    for tid in c["tickets"]:
                        labels = (inputs.get(tid) or {}).get("labels", [])[:MAX_LABELS]
                        for label in labels:
                            box, text, occluded = _find(page, label)
                            if not box:
                                continue
                            st = states[-1]
                            img_box = to_image_box(box)
                            crop = None
                            if use_ocr:
                                with Image.open(st["path"]) as im:
                                    crop = Q.verify_crop(im, img_box, [label])[0]
                            ev = shot_evidence(label, text, img_box, crop, occluded,
                                               st["tenant_dom"], st["tenant_ocr"])
                            manifest["shots"].append({
                                "shot_id": f"s{len(manifest['shots']) + 1}", "ticket": tid,
                                "persona": persona, "surface": c["surface"],
                                "frame": st["path"], "verified": ev is not None,
                                "source": f"live:{demo['base_url'].split('//')[-1]}{c['url']} "
                                          f"{_dt.datetime.now().isoformat(timespec='minutes')}",
                                **(ev or {"label": label})})
                    done.add((persona, c["surface"]))
            except SessionExpired:
                left_reason = persona
                ctx.close()
                break
            ctx.close()
        browser.close()

    manifest_path.write_text(json.dumps(manifest, indent=1))
    new = manifest["shots"][n0:]
    verified = sum(1 for s in new if s["verified"])
    if left_reason:
        rp = resume_plan(plan, done)
        (out_dir / "resume.json").write_text(json.dumps(rp, indent=1))
        left = sorted({c["surface"] for c in rp["capture_plan"]})
        print(f"SESSION EXPIRED for {left_reason}. Kept {len(new)} shots; {len(left)} surfaces left. "
              f"Sign in: kb_capture.py login --persona {left_reason}; "
              f"then: kb_capture.py run --plan {out_dir / 'resume.json'} …")
        emit({"ok": False, "action": "run", "shots": len(new), "verified": verified,
              "visits": visits, "left": len(left), "expired": left_reason})
        return 3
    print(f"captured {visits} surfaces · {len(new)} candidate shots · {verified} verified")
    emit({"ok": True, "action": "run", "shots": len(new), "verified": verified,
          "visits": visits, "left": 0, "ocr": use_ocr})
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("login"); s.add_argument("--persona", required=True,
                                                choices=("admin", "faculty", "learner"))
    s.add_argument("--routes", default=str(DEFAULT_ROUTES)); s.add_argument("--session-dir")
    s = sub.add_parser("check"); s.add_argument("--routes", default=str(DEFAULT_ROUTES))
    s.add_argument("--session-dir"); s.add_argument("--out")
    s = sub.add_parser("run"); s.add_argument("--plan", required=True)
    s.add_argument("--inputs", required=True); s.add_argument("--out-dir", required=True)
    s.add_argument("--routes", default=str(DEFAULT_ROUTES)); s.add_argument("--session-dir")
    s.add_argument("--no-ocr", action="store_true")
    a = ap.parse_args(argv)
    try:
        return {"login": cmd_login, "check": cmd_check, "run": cmd_run}[a.cmd](a)
    except (OSError, json.JSONDecodeError, KeyError) as e:
        emit({"ok": False, "action": a.cmd, "error": f"{type(e).__name__}: {e}"})
        return 1


if __name__ == "__main__":
    sys.exit(main())
