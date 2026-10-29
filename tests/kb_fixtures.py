"""Shared fixtures for the KB-article tests (not a test module itself).

Puts scripts/kb on sys.path so the flat KB modules import the way their CLIs
import each other, and builds a golden spec that passes every draft gate.
"""

import copy
import os
import sys
from pathlib import Path

KB_DIR = Path(__file__).resolve().parent.parent / "scripts" / "kb"
if str(KB_DIR) not in sys.path:
    sys.path.insert(0, str(KB_DIR))

OVERVIEW = (
    "The object picker lists every item you can attach to an assignment. When a "
    "list runs long, **Filter to Selected** narrows it to the items you have "
    "already chosen, so you can check a selection before you save it without "
    "scrolling past everything you did not pick."
)

GOLDEN = {
    "title": "How to Filter an Object Picker to Selected Items",
    "steps_heading": "Steps to filter the picker",
    "overview": OVERVIEW,
    "steps": [
        {"text": "Open the picker and tick the items you want; the badge reads **N selected**.",
         "image": "assets/01.jpg",
         "caption": "The badge counts the items you have ticked",
         "evidence": {"anchor": "label_box", "label": "2 selected", "label_score": 0.9,
                      "label_as_written": "N selected", "tenant": "CLEAR",
                      "frame": "state02.png", "source": "live:demo"}},
        {"text": "Click **Filter to Selected** to show only the ticked items.",
         "image": "assets/02.jpg",
         "caption": "Filter to Selected hides everything you did not tick",
         "evidence": {"anchor": "label_box", "label": "Filter to Selected", "label_score": 1.0,
                      "tenant": "CLEAR", "frame": "state03.png", "source": "live:demo"}},
        {"text": "Click **Save** when the list looks right."},
    ],
    "considerations": ["**The filter is a view.** Unticked items are hidden, not removed."],
    "conclusion": "You can now check a long selection at a glance. Click the filter again to undo it.",
    "meta": {"ticket": "M1-1399", "audience": "Program administrators",
             "portal": "Administration portal"},
    "provenance": {"note": "Captured on demo.medtrics.sg."},
}


def golden():
    return copy.deepcopy(GOLDEN)


def write_assets(root):
    """Create the image files GOLDEN references, under root."""
    from_dir = Path(root) / "assets"
    from_dir.mkdir(parents=True, exist_ok=True)
    for name in ("01.jpg", "02.jpg"):
        (from_dir / name).write_bytes(b"\xff\xd8\xff\xd9")
    return str(root)


def has_module(name):
    try:
        __import__(name)
        return True
    except Exception:
        return False


__all__ = ["KB_DIR", "golden", "write_assets", "has_module", "os"]
