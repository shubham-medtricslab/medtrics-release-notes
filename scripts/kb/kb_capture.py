#!/usr/bin/env python3
"""
kb_capture.py — screenshot capture on the demo tenant.

CURRENT PATH (Claude in Chrome, the owner's own signed-in Chrome):

  plan-calls --plan groups.json --inputs inputs.json --out-dir $RUN/shots
             [--per-batch 3] [--routes kb_routes.json]
          Writes calls.json (install action, visits, batches of browser_batch
          actions, restore) and capture.md (the only input the capturer reads).
          Each visit is recorded with gif_creator (no screenshot); calls.json also
          carries `canary`, `abort`, `gif_dir` and a `gif` filename per visit.
  ingest     --calls calls.json --raw-dir raw/ --out-dir $RUN/shots
             [--downloads-dir D] [--no-ocr] [--routes kb_routes.json]
          Turns the capturer's raw files (probe JSON + exported GIF name) into
          shots.json entries: waits for D/<gif>, moves it to shots/gifs/, takes
          its last frame as frames/<visit_id>.png. 0.23.0 raw files with a
          screenshot_path still ingest (frames/<visit_id>.jpg). Re-ingest replaces
          by visit_id. Anything left -> resume.json and exit 3
          (download_missing / empty_export / bad_gif / retry / ...).
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


# Plain JS, no comments, every statement ends with ';' (lines are joined with
# spaces). __TENANT__ and __NONLATIN__ are filled from the Python side.
_PROBE_JS = r"""
(function(a){
var D=document,W=window;
var norm=function(s){return String(s||'').replace(/\s+/g,' ').trim();};
if(location.pathname.indexOf(a.login)>=0||D.querySelector('input[type=password]'))throw new Error('KB_LOGIN');
var pl=Array.prototype.slice.call(D.querySelectorAll('a[href*="portal_change"]'));
if(a.portal&&pl.length){
var CL=/active|btn-danger|btn-primary|btn-warning|selected/;
var pick=function(f){var c=pl.filter(f);return c.length&&(c.length<pl.length||pl.length===1)?c:null;};
var act=pick(function(e){return CL.test(String(e.className||''));})||pick(function(e){var m=String(W.getComputedStyle(e).backgroundColor).match(/[\d.]+/g)||[];var r=+m[0],g=+m[1],b=+m[2],al=m.length>3?+m[3]:1;return al>0&&!(r===g&&g===b);});
if(act&&!act.some(function(e){return norm(e.innerText||e.textContent).toLowerCase()===String(a.portal).toLowerCase();}))throw new Error('KB_PERSONA');
}
var all=Array.prototype.slice.call(D.body.querySelectorAll('*'));
var vis=function(e){var r=e.getBoundingClientRect();return r.width>0&&r.height>0;};
var inSb=function(e){try{return !!e.closest(a.side);}catch(x){return false;}};
var iw=W.innerWidth,ih=W.innerHeight;
var rd=function(n){return Math.round(n*10)/10;};
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
var re=rx('email',true),m2;
while((m2=re.exec(low))){if(m2[1].toLowerCase().indexOf(String(a.domain).toLowerCase())<0)add('foreign_email_domain',true);}
if(rx('inst',false).test(low))add('institution_string',false);
if(cased.match(rx('course',true)))add('course_codes',false);
if(rx('disc',false).test(low))add('discipline_word',false);
var re2=rx('slug',true),m3;
var STOP=__SLUG_STOP__; while((m3=re2.exec(low))){if(STOP.indexOf(m3[1])<0)add('tenant_slug',true);}
var nl=0;
for(var i=0;i<low.length;i++){if(low.charCodeAt(i)>0x3FF)nl++;}
if(nl>=__NONLATIN__)add('non_latin_script',true);
return {v:a.v,u:location.pathname+location.search,iw:iw,ih:ih,dpr:W.devicePixelRatio,labels:labels,tn:{v:hard?'BLOCK':(k.length?'REVIEW':'CLEAR'),k:k}};
})
"""


def probe_source():
    """The probe as one JS function expression (evaluates to a function)."""
    src = _PROBE_JS.replace("__TENANT__", json.dumps(tenant_regexes())) \
                   .replace("__NONLATIN__", str(Q.NONLATIN_MIN)) \
                   .replace("__SLUG_STOP__", json.dumps(sorted(Q.SLUG_STOP)))
    return " ".join(l.strip() for l in src.strip().splitlines())


def install_action():
    """javascript_tool input that stores the probe in sessionStorage once."""
    src = probe_source()
    text = (f"var S={json.dumps(src)};sessionStorage.setItem('{KEY}',S);'{KEY}:'+S.length")
    return {"action": "javascript_exec", "tabId": "$TAB", "text": text}


def stub(args):
    """Short per-visit call: runs the stored probe, throws KB_NOPROBE if absent."""
    return (f"(sessionStorage.getItem('{KEY}')?(0,eval)(sessionStorage.getItem('{KEY}'))"
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


def visit_actions(base_url, visit, demo):
    """One recording per visit, no screenshot: the exported GIF's last frame is
    the settled page. The probe (javascript_tool) produces no frame."""
    park = demo.get("cursor_park") or [1465, 740]
    return [_act("gif_creator", action="start_recording"),
            _act("navigate", url=base_url + visit["url"]),
            _wait(settle_seconds(demo, visit.get("wait_s"))),
            _act("computer", action="hover", coordinate=list(park)),
            _act("javascript_tool", action="javascript_exec", text=stub(visit["args"])),
            _act("gif_creator", action="stop_recording"),
            _export(visit["gif"])]


def canary_actions(base_url, first_persona, demo, gif):
    entry = (demo.get("portal_entry") or {}).get(first_persona) or "/"
    park = demo.get("cursor_park") or [1465, 740]
    return [_act("gif_creator", action="start_recording"),
            _act("navigate", url=base_url + entry),
            _wait(CANARY_WAIT_S),
            _act("computer", action="hover", coordinate=list(park)),
            _act("gif_creator", action="stop_recording"),
            _export(gif)]


def build_calls(plan, inputs, routes, per_batch=3):
    demo = routes["demo"]
    gif_dir = f"{DL_ROOT}/{plan.get('run_id') or 'run'}"
    surface_wait = {(s.get("persona"), s.get("surface")): s.get("wait_s")
                    for s in routes.get("surfaces", []) if s.get("wait_s") is not None}
    base = demo["base_url"]
    entry = demo.get("portal_entry") or {}
    labels_of = demo.get("portal_label") or {}
    visits = []
    for c in plan["capture_plan"]:
        if not c.get("url"):
            continue
        per_ticket = {t: list((inputs.get(t) or {}).get("labels", []))[:MAX_LABELS]
                      for t in c.get("tickets", [])}
        flat = []
        for ls in per_ticket.values():
            for l in ls:
                if l not in flat:
                    flat.append(l)
        v = {"visit_id": f"v{len(visits) + 1:02d}", "persona": c["persona"],
             "surface": c["surface"], "url": c["url"], "tickets": list(c.get("tickets", [])),
             "session": c.get("session", "unknown"), "labels": per_ticket}
        v["gif"] = f"{gif_dir}/{v['visit_id']}.gif"
        wait = c.get("wait_s", surface_wait.get((c["persona"], c["surface"])))
        if wait is not None:
            v["wait_s"] = wait
        v["args"] = {"v": PROBE_VERSION, "login": demo["login_path"],
                     "portal": labels_of.get(c["persona"]), "labels": flat,
                     "side": demo.get("sidebar_selector", ""),
                     "allow": list(demo.get("allow_strings", [])), "domain": demo["domain"]}
        visits.append(v)
    batches, seen = [], set()
    personas = []
    for v in visits:
        if v["persona"] not in personas:
            personas.append(v["persona"])
    for persona in personas:
        mine = [v for v in visits if v["persona"] == persona]
        for n in range(0, len(mine), max(1, per_batch)):
            chunk = mine[n:n + max(1, per_batch)]
            actions = []
            if persona not in seen and entry.get(persona):
                actions += [_act("navigate", url=base + entry[persona]), _wait()]
            seen.add(persona)
            for v in chunk:
                actions += visit_actions(base, v, demo)
            batches.append({"batch_id": f"b{len(batches) + 1:02d}", "persona": persona,
                            "visit_ids": [v["visit_id"] for v in chunk], "actions": actions})
    home = (demo.get("portal_entry") or {}).get(demo.get("home_portal", "admin"))
    restore = [_act("navigate", url=base + home), _wait()] if home else []
    canary_gif = f"{gif_dir}/canary.gif"
    return {"run_id": plan.get("run_id"), "base_url": base, "probe_version": PROBE_VERSION,
            "gif_dir": gif_dir, "install": install_action(), "visits": visits,
            "batches": batches, "restore": restore,
            "abort": [_act("gif_creator", action="clear")],
            "canary": {"gif": canary_gif,
                       "actions": canary_actions(base, personas[0] if personas else "admin",
                                                 demo, canary_gif)}}


def _j(x):
    return "```json\n" + json.dumps(x, indent=1) + "\n```"


def capture_md(calls):
    L = ["# KB capture — instructions for the capturer", "",
         "You only drive the user's Chrome. Never type, never click, never close a tab. "
         "Replace the string `\"$TAB\"` with the numeric tab id (no quotes) in every action "
         "before sending it.", "",
         "## 1. Tab", "",
         "1. Load the Chrome tools with ToolSearch (`select:mcp__claude-in-chrome__tabs_context_mcp,"
         "mcp__claude-in-chrome__browser_batch,mcp__claude-in-chrome__javascript_tool,"
         "mcp__claude-in-chrome__navigate,mcp__claude-in-chrome__computer,"
         "mcp__claude-in-chrome__tabs_create_mcp,mcp__claude-in-chrome__gif_creator`). If they do not load, or `tabs_context_mcp` "
         "fails, reply `KB_CAPTURE NO_CHROME_TOOLS` and stop.",
         "2. Call `tabs_context_mcp`; use the tab it returns (create one with `tabs_create_mcp` only "
         "if there is none). That tab's numeric id is `$TAB`.", "",
         "## 2. Install the probe (once)", "",
         "Send this one `javascript_tool` call. It must return `kbp:<number>`.", "",
         _j(calls["install"]), ""]
    L += [f"## 3. Batches ({len(calls['batches'])})", "",
          "For each batch, in order, call `browser_batch` with exactly the `actions` array below. "
          "Visits (ids in the heading) are in order; each visit is one `gif_creator` recording: "
          "start_recording, navigate, wait, hover, probe, stop_recording, export. There are no "
          "screenshots: never call `computer` with `screenshot`, and you never receive a page "
          "image. The probe result is the `javascript_tool` result. The `export` result says "
          "`Downloaded \"<filename>\"`; that filename is the visit's `gif`.", ""]
    for b in calls["batches"]:
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
          "back as text) — do not edit it.", "",
          "## 5. Stop rules", "",
          "- An error containing `KB_LOGIN`: status `expired`, write the file, stop.",
          "- An error containing `KB_PERSONA`: status `wrong_persona`, write the file, stop.",
          "- An error containing `KB_NOPROBE`: send the install action again once, then retry that "
          "batch once. If it fails again: status `no_probe`, write the file, stop.",
          "- A transport or bridge error (tool unavailable, extension disconnected): status "
          "`bridge_down`, write the file, stop.",
          "- Otherwise status `ok` and continue with the next batch.", "",
          "**Abort rule.** After any stop rule above fires (except a bridge error, when the tool "
          "is unreachable), send this once with `browser_batch` so a half recording is never "
          "exported later, then write the raw file:", "",
          _j(calls["abort"]), "",
          "## 6. Restore (always, after the last batch or after a stop)", "",
          "Send this once with `browser_batch` so the browser returns to the home portal:", "",
          _j(calls["restore"]), "",
          "Reply `KB_CAPTURE_DONE ok=<batches written with status ok> stop=<first non-ok status|none>`.", ""]
    return "\n".join(L)


def cmd_plan_calls(a):
    plan = json.loads(Path(a.plan).read_text())
    inputs = json.loads(Path(a.inputs).read_text())
    routes = json.loads(Path(a.routes).read_text())
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    calls = build_calls(plan, inputs, routes, a.per_batch)
    (out_dir / "calls.json").write_text(json.dumps(calls, indent=1))
    (out_dir / "capture.md").write_text(capture_md(calls))
    skipped = sum(1 for c in plan["capture_plan"] if not c.get("url"))
    print(f"planned {len(calls['visits'])} visits in {len(calls['batches'])} batches "
          f"({skipped} without a URL skipped) -> {out_dir / 'capture.md'}")
    emit({"ok": True, "action": "plan-calls", "visits": len(calls["visits"]),
          "batches": len(calls["batches"]), "skipped": skipped,
          "calls": str(out_dir / "calls.json"), "capture_md": str(out_dir / "capture.md")})
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


def frame_problem(img_w, img_h, probe):
    """Why a frame can't carry anchors, or None."""
    iw, ih = probe["iw"], probe.get("ih") or 0
    if img_w < F.SHOT_PX_W:
        return "low_resolution"
    if ih and abs((img_h / img_w) - (ih / iw)) / (ih / iw) > MAX_ASPECT_DRIFT:
        return "aspect_mismatch"
    return None


def _load_raw(raw_dir):
    """visit_id -> item, batch_id -> raw (later files / items win)."""
    items, batches = {}, {}
    for f in sorted(Path(raw_dir).glob("*.json")):
        try:
            r = json.loads(f.read_text())
        except ValueError:
            continue
        bid = r.get("batch_id") or f.stem
        batches[bid] = r
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
    """Wait for the exported GIF, move it into shots/gifs/, extract its last
    frame -> (frame_path, reason)."""
    if not downloads_dir:
        return None, "download_missing"
    src = Path(downloads_dir) / str(item["gif"])
    status, _ = wait_for_file(src, timeout_s)
    if status != "ready":
        return None, _WAIT_REASON[status]
    gifs = out_dir / "gifs"
    gifs.mkdir(parents=True, exist_ok=True)
    kept = gifs / f"{visit['visit_id']}.gif"
    shutil.move(str(src), str(kept))
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
        source = f"chrome:{calls['base_url'].split('//')[-1]}{probe.get('u', visit['url'])} {now}"
        for tid in visit["tickets"]:
            for label in (visit["labels"].get(tid) or []):
                pl = by_label.get(label)
                if not pl or not pl.get("f") or not pl.get("b"):
                    continue
                ev = None
                if not problem:
                    x, y, w, h = pl["b"]
                    box = to_image_box({"x": x, "y": y, "width": w, "height": h}, scale)
                    crop = Q.verify_crop(im, box, [label])[0] if use_ocr else None
                    ev = shot_evidence(label, pl.get("t", ""), box, crop,
                                       bool(pl.get("oc")) or not pl.get("iv"),
                                       tenant_dom, tenant_ocr)
                entries.append({"ticket": tid, "persona": visit["persona"],
                                "surface": visit["surface"], "frame": str(dest),
                                "verified": ev is not None, "source": source,
                                "visit_id": visit["visit_id"], "sidebar": bool(pl.get("sb")),
                                "frame_source": frame_source,
                                **(ev or {"label": label})})
    return entries, None


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

    done, reasons, new = set(), {}, []
    ingested = set()
    fetched = 0
    for v in calls["visits"]:
        vid = v["visit_id"]
        key = f"{v['persona']}/{v['surface']}"
        if vid in items:
            timeout = FIRST_FILE_TIMEOUT_S if not fetched else NEXT_FILE_TIMEOUT_S
            if items[vid].get("gif"):
                fetched += 1
            entries, reason = ingest_visit(v, items[vid], calls, demo, out_dir, use_ocr, now,
                                           a.downloads_dir, timeout)
            if reason is None:
                ingested.add(vid)
                done.add((v["persona"], v["surface"]))
                new += entries
                continue
        else:
            b = batches.get(batch_of.get(vid, {}).get("batch_id"))
            st = (b or {}).get("status", "ok")
            reason = "not_reached" if not b else (st if st != "ok" else "not_reached")
        reasons[key] = reason

    if a.downloads_dir and calls.get("gif_dir"):
        for d in (Path(a.downloads_dir) / calls["gif_dir"], Path(a.downloads_dir) / DL_ROOT):
            try:
                d.rmdir()                                        # only when empty
            except OSError:
                pass
    kept = [s for s in manifest["shots"] if s.get("visit_id") not in ingested]
    n = max([int(re.sub(r"\D", "", s.get("shot_id", "")) or 0) for s in kept] + [0])
    for e in new:
        n += 1
        kept.append({"shot_id": f"s{n}", **e})
    manifest["shots"] = kept
    manifest_path.write_text(json.dumps(manifest, indent=1))
    verified = sum(1 for e in new if e["verified"])

    plan = {"run_id": calls.get("run_id"),
            "capture_plan": [{"persona": v["persona"], "surface": v["surface"], "url": v["url"],
                              "recipe": [], "tickets": v["tickets"], "session": v.get("session", "unknown")}
                             for v in calls["visits"]]}
    rp = resume_plan(plan, done)
    left = len(rp["capture_plan"])
    resume_path = out_dir / "resume.json"
    if left:
        resume_path.write_text(json.dumps({**rp, "left_reasons": reasons}, indent=1))
        print(f"ingested {len(ingested)} visits · {len(new)} shots · {verified} verified · "
              f"{left} left ({', '.join(sorted(set(reasons.values())))}) -> {resume_path}")
        emit({"ok": False, "action": "ingest", "shots": len(new), "verified": verified,
              "visits": len(ingested), "left": left, "left_reasons": reasons, "ocr": use_ocr})
        return 3
    if resume_path.exists():
        resume_path.unlink()
    print(f"ingested {len(ingested)} visits · {len(new)} shots · {verified} verified")
    emit({"ok": True, "action": "ingest", "shots": len(new), "verified": verified,
          "visits": len(ingested), "left": 0, "left_reasons": {}, "ocr": use_ocr})
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
    src.unlink()
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
    s = sub.add_parser("ingest"); s.add_argument("--calls", required=True)
    s.add_argument("--raw-dir", required=True); s.add_argument("--out-dir", required=True)
    s.add_argument("--routes", default=str(DEFAULT_ROUTES)); s.add_argument("--no-ocr", action="store_true")
    s.add_argument("--downloads-dir")
    s = sub.add_parser("pickup"); s.add_argument("--downloads-dir", required=True)
    s.add_argument("--name", required=True); s.add_argument("--timeout", type=float, default=20.0)
    s = sub.add_parser("sweep"); s.add_argument("--downloads-dir", required=True)
    s.add_argument("--into", required=True)
    a = ap.parse_args(argv)
    try:
        return {"login": cmd_login, "check": cmd_check, "run": cmd_run,
                "plan-calls": cmd_plan_calls, "ingest": cmd_ingest,
                "pickup": cmd_pickup, "sweep": cmd_sweep}[a.cmd](a)
    except (OSError, json.JSONDecodeError, KeyError) as e:
        emit({"ok": False, "action": a.cmd, "error": f"{type(e).__name__}: {e}"})
        return 1


if __name__ == "__main__":
    sys.exit(main())
