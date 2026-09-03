#!/usr/bin/env python3
"""
assemble_oracle_root.py — deterministic root assembler for /oracle-release.

Builds the combined Slack root post from the guarded client-communication
file and the internal-index file. Because assembly is code, the guarded
client text lands in the message VERBATIM BY CONSTRUCTION — no grep/cmp
verification step, no drift window. Guard-rewrite loops or preview edits
simply re-run this assembler.

Root layout (exact):

    :rocket: *Medtrics Oracle Release — {date}*

    :clipboard: *Client communication — safe to forward*
    {client-file content, verbatim}
    {marker}

    :lock: *Internal reference — do not forward*
    {internal-file content, possibly truncated at whole-line boundaries}

    Details for each item are in this thread.

Budget: root <= 2,500 chars. Client content is NEVER cut here (the skill
enforces its 1,200-char cap); if the client file alone makes the root unfit,
this script hard-errors. The internal index keeps whole lines that fit; when
lines are dropped, an "…and N more" line is appended and ALL dropped lines
are written to --overflow-output (created ONLY on overflow). A section
header whose entire section is dropped moves into the overflow too — the
root never carries a dangling header with no entries.

Deterministic: same inputs -> identical output bytes. No timestamps or
randomness (the date arrives pre-formatted via --date).

Exit codes: 0 success, 1 configuration/budget error.
"""

import argparse
import re
import sys
from pathlib import Path

ROOT_CAP = 2500

MARKER_DEFAULT = "_(everything between the markers is client-safe)_"
MARKER_CONTINUES = ("_(client-safe text — full version continues in the "
                    "first thread reply)_")

OVERFLOW_LINE_TEMPLATE = "…and {n} more — index continues in the thread"

# A section header line is a line that is entirely one *…* bold span
# (same shape slack_lint treats as a section header).
SECTION_HEADER_RE = re.compile(r"^\*[^*\n]+\*$")


def is_section_header(line):
    return bool(SECTION_HEADER_RE.match(line.strip()))


def is_entry(line):
    """An index entry: non-blank and not a section header."""
    return bool(line.strip()) and not is_section_header(line)


def build_root(date, client_text, internal_body, marker):
    """Render the exact root layout. internal_body may be ''."""
    parts = [
        f":rocket: *Medtrics Oracle Release — {date}*",
        "",
        ":clipboard: *Client communication — safe to forward*",
        client_text,
        marker,
        "",
        ":lock: *Internal reference — do not forward*",
    ]
    if internal_body:
        # Blank line before the internal body: its first line is a
        # *…* section header, and slack_lint's section_header_blank rule
        # requires a blank line before every header line. Without this the
        # assembled root fails its own mandatory lint gate every time.
        parts.append("")
        parts.append(internal_body)
    parts.append("")
    parts.append("Details for each item are in this thread.")
    return "\n".join(parts) + "\n"


def _split_dangling_headers(kept):
    """Trim trailing blanks and dangling section headers off `kept`.

    Returns (kept_trimmed, moved_headers) where moved_headers are the header
    lines whose entire section was dropped — they belong in the overflow,
    ahead of their entries.
    """
    kept = list(kept)
    moved = []
    while kept:
        last = kept[-1]
        if not last.strip():
            kept.pop()  # trailing blank — drop silently
        elif is_section_header(last):
            moved.insert(0, kept.pop())  # dangling header -> overflow front
        else:
            break
    return kept, moved


def assemble(date, client_text, internal_lines, client_continues):
    """Compute (root_text, overflow_lines, kept_count).

    Raises ValueError when the client content alone exceeds the root budget.
    """
    marker = MARKER_CONTINUES if client_continues else MARKER_DEFAULT
    client_text = client_text.rstrip("\n")

    # Normalize: drop trailing blank internal lines (never leading/interior).
    lines = list(internal_lines)
    while lines and not lines[-1].strip():
        lines.pop()

    # Try keeping k lines, largest k first; the first fit wins.
    for k in range(len(lines), -1, -1):
        kept = lines[:k]
        dropped = lines[k:]
        kept, moved_headers = _split_dangling_headers(kept)
        overflow = moved_headers + dropped
        if dropped or moved_headers:
            n_more = sum(1 for ln in overflow if is_entry(ln))
            body_lines = kept + [OVERFLOW_LINE_TEMPLATE.format(n=n_more)]
        else:
            body_lines = kept
        internal_body = "\n".join(body_lines)
        root = build_root(date, client_text, internal_body, marker)
        if len(root) <= ROOT_CAP:
            # Trim leading/trailing blanks off the overflow fragment.
            while overflow and not overflow[0].strip():
                overflow.pop(0)
            while overflow and not overflow[-1].strip():
                overflow.pop()
            return root, overflow, len(kept)

    raise ValueError(
        f"client content alone makes the root exceed {ROOT_CAP} chars — the "
        f"client section must be shortened (skill cap is 1,200 chars)")


def main():
    ap = argparse.ArgumentParser(
        description="Deterministic root assembler for the Oracle release "
                    "Slack post")
    ap.add_argument("--client-file", required=True,
                    help="Guarded client-communication text (embedded "
                         "verbatim, never cut)")
    ap.add_argument("--internal-file", required=True,
                    help="Internal index lines (+ section headers)")
    ap.add_argument("--date", required=True,
                    help='Pre-formatted date string, e.g. '
                         '"Tuesday, August 26, 2026"')
    ap.add_argument("--output", required=True,
                    help="Root file path to write")
    ap.add_argument("--overflow-output", required=True,
                    help="Path for internal-index overflow lines; created "
                         "ONLY on overflow")
    ap.add_argument("--client-continues", action="store_true",
                    help="Set when a client continuation reply exists; "
                         "switches the marker text")
    args = ap.parse_args()

    client_path = Path(args.client_file)
    internal_path = Path(args.internal_file)
    for p in (client_path, internal_path):
        if not p.exists():
            print(f"ERROR: input file not found: {p}", file=sys.stderr)
            return 1

    client_text = client_path.read_text()
    internal_lines = internal_path.read_text().splitlines()

    try:
        root, overflow, kept_count = assemble(
            args.date, client_text, internal_lines, args.client_continues)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    Path(args.output).write_text(root)
    if overflow:
        Path(args.overflow_output).write_text("\n".join(overflow) + "\n")

    overflowed = sum(1 for ln in overflow if is_entry(ln))
    print(f"root: {len(root)} chars; internal lines kept: {kept_count}, "
          f"overflowed: {overflowed}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
