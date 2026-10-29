#!/usr/bin/env python3
"""
qa_gif_to_shots.py — turn a QA1 run GIF into an article shot list.

Three jobs, all of which were verified against real Medtrics QA GIFs:

  1. SEGMENT   split the GIF into stable UI states (one settled frame per state)
  2. VERIFY    confirm a candidate UI label is actually on screen (OCR as verifier,
               never as transcriber — transcription mis-reads e.g. MCAP5010 as MCAPSO10)
  3. SCREEN    flag frames that show live or client-shaped tenant data, which must
               never reach a published article

Usage
  python3 qa_gif_to_shots.py segment  RUN.gif --out shots/
  python3 qa_gif_to_shots.py verify   shots/state08.png --labels "Include child keywords" "Keywords & Themes"
  python3 qa_gif_to_shots.py screen   shots/ --demo-domain medtrics.dev
  python3 qa_gif_to_shots.py all      RUN.gif --out shots/ --labels-file labels.txt

Requires: pillow, numpy, pytesseract + the tesseract binary.
"""
import argparse, difflib, json, os, re, sys

import numpy as np
from PIL import Image, ImageSequence

# ── tuning, all values measured rather than guessed ──────────────────────────
STATE_CHANGE_PCT = 3.0    # % of pixels differing to call a frame a new UI state
PIXEL_DELTA = 24          # per-pixel RGB sum delta that counts as "changed"
LABEL_CUTOFF = 0.82       # fuzzy-match floor; measured separation was 0.98+ vs 0.52-
OCR_MIN_CONF = 60


# ── 1. SEGMENT ───────────────────────────────────────────────────────────────
def segment(gif_path, out_dir, change_pct=STATE_CHANGE_PCT):
    """A QA GIF is ~1.3s per frame — effectively one frame per settled UI state.
    Group runs of near-identical frames; the LAST frame of a run is the settled one."""
    os.makedirs(out_dir, exist_ok=True)
    im = Image.open(gif_path)
    frames = [f.convert("RGB").copy() for f in ImageSequence.Iterator(im)]
    arrs = [np.asarray(f, dtype=np.int16) for f in frames]

    groups, cur = [], [0]
    for i in range(1, len(arrs)):
        d = np.abs(arrs[i] - arrs[i - 1]).sum(axis=2)
        pct = 100.0 * (d > PIXEL_DELTA).sum() / d.size
        if pct >= change_pct:
            groups.append(cur)
            cur = [i]
        else:
            cur.append(i)
    groups.append(cur)

    shots = []
    for si, grp in enumerate(groups):
        rep = grp[-1]
        bbox = None
        if si > 0:
            prev_rep = groups[si - 1][-1]
            d = np.abs(arrs[grp[0]] - arrs[prev_rep]).sum(axis=2)
            m = d > PIXEL_DELTA
            if m.any():
                ys, xs = np.where(m)
                bbox = [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]
        name = f"state{si:02d}.png"
        frames[rep].save(os.path.join(out_dir, name))
        shots.append({
            "state": si, "file": name, "frames": grp, "rep_frame": rep,
            "change_bbox": bbox,
            "full_page_change": bool(bbox and (bbox[2] - bbox[0]) > 0.9 * im.size[0]),
        })

    meta = {"source": os.path.basename(gif_path), "size": list(im.size),
            "frames": len(frames), "states": len(shots), "shots": shots}
    with open(os.path.join(out_dir, "shots.json"), "w") as fh:
        json.dump(meta, fh, indent=1)
    return meta


# ── 2. VERIFY ────────────────────────────────────────────────────────────────
def _ocr_text(path, lower=True):
    import pytesseract
    text = re.sub(r"\s+", " ", pytesseract.image_to_string(Image.open(path)))
    return text.lower() if lower else text


def verify_labels(image_path, labels, cutoff=LABEL_CUTOFF):
    """Is this candidate label actually on screen?

    Candidates come from the QA checklist's quoted strings and `Navigate:` paths —
    i.e. a human wrote them. OCR only CONFIRMS. Measured on real frames: labels
    present scored 1.000 / 0.976; labels absent scored 0.400-0.516. Nothing landed
    in between, which is what makes the 0.82 cutoff safe."""
    hay = _ocr_text(image_path)
    toks = hay.split()
    out = []
    for label in labels:
        l = label.lower().strip()
        if l in hay:
            out.append({"label": label, "verdict": "EXACT", "score": 1.0})
            continue
        n = len(l.split())
        best = 0.0
        for i in range(max(0, len(toks) - n + 1)):
            cand = " ".join(toks[i:i + n])
            best = max(best, difflib.SequenceMatcher(None, l, cand).ratio())
        out.append({"label": label,
                    "verdict": "FUZZY" if best >= cutoff else "MISS",
                    "score": round(best, 3)})
    return out


# ── 3. SCREEN ────────────────────────────────────────────────────────────────
INSTITUTION_RE = re.compile(
    r"\b(University|College|School of|Hospital|Medical Center|Medical Centre|"
    r"Health System|Institute)\b", re.I)
# Hardened after a live miss: a BGU frame passed as CLEAR because OCR read
# "medicine" but dropped "School", so the phrase pattern never fired.
DISCIPLINE_RE = re.compile(
    r"\b(Medicine|Nursing|Veterinary|Pharmacy|Dental|Osteopathic|Allied Health)\b", re.I)
# a tenant slug: short lowercase alphanumeric run that is not a common word
SLUG_RE = re.compile(r"\b([a-z]{2,6}(?:med|vet|nurse|univ|edu|test)[a-z0-9]{0,6})\b")
# Ordinary words SLUG_RE also matches ("sch-edu-le", "na-med", "la-test"). Live on
# medtrics.medtricslab.com every page tripped it via the "Schedule" header button,
# so every frame screened BLOCK; the next run tripped on "schedulers". These are never
# tenant slugs. Whole words first, then stems (see slug_is_word): a stem covers every
# inflection (s/ed/ing/er/ers) without listing each one.
SLUG_STOP = frozenset("""
medtrics medtricslab
schedule schedules scheduled scheduler schedulers scheduling unscheduled rescheduled
named renamed unnamed nicknamed surnamed misnamed
armed formed informed uninformed reformed performed transformed preformed deformed
confirmed unconfirmed claimed unclaimed reclaimed assumed presumed consumed resumed
deemed redeemed esteemed seemed framed reframed aimed timed untimed trimmed summed
filmed welcomed programmed harmed alarmed charmed warmed stemmed skimmed streamed
dreamed teamed beamed blamed flamed famed tamed homed bloomed zoomed groomed
latest fastest greatest shortest smartest lightest brightest tightest slightest
hottest wettest contest protest attest detest pretest posttest retest untested
ashamed shamed inflamed maimed plumed palmed roomed bottomed rimmed dimmed seamed
uniformed malformed disarmed firearmed someday
""".split())
# A matched word is also ignored when it starts with one of these (after one re/un
# prefix is stripped) or contains one of STOP_HAS / STOP_LEAD. Verified by running
# SLUG_RE over /usr/share/dict/words (696 hits) and the words in the captured probe text:
#   schedul   schedule(s/d/r/rs), scheduling, rescheduling, unscheduled
#   procedur  procedure(s), procedural        (pro-c-edu-re)
#   contest protest attest detest pretest posttest retest untest
#             contested, contestant, attestation, protester(s), retesting ...
#   latest fastest greatest ...   superlatives ending in -test
#   intermedi immedi remedi comedi comedy   intermediate, immediately, remedy, comedian
#   velvet rivet privet covet dovetail credul intestin   ordinary words with vet/edu/test
STOP_STEMS = ("contest", "protest", "attest", "detest", "pretest",
              "posttest", "retest", "untest", "latest", "fastest", "greatest", "shortest",
              "smartest", "lightest", "brightest", "tightest", "slightest", "hottest",
              "wettest", "intermedi", "immedi", "remedi", "remedy", "comedi", "comedy",
              "premedit", "premedic", "nonmedic", "unmedic", "velvet", "rivet", "privet", "revet",
              "covet", "dovetail", "credul", "incredul", "intestin", "intestat")
# Word-START stems (after an optional prefix in STOP_STRIP): mediate/median/medium/medieval,
# meddle, medullary, medusa, university/universal/universe/universities, education/educator(s),
# coeducation, reduce/reduction, deduct, seduce, aqueduct. Anchored at the start so a tenant
# slug that merely ENDS in one ("uwmedicine", "ucmedical", "stateuniversity") stays flagged.
STOP_LEAD = ("medi", "meddl", "medul", "medus", "universi", "universal", "universe",
             "educat", "reduc", "deduc", "seduc", "aqueduct")
# Unambiguous anywhere in the word: schedule(s/d/r), scheduling, "noschedule" (OCR runs
# "No schedule" together), procedure(s), "noprocedure".
STOP_HAS = ("schedul", "procedur")
STOP_STRIP = ("re", "un", "in", "im", "co", "pre", "inter", "non")


def slug_is_word(w):
    """True when a SLUG_RE match is an ordinary English word, not a tenant slug."""
    cands = [w] + [w[len(p):] for p in STOP_STRIP if w.startswith(p)]
    if any(c in SLUG_STOP for c in cands) or any(h in w for h in STOP_HAS):
        return True
    return any(c.startswith(st) for c in cands for st in STOP_STEMS + STOP_LEAD)


def slug_stop_spec():
    """The same constants as plain JSON, for the JS probe (one source of truth)."""
    return {"words": sorted(SLUG_STOP), "stems": list(STOP_STEMS),
            "has": list(STOP_HAS), "lead": list(STOP_LEAD), "strip": list(STOP_STRIP)}


NONLATIN_MIN = 3
EMAIL_RE = re.compile(r"[\w.+-]+@([\w-]+\.[\w.-]+)")
COURSE_CODE_RE = re.compile(r"\b[A-Z]{2,5}\d{3,5}(?:\.[A-Za-z0-9]+)*\b")


def screen_text(raw, demo_domain="medtrics.dev", client_names=(), allow_strings=()):
    """Default deny over a block of text — OCR output or a page's DOM text.

    allow_strings are the demo tenant's own synthetic names ("Medtrics Medical
    Center"); they are removed before matching so a demo frame is not flagged
    for the very data that makes it safe. Matching is case-insensitive except
    for course codes, which are upper-case by shape, so raw keeps its case."""
    for a in allow_strings:
        raw = re.sub(re.escape(a), " ", raw, flags=re.I)
    cased, raw = raw, raw.lower()
    findings = []

    for dom in set(EMAIL_RE.findall(raw)):
        if demo_domain.lower() not in dom.lower():
            findings.append({"kind": "foreign_email_domain", "value": dom})

    for m in INSTITUTION_RE.finditer(raw):
        ctx = raw[max(0, m.start() - 45): m.end() + 35].strip()
        findings.append({"kind": "institution_string", "value": ctx[:90]})

    codes = set(COURSE_CODE_RE.findall(cased))
    if codes:
        findings.append({"kind": "course_codes", "value": sorted(codes)[:8]})

    for m in DISCIPLINE_RE.finditer(raw):
        findings.append({"kind": "discipline_word", "value": m.group(1)})
        break

    slugs = {s for s in SLUG_RE.findall(raw) if not slug_is_word(s)}
    if slugs:
        findings.append({"kind": "tenant_slug", "value": sorted(slugs)[:6]})

    nonlatin = sum(1 for c in raw if ord(c) > 0x3FF)
    if nonlatin >= NONLATIN_MIN:
        findings.append({"kind": "non_latin_script", "value": nonlatin})

    for name in client_names:
        if name.lower() in raw:
            findings.append({"kind": "known_client_name", "value": name})

    hard = [f for f in findings
            if f["kind"] in ("foreign_email_domain", "known_client_name",
                             "tenant_slug", "non_latin_script")]
    verdict = "BLOCK" if hard else ("REVIEW" if findings else "CLEAR")
    return {"verdict": verdict, "findings": findings}


def screen_frame(path, demo_domain="medtrics.dev", client_names=(), allow_strings=()):
    """Default deny. A frame is publishable only if nothing client-shaped is on it.

    Measured: one run was clean synthetic data (@medtrics.dev, 'Institution 1562',
    'Program 1001'); another showed real-looking course codes and faculty ids.
    It varies per run, so this has to be checked per frame, never assumed."""
    r = screen_text(_ocr_text(path, lower=False), demo_domain, client_names, allow_strings)
    return {"file": os.path.basename(path), **r}


_RANK = {"CLEAR": 0, "REVIEW": 1, "BLOCK": 2}


def worst(*verdicts):
    """The strictest of several screen verdicts (DOM and OCR read differently)."""
    return max(verdicts, key=lambda v: _RANK[v])


def verify_crop(img, box, labels, pad=12, cutoff=LABEL_CUTOFF):
    """OCR-verify labels inside one element's box rather than the whole frame.

    The DOM says where the element is; OCR on that crop proves the pixels
    actually show the label (not covered, clipped or behind an overlay). The
    crop is cheaper and reads more reliably than full-frame OCR. box is
    (x, y, w, h) in image pixels."""
    import pytesseract
    x, y, w, h = box
    crop = img.crop((max(0, x - pad), max(0, y - pad),
                     min(img.width, x + w + pad), min(img.height, y + h + pad)))
    crop = crop.resize((crop.width * 2, crop.height * 2))
    hay = re.sub(r"\s+", " ", pytesseract.image_to_string(crop)).lower()
    toks = hay.split()
    out = []
    for label in labels:
        l = label.lower().strip()
        if l and l in hay:
            out.append({"label": label, "verdict": "EXACT", "score": 1.0})
            continue
        n = len(l.split())
        best = 0.0
        for i in range(max(0, len(toks) - n + 1)):
            best = max(best, difflib.SequenceMatcher(None, l, " ".join(toks[i:i + n])).ratio())
        out.append({"label": label, "verdict": "FUZZY" if best >= cutoff else "MISS",
                    "score": round(best, 3)})
    return out


# ── CLI ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("segment"); s.add_argument("gif"); s.add_argument("--out", required=True)
    s.add_argument("--change-pct", type=float, default=STATE_CHANGE_PCT)

    v = sub.add_parser("verify"); v.add_argument("image")
    v.add_argument("--labels", nargs="+", required=True)
    v.add_argument("--cutoff", type=float, default=LABEL_CUTOFF)

    c = sub.add_parser("screen"); c.add_argument("dir")
    c.add_argument("--demo-domain", default="medtrics.dev")
    c.add_argument("--client-names", nargs="*", default=[])

    a = sub.add_parser("all"); a.add_argument("gif"); a.add_argument("--out", required=True)
    a.add_argument("--labels-file"); a.add_argument("--demo-domain", default="medtrics.dev")

    args = ap.parse_args()

    if args.cmd == "segment":
        print(json.dumps(segment(args.gif, args.out, args.change_pct), indent=1))

    elif args.cmd == "verify":
        for r in verify_labels(args.image, args.labels, args.cutoff):
            print(f"{r['verdict']:6s} {r['score']:5.3f}  {r['label']}")

    elif args.cmd == "screen":
        for f in sorted(os.listdir(args.dir)):
            if f.endswith(".png"):
                r = screen_frame(os.path.join(args.dir, f), args.demo_domain, args.client_names)
                print(f"{r['verdict']:6s} {r['file']}  ({len(r['findings'])} findings)")

    elif args.cmd == "all":
        meta = segment(args.gif, args.out)
        print(f"{meta['frames']} frames -> {meta['states']} states  ({meta['size'][0]}x{meta['size'][1]})")
        labels = []
        if args.labels_file:
            labels = [l.strip() for l in open(args.labels_file) if l.strip()]
        report = []
        for shot in meta["shots"]:
            p = os.path.join(args.out, shot["file"])
            entry = screen_frame(p, args.demo_domain)
            if labels:
                entry["labels"] = verify_labels(p, labels)
            report.append(entry)
            found = [l["label"] for l in entry.get("labels", []) if l["verdict"] != "MISS"]
            print(f"  {shot['file']}  {entry['verdict']:6s}  labels_on_screen={found}")
        with open(os.path.join(args.out, "report.json"), "w") as fh:
            json.dump(report, fh, indent=1)


if __name__ == "__main__":
    main()
