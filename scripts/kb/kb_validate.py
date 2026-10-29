#!/usr/bin/env python3
"""
kb_validate.py — the gates that decide whether a spec may be rendered.

Fail-closed by design. Every gate returns named failures with the step they
belong to, so a refusal tells the writer what to fix rather than that something
was wrong. The renderer calls check() before it writes a single paragraph.

Gates
  G1 schema       required keys present and the right shape
  G2 anatomy      every required section can actually be filled
  G3 evidence     a step that shows a screenshot proves which label it rings
  G4 tenant       no unscreened pixels; publish needs every frame CLEAR or
                  REVIEW AND confirmed by the owner (tenant_confirmed_by);
                  a BLOCK frame never ships
  G5 assets       referenced image files exist on disk
  G6 voice        client-facing copy carries no internal language
  G7 mode         draft carries provenance; publish carries no banner. A step
                  without a screenshot is fine on publish: it keeps its text
                  and the render prints no marker and no reason.

Modes: "draft" is the candidate check at assembly (images not yet seen by the
owner, BLOCK frames included so the contact sheet can show them); "publish" is
the final render's check, after the owner's contact-sheet confirmation.
"""
import os
import re

import kb_format as F


class Failure:
    __slots__ = ("gate", "where", "message")

    def __init__(self, gate, where, message):
        self.gate, self.where, self.message = gate, where, message

    def __str__(self):
        return f"{self.gate}  {self.where:12s}  {self.message}"


def _is_str(v):
    return isinstance(v, str) and v.strip() != ""


# ── G1 ───────────────────────────────────────────────────────────────────────
def g1_schema(spec):
    out = []
    for key in ("title", "overview", "steps", "considerations", "conclusion", "meta"):
        if key not in spec:
            out.append(Failure("G1", "spec", f"missing required key '{key}'"))
    if not out:
        if not isinstance(spec["steps"], list):
            out.append(Failure("G1", "steps", "must be a list"))
        if not isinstance(spec["considerations"], list):
            out.append(Failure("G1", "considerations", "must be a list"))
        meta = spec.get("meta") or {}
        for key in ("ticket", "audience", "portal"):
            if not _is_str(meta.get(key)):
                out.append(Failure("G1", "meta", f"meta.{key} is required"))
    return out


# ── G2 ───────────────────────────────────────────────────────────────────────
def g2_anatomy(spec):
    out = []
    title = spec.get("title", "")
    if len(title) > F.MAX_TITLE_CHARS:
        out.append(Failure("G2", "title",
                           f"{len(title)} chars, limit {F.MAX_TITLE_CHARS}"))
    if not title.lower().startswith(("how to", "what", "where", "understanding", "using")):
        out.append(Failure("G2", "title",
                           "must open with a task phrase (How to / What / Where / Using / Understanding)"))

    words = len(re.sub(r"\*\*", "", spec.get("overview", "")).split())
    if words < F.MIN_OVERVIEW_WORDS:
        out.append(Failure("G2", "overview",
                           f"{words} words, minimum {F.MIN_OVERVIEW_WORDS}"))

    steps = spec.get("steps") or []
    if len(steps) < F.MIN_STEPS:
        out.append(Failure("G2", "steps",
                           f"{len(steps)} steps, minimum {F.MIN_STEPS}"))
    for i, st in enumerate(steps, 1):
        if not _is_str(st.get("text")):
            out.append(Failure("G2", f"step {i}", "empty text"))
        if st.get("image") and not _is_str(st.get("caption")):
            out.append(Failure("G2", f"step {i}",
                               "a step that shows a screenshot must caption it"))

    if len(spec.get("considerations") or []) < F.MIN_CONSIDERATIONS:
        out.append(Failure("G2", "considerations",
                           f"minimum {F.MIN_CONSIDERATIONS}"))
    for i, c in enumerate(spec.get("considerations") or [], 1):
        if "**" not in c:
            out.append(Failure("G2", f"consideration {i}",
                               "must open with a bold lead-in"))
    if not _is_str(spec.get("conclusion")):
        out.append(Failure("G2", "conclusion", "empty"))
    return out


# ── G3 ───────────────────────────────────────────────────────────────────────
def g3_evidence(spec, mode="draft"):
    """A screenshot makes a claim about which element the reader should look at.
    The article prints that element's label in bold. This gate is what ties the
    two together: the ring must be anchored on a label the OCR verifier
    confirmed, and that label must appear in the step's own bold text."""
    out = []
    for i, st in enumerate(spec.get("steps") or [], 1):
        if not st.get("image"):
            continue
        ev = st.get("evidence") or {}
        anchor = ev.get("anchor")
        if anchor not in F.ANCHORS:
            out.append(Failure("G3", f"step {i}",
                               f"anchor must be one of {F.ANCHORS}, got {anchor!r}"))
            continue
        if (mode == "publish" and anchor in F.DRAFT_ONLY_ANCHORS
                and not _is_str(ev.get("tenant_confirmed_by"))):
            # dom_box is not OCR-verified; it ships only when the owner saw the
            # ring on the contact sheet and confirmed the frame.
            out.append(Failure("G3", f"step {i}",
                               f"anchor '{anchor}' was never OCR-verified; "
                               "publish needs label_box or an owner-confirmed frame"))
            continue
        if anchor == "none":
            out.append(Failure("G3", f"step {i}",
                               "screenshot has no anchor; annotate it or drop the image"))
            continue
        label, score = ev.get("label"), ev.get("label_score")
        if not _is_str(label):
            out.append(Failure("G3", f"step {i}", "no verified label recorded"))
            continue
        if not isinstance(score, (int, float)):
            out.append(Failure("G3", f"step {i}", "label_score missing"))
            continue
        if anchor == "label_box" and score < F.LABEL_CUTOFF:
            out.append(Failure("G3", f"step {i}",
                               f"label '{label}' scored {score:.2f}, floor {F.LABEL_CUTOFF}"))

        # An on-screen label often carries tenant data the article must not
        # print — "2 selected" on this frame is "N selected" in every tenant.
        # The spec may declare the generalised form it prints; it has to be the
        # same label with only the variable slot differing.
        written = ev.get("label_as_written")
        if written:
            if _generalise(written) != _generalise(label):
                out.append(Failure("G3", f"step {i}",
                                   f"label_as_written '{written}' is not a generalisation "
                                   f"of the ringed label '{label}'"))
            wanted = written
        else:
            wanted = label

        bold = " ".join(re.findall(r"\*\*(.+?)\*\*", st.get("text", "")))
        if wanted.lower() not in bold.lower():
            out.append(Failure("G3", f"step {i}",
                               f"ringed label '{wanted}' is not printed in bold in the step text"))
    return out


_VAR = re.compile(r"\b(?:\d+|[Nn]|x|X)\b")


def _generalise(s):
    """Collapse the variable slot so '2 selected', '17 selected' and
    'N selected' all compare equal."""
    return _VAR.sub("#", s.strip().lower())


# ── G4 ───────────────────────────────────────────────────────────────────────
def g4_tenant(spec, mode):
    out = []
    for i, st in enumerate(spec.get("steps") or [], 1):
        if not st.get("image"):
            continue
        ev = st.get("evidence") or {}
        verdict = ev.get("tenant")
        if verdict not in F.TENANT_VERDICTS:
            out.append(Failure("G4", f"step {i}",
                               f"unscreened frame; tenant must be one of {F.TENANT_VERDICTS}"))
            continue
        if mode == "publish" and verdict == "BLOCK":
            out.append(Failure("G4", f"step {i}",
                               "frame screened BLOCK; a BLOCK frame never appears in an article"))
            continue
        # CLEAR is not a warrant, and REVIEW is a question. The screen reads a
        # frame with OCR, so it can prove a frame is dirty and never that it is
        # clean: on M1-1337 it passed a frame carrying a real institution's
        # address and tenant slug in sidebar text too small for OCR to resolve,
        # even at 2x. Publishing therefore needs the owner to have looked
        # (contact sheet -> confirm) at every CLEAR and REVIEW frame.
        if mode == "publish" and not _is_str(ev.get("tenant_confirmed_by")):
            out.append(Failure("G4", f"step {i}",
                               f"frame screened {verdict} but nobody confirmed it; "
                               "set evidence.tenant_confirmed_by once the owner has checked the pixels"))
    return out


def shippable(step):
    """True when a step's screenshot may appear in the article: CLEAR or REVIEW
    and owner-confirmed. BLOCK, unscreened and unconfirmed frames do not."""
    ev = step.get("evidence") or {}
    return bool(step.get("image") and ev.get("tenant") in ("CLEAR", "REVIEW")
                and _is_str(ev.get("tenant_confirmed_by")))


# ── G5 ───────────────────────────────────────────────────────────────────────
def g5_assets(spec, root="."):
    out = []
    for i, st in enumerate(spec.get("steps") or [], 1):
        img = st.get("image")
        if img and not os.path.exists(os.path.join(root, img)):
            out.append(Failure("G5", f"step {i}", f"image not found: {img}"))
    return out


# ── G6 ───────────────────────────────────────────────────────────────────────
def _client_facing(spec):
    yield "overview", spec.get("overview", "")
    for i, st in enumerate(spec.get("steps") or [], 1):
        yield f"step {i}", st.get("text", "")
        if st.get("caption"):
            yield f"caption {i}", st["caption"]
    for i, c in enumerate(spec.get("considerations") or [], 1):
        yield f"consideration {i}", c
    yield "conclusion", spec.get("conclusion", "")
    yield "title", spec.get("title", "")


def g6_voice(spec):
    out = []
    for where, text in _client_facing(spec):
        low = text.lower()
        for bad in F.BANNED_SUBSTRINGS:
            if bad.lower() in low:
                out.append(Failure("G6", where, f"contains internal term '{bad}'"))
        for pat, why in F.BANNED_PATTERNS:
            m = re.search(pat, text)
            if m:
                out.append(Failure("G6", where, f"{why}: '{m.group(0).strip()}'"))
    return out


# ── G7 ───────────────────────────────────────────────────────────────────────
def g7_mode(spec, mode):
    out = []
    has_prov = bool(spec.get("provenance") or spec.get("notes"))
    if mode == "draft" and not has_prov:
        out.append(Failure("G7", "provenance",
                           "a draft carries its provenance; nothing to strip later otherwise"))
    if mode == "publish":
        # The publish render drops the banner itself; the gate exists so a spec
        # that was hand-edited to smuggle one through is caught rather than printed.
        # (missing_shot is internal: the render never prints it, so no gate.)
        if spec.get("banner_override"):
            out.append(Failure("G7", "banner",
                               "publish render carries no draft banner"))
    return out


# ── runner ───────────────────────────────────────────────────────────────────
def check(spec, mode="draft", root="."):
    """Run every gate. Returns (ok, failures). G1 short-circuits: the later
    gates index into a shape G1 is still proving exists."""
    fails = g1_schema(spec)
    if fails:
        return False, fails
    fails += g2_anatomy(spec)
    fails += g3_evidence(spec, mode)
    fails += g4_tenant(spec, mode)
    fails += g5_assets(spec, root)
    fails += g6_voice(spec)
    fails += g7_mode(spec, mode)
    return (not fails), fails


def report(fails, mode):
    if not fails:
        return f"PASS  all gates clear for {mode} render"
    lines = [f"FAIL  {len(fails)} gate failure(s) for {mode} render", ""]
    lines += ["  " + str(f) for f in fails]
    lines += ["", "  Nothing was written. Fix the above and re-run."]
    return "\n".join(lines)
