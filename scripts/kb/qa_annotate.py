#!/usr/bin/env python3
"""
qa_annotate.py — turn an aligned GIF state into an annotated article screenshot.

The QA GIFs carry a click indicator. Finding it gives the annotation anchor for
free: the point the step is about.

Detection is by TRANSIENCE, not colour — Medtrics brand orange is everywhere in
the UI, so a colour filter returns thousands of pixels. A click indicator is the
thing that appears in one frame and is gone in both neighbours. Connected-
component labelling then keeps only compact blobs of plausible marker size.

One exclusion matters: a 36x25 blob recurs at roughly (50, 20) on most frames.
That is the capture harness's own anti-dedup frame marker, not a click. The
top-left corner is therefore ignored.

  annotate  RUN.gif --state 3 --out shot.png --number 1 --caption "Open Step 3"
  batch     RUN.gif --alignment shots/alignment.json --out annotated/
"""
import argparse, json, os, sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageSequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kb_format as F

FRAME_MARKER_BOX = (0, 0, 120, 60)      # harness marker, never a click
MIN_W, MAX_W = 15, 220
MIN_H, MAX_H = 10, 90
MIN_PX = 60
DELTA = 40

def _rgb(hexstr):
    return tuple(int(hexstr[i:i + 2], 16) for i in (0, 2, 4))


ACCENT = _rgb(F.ORANGE)                  # rings and badges
INK = _rgb(F.NAVY)
RED = _rgb(F.RED)                        # watermark only — never a ring


def _frames(gif):
    im = Image.open(gif)
    return [f.convert("RGB").copy() for f in ImageSequence.Iterator(im)]


def find_click(frames, i):
    """The strongest compact transient on frame i, excluding the harness marker."""
    from scipy import ndimage       # GIF path only; live capture never needs it
    if i <= 0 or i >= len(frames) - 1:
        return None
    a = [np.asarray(f, dtype=np.int16) for f in (frames[i - 1], frames[i], frames[i + 1])]
    m = (np.abs(a[1] - a[0]).sum(axis=2) > DELTA) & (np.abs(a[1] - a[2]).sum(axis=2) > DELTA)
    m = ndimage.binary_closing(m, structure=np.ones((3, 3)))
    lab, n = ndimage.label(m)
    best = None
    for s in ndimage.find_objects(lab):
        h = s[0].stop - s[0].start
        w = s[1].stop - s[1].start
        px = int((lab[s] > 0).sum())
        cx, cy = s[1].start + w // 2, s[0].start + h // 2
        if (FRAME_MARKER_BOX[0] <= cx <= FRAME_MARKER_BOX[2]
                and FRAME_MARKER_BOX[1] <= cy <= FRAME_MARKER_BOX[3]):
            continue
        if MIN_W <= w <= MAX_W and MIN_H <= h <= MAX_H and px >= MIN_PX:
            if best is None or px > best["px"]:
                best = {"px": px, "x": cx, "y": cy, "w": w, "h": h}
    return best


def find_label_box(img, label, cutoff=0.82):
    """Locate a VERIFIED label's own bounding box via OCR word boxes.

    This is a better anchor than the click indicator. The click marker is
    semi-transparent and sits over arbitrary backgrounds, so detecting it is
    unreliable; and anchoring on the label gives a stronger guarantee — the text
    the article prints in bold and the element the screenshot rings are then
    provably the same thing.

    Multi-word labels are matched as a contiguous run on the same OCR line."""
    import difflib, pytesseract
    # Output.DICT, not DATAFRAME: the dataframe form silently needs pandas.
    d = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    words = [(str(t).strip(), int(l), int(tp), int(w), int(h), int(ln), int(bk))
             for t, c, l, tp, w, h, ln, bk in zip(d["text"], d["conf"], d["left"], d["top"],
                                                  d["width"], d["height"], d["line_num"],
                                                  d["block_num"])
             if str(t).strip() and float(c) > 30]
    target = label.lower().split()
    n = len(target)
    best, best_score = None, 0.0
    for i in range(len(words) - n + 1):
        run = words[i:i + n]
        if len({(w[6], w[5]) for w in run}) != 1:        # same block and line
            continue
        cand = " ".join(w[0] for w in run).lower()
        sc = difflib.SequenceMatcher(None, " ".join(target), cand).ratio()
        if sc > best_score:
            x0 = min(w[1] for w in run); y0 = min(w[2] for w in run)
            x1 = max(w[1] + w[3] for w in run); y1 = max(w[2] + w[4] for w in run)
            best, best_score = {"x": (x0 + x1) // 2, "y": (y0 + y1) // 2,
                                "w": x1 - x0, "h": y1 - y0, "score": round(sc, 3)}, sc
    return best if best_score >= cutoff else None


def click_for_state(frames, group, next_first=None):
    """A step's click is usually the action that ENDS its state — search the
    state's own frames, then the frame that opens the next one."""
    for i in list(group) + ([next_first] if next_first is not None else []):
        c = find_click(frames, i)
        if c:
            return c, i
    return None, None


FONT_LADDER = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",     # Linux / Cowork
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",       # macOS
    "/Library/Fonts/Arial Bold.ttf",
)


def _font(size):
    """KB_FONT env first, then the ladder. The last resort is Pillow's scalable
    default (Pillow >= 10.1), never the tiny fixed bitmap font."""
    for p in (os.environ.get("KB_FONT"),) + FONT_LADDER:
        if p and os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def watermark(img, text="NOT FOR PUBLICATION — tenant data"):
    """A frame that fails the tenant screen is a re-capture reference, never an
    asset. Burn that into the pixels so it cannot be lost downstream."""
    d = ImageDraw.Draw(img, "RGBA")
    f = _font(26)
    tb = d.textbbox((0, 0), text, font=f)
    w, h = tb[2] - tb[0], tb[3] - tb[1]
    x, y = (img.width - w) // 2, int(img.height * 0.08)
    d.rectangle([x - 16, y - 10, x + w + 16, y + h + 14], fill=RED + (225,))
    d.text((x, y), text, font=f, fill=(255, 255, 255))
    d.rectangle([0, 0, img.width - 1, img.height - 1], outline=RED, width=6)
    return img


def anchor_from_box(box):
    """A DOM bounding box (x, y, w, h; top-left origin) as the centre-based
    anchor annotate() takes."""
    x, y, w, h = box
    return {"x": int(x + w / 2), "y": int(y + h / 2), "w": int(w), "h": int(h)}


def annotate(frame, click, number=None, caption=None, pad=F.RING_PAD_PX):
    """Ring the anchor, number it, and (GIF path only) caption the shot. The KB
    renderer prints the caption under the image, so the KB path passes no
    caption and gets no bar. Nothing is cropped — a reader needs the
    surrounding page to orient."""
    img = frame.convert("RGB").copy()
    d = ImageDraw.Draw(img, "RGBA")

    if click:
        x, y = click["x"], click["y"]
        rw = max(click["w"], 34) // 2 + pad
        rh = max(click["h"], 24) // 2 + pad // 2
        # Keep the ring (and its 3 px halo) inside the frame: a control at the
        # page edge otherwise gets a ring cut off by the border.
        m = 5
        box = [max(m, x - rw), max(m, y - rh),
               min(img.width - 1 - m, x + rw), min(img.height - 1 - m, y + rh)]
        d.rounded_rectangle(box, radius=F.RING_RADIUS_PX, outline=ACCENT,
                            width=F.RING_WIDTH_PX)
        d.rounded_rectangle([box[0] - 3, box[1] - 3, box[2] + 3, box[3] + 3],
                            radius=F.RING_RADIUS_PX + 2, outline=ACCENT + (70,), width=3)
        if number is not None:
            r = F.BADGE_RADIUS_PX
            bx, by = box[0] - r, box[1] - r
            # A badge must never sit on content: a ring that starts in the left
            # band gets its badge snapped onto the dark sidebar, with a leader.
            if box[0] < F.BADGE_SIDEBAR_SNAP * img.width:
                bx, by = int(0.045 * img.width), y
                d.line([bx + r, by, box[0] - 2, by], fill=ACCENT, width=3)
            bx = min(max(r + 2, bx), img.width - r - 2)
            by = min(max(r + 2, by), img.height - r - 2)
            d.ellipse([bx - r, by - r, bx + r, by + r], fill=ACCENT)
            f = _font(20)
            t = str(number)
            tw = d.textbbox((0, 0), t, font=f)
            d.text((bx - (tw[2] - tw[0]) / 2, by - (tw[3] - tw[1]) / 2 - 2), t,
                   font=f, fill=(255, 255, 255))

    if caption:
        f = _font(19)
        bar = 44
        out = Image.new("RGB", (img.width, img.height + bar), (255, 255, 255))
        out.paste(img, (0, 0))
        d2 = ImageDraw.Draw(out)
        d2.rectangle([0, img.height, img.width, img.height + bar], fill=(246, 247, 250))
        d2.line([0, img.height, img.width, img.height], fill=(214, 218, 224), width=1)
        label = f"{number}. {caption}" if number is not None else caption
        d2.text((18, img.height + 12), label[:150], font=f, fill=INK)
        return out
    return img


def finalize(img, out, width=F.SHOT_PX_W):
    """Downscale to the shot width and save in the house format."""
    if img.width > width:
        img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
    img.convert("RGB").save(out, F.SHOT_FORMAT, quality=F.SHOT_QUALITY)
    return out


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("batch")
    b.add_argument("gif")
    b.add_argument("--shots", required=True, help="dir holding shots.json")
    b.add_argument("--alignment", help="alignment.json; defaults to <shots>/alignment.json")
    b.add_argument("--captions", help="JSON {step_id: caption}")
    b.add_argument("--anchors", help="JSON {step_id: label-to-ring}")
    b.add_argument("--screen", action="store_true",
                   help="run the tenant screen and watermark anything not CLEAR")
    b.add_argument("--out", required=True)
    a = ap.parse_args()

    frames = _frames(a.gif)
    shots = json.load(open(os.path.join(a.shots, "shots.json")))
    align = json.load(open(a.alignment or os.path.join(a.shots, "alignment.json")))
    caps = json.load(open(a.captions)) if a.captions else {}
    anchors = json.load(open(a.anchors)) if a.anchors else {}
    by_file = {s["file"]: s for s in shots["shots"]}
    os.makedirs(a.out, exist_ok=True)

    n = 0
    for entry in align:
        if not entry.get("frame"):
            continue
        shot = by_file.get(entry["frame"])
        if not shot:
            continue
        n += 1
        nxt = shot["state"] + 1
        next_first = (shots["shots"][nxt]["frames"][0]
                      if nxt < len(shots["shots"]) else None)
        rep = frames[shot["rep_frame"]]
        anchor, how = None, "none"
        lab = anchors.get(entry["step"])
        if lab:
            anchor = find_label_box(rep, lab)
            if anchor:
                how = f'label "{lab}" @{anchor["score"]}'
        if anchor is None:
            anchor, srcf = click_for_state(frames, shot["frames"], next_first)
            if anchor:
                how = f"click transient (frame {srcf})"
        img = annotate(rep, anchor, number=n, caption=caps.get(entry["step"]))
        verdict = "-"
        if a.screen:
            import sys as _s
            _s.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from qa_gif_to_shots import screen_frame
            verdict = screen_frame(os.path.join(a.shots, entry["frame"]))["verdict"]
            if verdict != "CLEAR":
                img = watermark(img)
        name = f"{n:02d}_{entry['step']}.png"
        img.save(os.path.join(a.out, name))
        pos = f"@({anchor['x']},{anchor['y']})" if anchor else "no anchor"
        print(f"  {name:16s} <- {entry['frame']:13s} {verdict:6s} {pos:14s} {how}")
    print(f"\n{n} annotated screenshots -> {a.out}")


if __name__ == "__main__":
    main()
