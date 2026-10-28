#!/usr/bin/env python3
"""
kb_format.py — the Medtrics KB article house format, as data.

This module is the single source of truth. The renderer imports it, the
validator imports it, and the standard document is generated from it. Changing
a constant here changes the format everywhere; nothing else hard-codes a font,
a colour or a size.

Every value was measured out of the approved reference document
(CX-DRAFT_How_to_Filter_an_Object_Picker_to_Selected_Items.docx) rather than
chosen, so a re-render of that spec reproduces it exactly.
"""

FORMAT_VERSION = "1.1"

# ── palette ──────────────────────────────────────────────────────────────────
NAVY = "1F3064"        # headings and title
ORANGE = "ED601A"      # screenshot annotation accent (set by qa_annotate.py)
INK = "2D3448"         # body ink
MID = "6C717C"         # subtitle, captions, provenance note
RED = "B00020"         # draft banner, watermark

# ── type ─────────────────────────────────────────────────────────────────────
FONT = "Calibri"
SIZE_TITLE = 26
SIZE_H1 = 16
SIZE_H2 = 13
SIZE_BODY = 11
SIZE_SUBTITLE = 10
SIZE_BANNER = 10.5
SIZE_CAPTION = 9
SIZE_TABLE = 9
SIZE_PROVENANCE_NOTE = 9.5

# ── page ─────────────────────────────────────────────────────────────────────
PAGE_W_IN, PAGE_H_IN = 8.5, 11.0
MARGIN_LR_IN = 1.25
MARGIN_TB_IN = 1.0
IMAGE_W_IN = 6.10          # full text column; every screenshot is this wide
TABLE_STYLE = "Light Grid Accent 1"

# ── screenshot annotation ────────────────────────────────────────────────────
# qa_annotate.py draws with these. Orange marks what the reader acts on; red is
# reserved for "not for publication" (watermark, draft banner), so a ring is
# never red.
RING_WIDTH_PX = 4
RING_RADIUS_PX = 8
RING_PAD_PX = 28
BADGE_RADIUS_PX = 17
BADGE_SIDEBAR_SNAP = 0.32  # a ring left of this fraction of width sits in the sidebar
SHOT_PX_W = 1200           # annotated shots are downscaled to this width
SHOT_FORMAT = "JPEG"
SHOT_QUALITY = 85

# ── anatomy ──────────────────────────────────────────────────────────────────
# Order is load-bearing: the renderer emits these in this sequence and the
# validator rejects a spec that cannot fill a required one.
ANATOMY = [
    ("title",          True,  "Title"),
    ("subtitle",       True,  "Audience line, generated from meta"),
    ("banner",         False, "Draft banner; draft render only"),
    ("overview",       True,  "Overview"),
    ("steps",          True,  "Numbered steps, each with its screenshot"),
    ("considerations", True,  "Key considerations"),
    ("conclusion",     True,  "Conclusion"),
    ("provenance",     False, "Provenance appendix; draft render only"),
]

H_OVERVIEW = "Overview"
H_CONSIDERATIONS = "Key considerations"
H_CONCLUSION = "Conclusion"
H_PROVENANCE = "Provenance — strip before publishing"

MIN_STEPS = 2
MAX_TITLE_CHARS = 90
MIN_OVERVIEW_WORDS = 40
MIN_CONSIDERATIONS = 1

PROVENANCE_HEADERS = ["Step", "Source", "Frame", "Tenant", "Anchor", "Label verified"]

DRAFT_BANNER = (
    "DRAFT — screenshots are re-capture references, not publishable assets. "
    "{blocked} of the {total} screenshots in this article failed the tenant screen. "
    "Re-capture each shot against the demo tenant before this goes out."
)
MISSING_SHOT = "[Screenshot missing — {reason}]"
NO_SCREEN = "no_screen"      # sentinel: the change has no screen; no marker, one note
NO_SCREEN_NOTE = "No screenshot: this change has no screen of its own."
# Per-screen capture reasons -> the words after "Screenshot missing —".
MISSING_REASONS = {
    "needs_write": "This step happens after you save; no screenshot",
    "click_refused": "the capture could not safely open this screen",
    "click_not_found": "the capture could not find the button that opens this screen",
    "tenant_row": "the first row on this screen shows real tenant data",
    "page_not_found": "the capture could not find this page in the app",
    "label_not_found": "the screen did not show the labelled item",
}


def missing_reason_text(code, detail=""):
    """Words for a missing-screenshot marker from a capture reason code.
    Unknown codes pass through unchanged; NO_SCREEN is returned as the sentinel."""
    code = (code or "").strip()
    if code.split(":")[0] == NO_SCREEN:
        return NO_SCREEN
    base = MISSING_REASONS.get(code)
    if not base:
        return code
    return f"{base} ({detail})" if detail and code in ("click_refused", "click_not_found") else base
SUBTITLE = "Audience: {audience} · {portal} · Generated from {ticket} · {state}"

# ── gates ────────────────────────────────────────────────────────────────────
LABEL_CUTOFF = 0.82        # same floor the OCR verifier uses; see qa_gif_to_shots.py
TENANT_VERDICTS = ("CLEAR", "REVIEW", "BLOCK")
ANCHORS = ("label_box", "click_transient", "dom_box", "none")
# dom_box: the DOM text equals the label exactly and the element is visible and
# unoccluded, but no OCR confirmed the pixels. Accepted in a draft only — the
# fallback for a sandbox without tesseract. Publish still needs label_box.
DRAFT_ONLY_ANCHORS = ("dom_box",)

# A published article is client-facing copy. These never appear in it. The list
# is the standing release-notes rule, restated here so the renderer can enforce
# it rather than relying on the writer remembering.
BANNED_SUBSTRINGS = [
    "vulnerability", "XSS", "IDOR", "FERPA", "SSRF", "CSRF",
    "merge request", "!MR", "pull request", "commit ", "stack trace",
    "localhost", "staging", "SECURITY DEFINER",
]
BANNED_PATTERNS = [
    (r"\bMR\s*!?\d{3,}\b",          "merge-request reference"),
    (r"\b[45]\d{2}\s+(?:error|response|status)\b", "HTTP status code"),
    (r"(?:^|\s)/(?:home|Users|var|srv)/\S+", "file path"),
    (r"\b[a-z_]+\.(?:py|sql|tsx?|jsx?|vue)\b", "source filename"),
    (r"\bQA[12]\b",                  "internal lane key"),
]


def palette():
    return {"navy": NAVY, "orange": ORANGE, "ink": INK, "mid": MID, "red": RED}


def summary():
    """One-line description of the format, for the audit trail."""
    return (f"medtrics-kb-article v{FORMAT_VERSION} · {FONT} "
            f"{SIZE_BODY}/{SIZE_H1}/{SIZE_TITLE} · image {IMAGE_W_IN}in · "
            f"label floor {LABEL_CUTOFF}")
