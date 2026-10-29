#!/usr/bin/env python3
"""
kb_capture.py — screenshot capture on the demo tenant.

CURRENT PATH (Claude in Chrome, the owner's own signed-in Chrome):

  plan-calls --plan groups.json --inputs inputs.json --out-dir $RUN/shots
             [--per-batch 3] [--routes kb_routes.json]
             [--data-plan data_plan.json] [--external-link URL]
          Writes calls.json (install action, visits, batches of browser_batch
          actions, restore) and capture.md (the only input the capturer reads).
          Each visit is recorded with gif_creator (no screenshot); calls.json also
          carries `canary`, `abort`, `gif_dir` and a `gif` filename per visit.
          A capture_plan entry with "clicks" ([{"text"} | {"row": true}]) gets one
          extra visit per click ("<entry>-s0", "-s1", ...): kbclick (the click
          guard installed with the probe) picks the point, the capturer clicks
          it, then the usual settle/probe/export. A click batch has `pick` and
          `act` (with "$X","$Y"). "write_gated" becomes a needs_write screen.
          {"row": true} takes the first link row or clickable <tr> (0.27.0).
          Per-step labels: start screen = page label + first click text, click
          screen = the next click's text, last screen = the ticket's labels.
          calls.json["accounts"] groups batches by sign-in (main: admin+faculty,
          learner, external); the first batch of an account with a "signin"
          carries needs_signin. --external-link sets the external entries' start
          (path+query, run only). --data-plan (approved) adds "write_visits"
          (<entry>-w<k>), outside every batch; they replace needs_write.
          kbclick also takes {"text", "write": [texts]} (write mode, data agent).
          0.27.0 page script: the probe, kbclick and kbopt install a network write
          guard first (only GET leaves the page; blocks counted in window.__kbBlocked,
          reported as probe `wb`); Vue pages drop button_no_toggle/anchor_no_target;
          {"row": true} prefers leaf rows (group rows return "group": true, the
          capturer retries once); the probe's `h` (dialog title / first h1-h2) lets
          ingest keep a click/write screen as a draft-only dom_box; kbopt
          {"option": text} picks a listbox/dropdown option; write mode searches only
          the top-most open dialog and refuses >1 match (ambiguous). After a
          learner/external account a final `restore` batch needs_signin:main.
          0.28.0 page script: the probe also returns `fields` (the visible form controls:
          label, kind, required, type, name[, opts, zz]), `dlg` (the open prompt: title,
          text, button texts), `tw` (the words behind a tenant verdict, <= 5 per kind),
          `wk` (the channel of the last blocked write), `al` / `saved` (the one allowed
          request and its response). kbclick {"choose": true} picks a prompt's safe answer by
          rule; kbclick {"text", "write", "allow": {method, path, dlg, query}} prepares the write
          guard's ONE-SHOT allowance: it is armed only by the owner's real click on the exact
          element kbclick checked (a click anywhere else cancels it), only after the probe saw
          the dialog, never on Save, and then lets exactly one fetch/XHR through - that method
          + path, a query that is empty or the learned one - within ALLOW_TTL_S of the click. ingest keeps
          `fields` / `tw` / `btn` / `found` on each screen record (kb_data.py plan reads
          them), skips labels outside the frame before OCR, and turns a dry-run write item
          (mode dry_run) into needs_write without shots.
  route-check-js --plan groups.json [--routes kb_routes.json]
          Prints one javascript_tool snippet (starts with `await`) for the signed-in tab: fetch()es each
          distinct start URL (and its /administration <-> /vadministration
          alternate; max 20) and returns a JSON string
          [{url, status, final, login, nf}].
  route-fix  --plan groups.json --result <json file> --out groups.json
          A start page that is a 404 moves to the first ok alternate (hash kept),
          else the entry gets "skip_reason": "page_not_found" (plan-calls skips it;
          its tickets get that reason in `missing`). A login page: KB_LOGIN
          expired, exit 3, nothing written.
  ingest     --calls calls.json --raw-dir raw/ --out-dir $RUN/shots
             [--downloads-dir D] [--no-ocr] [--routes kb_routes.json]
          Turns the capturer's raw files (probe JSON + exported GIF name) into
          shots.json entries: waits for D/<gif>, COPIES it to shots/gifs/ (Downloads
          is not touched), takes its last frame as frames/<visit_id>.png. shots.json
          also gets `screens` and `missing` ({ticket: {reason, detail}}); the result
          carries `downloads_left`. Write visits (calls["write_visits"]) are read from any
          raw file (raw/data-<ticket>.json); `write_error` or no item -> reason write_failed.
          0.23.0 raw files with a
          screenshot_path still ingest (frames/<visit_id>.jpg). Re-ingest replaces
          by visit_id. Anything left -> resume.json and exit 3
          (download_missing / empty_export / bad_gif / retry / ...).
  cleanup    --downloads-dir D --names-from calls.json
          Deletes the run's GIFs (and canary) from D/kb-capture/ — only after the
          owner approves — and the emptied folders.
  pickup     --downloads-dir D --name kb-capture/<run>/canary.gif [--timeout 20]
          Waits for the canary GIF, checks it decodes, deletes it. Exit 0 ok,
          1 download_missing, 2 empty_export, 3 bad_gif.
  sweep      --downloads-dir D --into DIR
          Moves leftover D/kb-capture/* folders (crashed runs) into DIR. Never
          deletes a file.

LEGACY PATH (Playwright; login/check/run, kept working, not documented):

A human signs in ONCE per persona with `login`; the browser's storage state is
saved and every later run reuses it headless. Nothing here ever types a
password, and the command never runs `login` itself.

  login   --persona admin|faculty|learner [--env-file [.env-kb-login]]
          With --env-file: fills the sign-in form from the credentials file
          (KB_<PERSONA>_EMAIL / KB_<PERSONA>_PASSWORD, or KB_EMAIL / KB_PASSWORD)
          headless. Without it: a visible browser waits for a person to sign
          in. Either way saves <session-dir>/<persona>.json. A person (or their
          scheduler) runs this; /kb-articles never does.
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
    (ingest adds "left_reasons": {"persona/surface": reason})

wait_for_file is vendored from medtrics-qa-automation 0.17.1
(skills/qa-screenshot-capture/scripts/poll_download.py) by Charles.
"""

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import secrets
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
    # kb_doctor.py --setup downloads Chromium into <home>/.kb-env/ms-playwright.
    if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        local = Path(os.environ.get("KB_HOME") or os.getcwd()) / ".kb-env" / "ms-playwright"
        if local.exists():
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(local)
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


LOGIN_FILE = ".env-kb-login"


def read_login_file(path):
    """KEY=value pairs from the credentials file; {} when it is missing.
    Values are never printed."""
    out = {}
    p = Path(path)
    if not p.exists():
        return out
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip().removeprefix("export ").strip()
        out[k] = v.strip().strip("'\"")
    return out


def creds_for(persona, kv):
    """(email, password) for a persona: KB_<PERSONA>_EMAIL / _PASSWORD, else
    the shared KB_EMAIL / KB_PASSWORD. (None, None) when either is missing."""
    p = persona.upper()
    email = kv.get(f"KB_{p}_EMAIL") or kv.get("KB_EMAIL")
    password = kv.get(f"KB_{p}_PASSWORD") or kv.get("KB_PASSWORD")
    return (email, password) if email and password else (None, None)


def _fill_login(page, email, password):
    """The sign-in form on the capture site: #user_email, #user_password,
    optional #stay_logged, then the Sign In button."""
    page.fill("#user_email", email)
    page.fill("#user_password", password)
    if page.locator("#stay_logged").count():
        page.check("#stay_logged")
    page.get_by_role("button", name="Sign In").click()


def cmd_login(a):
    """Save a persona's session. With --env-file the form is filled from the
    credentials file (headless unless --headed); without it a visible browser
    waits for a person to sign in. Run by a person or their scheduler —
    /kb-articles never runs it."""
    routes = json.loads(Path(a.routes).read_text())
    demo = routes["demo"]
    d = session_dir(a.session_dir)
    d.mkdir(parents=True, exist_ok=True)
    email = password = None
    if a.env_file:
        kv = read_login_file(a.env_file)
        if not kv:
            emit({"ok": False, "action": "login", "issue": "login_file_missing",
                  "error": f"{a.env_file} not found or empty — add KB_{a.persona.upper()}_EMAIL "
                           f"and KB_{a.persona.upper()}_PASSWORD (or KB_EMAIL / KB_PASSWORD)"})
            return 1
        email, password = creds_for(a.persona, kv)
        if not email:
            emit({"ok": False, "action": "login", "issue": "login_creds_missing",
                  "error": f"no credentials for {a.persona} in {a.env_file}"})
            return 1
    with _playwright()() as p:
        browser = p.chromium.launch(headless=bool(email) and not a.headed)
        ctx = browser.new_context(viewport=VIEWPORT)
        page = ctx.new_page()
        page.goto(demo["base_url"] + demo["login_path"])
        if email:
            _fill_login(page, email, password)
            wait_s = 60
        else:
            print(f"Sign in as the {a.persona} capture account in the browser window. "
                  f"Waiting up to {LOGIN_WAIT_S // 60} minutes…")
            wait_s = LOGIN_WAIT_S
        t0 = time.time()
        while time.time() - t0 < wait_s:
            page.wait_for_timeout(2000)
            if not _expired(page, demo["login_path"]):
                break
        else:
            browser.close()
            emit({"ok": False, "action": "login", "issue": "login_failed",
                  "error": "still on the sign-in page — check the credentials"
                           if email else "timed out waiting for sign-in"})
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


# ── Chrome path: plan-calls / ingest ─────────────────────────────────────────
PROBE_VERSION = 1
KEY = "kbp"
PERSONA_ENTRY_WAIT_S = 2
DEFAULT_SETTLE_S = 4
MAX_WAIT_S = 10
CANARY_WAIT_S = 3
EXPORT_OPTS = {"showWatermark": False, "showProgressBar": False, "showActionLabels": False,
               "showClickIndicators": False, "showDragPaths": False, "quality": 1}
DL_ROOT = "kb-capture"
FIRST_FILE_TIMEOUT_S = 20.0
NEXT_FILE_TIMEOUT_S = 5.0
MAX_ASPECT_DRIFT = 0.02
STATUSES = ("ok", "expired", "wrong_persona", "bridge_down", "no_probe")


def tenant_regexes():
    """The tenant-screen patterns exactly as qa_gif_to_shots defines them
    (one source of truth): name -> [pattern source, JS flags]."""
    def fl(rx):
        return "i" if rx.flags & re.I else ""
    return {"inst": [Q.INSTITUTION_RE.pattern, fl(Q.INSTITUTION_RE)],
            "disc": [Q.DISCIPLINE_RE.pattern, fl(Q.DISCIPLINE_RE)],
            "slug": [Q.SLUG_RE.pattern, fl(Q.SLUG_RE)],
            "email": [Q.EMAIL_RE.pattern, fl(Q.EMAIL_RE)],
            "course": [Q.COURSE_CODE_RE.pattern, fl(Q.COURSE_CODE_RE)]}


# The app's 404 page ("Oh No! Looks like we are a little lost ... Error 404").
# One source for the probe, the route-check snippet and the Python check.
NF_SOURCE = r"Error 404|page that does not exist|Page not found"
NF_RE = re.compile(NF_SOURCE, re.I)


def is_not_found(text):
    return bool(NF_RE.search(str(text or "")))

# ── network write guard (0.27.0, one-shot allowance 0.28.0) ──────────────────
# Installed by the probe, kbclick and kbopt on EVERY call. Nothing may reach the
# server with a method other than GET: fetch is rejected, XHR.send throws,
# sendBeacon returns false, form.submit()/requestSubmit() do nothing and a
# capture-phase submit listener cancels any non-GET form. Each block counts in
# window.__kbBlocked (last "METHOD /path" in window.__kbLast, its channel -
# fetch / xhr / form / beacon / ws - in window.__kbLastSrc). Idempotent: every
# wrapper carries a __kb mark, so a second install (each stub call runs it, SPA
# route changes keep the window) wraps nothing twice; a full navigation gives a
# fresh window that the next call patches again.
# Also: an XHR that was opened before the install (no recorded method) is blocked on
# send (fail closed); WebSocket.prototype.send throws; the guard installs into every
# same-origin iframe (window.frames, re-installed on the frame's load, and a
# MutationObserver for frames added later). Not covered: a fetch reference page code
# saved before the install (the legacy app uses axios / jQuery over XHR).
# Every blocked non-GET is also logged, with a sequence number and a timestamp, in
# window.__kbLog (the last 20: {n, m, p, q, s, t}; q is the query string); the last one's
# query is window.__kbLastQ. The probe returns the log as `bl`.
# The ONE-SHOT ALLOWANCE (save mode only) lives INSIDE the guard's own wrappers: the guard keeps
# the raw fetch / XHR.send, the allowance state and each XHR's method + resolved URL (a WeakMap)
# in one closure, and no readable property holds a raw function. kbclick arms it through the
# non-enumerable `__kbA(token, spec)` on the wrapper (token baked into the page scripts by
# install_action); `__kbA(token, null)` disarms. Clock, URL and the other builtins are captured
# at install. In-page code cannot be made airtight against deliberately hostile same-page
# script (see skills/kb-capture/SKILL.md Limits).
_GUARD_JS = r"""
(function G(W){
var D=W.document;
var now=Date.now.bind(Date),U=W.URL,OA=Object.assign,DP=Object.defineProperty,OD=Object.getOwnPropertyDescriptor,WM=WeakMap;
var CT=W.Node&&W.Node.prototype&&W.Node.prototype.contains;
W.__kbBlocked=W.__kbBlocked||0;
var P=function(u){try{return new U(String(u),(D&&D.baseURI)||W.location.href);}catch(x){return null;}};
var note=function(m,u,s){W.__kbBlocked=(W.__kbBlocked||0)+1;var q=P(u),p=q?q.pathname:String(u||'').slice(0,60),qs=q?q.search:'';W.__kbLast=String(m).trim().toUpperCase()+' '+p;W.__kbLastQ=qs;W.__kbLastSrc=s||'';var L=W.__kbLog;if(!Array.isArray(L))L=W.__kbLog=[];L.push({n:W.__kbBlocked,m:String(m).trim().toUpperCase(),p:p,q:qs,s:s||'',t:now()});while(L.length>20)L.shift();};
var nm=function(m){return String(m||'GET').trim().toUpperCase();};
var get=function(m){return nm(m)==='GET';};
var fm=function(f,s){return String((s&&s.formMethod)||(f&&f.method)||'get').trim().toLowerCase();};
var TOK='__TOK__',cur=null,XM=new WM();
var has=function(n,c){try{return CT?CT.call(n,c):n.contains(c);}catch(x){return false;}};
var drop=function(ly){try{D.removeEventListener('click',ly.l,true);}catch(x){}};
var arm=function(tok,sp){
if(tok!==TOK)return false;
if(cur){drop(cur);cur=null;}
if(!sp)return true;
var tp=false;try{tp=!W.top||W.top===W;}catch(x){}
var mt=String(sp.mt),pth=String(sp.pth),qs=String(sp.qs);
if(!tp||!sp.el||(!CT&&typeof sp.el.contains!=='function')||!(mt==='POST'||mt==='PUT'||mt==='PATCH')||!/^\/[^?#\s]*$/.test(pth)||!/^(\?[^#\s]*)?$/.test(qs))return false;
var ly={a:false,u:false,t:0,p:now()+60000,el:sp.el,mt:mt,pth:pth,qs:qs,ttl:Math.min(Math.max(Number(sp.ttl)||0,0),15000)};
ly.l=function(e){if(cur!==ly||ly.a||ly.u||e.isTrusted===false)return;drop(ly);if(now()>ly.p)return;var t=e.target;if(t&&(t===ly.el||has(ly.el,t))){ly.a=true;ly.t=now()+ly.ttl;}};
cur=ly;D.addEventListener('click',ly.l,true);return true;};
var take=function(m,u){var ly=cur;if(!ly||!ly.a||ly.u||now()>ly.t||m!==ly.mt)return null;var q=P(u);if(!q||q.origin!==W.location.origin||q.pathname!==ly.pth||(q.search!==''&&q.search!==ly.qs))return null;ly.u=true;(W.__kbAllowed=W.__kbAllowed||[]).push(ly.mt+' '+ly.pth);return ly;};
var idOf=function(j){try{var v=j&&(j.id!==undefined?j.id:(j.data&&j.data.id));return (typeof v==='string'||typeof v==='number')?String(v).slice(0,40):'';}catch(x){return '';}};
var sv=function(ly,u,st,id){var q=P(u);W.__kbSaved={m:ly.mt,p:q?q.pathname:'',s:st,id:id||''};};
var F=W.fetch,RQ=W.Request,RM=null,RU=null;
try{RM=OD(RQ.prototype,'method').get;RU=OD(RQ.prototype,'url').get;}catch(x){RM=null;RU=null;}
if(typeof F==='function'&&!F.__kb){var nf=function(i,o){
var isR=false;try{isR=!!RM&&!!RU&&i instanceof RQ;}catch(x){}
var o2=(o&&typeof o==='object')?OA({},o):o;
var mo=o2&&o2.method,mi,u,i2=i;
if(isR){mi=RM.call(i);u=RU.call(i);}
else if(i&&typeof i==='object'&&!(U&&i instanceof U)){var iu=i.url;mi=i.method;u=String(iu||i);i2=iu?{url:u,method:mi}:u;}
else{u=String(i);i2=u;}
var ms=String(mo||mi||'GET');
if(mo)o2.method=ms;
if(get(ms))return F.call(W,i2,o2);
var ly=take(nm(ms),u);
if(!ly){note(ms,u,'fetch');return Promise.reject(new TypeError('KB_WRITE_BLOCKED'));}
o2=OA({},o2||{});o2.method=ms;
var pr=F.call(W,i2,o2);
try{pr.then(function(r){try{sv(ly,u,r.status,'');r.clone().json().then(function(j){if(W.__kbSaved)W.__kbSaved.id=idOf(j);}).catch(function(){});}catch(x){}},function(){sv(ly,u,0,'');});}catch(x){}
return pr;};nf.__kb=1;try{DP(nf,'__kbA',{value:arm});}catch(x){}W.fetch=nf;}
var X=W.XMLHttpRequest&&W.XMLHttpRequest.prototype;
if(X&&X.open&&!X.open.__kb){var xo=X.open,xs=X.send,xa=X.addEventListener;X.open=function(m,u){XM.delete(this);var r=xo.apply(this,arguments);var q=P(u);XM.set(this,{m:String(m),u:q?q.href:String(u)});return r;};X.open.__kb=1;
X.send=function(){var r=XM.get(this);if(r&&get(r.m))return xs.apply(this,arguments);
if(r){var ly=take(nm(r.m),r.u);if(ly){var x=this;try{xa.call(x,'loadend',function(){var id='';try{id=idOf(JSON.parse(x.responseText));}catch(e){}sv(ly,r.u,x.status,id);});}catch(e){}return xs.apply(this,arguments);}}
note(r?r.m:'OPEN?',r&&r.u,'xhr');throw new Error('KB_WRITE_BLOCKED');};X.send.__kb=1;try{DP(X.send,'__kbA',{value:arm});}catch(x){}}
var N=W.navigator;
if(N&&typeof N.sendBeacon==='function'&&!N.sendBeacon.__kb){var nb=function(u){note('POST',u,'beacon');return false;};nb.__kb=1;try{N.sendBeacon=nb;}catch(x){}}
var H=W.HTMLFormElement&&W.HTMLFormElement.prototype;
if(H&&H.submit&&!H.submit.__kb){var hs=H.submit,hr=H.requestSubmit;H.submit=function(){if(fm(this)!=='get'){note(fm(this),this.action,'form');return;}return hs.apply(this,arguments);};H.submit.__kb=1;
if(hr){H.requestSubmit=function(s){if(fm(this,s)!=='get'){note(fm(this,s),this.action,'form');return;}return hr.apply(this,arguments);};H.requestSubmit.__kb=1;}}
var S=W.WebSocket&&W.WebSocket.prototype;
if(S&&S.send&&!S.send.__kb){S.send=function(){note('WS',this.url,'ws');throw new Error('KB_WRITE_BLOCKED');};S.send.__kb=1;}
if(D&&D.addEventListener&&!D.__kbS){D.__kbS=1;D.addEventListener('submit',function(e){var f=e.target;if(fm(f,e.submitter)!=='get'){e.preventDefault();if(e.stopImmediatePropagation)e.stopImmediatePropagation();note(fm(f,e.submitter),f&&f.action,'form');}},true);}
var hk=function(f){try{if(f.contentWindow&&f.contentWindow.document)G(f.contentWindow);}catch(x){}try{if(!f.__kbL&&f.addEventListener){f.__kbL=1;f.addEventListener('load',function(){try{G(f.contentWindow);}catch(x){}});}}catch(x){}};
var fr=function(n){try{if(n&&n.tagName==='IFRAME')hk(n);else if(n&&n.querySelectorAll)Array.prototype.forEach.call(n.querySelectorAll('iframe'),hk);}catch(x){}};
try{for(var i=0;i<W.frames.length;i++){try{var c=W.frames[i];if(c&&c.document)G(c);}catch(x){}}}catch(x){}
fr(D);
if(D&&!D.__kbM&&W.MutationObserver){D.__kbM=1;try{new W.MutationObserver(function(ms){Array.prototype.forEach.call(ms,function(m){Array.prototype.forEach.call(m.addedNodes||[],fr);});}).observe(D,{childList:true,subtree:true});}catch(x){}}
})(window);
"""


def guard_source():
    """The write guard as one JS statement (an IIFE), lines joined by spaces."""
    return " ".join(l.strip() for l in _GUARD_JS.strip().splitlines()).replace("__TOK__", ARM_TOKEN)


def request_blocked(method=None, request_method=None):
    """Python mirror of the guard's fetch/XHR rule: only GET passes. `method` is
    init.method / the XHR method; `request_method` a Request object's method (used
    when `method` is empty). No method at all = GET."""
    m = method or request_method or "GET"
    return str(m).strip().upper() != "GET"


def form_blocked(form_method=None, submitter_method=None):
    """Python mirror of the guard's form rule: the submitter's formmethod, else the
    form's method, else get; anything but get (post, dialog, ...) is blocked."""
    m = submitter_method or form_method or "get"
    return str(m).strip().lower() != "get"


# Form-control reading, shared by the probe (`fields`) and kb_data's field script
# (so a label the probe reported is a label the fill script finds). Plain JS like the
# probe; needs `D` (document) in scope; defines kfList(root, sideSelector) ->
# [{e, d}] with d = {label, kind, required, type, name[, opts, zz]}.
_FIELDS_JS = r"""
var kfN=function(s){return String(s||'').replace(/\s+/g,' ').replace(/[\s*:]+$/,'').trim();};
var kfV=function(e){var r=e.getBoundingClientRect();return r.width>0&&r.height>0;};
var kfT=function(e){return e?String(e.innerText||e.textContent||''):'';};
var kfWG='.multiselect,.vs__dropdown-toggle,[role=combobox]';
var kfG=function(e,n){return e.getAttribute?e.getAttribute(n):null;};
var kfRaw=function(e){var t='',ls=e.labels;if(ls&&ls.length)t=kfT(ls[0]);if(kfN(t).length>60)t='';if(!kfN(t)&&e.id&&D.querySelector){try{var l2=D.querySelector('label[for="'+String(e.id).replace(/"/g,'')+'"]');if(l2)t=kfT(l2);}catch(x){}}if(!kfN(t)){var lb=kfG(e,'aria-labelledby');if(lb&&D.getElementById)t=lb.split(/\s+/).map(function(i){return kfT(D.getElementById(i));}).join(' ');}if(!kfN(t))t=kfG(e,'aria-label')||'';if(!kfN(t))t=kfG(e,'placeholder')||'';if(!kfN(t)){var p=e;for(var k=0;k<3&&p&&!kfN(t);k++,p=p.parentElement){var sb=p.previousElementSibling;if(sb){var st=kfT(sb);if(kfN(st)&&kfN(st).length<=60)t=st;}}}return t;};
var kfKind=function(e){var tg=String(e.tagName||'').toLowerCase(),ty=String(e.type||kfG(e,'type')||'').toLowerCase();if(tg==='select')return e.multiple?'multiselect':'select';if(tg==='textarea')return 'textarea';if(tg!=='input')return 'other';if(ty==='date'||ty==='datetime-local')return 'date';if(ty==='time')return 'time';if(ty==='checkbox')return 'checkbox';if(ty==='radio')return 'radio';if(/^(text|search|number|email|tel|url|)$/.test(ty))return 'text';return 'other';};
var kfReq=function(e,raw){var l0=e.labels&&e.labels[0];return !!(e.required||kfG(e,'aria-required')==='true'||kfG(e,'required')!==null||/\*/.test(raw)||/(^|\s)required(\s|$)/.test(String(e.className||''))||(l0&&/(^|\s)required(\s|$)/.test(String(l0.className||''))));};
var kfList=function(root,side){
var inS=function(e){try{return !!(side&&e.closest(side));}catch(x){return false;}};
var out=[];
var add=function(e,k){var raw=kfRaw(e),d={label:kfN(raw),kind:k,required:kfReq(e,raw),type:String(e.type||e.tagName||'').toLowerCase().slice(0,20),name:String(kfG(e,'name')||e.id||'').slice(0,60)};
if(k==='select'){var os=Array.prototype.slice.call(e.options||[]).map(function(o){return kfN(o.text).slice(0,40);});d.opts=os.slice(0,12);var zz=os.filter(function(o){return /^zz demo/i.test(o);})[0];if(zz)d.zz=zz;}
if(out.some(function(x){return x.d.label===d.label&&x.d.kind===d.kind&&x.d.name===d.name&&x.e===e;}))return;out.push({e:e,d:d});};
var ws=Array.prototype.slice.call(root.querySelectorAll(kfWG)).filter(function(e){return kfV(e)&&!inS(e);});
var rt=kfT(root===D?D.body:root);
var dl=(/available options/i.test(rt)&&/selected options/i.test(rt))?Array.prototype.slice.call(root.querySelectorAll('div,section,fieldset')).filter(function(e){if(!kfV(e)||inS(e))return false;var t=kfT(e);return /available options/i.test(t)&&/selected options/i.test(t)&&t.length<4000;}):[];
dl=dl.filter(function(e){return !dl.some(function(o){return o!==e&&e.contains(o);});});
var cs=Array.prototype.slice.call(root.querySelectorAll('input,textarea,select')).filter(function(e){var ty=String(e.type||'').toLowerCase();return kfV(e)&&!inS(e)&&!/^(hidden|password|file|submit|button|reset|image)$/.test(ty);});
cs.forEach(function(e){if(ws.some(function(w){return w!==e&&w.contains(e);})||dl.some(function(w){return w.contains(e);}))return;add(e,kfKind(e));});
ws.forEach(function(e){add(e,'multiselect');});
dl.forEach(function(e){add(e,'duallist');});
var pos=function(x){var r=x.e.getBoundingClientRect();return Math.round(r.top/6)*100000+r.left;};
out.sort(function(x,y){return pos(x)-pos(y);});
return out.slice(0,40);
};
"""


def fields_source():
    """The form-control reader as JS statements (defines kfList); needs `D` in scope."""
    return " ".join(l.strip() for l in _FIELDS_JS.strip().splitlines())


# Plain JS, no comments, every statement ends with ';' (lines are joined with
# spaces). __TENANT__ and __NONLATIN__ are filled from the Python side.
_PROBE_JS = r"""
(function(a){
var D=document,W=window;
__GUARD__
__FIELDS__
var norm=function(s){return String(s||'').replace(/\s+/g,' ').trim();};
if(location.pathname.indexOf(a.login)>=0||D.querySelector('input[type=password]'))throw new Error('KB_LOGIN');
var pl=Array.prototype.slice.call(D.querySelectorAll('a[href*="portal_change"]'));
if(a.portal&&pl.length){
var CL=/active|btn-danger|btn-primary|btn-warning|selected/;
var pick=function(f){var c=pl.filter(f);return c.length&&(c.length<pl.length||pl.length===1)?c:null;};
var act=pick(function(e){return CL.test(String(e.className||''));})||pick(function(e){var m=String(W.getComputedStyle(e).backgroundColor).match(/[\d.]+/g)||[];var r=+m[0],g=+m[1],b=+m[2],al=m.length>3?+m[3]:1;return al>0&&!(r===g&&g===b);});
if(act&&!act.some(function(e){return norm(e.innerText||e.textContent).toLowerCase()===String(a.portal).toLowerCase();}))throw new Error('KB_PERSONA');
}
var NFR=new RegExp(__NF__,'i');
var hd=D.querySelector('h1,h2');
var nf=NFR.test(String(D.title||'')+' '+norm(hd&&hd.innerText)+' '+norm(D.body.innerText).slice(0,1500));
var all=Array.prototype.slice.call(D.body.querySelectorAll('*'));
var vis=function(e){var r=e.getBoundingClientRect();return r.width>0&&r.height>0;};
var inSb=function(e){try{return !!e.closest(a.side);}catch(x){return false;}};
var iw=W.innerWidth,ih=W.innerHeight;
var rd=function(n){return Math.round(n*10)/10;};
var hb=function(e){var r=e.getBoundingClientRect();return {t:norm(e.innerText),b:[rd(r.left),rd(r.top),rd(r.width),rd(r.height)]};};
var hv=function(l){return l.filter(function(e){return vis(e)&&norm(e.innerText)&&!inSb(e);});};
var dt=hv(Array.prototype.slice.call(D.querySelectorAll('.modal-title,[role=dialog] h1,[role=dialog] h2,[role=dialog] h3,.swal2-title')));
var ht=dt.length?dt[dt.length-1]:hv(Array.prototype.slice.call(D.querySelectorAll('h1,h2')))[0];
var dls=Array.prototype.slice.call(D.querySelectorAll(__DLG__)).filter(vis);
var dlg=dls.length?dls[dls.length-1]:null;
var bt=[];
if(dlg){Array.prototype.forEach.call(dlg.querySelectorAll('button,[role=button],input[type=button],input[type=submit],a.btn'),function(e){if(!vis(e))return;var t=norm(e.innerText||e.value||e.textContent).slice(0,40);if(t&&bt.indexOf(t)<0&&bt.length<8)bt.push(t);});}
var dx=dlg?norm(dlg.innerText):'';
W.__kbSeen=dlg?(norm(dt.length?dt[dt.length-1].innerText:'')+' '+dx).toLowerCase().slice(0,600):'';
var fl=kfList(dlg||D,a.side).map(function(x){return x.d;});
var labels=a.labels.map(function(l){
var want=norm(l);
var m=all.filter(function(e){return String(e.textContent||'').indexOf(l)>=0&&norm(e.innerText)===want&&vis(e);});
m=m.filter(function(e){return !m.some(function(o){return o!==e&&e.contains(o);});});
var ns=m.filter(function(e){return !inSb(e);});
var e=ns[0]||m[0];
if(!e)return {l:l,f:false,t:'',b:null,iv:false,oc:false,sb:false};
var r=e.getBoundingClientRect();
var t=D.elementFromPoint(r.left+r.width/2,r.top+r.height/2);
return {l:l,f:true,t:norm(e.innerText),b:[rd(r.left),rd(r.top),rd(r.width),rd(r.height)],iv:r.left>=0&&r.top>=0&&r.right<=iw&&r.bottom<=ih,oc:t?!(t===e||e.contains(t)||t.contains(e)):false,sb:inSb(e)};
});
var T=__TENANT__;
var rx=function(k,g){return new RegExp(T[k][0],T[k][1]+(g?'g':''));};
var raw=String(D.body.innerText||'');
a.allow.forEach(function(s){raw=raw.replace(new RegExp(String(s).replace(/[.*+?^${}()|[\]\\]/g,'\\$&'),'gi'),' ');});
var cased=raw,low=raw.toLowerCase(),k=[],hard=false;
var add=function(n,h){if(k.indexOf(n)<0)k.push(n);if(h)hard=true;};
var tw={};
var tws=function(n,w){w=String(w||'').trim().slice(0,40);if(!w)return;var l=tw[n]||(tw[n]=[]);if(l.length<5&&l.indexOf(w)<0)l.push(w);};
var re=rx('email',true),m2;
while((m2=re.exec(low))){if(m2[1].toLowerCase().indexOf(String(a.domain).toLowerCase())<0){add('foreign_email_domain',true);tws('foreign_email_domain','@'+String(m2[1]).replace(/[.\-]+$/,''));}}
var re4=rx('inst',true),m4;
while((m4=re4.exec(low))){add('institution_string',false);tws('institution_string',m4[0]);if(m4[0]==='')re4.lastIndex++;}
var re5=rx('course',true),m5;
while((m5=re5.exec(cased))){add('course_codes',false);tws('course_codes',m5[0]);if(m5[0]==='')re5.lastIndex++;}
var re6=rx('disc',true),m6;
while((m6=re6.exec(low))){add('discipline_word',false);tws('discipline_word',m6[0]);if(m6[0]==='')re6.lastIndex++;}
var re2=rx('slug',true),m3;
var STOP=__SLUG_STOP__; while((m3=re2.exec(low))){if(STOP.indexOf(m3[1])<0){add('tenant_slug',true);tws('tenant_slug',m3[1]);}}
var nl=0;
for(var i=0;i<low.length;i++){if(low.charCodeAt(i)>0x3FF)nl++;}
if(nl>=__NONLATIN__)add('non_latin_script',true);
return {v:a.v,u:location.pathname+location.search,iw:iw,ih:ih,dpr:W.devicePixelRatio,nf:nf,labels:labels,h:ht?hb(ht):null,wb:W.__kbBlocked||0,wl:String(W.__kbLast||''),wq:String(W.__kbLastQ||''),wk:String(W.__kbLastSrc||''),bl:(Array.isArray(W.__kbLog)?W.__kbLog:[]).slice(-20),al:W.__kbAllowed||[],saved:W.__kbSaved||null,fields:fl,dlg:dlg?{t:norm(dt.length?dt[dt.length-1].innerText:'').slice(0,80),x:dx.slice(0,160),bt:bt}:null,tn:{v:hard?'BLOCK':(k.length?'REVIEW':'CLEAR'),k:k},tw:tw};
})
"""


# ── click guard (kbclick): refusal rules, Python mirror + the JS built from them ─
# The same constants feed both, so tests of click_refusal() pin what the page runs.
WRITE_VERBS = ("save", "submit", "send", "confirm", "delete", "remove", "archive",
               "publish", "approve", "reject", "sign", "notify", "invite", "resend", "copy",
               "duplicate", "remind", "clone", "reset", "unpublish", "reactivate", "enroll",
               "unenroll")
WORD_START = r"(?:^|[^a-z])(?:un|re)?"       # word start; "resend", "unpublish", "reactivate" match
CLICK_RX = {
    "write": WORD_START + r"(?:" + "|".join(WRITE_VERBS) + r")",     # prefix match: "signed", "sending"
    "cls": r"btn-danger|text-danger|delete|remove",
    "attr": (r"delete|remove|destroy|toggle|archive|portal_change|logout|signout|"
             + WORD_START + r"(?:approve|reject|publish|activate|deactivate|enable|disable|"
             r"impersonate|login[-_]?as|switch|reset|unlock|cancel|withdraw|send|sign|"
             r"submit|confirm|save|notify|invite|resend|copy|duplicate|remind|clone|"
             r"unpublish|reactivate|enroll|unenroll)"),   # word-start: "assign" stays allowed
    "tok": r"^(?:js-.+|ajax.*|post)$",                     # class tokens of script-driven links
    "dat": r"^data-(?:method|remote|ajax)$",
    "tog": r"^(?:modal|tab|collapse|dropdown)$",
    "wd": r"\b(?:delete|remove|archive|destroy|invite|publish|approve)",   # write mode: always dangerous
    "sn": r"\b(?:send|notify)",                            # write mode: dangerous unless negated
    "neg": r"(?:\bdon['\u2019]?t|\bdo not|\bnot)[\s,]*$",       # ... by don't / do not / not just before it
    "icon": r"triangle|expand|icon|caret|chevron",         # row cell parts that are never the click
    "ctl": r"^(?:button|a|i|svg|input)$",
    "grp": r"(?:^|\s)(?:group|expandable|parent)(?:-(?:row|header|head))?(?:\s|$)",   # a row class that marks a group header
    # prompt answers (dry-run "choose" mode, and kb_data's choose rule): a negated answer
    # first ("No, don't send notifications", "Don't send"), a bare "No", then Cancel/Close/Skip;
    # yes / save / submit / confirm / continue / ok / proceed anywhere in the text refuse it.
    "cn": r"^(?:no[,:]?\s+)?(?:don['\u2019]?t|do not)\b",
    "cc": r"^(?:cancel|close|skip)\b",
    "no": r"^no[.!]?$",
    "cb": r"\b(?:yes|save|submit|confirm|continue|ok|proceed)",
}
ALLOW_TTL_S = 10          # a one-shot allowance lives this long after kbclick armed it
ALLOW_METHODS = ("POST", "PUT", "PATCH")
SAVE_WORDS_RE = re.compile(r"^(?:save|create|add|submit)$", re.I)     # a Save button never arms an allowance
# Selectors shared by the JS and the docs. A row is a group when it holds a
# GROUP_SEL element or a class matched by CLICK_RX["grp"].
GROUP_SEL = ".expand,.triangle,[aria-expanded]"
DIALOG_SEL = ".modal.show,[role=dialog],.swal2-popup,dialog[open]"
OPTION_SEL = ("[role=listbox] [role=option],.multiselect__content li,.dropdown-menu .dropdown-item,"
              ".vs__dropdown-option,ul[role=listbox] li")
_CLICK_RE = {k: re.compile(v, re.I if k != "tog" else 0) for k, v in CLICK_RX.items()}
KBC_KEY = "kbc"
KBO_KEY = "kbo"
KBC_ERRORS = {"KB_CLICK_REFUSED": "click_refused", "KB_CLICK_NOTFOUND": "click_not_found",
              "KB_TENANT_ROW": "tenant_row"}


def _write_ok(el, write):
    """Write mode: the element's visible text equals one of the allowed texts
    (case-insensitive, trimmed, exact)."""
    t = " ".join(str(el.get("text") or "").split()).lower()
    return bool(t) and t in [" ".join(str(w).split()).lower() for w in (write or [])]


def write_danger(text):
    """Write mode / kbopt: True when the words say delete/remove/archive/destroy/invite/
    publish/approve anywhere, or send/notify not directly preceded by don't / do not /
    not (a bare "No" no longer excuses them: "No, send notifications" is refused)."""
    t = " ".join(str(text or "").split())
    if _CLICK_RE["wd"].search(t):
        return True
    return any(not _CLICK_RE["neg"].search(t[:m.start()]) for m in _CLICK_RE["sn"].finditer(t))


def choose_rank(text):
    """Dry-run "choose" mode (kbclick {"choose": true}): how good a dialog button is as the
    answer, 0 best, or None when it must never be clicked. 0 = a negated answer ("No,
    don't send notifications", "Don't send"), 1 = a bare "No", 2 = Cancel / Close / Skip.
    Anything with yes / save / submit / confirm / continue / ok / proceed in it, or that
    write_danger() flags, is None."""
    t = " ".join(str(text or "").split())
    if not t or _CLICK_RE["cb"].search(t) or write_danger(t):
        return None
    for rank, key in enumerate(("cn", "no", "cc")):
        if _CLICK_RE[key].search(t):
            return rank
    return None


def choose_pick(texts):
    """Python mirror of the choose-mode pick: the index of the best button (lowest
    rank; the first among equals) or None."""
    best = None
    for i, t in enumerate(texts):
        r = choose_rank(t)
        if r is not None and (best is None or r < best[0]):
            best = (r, i)
    return best[1] if best else None


def allow_refusal(allow, el_text, in_dialog, dialog_text, seen, top=True):
    """Why kbclick will NOT arm a one-shot allowance, or "" when it will (Python mirror
    of the JS). allow {"method", "path", "dlg", "query" (optional, "" or "?a=b")}; el_text the clicked button's text;
    in_dialog it sits in the top-most open dialog; dialog_text that dialog's text;
    seen what the last probe recorded of the open dialog (window.__kbSeen); top the
    window is the top window. Only a click on a dialog button after the probe saw the
    expected dialog arms it; the Save button never does."""
    a = allow if isinstance(allow, dict) else {}
    method, path = str(a.get("method") or "").upper(), str(a.get("path") or "")
    ex = " ".join(str(a.get("dlg") or "").split()).lower()
    query = "" if a.get("query") is None else str(a.get("query"))
    if (method not in ALLOW_METHODS or not re.fullmatch(r"/[^?#\s]*", path) or not ex
            or not re.fullmatch(r"(?:\?[^#\s]*)?", query)):
        return "allow_bad_request"
    if not top:
        return "allow_not_top"
    if not dialog_text:
        return "allow_no_dialog"
    if ex not in " ".join(str(dialog_text).split()).lower() or ex not in str(seen or ""):
        return "allow_dialog_not_seen"
    if not in_dialog:
        return "allow_outside_dialog"
    if SAVE_WORDS_RE.match(" ".join(str(el_text or "").split())):
        return "allow_on_save"
    return ""


def allowance_passes(armed, method, url, origin, now, src="fetch", top=True, base=None):
    """Python mirror of the guard's allowance test -> (passes, armed after). armed:
    {"m", "p", "q" (the learned query, "" or "?a=b"), "t" (expiry, same unit as now),
    "used"} or None. The URL is resolved against the page (`base`, default the origin's
    root) exactly as `new URL(url, location.href)` does - so "//evil.test/x" is another
    origin - and passes only when the allowance is armed and unused, unexpired, in the top
    window, fetch/XHR, same origin, with exactly the armed method and path, and a query that is
    empty or exactly the armed one; it is then marked used. Everything else (including a
    second identical request) is blocked and the allowance stays as it was."""
    from urllib.parse import urljoin, urlsplit
    if not armed or armed.get("used") or not top or src not in ("fetch", "xhr") or now > armed.get("t", 0):
        return False, armed
    raw = re.sub(r"[\t\r\n]", "", str(url)).replace("\\", "/")             # what the URL parser drops / folds
    u = urlsplit(urljoin(base or origin + "/", raw))
    if f"{u.scheme}://{u.netloc}" != origin:
        return False, armed
    if str(method).strip().upper() != armed["m"] or u.path != armed["p"]:
        return False, armed
    query = "?" + u.query if u.query else ""
    if query and query != armed.get("q", ""):
        return False, armed
    return True, {**armed, "used": True}


def click_refusal(el, text_click=True, write=None, vue=False):
    """Why kbclick refuses `el`, or "" when the click is allowed (Python mirror of
    the JS). el: {"tag", "attrs": {name: value, incl. data-*, type, href, class, title,
    aria-label, onclick, role}, "text", "visible", "in_sidebar", "in_post_form", "occluded"}.
    text_click is False for a {"row": true} click (sidebar elements refused).
    write: the texts of args.write; an element whose text equals one exactly skips
    the submit / post_form / write_verb / button_no_toggle refusals (never the danger
    ones) and gets write_danger when its words say send/notify/invite/publish/
    approve/delete/remove/archive/destroy ("send" / "notify" pass only right after
    don't / do not / not).
    vue: the page is a Vue app (see is_vue_page); the network write guard makes the
    button_no_toggle and anchor_no_target refusals unnecessary there, so they are
    dropped. Every danger_* / write_verb / submit / post_form refusal stays."""
    at = el.get("attrs") or {}
    g = lambda n: str(at.get(n, "") or "")
    tag = str(el.get("tag") or "").lower()
    ty, href = g("type").lower(), g("href")
    plain_get = tag == "a" and href and not re.match(r"(#|javascript:)", href, re.I)
    wr = text_click and _write_ok(el, write)
    if not el.get("visible", True):
        return "not_visible"
    if el.get("in_sidebar") and not text_click:
        return "sidebar"
    if not text_click and not str(el.get("text") or "").strip():
        return "icon_only"                       # a row link with no words: can't tell what it does
    if ty == "submit" and not wr:
        return "submit"
    if el.get("in_post_form") and not plain_get and not wr:
        return "post_form"
    if "data-href" in at:
        return "data_href"
    if "onclick" in at:
        return "onclick"
    if _CLICK_RE["cls"].search(g("class")):
        return "danger_class"
    if tag == "a" and (any(_CLICK_RE["tok"].search(t) for t in g("class").split())
                       or any(_CLICK_RE["dat"].search(n) for n in at)):
        return "ajax_link"
    if _CLICK_RE["attr"].search(href) or any(
            n.lower().startswith("data-") and _CLICK_RE["attr"].search(str(v or ""))
            for n, v in at.items()):
        return "danger_attr"
    words = (g("title"), g("aria-label"), str(el.get("text") or ""))
    if wr:
        if any(write_danger(t) for t in words):
            return "write_danger"
    elif any(_CLICK_RE["write"].search(t) for t in words):
        return "write_verb"
    ok = bool(_CLICK_RE["tog"].search(g("data-bs-toggle") or g("data-toggle")))
    if (tag == "button" or ty == "button" or g("role") == "button") and not ok and not wr and not vue:
        return "button_no_toggle"
    if tag == "a" and not ok and not vue and (not href or re.match(r"(#|javascript:)", href, re.I)):
        return "anchor_no_target"
    if el.get("occluded"):
        return "occluded"
    return ""


def row_group(row):
    """Python mirror of the group test: the row holds an .expand/.triangle/
    [aria-expanded] element (row["has_expand"]) or its class says group/expandable/parent.
    row["group"] is accepted as a precomputed answer."""
    return bool(row.get("group") or row.get("has_expand")
                or _CLICK_RE["grp"].search(str(row.get("class") or "")))


def row_pick(rows):
    """Python mirror of the row loop -> (element, group). A leaf row (a GET link row,
    or a clickable row that is not a group) wins over any group row; if only group
    rows are visible the first group row's element comes back with group=True (the
    capturer clicks it, then asks for a row again to reach a leaf). (None, False)
    when no row is a candidate."""
    grp = None
    for r in rows:
        e = row_target(r)
        if e is None:
            continue
        if row_group(r) and not r.get("link"):
            grp = grp or e
        else:
            return e, False
    return (grp, True) if grp is not None else (None, False)


def is_vue_page(has_data_v_app=False, has_vue_app_prop=False, has_global=False):
    """Python mirror of the JS Vue test: [data-v-app] present, any element with
    __vue_app__, or window.__VUE__."""
    return bool(has_data_v_app or has_vue_app_prop or has_global)


def option_refusal(text):
    """kbopt: a menu option whose words say send/notify/invite/publish/approve/delete/
    remove/archive/destroy is refused (write_danger); "send" / "notify" pass only right
    after don't / do not / not (the same rule as write mode and kb_data's choose rule)."""
    return "write_danger" if write_danger(text) else ""


def row_target(row):
    """Python mirror of the row pick. row: {"link": el|None (a GET <a href> in the
    row), "class", "cursor", "cell_cursor", "spans": [el], "cells": [el]} with el as
    for click_refusal plus "has_control" / "in_control" (a/button inside or around).
    -> the element kbclick would click, or None when the row is not a candidate.
    A link wins; else a clickable row (class "clickable" or cursor:pointer on the row
    or its first cell) gives its first non-empty text span (else cell) that is no
    icon/control and that click_refusal() allows."""
    if row.get("link"):
        return row["link"]
    clickable = (re.search(r"(?:^|\s)clickable(?:\s|$)", str(row.get("class") or ""))
                 or row.get("cursor") == "pointer" or row.get("cell_cursor") == "pointer")
    if not clickable:
        return None

    def usable(e):
        return (e.get("visible", True) and str(e.get("text") or "").strip()
                and not _CLICK_RE["ctl"].search(str(e.get("tag") or ""))
                and not _CLICK_RE["icon"].search(str((e.get("attrs") or {}).get("class", "")))
                and not e.get("has_control") and not e.get("in_control"))
    cand = [e for e in (row.get("spans") or []) + (row.get("cells") or []) if usable(e)]
    return next((e for e in cand if not click_refusal(e, text_click=False)), cand[0] if cand else None)


# Plain JS like the probe: no comments, every statement ends with ';'.
# kbclick's side of the one-shot allowance: it only ASKS the guard (see _GUARD_JS) to arm one
# request, through the guard wrapper's non-enumerable `__kbA(token, spec)`. The guard does the
# rest in its own closure: it arms only from a capture-phase, trusted click whose target is (or
# is inside) `el` - any other real click cancels it - and then lets exactly ONE fetch / XHR
# through, in the top window: the armed method (POST / PUT / PATCH), the same-origin pathname,
# a query that is empty or exactly the learned one, within the ttl of the CLICK (max 15 s).
# Every kbclick / kbopt starts with kbA(null), which disarms any older allowance.
KBA_JS = r"""
var kbA=function(sp){var f=W.fetch,s=W.XMLHttpRequest&&W.XMLHttpRequest.prototype.send;var a=(f&&f.__kbA)||(s&&s.__kbA);return typeof a==='function'?a('__TOK__',sp):undefined;};
kbA(null);
"""
ALLOW_JS = r"""
var armAllow=function(el,mt,pth,qs){if(kbA({el:el,mt:mt,pth:pth,qs:qs,ttl:__TTL__})!==true)no('allow_no_guard');};
"""

_CLICK_JS = r"""
(function(a){
var D=document,W=window,R=__RX__,S=__SEL__;
__GUARD__
__KBA__
var norm=function(s){return String(s||'').replace(/\s+/g,' ').trim();};
var WR=new RegExp(R.write,'i'),CR=new RegExp(R.cls,'i'),AR=new RegExp(R.attr,'i'),TR=new RegExp(R.tog),KR=new RegExp(R.tok),DR=new RegExp(R.dat),WD=new RegExp(R.wd,'i'),IC=new RegExp(R.icon,'i'),CT=new RegExp(R.ctl,'i'),GR=new RegExp(R.grp,'i');
var SN=new RegExp(R.sn,'ig'),NG=new RegExp(R.neg,'i');
var dng=function(s){if(WD.test(s))return true;SN.lastIndex=0;var m;while((m=SN.exec(s))){if(!NG.test(s.slice(0,m.index)))return true;}return false;};
var WL=(a.write||[]).map(function(t){return norm(t).toLowerCase();});
var no=function(w){throw new Error('KB_CLICK_REFUSED:'+w);};
__ALLOW__
var inSb=function(e){try{return !!e.closest(a.side);}catch(x){return false;}};
var vis=function(e){var r=e.getBoundingClientRect();return r.width>0&&r.height>0;};
var txt=function(e){return norm(e.innerText||e.value||e.textContent);};
var vue=!!(D.querySelector('[data-v-app]')||W.__VUE__||Array.prototype.some.call(D.querySelectorAll('*'),function(e){return e.__vue_app__;}));
var why=function(e,tc){
var tag=String(e.tagName||'').toLowerCase();
var g=function(n){var v=e.getAttribute(n);return v===null||v===undefined?'':String(v);};
var ty=g('type').toLowerCase(),href=g('href'),plain=tag==='a'&&href&&!/^(#|javascript:)/i.test(href);
var wr=tc&&WL.indexOf(txt(e).toLowerCase())>=0;
if(!vis(e))return 'not_visible';
if(inSb(e)&&!tc)return 'sidebar';
if(!tc&&!txt(e))return 'icon_only';
if(ty==='submit'&&!wr)return 'submit';
var f=e.closest('form');
if(f&&/^post$/i.test(f.getAttribute('method')||'')&&!plain&&!wr)return 'post_form';
if(e.hasAttribute('data-href'))return 'data_href';
if(e.hasAttribute('onclick'))return 'onclick';
if(CR.test(String(e.className||'')))return 'danger_class';
var tk=g('class').split(/\s+/).filter(Boolean).some(function(c){return KR.test(c);});
var da=Array.prototype.some.call(e.attributes,function(t){return DR.test(t.name);});
if(tag==='a'&&(tk||da))return 'ajax_link';
var bad=AR.test(href);
Array.prototype.forEach.call(e.attributes,function(t){if(/^data-/i.test(t.name)&&AR.test(t.value))bad=true;});
if(bad)return 'danger_attr';
var ws=[g('title'),g('aria-label'),txt(e)];
if(wr){if(ws.some(dng))return 'write_danger';}
else if(ws.some(function(s){return WR.test(s);}))return 'write_verb';
var ok=TR.test(g('data-bs-toggle')||g('data-toggle'));
if((tag==='button'||ty==='button'||g('role')==='button')&&!ok&&!wr&&!vue)return 'button_no_toggle';
if(tag==='a'&&!ok&&!vue&&(!href||/^(#|javascript:)/i.test(href)))return 'anchor_no_target';
return '';
};
var pickRow=function(r){
var l=r.querySelector('a[href]');
if(l)return {e:l,g:false};
var c=r.querySelector('td,th');
if(!(/(^|\s)clickable(\s|$)/.test(String(r.className||''))||getComputedStyle(r).cursor==='pointer'||(c&&getComputedStyle(c).cursor==='pointer')))return null;
var ok=function(e){return vis(e)&&txt(e)&&!CT.test(e.tagName)&&!IC.test(String(e.className||''))&&!e.querySelector('a,button,[role=button]')&&!e.closest('a,button,[role=button]');};
var cd=Array.prototype.slice.call(r.querySelectorAll('span')).filter(ok).concat(Array.prototype.slice.call(r.querySelectorAll('td,th')).filter(ok));
var e=cd.filter(function(e){return !why(e,false);})[0]||cd[0];
return e?{e:e,g:!!r.querySelector(S.grp)||GR.test(String(r.className||''))}:null;
};
var dls=Array.prototype.slice.call(D.querySelectorAll(S.dlg)).filter(vis);
var dlg=dls.length?dls[dls.length-1]:null;
var el=null,grp=false;
if(a.row){
var p=(0,eval)(sessionStorage.getItem('kbp'));
if(p(a.pa).tn.v==='BLOCK')throw new Error('KB_TENANT_ROW');
var tb=Array.prototype.slice.call(D.querySelectorAll('table')).filter(vis).sort(function(x,y){var u=x.getBoundingClientRect(),v=y.getBoundingClientRect();return v.width*v.height-u.width*u.height;})[0];
var rs=tb?Array.prototype.slice.call(tb.querySelectorAll('tbody tr')):[];
var lf=null,gp=null;
for(var i=0;i<rs.length&&!lf;i++){var pk=pickRow(rs[i]);if(!pk)continue;if(pk.g){if(!gp)gp=pk;}else{lf=pk;}}
var ch=lf||gp;
if(!ch)throw new Error('KB_CLICK_NOTFOUND:row');
el=ch.e;grp=!lf;
}else if(a.choose){
if(!dlg)throw new Error('KB_CLICK_NOTFOUND:dialog');
var CN=new RegExp(R.cn,'i'),CC=new RegExp(R.cc,'i'),CO=new RegExp(R.no,'i'),CB=new RegExp(R.cb,'i');
var bs=Array.prototype.slice.call(dlg.querySelectorAll('button,[role=button],input[type=button],input[type=submit],a.btn')).filter(function(e){return vis(e)&&txt(e);});
var rk=function(t){if(CB.test(t)||dng(t))return 9;if(CN.test(t))return 0;if(CO.test(t))return 1;if(CC.test(t))return 2;return 9;};
var best=null,bk=9;
bs.forEach(function(e){var k=rk(txt(e));if(k<bk){bk=k;best=e;}});
if(!best)throw new Error('KB_CLICK_NOTFOUND:choose');
el=best;WL.push(txt(el).toLowerCase());
}else{
var wt=WL.indexOf(norm(a.text).toLowerCase())>=0;
if(WR.test(a.text)&&!wt)no('write_verb');
var want=norm(a.text).toLowerCase();
var dl2=WL.length?dlg:null;
var all=Array.prototype.slice.call(D.querySelectorAll('a,button,input[type=button],[role=button],[role=tab],[role=menuitem]'+(WL.length?',input[type=submit]':''))).filter(function(e){return vis(e)&&txt(e).toLowerCase()===want&&(!dl2||dl2.contains(e));});
if(!all.length)throw new Error('KB_CLICK_NOTFOUND:'+a.text);
if(WL.length&&all.length>1)no('ambiguous');
var pool=all.filter(function(e){return !inSb(e);});
var okOf=function(l){return l.filter(function(e){return !why(e,true);});};
var good=okOf(pool);
if(!good.length)good=okOf(all);
var h0=good.length?String(good[0].getAttribute('href')||''):'';
if(good.some(function(e){return String(e.getAttribute('href')||'')!==h0;}))no('ambiguous');
el=good[0]||pool[0]||all[0];
}
var w=why(el,!a.row);
if(w)no(w);
el.scrollIntoView({block:'center',inline:'center',behavior:'instant'});
var r=el.getBoundingClientRect(),x=r.left+r.width/2,y=r.top+r.height/2;
var t=D.elementFromPoint(x,y);
if(!t||!(t===el||el.contains(t)))no('occluded');
var out={ok:true,x:Math.round(x),y:Math.round(y),label:txt(el).slice(0,60)};
if(grp)out.group=true;
if(a.allow){
var al=a.allow,tp=true;
try{tp=!W.top||W.top===W;}catch(x){tp=false;}
var ex=norm(al.dlg).toLowerCase(),mt=String(al.method||'').toUpperCase(),qs=al.query===undefined||al.query===null?'':String(al.query);
if(!ex||['POST','PUT','PATCH'].indexOf(mt)<0||!/^\/[^?#\s]*$/.test(String(al.path||''))||!/^(\?[^#\s]*)?$/.test(qs))no('allow_bad_request');
if(!tp)no('allow_not_top');
if(!dlg)no('allow_no_dialog');
if(norm(dlg.innerText).toLowerCase().indexOf(ex)<0||String(W.__kbSeen||'').indexOf(ex)<0)no('allow_dialog_not_seen');
if(!dlg.contains(el))no('allow_outside_dialog');
if(/^(save|create|add|submit)$/i.test(txt(el)))no('allow_on_save');
armAllow(el,mt,String(al.path),qs);
out.armed=mt+' '+al.path+qs;
}
return out;
})
"""

# kbopt: coordinates of one visible option of an open listbox / dropdown / menu.
_OPT_JS = r"""
(function(a){
var D=document,W=window,R=__RX__,S=__SEL__;
__GUARD__
__KBA__
var norm=function(s){return String(s||'').replace(/\s+/g,' ').trim();};
var no=function(w){throw new Error('KB_CLICK_REFUSED:'+w);};
var want=norm(a.option).toLowerCase();
if(!want)throw new Error('KB_CLICK_NOTFOUND:option');
var WD=new RegExp(R.wd,'i'),SN=new RegExp(R.sn,'ig'),NG=new RegExp(R.neg,'i');
var dng=function(s){if(WD.test(s))return true;SN.lastIndex=0;var m;while((m=SN.exec(s))){if(!NG.test(s.slice(0,m.index)))return true;}return false;};
if(dng(want))no('write_danger');
var vis=function(e){var r=e.getBoundingClientRect();return r.width>0&&r.height>0;};
var all=Array.prototype.slice.call(D.querySelectorAll(S.opt)).filter(function(e){return vis(e)&&norm(e.innerText||e.textContent).toLowerCase()===want;});
if(!all.length)throw new Error('KB_CLICK_NOTFOUND:'+a.option);
if(all.length>1)no('ambiguous');
var el=all[0];
el.scrollIntoView({block:'center',inline:'center',behavior:'instant'});
var r=el.getBoundingClientRect(),x=r.left+r.width/2,y=r.top+r.height/2;
var t=D.elementFromPoint(x,y);
if(!t||!(t===el||el.contains(t)))no('occluded');
return {ok:true,x:Math.round(x),y:Math.round(y),label:norm(el.innerText||el.textContent).slice(0,60)};
})
"""


def _sel_json():
    return json.dumps({"grp": GROUP_SEL, "dlg": DIALOG_SEL, "opt": OPTION_SEL})


def click_source():
    """kbclick as one JS function expression (evaluates to a function)."""
    src = _CLICK_JS.replace("__ALLOW__", ALLOW_JS).replace("__KBA__", KBA_JS).replace("__RX__", json.dumps(CLICK_RX)).replace("__SEL__", _sel_json()) \
                   .replace("__GUARD__", guard_source()).replace("__TTL__", str(ALLOW_TTL_S * 1000))
    src = src.replace("__TOK__", ARM_TOKEN)
    return " ".join(l.strip() for l in src.strip().splitlines())


def option_source():
    """kbopt as one JS function expression: args {"option": "<exact text>"} ->
    {ok,x,y,label} of the visible option in an open listbox/dropdown/menu."""
    src = _OPT_JS.replace("__KBA__", KBA_JS).replace("__TOK__", ARM_TOKEN).replace("__RX__", json.dumps(CLICK_RX)).replace("__SEL__", _sel_json()) \
                 .replace("__GUARD__", guard_source())
    return " ".join(l.strip() for l in src.strip().splitlines())


def row_tenant_refusal(verdict):
    """Python mirror of the row gate: only a BLOCK (hard) tenant verdict stops a
    {"row": true} click; REVIEW (a soft rule, e.g. a demo course code) does not."""
    return "tenant_row" if verdict == "BLOCK" else ""


def click_reason(error_text):
    """'KB_CLICK_REFUSED:onclick' -> ('click_refused', 'onclick'); None when not a click error."""
    t = str(error_text or "")
    for k, code in KBC_ERRORS.items():
        i = t.find(k)
        if i >= 0:
            return code, t[i + len(k):].lstrip(":").strip().strip("\"'.)")[:80]
    return None


# Capability token for the allowance, baked into the guard and kbclick / kbopt by
# install_action, so only scripts this plugin generated can arm or disarm. One token per
# KB run: plan-calls and kb_data brief are separate processes but drive the same tab, so
# both load it from <shots>/.arm_token (use_run_token); random per process otherwise.
ARM_TOKEN = secrets.token_hex(16)


def use_run_token(shots_dir):
    """Load (or create) the run's arm token from <shots_dir>/.arm_token and use it."""
    global ARM_TOKEN
    p = Path(shots_dir) / ".arm_token"
    try:
        tok = p.read_text().strip()
    except OSError:
        tok = ""
    if not re.fullmatch(r"[0-9a-f]{32}", tok):
        tok = secrets.token_hex(16)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(tok)
    ARM_TOKEN = tok
    return tok

# Every javascript_tool text the plugin generates starts with this marker; the
# kb_guard hook denies any other javascript_tool text for the Chrome agents.
JS_MARKER = "/*KB*/"


def probe_source():
    """The probe as one JS function expression (evaluates to a function)."""
    src = _PROBE_JS.replace("__GUARD__", guard_source()) \
                   .replace("__FIELDS__", fields_source()) \
                   .replace("__DLG__", json.dumps(DIALOG_SEL)) \
                   .replace("__TENANT__", json.dumps(tenant_regexes())) \
                   .replace("__NF__", json.dumps(NF_SOURCE)) \
                   .replace("__NONLATIN__", str(Q.NONLATIN_MIN)) \
                   .replace("__SLUG_STOP__", json.dumps(sorted(Q.SLUG_STOP)))
    return " ".join(l.strip() for l in src.strip().splitlines())


def install_action():
    """javascript_tool input that stores the probe (kbp), the click guard (kbc) and the
    option picker (kbo) in sessionStorage. Sent first in the first batch of every run."""
    text = (f"{JS_MARKER}var S={json.dumps(probe_source())};sessionStorage.setItem('{KEY}',S);"
            f"var K={json.dumps(click_source())};sessionStorage.setItem('{KBC_KEY}',K);"
            f"var O={json.dumps(option_source())};sessionStorage.setItem('{KBO_KEY}',O);"
            f"'{KEY}:'+S.length")
    return {"action": "javascript_exec", "tabId": "$TAB", "text": text}


def stub(args, key=KEY):
    """Short per-visit call: runs the stored function, throws KB_NOPROBE if absent."""
    return (f"{JS_MARKER}(sessionStorage.getItem('{key}')?(0,eval)(sessionStorage.getItem('{key}'))"
            f":()=>{{throw new Error('KB_NOPROBE')}})({json.dumps(args)})")


def _act(name, **inp):
    return {"name": name, "input": {**inp, "tabId": "$TAB"}}


def _wait(n=2):
    return _act("computer", action="wait", duration=n)


def _export(filename):
    return _act("gif_creator", action="export", download=True, filename=filename,
                options=dict(EXPORT_OPTS))


def settle_seconds(demo, surface_wait=None):
    """Pause after navigate: the surface's wait_s, else demo.settle_s (4), max 10."""
    w = surface_wait if surface_wait is not None else demo.get("settle_s", DEFAULT_SETTLE_S)
    try:
        w = float(w)
    except (TypeError, ValueError):
        w = DEFAULT_SETTLE_S
    w = min(max(w, 0), MAX_WAIT_S)
    return int(w) if w == int(w) else w


def _park(demo):
    return demo.get("cursor_park") or [1465, 740]


def visit_actions(base_url, visit, demo):
    """One recording per visit, no screenshot: the exported GIF's last frame is
    the settled page. The probe (javascript_tool) produces no frame."""
    return [_act("gif_creator", action="start_recording"),
            _act("navigate", url=base_url + visit["url"]),
            _wait(settle_seconds(demo, visit.get("wait_s"))),
            _act("computer", action="hover", coordinate=list(_park(demo))),
            _act("javascript_tool", action="javascript_exec", text=stub(visit["args"])),
            _act("gif_creator", action="stop_recording"),
            _export(visit["gif"])]


def click_visit_actions(visit, demo):
    """A click visit in two phases (the click point comes out of the first):
    pick = start the recording, run kbclick (returns {ok,x,y,label} or throws);
    act  = left_click at "$X","$Y" (the capturer substitutes the pick result),
           settle, park the cursor, probe, stop, export."""
    pick = [_act("gif_creator", action="start_recording"),
            _act("javascript_tool", action="javascript_exec",
                 text=stub(visit["click_args"], KBC_KEY))]
    act = [_act("computer", action="left_click", coordinate=["$X", "$Y"]),
           _wait(settle_seconds(demo, visit.get("wait_s"))),
           _act("computer", action="hover", coordinate=list(_park(demo))),
           _act("javascript_tool", action="javascript_exec", text=stub(visit["args"])),
           _act("gif_creator", action="stop_recording"),
           _export(visit["gif"])]
    return pick, act


def canary_actions(base_url, first_persona, demo, gif):
    entry = (demo.get("portal_entry") or {}).get(first_persona) or "/"
    park = demo.get("cursor_park") or [1465, 740]
    return [_act("gif_creator", action="start_recording"),
            _act("navigate", url=base_url + entry),
            _wait(CANARY_WAIT_S),
            _act("computer", action="hover", coordinate=list(park)),
            _act("gif_creator", action="stop_recording"),
            _export(gif)]


# Sign-in accounts: admin and faculty share one (portal switch); the learner has
# its own (the owner signs in by hand); an external evaluator arrives by an emailed link.
ACCOUNT_OF = {"admin": "main", "faculty": "main", "learner": "learner", "external": "external"}
ACCOUNT_ORDER = ("main", "learner", "external")
SIGNIN_OF = {"learner": "learner", "external": "external_link"}      # by account
PERSONA_ORDER = ("admin", "faculty", "learner", "external")
REASON_NO_EXTERNAL_LINK = "external_link_missing"      # kb_format words: "external link not given"
RETURN_SIGNIN = "main"              # after a learner/external batch the owner signs back in as admin


def account_of(persona):
    return ACCOUNT_OF.get(persona, "main")


def persona_order(personas):
    """Personas grouped by account (main, learner, external), admin before faculty."""
    def key(p):
        po = PERSONA_ORDER.index(p) if p in PERSONA_ORDER else len(PERSONA_ORDER)
        return (ACCOUNT_ORDER.index(account_of(p)), po, personas.index(p))
    return sorted(personas, key=key)


def page_label(url):
    """The menu/page label of a start URL: the last non-numeric path segment,
    title-cased ("/vadministration/curriculum/sessions/" -> "Sessions")."""
    path = str(url or "").split("#", 1)[0].split("?", 1)[0]
    segs = [x for x in path.split("/") if x and not x.isdigit()]
    return re.sub(r"[-_]+", " ", segs[-1]).strip().title() if segs else ""


def step_labels(clicks, step, page, last):
    """Labels the screen after `step` clicks should show. Start screen: the page's
    menu label + the first click's text; a middle screen: the NEXT click's text (the
    button the reader presses next, ringed); the last screen: `last` (the ticket's
    inputs labels). No clicks: `last`, as before."""
    if not clicks:
        return list(last)
    nxt = clicks[step] if step < len(clicks) else None
    nxt_text = [nxt["text"]] if nxt and nxt.get("text") else []
    if step == 0:
        return ([page] if page else []) + nxt_text
    if nxt is None:
        return list(last)
    return nxt_text


def external_start(link):
    """path+query of the emailed external link (host, hash dropped); "" if none."""
    from urllib.parse import urlsplit
    u = urlsplit(str(link or "").strip())
    return (u.path or "/") + ("?" + u.query if u.query else "") if (u.path or u.query) else ""


def _click_spec(c):
    """Normalise a capture_plan click to {"text"} or {"row": True}."""
    if isinstance(c, dict) and c.get("row"):
        return {"row": True}
    text = c.get("text") if isinstance(c, dict) else c
    return {"text": str(text)}


def _write_visits(data_plan, entries, args_of, gif_dir):
    """Visits for the approved data plan's records that carry an "after" block (the
    screen after Save). Not in any capture batch: the data agent drives them.
    {"visit_id": "<eid>-w<k>", "kind": "write", ...}, ordered by entry then k. A record bound
    to an entry (rec["entry"], set by kb_data.py plan) gets its write visit on exactly that
    entry; a bound record whose entry is gone or no longer carries the ticket gets none."""
    out, count = [], {}
    order = {eid: i for i, eid in enumerate(entries)}
    for tid, recs in (data_plan.get("tickets") or {}).items():
        for rec in (recs or {}).get("records") or []:
            after = rec.get("after")
            if not isinstance(after, dict):
                continue
            if rec.get("entry"):       # kb_data.py plan binds a record to the entry it was built from
                eid = rec["entry"] if rec["entry"] in entries and tid in entries[rec["entry"]]["tickets"] else None
            else:                      # a plan without the binding (older): the first entry of the ticket + persona
                mine = [eid for eid, e in entries.items() if tid in e["tickets"]]
                eid = next((x for x in mine if entries[x]["persona"] == rec.get("persona")),
                           mine[0] if mine else None)
            if eid is None:
                continue
            e = entries[eid]
            count[eid] = k = count.get(eid, 0) + 1
            labels = [str(x) for x in (after.get("labels") or [])][:MAX_LABELS]
            vid = f"{eid}-w{k}"
            v = {"visit_id": vid, "kind": "write", "entry": eid, "ticket": tid, "tickets": [tid],
                 "record_id": rec.get("id"), "persona": rec.get("persona") or e["persona"],
                 "surface": e["surface"], "url": rec.get("start") or e["url"],
                 "session": e.get("session", "unknown"), "labels": labels,
                 "gif": f"{gif_dir}/{vid}.gif", "args": {**args_of[eid], "labels": labels}}
            if e.get("private"):
                v["private"] = True
            out.append(v)
    return sorted(out, key=lambda v: (order[v["entry"]], int(v["visit_id"].rsplit("-w", 1)[1])))


def build_calls(plan, inputs, routes, per_batch=3, data_plan=None, external_link=None):
    demo = routes["demo"]
    gif_dir = f"{DL_ROOT}/{plan.get('run_id') or 'run'}"
    surface_wait = {(s.get("persona"), s.get("surface")): s.get("wait_s")
                    for s in routes.get("surfaces", []) if s.get("wait_s") is not None}
    base = demo["base_url"]
    entry = demo.get("portal_entry") or {}
    labels_of = demo.get("portal_label") or {}
    ext_url = external_start(external_link)
    visits, entries, screens, args_of = [], {}, [], {}
    for c in plan["capture_plan"]:
        url, private = c.get("url"), False
        if c["persona"] == "external":                # reached by the emailed link only
            if not ext_url:
                screens.append({"visit_id": f"x{len(screens) + 1:02d}-ext", "entry": "",
                                "persona": c["persona"], "surface": c["surface"],
                                "tickets": list(c.get("tickets", [])),
                                "reason": REASON_NO_EXTERNAL_LINK, "detail": ""})
                continue
            url, private = ext_url, True
        if not url:
            continue
        if c.get("skip_reason"):                      # route-fix: the page does not exist
            screens.append({"visit_id": f"x{len(screens) + 1:02d}-skip", "entry": "",
                            "persona": c["persona"], "surface": c["surface"],
                            "tickets": list(c.get("tickets", [])),
                            "reason": c["skip_reason"], "detail": url})
            continue
        per_ticket = {t: list((inputs.get(t) or {}).get("labels", []))[:MAX_LABELS]
                      for t in c.get("tickets", [])}
        eid = f"v{len(entries) + 1:02d}"
        clicks = [_click_spec(k) for k in (c.get("clicks") or [])]
        wait = c.get("wait_s", surface_wait.get((c["persona"], c["surface"])))
        args = {"v": PROBE_VERSION, "login": demo["login_path"],
                "portal": labels_of.get(c["persona"]),
                "side": demo.get("sidebar_selector", ""),
                "allow": list(demo.get("allow_strings", [])), "domain": demo["domain"]}
        entries[eid] = {"persona": c["persona"], "surface": c["surface"], "url": url,
                        "tickets": list(c.get("tickets", [])), "session": c.get("session", "unknown"),
                        "clicks": clicks, "write_gated": c.get("write_gated") or ""}
        if private:
            entries[eid]["private"] = True
        args_of[eid] = args
        page = page_label(url)
        for step in range(len(clicks) + 1):
            vid = (f"{eid}-s{step}" if clicks else eid)
            per_step = {t: step_labels(clicks, step, page, ls) for t, ls in per_ticket.items()}
            flat = []
            for ls in per_step.values():
                for l in ls:
                    if l not in flat:
                        flat.append(l)
            v = {"visit_id": vid, "entry": eid, "step": step,
                 "kind": "click" if step else "start",
                 "persona": c["persona"], "surface": c["surface"], "url": url,
                 "tickets": list(c.get("tickets", [])), "session": c.get("session", "unknown"),
                 "labels": per_step, "gif": f"{gif_dir}/{vid}.gif", "args": {**args, "labels": flat}}
            if private:
                v["private"] = True
            if wait is not None:
                v["wait_s"] = wait
            if step:
                k = clicks[step - 1]
                v["click"] = k
                v["click_args"] = {"side": args["side"], **k,
                                   **({"pa": {**args, "labels": []}} if k.get("row") else {})}
            if clicks and step == 0:
                v["has_clicks"] = True
            visits.append(v)
        if c.get("write_gated"):
            screens.append({"visit_id": f"{eid}-write", "entry": eid, "persona": c["persona"],
                            "surface": c["surface"], "tickets": list(c.get("tickets", [])),
                            "reason": "needs_write", "detail": c["write_gated"]})
    write_visits = _write_visits(data_plan, entries, args_of, gif_dir) if data_plan else []
    covered = {w["entry"] for w in write_visits}      # an approved record replaces the needs_write screen
    screens = [x for x in screens if not (x.get("reason") == "needs_write" and x["entry"] in covered)]
    batches, seen = [], set()
    install_sent = []
    personas = persona_order(list(dict.fromkeys(v["persona"] for v in visits)))

    def flush(persona, chunk):
        if not chunk:
            return
        actions = []
        if persona not in seen and entry.get(persona):
            actions += [_act("navigate", url=base + entry[persona]), _wait()]
        seen.add(persona)
        if not install_sent:                                  # every run, first batch
            actions.append({"name": "javascript_tool", "input": install_action()})
            install_sent.append(1)
        for v in chunk:
            actions += visit_actions(base, v, demo)
        batches.append({"batch_id": f"b{len(batches) + 1:02d}", "persona": persona, "kind": "visits",
                        "visit_ids": [v["visit_id"] for v in chunk], "actions": actions})
        chunk.clear()

    for persona in personas:
        chunk = []
        for v in (x for x in visits if x["persona"] == persona):
            if v["kind"] == "click":
                flush(persona, chunk)
                pick, act = click_visit_actions(v, demo)
                batches.append({"batch_id": f"b{len(batches) + 1:02d}", "persona": persona,
                                "kind": "click", "entry": v["entry"], "step": v["step"],
                                "visit_ids": [v["visit_id"]], "pick": pick, "act": act})
                continue
            chunk.append(v)
            if len(chunk) >= max(1, per_batch) or v.get("has_clicks"):
                flush(persona, chunk)       # an entry's clicks follow its start page directly
        flush(persona, chunk)
    accounts, ret = [], None
    for ac in ACCOUNT_ORDER:
        ps = [p for p in personas if account_of(p) == ac]
        mine = [b for b in batches if b["persona"] in ps]
        if not mine:
            continue
        row = {"account": ac, "personas": ps}
        if ac in SIGNIN_OF:
            row["signin"] = SIGNIN_OF[ac]
            mine[0]["needs_signin"] = SIGNIN_OF[ac]       # the capturer stops before this batch
        accounts.append({**row, "batches": [b["batch_id"] for b in mine]})
    for b in batches:
        b["account"] = account_of(b["persona"])
    home = (demo.get("portal_entry") or {}).get(demo.get("home_portal", "admin"))
    restore = [_act("navigate", url=base + home), _wait()] if home else []
    if accounts and accounts[-1]["account"] != "main":     # the browser ends signed in as the learner/evaluator
        ret = {"batch_id": f"b{len(batches) + 1:02d}", "persona": demo.get("home_portal", "admin"),
               "kind": "restore", "account": "main", "needs_signin": RETURN_SIGNIN,
               "visit_ids": [], "actions": restore}
        batches.append(ret)
        accounts.append({"account": "main", "personas": [ret["persona"]], "signin": RETURN_SIGNIN,
                         "return": True, "batches": [ret["batch_id"]]})
    canary_gif = f"{gif_dir}/canary.gif"
    return {"run_id": plan.get("run_id"), "base_url": base, "probe_version": PROBE_VERSION,
            "gif_dir": gif_dir, "install": install_action(), "visits": visits,
            "entries": entries, "screens": screens, "accounts": accounts,
            "write_visits": write_visits,
            "batches": batches, "restore": restore,
            "abort": [_act("gif_creator", action="clear")],
            "canary": {"gif": canary_gif,
                       "actions": canary_actions(base, personas[0] if personas else "admin",
                                                 demo, canary_gif)}}


def _j(x):
    return "```json\n" + json.dumps(x, indent=1) + "\n```"


def _accounts_md(calls):
    """capture.md text for the account groups (no JSON block): which batches belong to
    which sign-in, the stop before an account that needs one, and --from-batch."""
    L = ["## Accounts and sign-in", "",
         "Batches are ordered by account, then persona (admin before faculty). The accounts, in order:", ""]
    for a in calls.get("accounts") or []:
        L.append(f"- `{a['account']}` — personas {', '.join(a['personas'])}; "
                 + (f"sign-in `{a['signin']}`; " if a.get("signin") else "no sign-in step; ")
                 + f"batches {', '.join(a['batches'])}")
    L += ["",
          "When the next batch to send is the first batch of an account that has a `signin` "
          "(its heading says so), you are stopping unless the orchestrator started you at or after "
          "it: write the raw files for the batches already done, do **not** send the restore, and "
          "reply `KB_CAPTURE_DONE ok=<n> stop=needs_signin:<account>`. The orchestrator then has the "
          "owner sign in (learner) or gives the emailed link (external) and starts you again with "
          "`--from-batch <batch_id>`.", "",
          "**Return batch.** If the last batch is a `restore` batch (heading says so), it comes after "
          "a learner/external account: the browser is signed in as that user, so it is the first batch of "
          "account `main` with sign-in `main` and the same stop rule applies "
          "(`stop=needs_signin:main`); when you are started at it, send its `actions` (the navigation back "
          "to the home portal) and write `raw/<batch_id>.json` with no items.", "",
          "**Start at batch X.** If the prompt you were given says `--from-batch <id>`, skip every batch "
          "before `<id>` (their raw files already exist; do not rewrite them), send the install action "
          "from section 2 once before the first batch you send, and carry on from there. Everything "
          "else (stop rules, abort, restore) is unchanged.", ""]
    return L


def capture_md(calls):
    L = ["# KB capture — instructions for the capturer", "",
         "You only drive the user's Chrome. Never type, never press keys, never drag, never click "
         "anything except the one point a `kbclick` result gives you. Never close a tab. "
         "Replace the string `\"$TAB\"` with the numeric tab id (no quotes) in every action "
         "before sending it.", "",
         "The run folder is the folder this file is in. Write raw files to its `raw/` folder.", "",
         "## 1. Tab", "",
         "1. Load the Chrome tools with ToolSearch (`select:mcp__claude-in-chrome__tabs_context_mcp,"
         "mcp__claude-in-chrome__browser_batch,mcp__claude-in-chrome__javascript_tool,"
         "mcp__claude-in-chrome__navigate,mcp__claude-in-chrome__computer,"
         "mcp__claude-in-chrome__tabs_create_mcp,mcp__claude-in-chrome__gif_creator`). If they do not load, or `tabs_context_mcp` "
         "fails, reply `KB_CAPTURE NO_CHROME_TOOLS` and stop.",
         "2. Call `tabs_context_mcp`; use the tab it returns (create one with `tabs_create_mcp` only "
         "if there is none). That tab's numeric id is `$TAB`.", "",
         "## 2. The probe install", "",
         "The first batch below already starts with this install (every run installs it afresh; "
         "it stores the probe and the click guard in the tab). Send it alone, once, only after a "
         "`KB_NOPROBE` error. It must return `kbp:<number>`.", "",
         _j(calls["install"]), ""]
    L += [f"## 3. Batches ({len(calls['batches'])})", "",
          "For each batch, in order, follow its heading. Visits are `gif_creator` recordings: "
          "start_recording, navigate, wait, hover, probe, stop_recording, export. There are no "
          "screenshots: never call `computer` with `screenshot`, and you never receive a page "
          "image. The probe result is the `javascript_tool` result. The `export` result says "
          "`Downloaded \"<filename>\"`; that filename is the visit's `gif`.", "",
          "**Visit batches** (`actions`): call `browser_batch` with exactly the `actions` array.", "",
          "**Click batches** (`pick` and `act`) open the next screen by one click and are sent in two "
          "calls:", "",
          "1. Send `pick` with `browser_batch`. Its last result is the `kbclick` result, an object "
          "`{\"ok\":true,\"x\":<number>,\"y\":<number>,\"label\":\"…\"}`.",
          "2. In `act`, replace the two strings `\"$X\"` and `\"$Y\"` (quotes included) with the numbers "
          "`x` and `y` from that result and from nothing else, then send `act` with `browser_batch`. "
          "That one `left_click` is the only click you ever make.",
          "3. **Group rows.** If the `pick` result has `\"group\":true`, the row it found is a group "
          "header that expands to its sessions: send `act`'s first action (the `left_click`) alone, wait "
          "1 second, then send the `kbclick` action again (the last action of `pick`, nothing else) **once** "
          "in the same visit to get a leaf row, and use that result's `x`,`y` for the real `act`. If the "
          "second result is still `group` or an error, treat it as a refused click (step 4). The recording "
          "keeps running through all this.",
          "4. If `pick` fails with an error containing `KB_CLICK_REFUSED`, `KB_CLICK_NOTFOUND` or "
          "`KB_TENANT_ROW`: do not send `act`; send the abort actions (section 5); write the batch's "
          "raw file with status `ok` and the item `{\"visit_id\": …, \"click_error\": \"<the error "
          "text>\"}`; then skip every later batch with the same `entry` and carry on with the next "
          "batch of another entry. This is not a stop.", ""]
    L += _accounts_md(calls)
    for b in calls["batches"]:
        if b.get("needs_signin"):
            L += [f"> **First batch of account `{b['account']}` (sign-in `{b['needs_signin']}`).** "
                  f"Do not send it unless you were started with `--from-batch {b['batch_id']}` "
                  f"or a later batch; otherwise stop here (see \"Accounts and sign-in\").", ""]
        if b.get("kind") == "restore":
            L += [f"### {b['batch_id']} — return to the {b['persona']} portal (restore batch)", "",
                  "Send once, after the owner has signed back in as admin:", "", _j(b["actions"]), "",
                  f"Then write `raw/{b['batch_id']}.json` with an empty `items` list and reply as in section 6.", ""]
            continue
        if b.get("kind") == "click":
            c = next((v["click"] for v in calls["visits"] if v["visit_id"] == b["visit_ids"][0]), {})
            what = "the first row" if c.get("row") else f"\"{c.get('text')}\""
            L += [f"### {b['batch_id']} — {b['persona']} — click visit {b['visit_ids'][0]} "
                  f"(entry {b['entry']}, click {b['step']}: {what})", "",
                  "`pick`:", "", _j(b["pick"]), "", "`act` (substitute `$X`, `$Y`):", "", _j(b["act"]), "",
                  f"After this batch write `raw/{b['batch_id']}.json` (see section 4), then continue.", ""]
            continue
        L += [f"### {b['batch_id']} — {b['persona']} — visits {', '.join(b['visit_ids'])}", "",
              _j(b["actions"]), "",
              f"After this batch write `raw/{b['batch_id']}.json` (see section 4), then continue.", ""]
    L += ["## 4. Raw file for each batch", "",
          "Write `raw/<batch_id>.json` next to this file (directory `raw/` inside the same folder) "
          "right after the batch returns, even if it failed:", "",
          _j({"batch_id": "b01", "status": "ok", "error": None,
              "items": [{"visit_id": "v01", "probe": "<the javascript_tool result exactly as returned>",
                         "gif": "<filename from the export result: Downloaded \"…\">"}]}), "",
          "`status` is one of `ok`, `expired`, `wrong_persona`, `bridge_down`, `no_probe`. `items` are in "
          "visit order, one per visit that completed both its probe and its export; a visit not "
          "reached is simply absent. Copy the probe result verbatim (object, or the text if it came "
          "back as text) — do not edit it. A click visit's item also carries `\"click\"`: the `kbclick` "
          "result copied verbatim; a refused one is `{\"visit_id\", \"click_error\"}` only.", "",
          "## 5. Stop rules", "",
          "- An error containing `KB_LOGIN`: status `expired`, write the file, stop.",
          "- An error containing `KB_PERSONA`: status `wrong_persona`, write the file, stop.",
          "- An error containing `KB_NOPROBE`: send the install action again once, then retry that "
          "batch once. If it fails again: status `no_probe`, write the file, stop.",
          "- A transport or bridge error (tool unavailable, extension disconnected): status "
          "`bridge_down`, write the file, stop.",
          "- A click error (`KB_CLICK_*`, `KB_TENANT_ROW`) is not a stop: see section 3.",
          "- A tool call denied by a hook (an error naming `KB_GUARD`): you tried something not "
          "allowed; do not retry it, status `bridge_down`, write the file, stop.",
          "- Otherwise status `ok` and continue with the next batch.", "",
          "**Abort rule.** After any stop rule above fires or a click is refused (except a bridge "
          "error, when the tool is unreachable), send this once with `browser_batch` so a half "
          "recording is never exported later, then write the raw file:", "",
          _j(calls["abort"]), "",
          "## 6. Restore (after the last batch or after a status stop)", "",
          "Send this once with `browser_batch` so the browser returns to the home portal:", "",
          _j(calls["restore"]), "",
          "Send it only when you reached the end or a status stop; not for `needs_signin`.", "",
          "Reply `KB_CAPTURE_DONE ok=<batches written with status ok> stop=<first non-ok status|needs_signin:<account>|none>`.", ""]
    return "\n".join(L)


def data_plan_sha256(plan):
    """sha256 of the approved content: the plan's tickets only, canonical JSON. The
    same function as kb_data.plan_hash (a test pins that they agree); kb_data imports
    this module, so the few lines are repeated here rather than imported back."""
    body = json.dumps({"tickets": (plan or {}).get("tickets") or {}}, sort_keys=True,
                      ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def cmd_plan_calls(a):
    plan = json.loads(Path(a.plan).read_text())
    inputs = json.loads(Path(a.inputs).read_text())
    routes = json.loads(Path(a.routes).read_text())
    out_dir = Path(a.out_dir)
    use_run_token(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    data_plan, note = None, ""
    if a.data_plan:
        data_plan = json.loads(Path(a.data_plan).read_text())
        if data_plan.get("approved") is not True:     # the owner has not said y: no write visits
            data_plan, note = None, " (data plan not approved: no write visits)"
        elif data_plan.get("approved_sha256") != data_plan_sha256(data_plan):
            data_plan, note = None, " (data plan changed after approval: no write visits)"
    calls = build_calls(plan, inputs, routes, a.per_batch, data_plan, a.external_link)
    (out_dir / "calls.json").write_text(json.dumps(calls, indent=1))
    (out_dir / "capture.md").write_text(capture_md(calls))
    skipped = sum(1 for c in plan["capture_plan"] if not c.get("url") or c.get("skip_reason"))
    print(f"planned {len(calls['visits'])} visits in {len(calls['batches'])} batches "
          f"({skipped} without a URL skipped), {len(calls['accounts'])} account(s), "
          f"{len(calls['write_visits'])} write visit(s){note} -> {out_dir / 'capture.md'}")
    emit({"ok": True, "action": "plan-calls", "visits": len(calls["visits"]),
          "batches": len(calls["batches"]), "skipped": skipped,
          "accounts": [x["account"] for x in calls["accounts"]],
          "write_visits": len(calls["write_visits"]),
          "calls": str(out_dir / "calls.json"), "capture_md": str(out_dir / "capture.md")})
    return 0


# ── page check before capture: route-check-js / route-fix ─────────────────────
ROUTE_CHECK_MAX = 20
ALT_PREFIXES = (("/administration/", "/vadministration/"), ("/vadministration/", "/administration/"))

_ROUTE_CHECK_JS = r"""
(async function(U,L,N){
var NF=new RegExp(N,'i');
var one=async function(u){
try{
var r=await fetch(u,{method:'GET',credentials:'same-origin',redirect:'follow'});
var t=await r.text();
var d=new DOMParser().parseFromString(t,'text/html');
d.querySelectorAll('script,style').forEach(function(e){e.remove();});
var f=new URL(r.url).pathname;
var hd=d.querySelector('h1,h2');
var tx=String((d.body&&d.body.textContent)||'').replace(/\s+/g,' ').trim();
var login=f.indexOf(L)>=0||!!d.querySelector('input[type=password]');
var nf=r.status===404||NF.test(String(d.title||'')+' '+String((hd&&hd.textContent)||'')+' '+tx.slice(0,1500));
return {url:u,status:r.status,final:f,login:login,nf:nf};
}catch(e){return {url:u,status:0,final:'',login:false,nf:false,err:String(e).slice(0,80)};}
};
return JSON.stringify(await Promise.all(U.map(one)));
})
"""


def start_key(url):
    """path+query of a start URL, no hash."""
    return str(url or "").split("#", 1)[0]


def alt_route(url):
    """/administration/X <-> /vadministration/X (query kept), or None."""
    u = start_key(url)
    for a, b in ALT_PREFIXES:
        if u.startswith(a):
            return b + u[len(a):]
    return None


def route_check_urls(plan, cap=ROUTE_CHECK_MAX):
    """Distinct start URLs of the plan, each followed by its administration alternate."""
    urls = []
    for c in plan.get("capture_plan", []):
        if not c.get("url") or c.get("skip_reason"):
            continue
        for u in (start_key(c["url"]), alt_route(c["url"])):
            if u and u not in urls:
                urls.append(u)
    return urls[:cap], max(len(urls) - cap, 0)


def route_check_source(urls, login_path):
    """javascript_tool text: fetch()es every URL in the signed-in tab and returns a
    JSON string [{url,status,final,login,nf}]. It starts with the /*KB*/ marker, then `await`: the tool
    evaluates like a REPL, and an un-awaited async IIFE hands back its Promise."""
    return (JS_MARKER + "await " + _ROUTE_CHECK_JS.strip().replace("\n", " ")        # javascript_tool: REPL, top-level await
            + f"({json.dumps(urls)},{json.dumps(login_path)},{json.dumps(NF_SOURCE)})")


def route_fix(plan, results):
    """-> (new plan, expired, changes). results: [{url,status,final,login,nf}].
    A start whose page is a 404 is moved to the first checked-ok alternate, else
    marked skip_reason page_not_found. Starts that were not checked are untouched."""
    by = {start_key(r.get("url")): r for r in results if isinstance(r, dict)}

    def ok(r):
        return bool(r) and not r.get("nf") and not r.get("login") and 200 <= int(r.get("status") or 0) < 400
    expired = [r["url"] for r in by.values() if r.get("login")]
    out, changes = [], []
    for c in plan.get("capture_plan", []):
        e = dict(c)
        r = by.get(start_key(e.get("url")))
        if e.get("url") and not e.get("skip_reason") and r and r.get("nf"):
            alt = alt_route(e["url"])
            if alt and ok(by.get(alt)):
                h = "#" + e["url"].split("#", 1)[1] if "#" in e["url"] else ""
                changes.append({"surface": e.get("surface"), "from": e["url"], "to": alt + h})
                e["route_fixed_from"] = e["url"]
                e["url"] = alt + h
            else:
                e["skip_reason"] = "page_not_found"
                changes.append({"surface": e.get("surface"), "from": e["url"], "to": None})
        out.append(e)
    return {**plan, "capture_plan": out}, expired, changes


def cmd_route_check_js(a):
    plan = json.loads(Path(a.plan).read_text())
    routes = json.loads(Path(a.routes).read_text())
    urls, dropped = route_check_urls(plan)
    if dropped:
        print(f"note: {dropped} URL(s) over the {ROUTE_CHECK_MAX} cap not checked", file=sys.stderr)
    print(route_check_source(urls, routes["demo"]["login_path"]))
    return 0


def _load_route_results(path):
    r = json.loads(Path(path).read_text())
    for _ in range(2):                              # the tool may hand back the JSON as a string
        if isinstance(r, str):
            r = json.loads(r)
    if isinstance(r, dict):
        r = r.get("results") or r.get("result") or []
        if isinstance(r, str):
            r = json.loads(r)
    if not isinstance(r, list):
        raise ValueError("route-check result is not a list")
    return r


def cmd_route_fix(a):
    plan = json.loads(Path(a.plan).read_text())
    try:
        results = _load_route_results(a.result)
    except ValueError as e:
        emit({"ok": False, "action": "route-fix", "error": f"bad result: {e}"})
        return 1
    new, expired, changes = route_fix(plan, results)
    if expired:
        print(f"KB_LOGIN expired: the tab is signed out ({expired[0]} landed on the login page); "
              f"sign in again, then re-run route-check")
        emit({"ok": False, "action": "route-fix", "error": "expired", "checked": len(results)})
        return 3
    Path(a.out).write_text(json.dumps(new, indent=1))
    moved = [c for c in changes if c["to"]]
    gone = [c for c in changes if not c["to"]]
    print(f"route-fix: {len(results)} page(s) checked · {len(moved)} start page(s) moved"
          + "".join(f" ({c['from']} -> {c['to']})" for c in moved)
          + f" · {len(gone)} marked page_not_found"
          + "".join(f" ({c['from']})" for c in gone) + f" -> {a.out}")
    emit({"ok": True, "action": "route-fix", "checked": len(results), "moved": moved,
          "page_not_found": [c["from"] for c in gone], "out": str(a.out)})
    return 0


def parse_probe(raw):
    """The probe as a dict, or None when it was blocked/garbled (-> retry)."""
    p = raw
    for _ in range(2):
        if isinstance(p, str):
            t = p.strip()
            if t.startswith("[BLOCKED"):
                return None
            try:
                p = json.loads(t)
            except ValueError:
                return None
    if isinstance(p, dict) and isinstance(p.get("labels"), list) and p.get("iw"):
        return p
    return None


def box_in_frame(box, img_w, img_h):
    """True when an image-px box (x, y, w, h) has area and lies fully inside the frame. A
    label whose element is off-screen (an "Save" button below the fold) has no pixels to
    crop: OCR on it crashed the 0.27.0 ingest, so it is skipped before any crop."""
    x, y, w, h = box
    return w > 0 and h > 0 and x >= 0 and y >= 0 and x + w <= img_w and y + h <= img_h


def screen_form(probe):
    """What the probe saw of a form / dialog, for the screen record in shots.json (kb_data
    plans fills from it): {"fields": [...], "tw": {...}, "btn": [dialog buttons],
    "found": [labels the probe found]} - only the non-empty parts."""
    out = {}
    if isinstance(probe.get("fields"), list) and probe["fields"]:
        out["fields"] = probe["fields"][:40]
    if isinstance(probe.get("tw"), dict) and probe["tw"]:
        out["tw"] = probe["tw"]
    dlg = probe.get("dlg") if isinstance(probe.get("dlg"), dict) else {}
    if dlg.get("bt"):
        out["btn"] = list(dlg["bt"])[:8]
    found = [l.get("l") for l in probe.get("labels") or [] if isinstance(l, dict) and l.get("f") and l.get("l")]
    if found:
        out["found"] = found
    return out


def frame_problem(img_w, img_h, probe):
    """Why a frame can't carry anchors, or None."""
    iw, ih = probe["iw"], probe.get("ih") or 0
    if img_w < F.SHOT_PX_W:
        return "low_resolution"
    if ih and abs((img_h / img_w) - (ih / iw)) / (ih / iw) > MAX_ASPECT_DRIFT:
        return "aspect_mismatch"
    return None


DATA_RAW_PREFIX = "data-"


def _load_raw(raw_dir):
    """visit_id -> item, batch_id -> raw (later files / items win). The data agent's
    files (raw/data-<ticket>.json, DATA_RAW_PREFIX) only contribute items: they are
    no batch, and a batch id never starts with "data-"."""
    items, batches = {}, {}
    for f in sorted(Path(raw_dir).glob("*.json")):
        try:
            r = json.loads(f.read_text())
        except ValueError:
            continue
        if not isinstance(r, dict):
            continue
        if not f.name.startswith(DATA_RAW_PREFIX):
            batches[r.get("batch_id") or f.stem] = r
        for it in r.get("items") or []:
            if it.get("visit_id"):
                items[it["visit_id"]] = it
    return items, batches


def wait_for_file(path, timeout_s=15.0, interval_s=0.25):
    """Block until `path` exists with a stable size, or timeout.

    Returns (status, exit_code); status in {"ready", "timeout", "empty"}.
    Vendored from medtrics-qa-automation 0.17.1
    skills/qa-screenshot-capture/scripts/poll_download.py (Charles); only the
    interval is in seconds here. Ready = non-empty and unchanged between two
    consecutive polls, so Chrome has finished writing."""
    path = Path(path)
    deadline = time.monotonic() + timeout_s
    last_size = -1
    while time.monotonic() < deadline:
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            size = -1
        if size > 0 and size == last_size:
            return ("ready", 0)
        last_size = size
        time.sleep(interval_s)
    if last_size == 0:
        return ("empty", 2)
    return ("timeout", 1)


_WAIT_REASON = {"timeout": "download_missing", "empty": "empty_export"}


def last_frame_png(gif_path, dest):
    """Save the last composited frame of a GIF as an RGB PNG. Raises ValueError
    when the file is not a decodable GIF."""
    from PIL import Image, ImageSequence
    try:
        with Image.open(gif_path) as im:
            if im.format != "GIF":
                raise ValueError("not a GIF")
            last = None
            for fr in ImageSequence.Iterator(im):
                last = fr.convert("RGB")
        if last is None:
            raise ValueError("no frames")
        last.save(dest, "PNG")
    except (OSError, SyntaxError, EOFError, ValueError) as e:
        raise ValueError(f"{type(e).__name__}: {e}")
    return last.size


def _fetch_gif(visit, item, downloads_dir, out_dir, timeout_s):
    """Wait for the exported GIF, COPY it into shots/gifs/ (Downloads is never
    touched; `cleanup` removes the originals after the owner approves), extract
    its last frame -> (frame_path, reason)."""
    gifs = out_dir / "gifs"
    kept = gifs / f"{visit['visit_id']}.gif"
    if not downloads_dir:
        if not kept.is_file():
            return None, "download_missing"
    else:
        src = Path(downloads_dir) / str(item["gif"])
        status, _ = wait_for_file(src, 0.3 if kept.is_file() else timeout_s)
        if status == "ready":
            gifs.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(kept))
        elif not kept.is_file():                    # a re-ingest finds its earlier copy
            return None, _WAIT_REASON[status]
    frames = out_dir / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    dest = frames / f"{visit['visit_id']}.png"
    try:
        last_frame_png(kept, dest)
    except ValueError:
        return None, "bad_gif"
    return dest, None


def ingest_visit(visit, item, calls, demo, out_dir, use_ocr, now,
                 downloads_dir=None, timeout_s=NEXT_FILE_TIMEOUT_S):
    """-> (entries, reason). reason is None when the visit was ingested."""
    from PIL import Image
    probe = parse_probe(item.get("probe"))
    if probe is None:
        return [], "retry"
    src = Path(os.path.expanduser(str(item.get("screenshot_path") or "")))
    if item.get("gif"):
        dest, reason = _fetch_gif(visit, item, downloads_dir, out_dir, timeout_s)
        if reason:
            return [], reason
        frame_source = "gif"
    elif item.get("screenshot_path") and src.is_file():      # 0.23.0 raw files
        frames = out_dir / "frames"
        frames.mkdir(parents=True, exist_ok=True)
        dest = frames / f"{visit['visit_id']}.jpg"
        shutil.copyfile(src, dest)
        frame_source = "disk"
    else:
        return [], "retry"
    with Image.open(dest) as im:
        im.load()
        problem = frame_problem(im.width, im.height, probe)
        scale = im.width / probe["iw"]
        tn = probe.get("tn") or {}
        tenant_dom = tn.get("v") if tn.get("v") in ("CLEAR", "REVIEW", "BLOCK") else None
        tenant_ocr = None
        if use_ocr and not problem:
            tenant_ocr = Q.screen_frame(str(dest), demo["domain"],
                                        allow_strings=demo["allow_strings"])["verdict"]
        by_label = {l.get("l"): l for l in probe["labels"]}
        entries = []
        host = calls['base_url'].split('//')[-1]
        source = (f"chrome:{host} external-link {now}" if visit.get("private")      # never the link
                  else f"chrome:{host}{probe.get('u', visit['url'])} {now}")
        for tid in visit["tickets"]:
            for label in (visit["labels"].get(tid) or []):
                pl = by_label.get(label)
                if not pl or not pl.get("f") or not pl.get("b"):
                    continue
                ev = None
                if not problem:
                    x, y, w, h = pl["b"]
                    box = to_image_box({"x": x, "y": y, "width": w, "height": h}, scale)
                    if pl.get("oc") or not pl.get("iv") or not box_in_frame(box, im.width, im.height):
                        ev = None                  # off-screen / covered: nothing to crop or ring (Q3)
                    else:
                        try:
                            crop = Q.verify_crop(im, box, [label])[0] if use_ocr else None
                        except Exception:          # an OCR failure is a miss, never a crash
                            crop = {"verdict": "MISS", "score": 0.0}
                        ev = shot_evidence(label, pl.get("t", ""), box, crop, False,
                                           tenant_dom, tenant_ocr)
                entries.append({"ticket": tid, "persona": visit["persona"],
                                "surface": visit["surface"], "frame": str(dest),
                                "verified": ev is not None, "source": source,
                                "visit_id": visit["visit_id"], "sidebar": bool(pl.get("sb")),
                                "frame_source": frame_source,
                                **(ev or {"label": label})})
        h = probe.get("h") if isinstance(probe.get("h"), dict) else None
        if visit.get("kind") in ("click", "write") and not problem and h and h.get("t") and h.get("b"):
            have = {e["ticket"] for e in entries}
            x, y, w, hh = h["b"]
            box = to_image_box({"x": x, "y": y, "width": w, "height": hh}, scale)
            for tid in visit["tickets"]:
                if tid in have or not (visit["labels"].get(tid) or []):
                    continue                  # a label was found, or this ticket wants no shot here
                ev = shot_evidence(h["t"], h["t"], box, None, False, tenant_dom, tenant_ocr)
                entries.append({"ticket": tid, "persona": visit["persona"],
                                "surface": visit["surface"], "frame": str(dest),
                                "verified": True, "source": source,
                                "visit_id": visit["visit_id"], "sidebar": False,
                                "frame_source": frame_source, "fallback": "heading", **ev})
    return entries, None


def _screens_missing(calls, screens, shots):
    """ticket -> {"reason", "detail"}: why a ticket has no (more) screenshots. The
    first failing screen in visit order wins; needs_write screens come last. A
    ticket whose screens all loaded but shows none of its labels is label_not_found."""
    order = ([v["visit_id"] for v in calls["visits"]] + [w["visit_id"] for w in calls.get("write_visits") or []]
             + [x["visit_id"] for x in calls.get("screens") or []])
    pos = {vid: i for i, vid in enumerate(order)}
    missing = {}
    for sc in sorted(screens, key=lambda x: pos.get(x["visit_id"], len(pos))):
        if sc.get("reason"):
            for t in sc["tickets"]:
                missing.setdefault(t, {"reason": sc["reason"], "detail": sc.get("detail", "")})
    have = {s_["ticket"] for s_ in shots}
    for sc in screens:
        if not sc.get("reason"):
            for t in sc["tickets"]:
                if t not in have:
                    missing.setdefault(t, {"reason": "label_not_found", "detail": ""})
    return missing


def cmd_ingest(a):
    calls = json.loads(Path(a.calls).read_text())
    routes = json.loads(Path(a.routes).read_text())
    demo = routes["demo"]
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "shots.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"shots": []}
    use_ocr = ocr_available() and not a.no_ocr
    if not use_ocr:
        print("OCR unavailable — anchors will be dom_box (draft-only).")
    items, batches = _load_raw(a.raw_dir)
    now = _dt.datetime.now().isoformat(timespec="minutes")
    batch_of = {vid: b for b in calls["batches"] for vid in b["visit_ids"]}

    reasons, new = {}, []
    ingested, settled, dead = set(), set(), {}
    screens = {x["visit_id"]: x for x in manifest.get("screens") or []}
    fetched = 0
    for v in calls["visits"]:
        vid = v["visit_id"]
        step = v.get("step", 0)
        key = f"{v['persona']}/{v['surface']}" + (f"#s{step}" if step else "")
        screen = {"visit_id": vid, "entry": v.get("entry", vid), "step": step,
                  "persona": v["persona"], "surface": v["surface"], "tickets": v["tickets"],
                  "reason": None, "detail": ""}
        if v.get("entry", vid) in dead:                 # an earlier click of this entry failed
            settled.add(vid)
            screens.pop(vid, None)
            continue
        it = items.get(vid)
        cr = click_reason(it.get("click_error")) if it else None
        if cr:
            screen.update(reason=cr[0], detail=cr[1])
            screens[vid] = screen
            settled.add(vid)
            dead[v.get("entry", vid)] = cr[0]
            continue
        pr = parse_probe(it.get("probe")) if it else None
        if pr and pr.get("nf"):                         # the 404 page: no shot, no label misses
            screen.update(reason="page_not_found",
                          detail=v["surface"] if v.get("private") else pr.get("u") or v["url"])
            screens[vid] = screen
            settled.add(vid)
            dead[v.get("entry", vid)] = "page_not_found"
            continue
        if it:
            timeout = FIRST_FILE_TIMEOUT_S if not fetched else NEXT_FILE_TIMEOUT_S
            if it.get("gif"):
                fetched += 1
            entries, reason = ingest_visit(v, it, calls, demo, out_dir, use_ocr, now,
                                           a.downloads_dir, timeout)
            if reason is None:
                ingested.add(vid)
                settled.add(vid)
                screen.update(screen_form(pr or {}))
                screens[vid] = screen
                new += entries
                continue
        else:
            b = batches.get(batch_of.get(vid, {}).get("batch_id"))
            st = (b or {}).get("status", "ok")
            reason = "not_reached" if not b else (st if st != "ok" else "not_reached")
        reasons[key] = reason
    for w in calls.get("write_visits") or []:        # the data agent's screens (raw/data-<ticket>.json)
        vid, tid = w["visit_id"], w["ticket"]
        wv = {**w, "labels": {tid: list(w.get("labels") or [])}, "tickets": [tid]}
        screen = {"visit_id": vid, "entry": w.get("entry", vid), "step": 0, "persona": w["persona"],
                  "surface": w["surface"], "tickets": [tid], "reason": None, "detail": ""}
        it = items.get(vid)
        if not it:
            screen.update(reason="write_failed", detail="not run")
        elif it.get("write_error"):
            screen.update(reason="write_failed", detail=str(it["write_error"])[:80])
        elif it.get("mode") == "dry_run":
            # the dry run only LEARNS the prompt (kb_data learn); nothing was saved and its
            # screens are not article shots - the real save (and its screens) come next run.
            screen.update(reason="needs_write", detail="dry_run", **screen_form(parse_probe(it.get("probe")) or {}))
        else:
            if it.get("gif"):
                fetched += 1
            entries, reason = ingest_visit(wv, it, calls, demo, out_dir, use_ocr, now, a.downloads_dir,
                                           FIRST_FILE_TIMEOUT_S if not fetched else NEXT_FILE_TIMEOUT_S)
            if reason is None:
                ingested.add(vid)
                new += entries
                pr = parse_probe(it.get("probe")) or {}
                screen.update(screen_form(pr))
                if isinstance(it.get("created"), dict):
                    screen["note"] = "saved"               # informational: the allowed request went out once
                elif int(it.get("blocked") or pr.get("wb") or 0) >= 1:
                    screen["note"] = "write_blocked_ok"    # informational: the save was blocked, as planned
            else:
                screen.update(reason="write_failed",
                              detail="record_unverified" if it.get("pending") and not it.get("created") else reason)
        screens[vid] = screen
    for w in calls.get("screens") or []:
        screens[w["visit_id"]] = dict(w)

    downloads_left = []
    if a.downloads_dir:
        for v in calls["visits"] + (calls.get("write_visits") or []):
            if (Path(a.downloads_dir) / v["gif"]).is_file():
                downloads_left.append(v["gif"])
    kept = [s for s in manifest["shots"] if s.get("visit_id") not in ingested]
    n = max([int(re.sub(r"\D", "", s.get("shot_id", "")) or 0) for s in kept] + [0])
    for e in new:
        n += 1
        kept.append({"shot_id": f"s{n}", **e})
    manifest["shots"] = kept
    manifest["screens"] = list(screens.values())
    manifest["missing"] = _screens_missing(calls, manifest["screens"], kept)
    manifest_path.write_text(json.dumps(manifest, indent=1))
    verified = sum(1 for e in new if e["verified"])

    ent = calls.get("entries") or {}
    left_entries = []
    for eid in dict.fromkeys(v.get("entry", v["visit_id"]) for v in calls["visits"]):
        mine = [v for v in calls["visits"] if v.get("entry", v["visit_id"]) == eid]
        if all(v["visit_id"] in settled for v in mine):
            continue
        e = ent.get(eid) or mine[0]
        row = {"persona": e["persona"], "surface": e["surface"],
               "url": "" if e.get("private") else e["url"], "recipe": [],     # the external link stays in the run
               "tickets": e["tickets"], "session": e.get("session", "unknown")}
        if e.get("clicks"):
            row["clicks"] = e["clicks"]
        if e.get("write_gated"):
            row["write_gated"] = e["write_gated"]
        left_entries.append(row)
    rp = {"run_id": calls.get("run_id"), "capture_plan": left_entries}
    left = len(left_entries)
    extra = {"downloads_left": downloads_left,
             "missing": {t: m["reason"] for t, m in manifest["missing"].items()}}
    resume_path = out_dir / "resume.json"
    if left:
        resume_path.write_text(json.dumps({**rp, "left_reasons": reasons}, indent=1))
        print(f"ingested {len(ingested)} visits · {len(new)} shots · {verified} verified · "
              f"{left} left ({', '.join(sorted(set(reasons.values())))}) -> {resume_path}")
        emit({"ok": False, "action": "ingest", "shots": len(new), "verified": verified,
              "visits": len(ingested), "left": left, "left_reasons": reasons, "ocr": use_ocr, **extra})
        return 3
    if resume_path.exists():
        resume_path.unlink()
    print(f"ingested {len(ingested)} visits · {len(new)} shots · {verified} verified")
    if downloads_left:
        print(f"{len(downloads_left)} GIF(s) still in Downloads (copied, not moved); "
              f"`cleanup` removes them once the owner approves")
    emit({"ok": True, "action": "ingest", "shots": len(new), "verified": verified,
          "visits": len(ingested), "left": 0, "left_reasons": {}, "ocr": use_ocr, **extra})
    return 0


def cmd_cleanup(a):
    """Delete this run's exported GIFs (and the canary) from Downloads, then the
    emptied run folders. Only paths under Downloads/kb-capture are ever touched."""
    calls = json.loads(Path(a.names_from).read_text())
    root = (Path(a.downloads_dir) / DL_ROOT).resolve()
    names = [v["gif"] for v in calls.get("visits", []) + calls.get("write_visits", [])]
    if (calls.get("canary") or {}).get("gif"):
        names.append(calls["canary"]["gif"])
    deleted, absent, kept = [], [], []
    for name in names:
        p = (Path(a.downloads_dir) / name).resolve()
        if root not in p.parents:                      # never outside kb-capture/
            absent.append(name)
            continue
        if not p.is_file():
            absent.append(name)
            continue
        try:
            p.unlink()
            deleted.append(name)
        except OSError:                                # Cowork: delete not approved
            kept.append(name)
    for d in (Path(a.downloads_dir) / calls.get("gif_dir", DL_ROOT), root):
        try:
            d.rmdir()                                  # only when empty
        except OSError:
            pass
    print(f"deleted {len(deleted)} GIF(s) from Downloads"
          + (f"; {len(kept)} could not be deleted (no delete access): " + ", ".join(kept) if kept else ""))
    emit({"ok": not kept, "action": "cleanup", "deleted": deleted, "absent": absent, "kept": kept})
    return 0


PICKUP_EXIT = {"download_missing": 1, "empty_export": 2, "bad_gif": 3}


def cmd_pickup(a):
    """Canary check: the exported GIF arrived, is non-empty and decodes."""
    src = Path(a.downloads_dir) / a.name
    status, _ = wait_for_file(src, a.timeout)
    reason = _WAIT_REASON.get(status)
    size = None
    if not reason:
        try:
            from PIL import Image, ImageSequence
            with Image.open(src) as im:
                if im.format != "GIF":
                    raise ValueError("not a GIF")
                for fr in ImageSequence.Iterator(im):
                    size = fr.size
            if size is None:
                raise ValueError("no frames")
        except (OSError, SyntaxError, EOFError, ValueError):
            reason = "bad_gif"
    if reason:
        print(f"canary failed: {reason}")
        emit({"ok": False, "action": "pickup", "reason": reason, "name": a.name})
        return PICKUP_EXIT[reason]
    # The canary stays in Downloads: deleting there needs a Cowork approval, so
    # `cleanup` removes it with the run's GIFs once the owner agrees.
    print(f"canary ok: {size[0]}x{size[1]}")
    emit({"ok": True, "action": "pickup", "width": size[0], "height": size[1], "name": a.name})
    return 0


def cmd_sweep(a):
    """Move leftover Downloads/kb-capture/* (crashed runs) into --into. Never
    deletes a file; only an emptied kb-capture folder is removed."""
    root = Path(a.downloads_dir) / DL_ROOT
    moved = []
    if root.is_dir():
        into = Path(a.into)
        for p in sorted(root.iterdir()):
            into.mkdir(parents=True, exist_ok=True)
            dest = into / p.name
            n = 1
            while dest.exists():
                n += 1
                dest = into / f"{p.name}-{n}"
            shutil.move(str(p), str(dest))
            moved.append({"from": str(p), "to": str(dest)})
        try:
            root.rmdir()
        except OSError:
            pass
    for m in moved:
        print(f"moved {m['from']} -> {m['to']}")
    print(f"swept {len(moved)} leftover item(s)")
    emit({"ok": True, "action": "sweep", "moved": moved})
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("login"); s.add_argument("--persona", required=True,
                                                choices=("admin", "faculty", "learner"))
    s.add_argument("--env-file", nargs="?", const=LOGIN_FILE,
                   help=f"fill the form from this credentials file (default {LOGIN_FILE})")
    s.add_argument("--headed", action="store_true", help="show the browser even with --env-file")
    s.add_argument("--routes", default=str(DEFAULT_ROUTES)); s.add_argument("--session-dir")
    s = sub.add_parser("check"); s.add_argument("--routes", default=str(DEFAULT_ROUTES))
    s.add_argument("--session-dir"); s.add_argument("--out")
    s = sub.add_parser("run"); s.add_argument("--plan", required=True)
    s.add_argument("--inputs", required=True); s.add_argument("--out-dir", required=True)
    s.add_argument("--routes", default=str(DEFAULT_ROUTES)); s.add_argument("--session-dir")
    s.add_argument("--no-ocr", action="store_true")
    s = sub.add_parser("plan-calls"); s.add_argument("--plan", required=True)
    s.add_argument("--inputs", required=True); s.add_argument("--out-dir", required=True)
    s.add_argument("--per-batch", type=int, default=3)
    s.add_argument("--routes", default=str(DEFAULT_ROUTES))
    s.add_argument("--data-plan", help="the owner-approved data_plan.json: adds write visits")
    s.add_argument("--external-link", help="the emailed evaluator link (path+query used in the run only)")
    s = sub.add_parser("route-check-js"); s.add_argument("--plan", required=True)
    s.add_argument("--routes", default=str(DEFAULT_ROUTES))
    s = sub.add_parser("route-fix"); s.add_argument("--plan", required=True)
    s.add_argument("--result", required=True); s.add_argument("--out", required=True)
    s = sub.add_parser("ingest"); s.add_argument("--calls", required=True)
    s.add_argument("--raw-dir", required=True); s.add_argument("--out-dir", required=True)
    s.add_argument("--routes", default=str(DEFAULT_ROUTES)); s.add_argument("--no-ocr", action="store_true")
    s.add_argument("--downloads-dir")
    s = sub.add_parser("cleanup"); s.add_argument("--downloads-dir", required=True)
    s.add_argument("--names-from", required=True)
    s = sub.add_parser("pickup"); s.add_argument("--downloads-dir", required=True)
    s.add_argument("--name", required=True); s.add_argument("--timeout", type=float, default=20.0)
    s = sub.add_parser("sweep"); s.add_argument("--downloads-dir", required=True)
    s.add_argument("--into", required=True)
    a = ap.parse_args(argv)
    try:
        return {"login": cmd_login, "check": cmd_check, "run": cmd_run,
                "plan-calls": cmd_plan_calls, "route-check-js": cmd_route_check_js,
                "route-fix": cmd_route_fix, "ingest": cmd_ingest,
                "pickup": cmd_pickup, "sweep": cmd_sweep, "cleanup": cmd_cleanup}[a.cmd](a)
    except (OSError, json.JSONDecodeError, KeyError) as e:
        emit({"ok": False, "action": a.cmd, "error": f"{type(e).__name__}: {e}"})
        return 1


if __name__ == "__main__":
    sys.exit(main())
