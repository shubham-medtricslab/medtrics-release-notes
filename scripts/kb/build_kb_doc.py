#!/usr/bin/env python3
"""
build_kb_doc.py — render a KB article spec into the Medtrics house format.

The format itself lives in kb_format.py and the gates in kb_validate.py; this
file only assembles. That split is deliberate: the standard is data, so it can
be changed in one place and documented from the same place it is enforced.

Two renders come out of one spec.

  draft    a CLI preview for the writer. Carries the draft banner, a "draft, not
           published" subtitle and a marker for any missing shot. The provenance
           appendix is printed only with --provenance.
  publish  what goes to the knowledge base, and what kb_package renders after
           the owner's contact-sheet check. Client-clean: no banner, no
           provenance, no "generated from" line, no missing-shot marker (a step
           without a shot keeps its text), no watermark or frame on any shot. It
           refuses to build while any screenshot is BLOCK or not confirmed by
           the owner, and it never prints a frame that is not shippable.

Nothing is written unless every gate passes.

  python3 build_kb_doc.py validate spec.json
  python3 build_kb_doc.py draft    spec.json --out CX-DRAFT_Title.docx [--provenance]
  python3 build_kb_doc.py publish  spec.json --out Title.docx --md body.md
  python3 build_kb_doc.py provenance spec.json --out provenance.md
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kb_format as F
import kb_validate as V

# python-docx is imported on first render, so `validate` and `provenance` run
# in an environment that does not have it.
Document = WD_ALIGN_PARAGRAPH = Inches = Pt = RGBColor = None
_C = {}


def _load_docx():
    global Document, WD_ALIGN_PARAGRAPH, Inches, Pt, RGBColor
    if Document is not None:
        return
    from docx import Document as _D
    from docx.enum.text import WD_ALIGN_PARAGRAPH as _A
    from docx.shared import Inches as _I, Pt as _P, RGBColor as _R
    Document, WD_ALIGN_PARAGRAPH, Inches, Pt, RGBColor = _D, _A, _I, _P, _R
    _C.update({k: RGBColor.from_string(v) for k, v in F.palette().items()})


# ── house styling ────────────────────────────────────────────────────────────
def _style(doc):
    for s in doc.sections:
        s.page_width, s.page_height = Inches(F.PAGE_W_IN), Inches(F.PAGE_H_IN)
        s.left_margin = s.right_margin = Inches(F.MARGIN_LR_IN)
        s.top_margin = s.bottom_margin = Inches(F.MARGIN_TB_IN)

    n = doc.styles["Normal"]
    n.font.name, n.font.size = F.FONT, Pt(F.SIZE_BODY)
    for key, size in (("Title", F.SIZE_TITLE),
                      ("Heading 1", F.SIZE_H1),
                      ("Heading 2", F.SIZE_H2)):
        try:
            s = doc.styles[key]
        except KeyError:
            continue
        s.font.name, s.font.size = F.FONT, Pt(size)
        s.font.bold = True
        s.font.color.rgb = _C["navy"]


def _runs(par, text):
    """Render **bold** spans inline. Bold is not decoration in this format: it
    marks the UI label the reader must find, and G3 checks it against the label
    the screenshot rings."""
    for i, chunk in enumerate(text.split("**")):
        if chunk:
            par.add_run(chunk).bold = bool(i % 2)
    return par


def _tinted(doc, text, size, colour, bold=False, italic=False):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.bold, r.italic = bold, italic
    r.font.size, r.font.color.rgb = Pt(size), colour
    return p


# ── generated chrome ─────────────────────────────────────────────────────────
def subtitle_for(spec, mode):
    m = spec["meta"]
    text = F.SUBTITLE.format(audience=m["audience"], portal=m["portal"])
    return text + (F.SUBTITLE_DRAFT_SUFFIX if mode == "draft" else "")


def _image(st, mode):
    """The step's screenshot path, or None. A publish render never prints a
    frame that is not shippable (BLOCK, or not confirmed by the owner)."""
    img = st.get("image")
    if img and mode == "publish" and not V.shippable(st):
        return None
    return img


def banner_for(spec):
    shots = [s for s in spec["steps"] if s.get("image")]
    blocked = sum(1 for s in shots
                  if (s.get("evidence") or {}).get("tenant") != "CLEAR")
    if not blocked:
        return None
    return F.DRAFT_BANNER.format(blocked=blocked, total=len(shots))


def provenance_rows(spec):
    """Built from each step's own evidence, never hand-written. That is what
    makes the appendix checkable instead of decorative."""
    rows = []
    for i, st in enumerate(spec["steps"], 1):
        shown = bool(st.get("image"))
        ev = st.get("evidence") or (st.get("excluded") or {}).get("evidence") or {}
        label = ev.get("label")
        score = ev.get("label_score")
        verified = f"{label} {score:.2f}" if label and score is not None else "—"
        rows.append([
            str(i),
            ev.get("source", "—"),
            ev.get("frame", "—"),
            (ev.get("tenant", "—") if shown
             else f"{ev['tenant']} (left out)" if ev.get("tenant") else "—"),
            {"label_box": "label box",
             "click_transient": "click transient"}.get(ev.get("anchor"), "—"),
            verified,
            ev.get("tenant_confirmed_by") or "—",
        ])
    return rows


# ── render ───────────────────────────────────────────────────────────────────
def render(spec, out, mode, root=".", provenance=False):
    _load_docx()
    doc = Document()
    _style(doc)

    doc.add_paragraph(spec["title"], style="Title")
    _tinted(doc, subtitle_for(spec, mode), F.SIZE_SUBTITLE, _C["mid"], italic=True)

    if mode == "draft":
        banner = banner_for(spec)
        if banner:
            _tinted(doc, banner, F.SIZE_BANNER, _C["red"], bold=True)

    doc.add_heading(F.H_OVERVIEW, level=1)
    _runs(doc.add_paragraph(), spec["overview"])

    doc.add_heading(spec.get("steps_heading") or "Steps", level=1)
    for i, st in enumerate(spec["steps"], 1):
        _runs(doc.add_paragraph(style="List Number"), st["text"])
        img = _image(st, mode)
        if not img:
            if mode == "draft" and st.get("missing_shot"):
                p = _tinted(doc, F.MISSING_SHOT.format(reason=st["missing_shot"]),
                            F.SIZE_CAPTION, _C["red"], bold=True)
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            continue
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.add_run().add_picture(os.path.join(root, img), width=Inches(F.IMAGE_W_IN))
        cap = doc.add_paragraph()
        cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = cap.add_run(st["caption"])
        r.italic = True
        r.font.size, r.font.color.rgb = Pt(F.SIZE_CAPTION), _C["mid"]

    doc.add_heading(F.H_CONSIDERATIONS, level=1)
    for c in spec["considerations"]:
        _runs(doc.add_paragraph(style="List Bullet"), c)

    doc.add_heading(F.H_CONCLUSION, level=1)
    _runs(doc.add_paragraph(), spec["conclusion"])

    if mode == "draft" and provenance:
        _provenance(doc, spec)

    doc.save(out)
    return out


def _provenance(doc, spec):
    doc.add_page_break()
    doc.add_heading(F.H_PROVENANCE, level=1)
    note = (spec.get("provenance") or {}).get("note")
    if note:
        _tinted(doc, note, F.SIZE_PROVENANCE_NOTE, _C["mid"], italic=True)

    rows = provenance_rows(spec)
    t = doc.add_table(rows=1, cols=len(F.PROVENANCE_HEADERS))
    t.style = F.TABLE_STYLE
    for cell, head in zip(t.rows[0].cells, F.PROVENANCE_HEADERS):
        cell.text = head
        for par in cell.paragraphs:
            for run in par.runs:
                run.bold = True
                run.font.size = Pt(F.SIZE_TABLE)
    for row in rows:
        cells = t.add_row().cells
        for cell, val in zip(cells, row):
            cell.text = str(val)
            for par in cell.paragraphs:
                for run in par.runs:
                    run.font.size = Pt(F.SIZE_TABLE)

    for n in spec.get("notes") or []:
        _runs(doc.add_paragraph(style="List Bullet"), n)

    _tinted(doc, F.summary(), F.SIZE_TABLE, _C["mid"], italic=True)


# ── markdown render (Intercom body_markdown) ─────────────────────────────────
def render_markdown(spec, out, mode, asset_base=""):
    L = [f"# {spec['title']}", "", f"*{subtitle_for(spec, mode)}*", ""]
    if mode == "draft":
        b = banner_for(spec)
        if b:
            L += [f"> **{b}**", ""]
    L += [f"## {F.H_OVERVIEW}", "", spec["overview"], "",
          f"## {spec.get('steps_heading') or 'Steps'}", ""]
    for i, st in enumerate(spec["steps"], 1):
        L.append(f"{i}. {st['text']}")
        img = _image(st, mode)
        if mode == "draft" and st.get("missing_shot") and not img:
            L += ["", f"   **{F.MISSING_SHOT.format(reason=st['missing_shot'])}**", ""]
        if img:
            name = os.path.basename(img)
            L += ["", f"   ![{st['caption']}]({asset_base}{name})",
                  "", f"   *{st['caption']}*", ""]
    L += ["", f"## {F.H_CONSIDERATIONS}", ""]
    L += [f"- {c}" for c in spec["considerations"]]
    L += ["", f"## {F.H_CONCLUSION}", "", spec["conclusion"], ""]
    with open(out, "w") as fh:
        fh.write("\n".join(L))
    return out


# ── provenance sidecar ───────────────────────────────────────────────────────
def render_provenance_md(spec, out):
    """The audit table as its own file, so the package keeps it while the
    article a reviewer forwards does not."""
    head = F.PROVENANCE_HEADERS
    L = [f"# Provenance — {spec['meta']['ticket']} · {spec['title']}", ""]
    note = (spec.get("provenance") or {}).get("note")
    if note:
        L += [f"*{note}*", ""]
    L += ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    L += ["| " + " | ".join(str(c) for c in row) + " |" for row in provenance_rows(spec)]
    for n in spec.get("notes") or []:
        L.append(f"- {n}")
    L += ["", f"*{F.summary()}*", ""]
    with open(out, "w") as fh:
        fh.write("\n".join(L))
    return out


# ── CLI ──────────────────────────────────────────────────────────────────────
def _load(path):
    with open(path) as fh:
        return json.load(fh)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("validate", "draft", "publish", "provenance"))
    ap.add_argument("spec")
    ap.add_argument("--out", help="output .docx (required for draft/publish)")
    ap.add_argument("--md", help="also write Intercom markdown here")
    ap.add_argument("--provenance", action="store_true",
                    help="print the provenance appendix in a draft render")
    ap.add_argument("--asset-base", default="",
                    help="URL prefix prepended to image names in the markdown")
    ap.add_argument("--root", default=None,
                    help="base directory image paths resolve against "
                         "(default: the spec's own directory)")
    a = ap.parse_args()

    spec = _load(a.spec)
    root = a.root or os.path.dirname(os.path.abspath(a.spec))
    if a.mode == "provenance":
        if not a.out:
            print("--out is required for provenance", file=sys.stderr)
            return 2
        render_provenance_md(spec, a.out)
        print(f"  wrote {a.out}")
        return 0
    gate_mode = "draft" if a.mode in ("validate", "draft") else "publish"

    ok, fails = V.check(spec, gate_mode, root)
    print(V.report(fails, gate_mode))
    if not ok:
        return 1
    if a.mode == "validate":
        print(f"\n  {F.summary()}")
        print(f"  {len(spec['steps'])} steps, "
              f"{sum(1 for s in spec['steps'] if s.get('image'))} screenshots")
        return 0

    if not a.out:
        print("\n--out is required for a render", file=sys.stderr)
        return 2
    render(spec, a.out, a.mode, root, provenance=a.provenance)
    print(f"\n  wrote {a.out}  ({a.mode} render)")
    if a.md:
        render_markdown(spec, a.md, a.mode, a.asset_base)
        print(f"  wrote {a.md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
