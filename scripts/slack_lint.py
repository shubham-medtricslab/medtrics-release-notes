#!/usr/bin/env python3
"""
slack_lint.py — deterministic Slack mrkdwn lint for composed release messages.

Runs before the human preview in /release-slack, over the
composed root post AND every thread reply. Fail-closed: anything that would
render wrong in Slack is a violation, and violations block the pipeline until
fixed (the commands iterate a fix-and-relint loop, max 3 passes).

The linter tokenizes FIRST, then applies rules outside tokens only. Tokens:

  - inline code spans (`...`)
  - user mentions <@U…> / <@W…> (W = enterprise grid)
  - channel refs <#C…> (with or without |label)
  - special mentions <!here> / <!channel>
  - links <url> and <url|label>
  - bare auto-linked URLs (https://… — LEGAL: the internal-storyteller
    mandates raw URLs for ticket links; never flagged)

Rules (applied outside tokens unless the rule is about a token's own shape):

  mention          user mentions must be exactly <@[UW][A-Z0-9]{7,}> — a
                   |label or any other shape inside <@…> is flagged
  channel          channel refs must be <#C…> or <#C…|label>
  special_mention  <!…> must be <!here> or <!channel>
  link_label       <url|label> requires a non-empty label
                   (raw <url> and bare URLs are legal)
  bold             **double-asterisk bold** (mrkdwn uses *single*)
  header           markdown # header at line start
  fence            ``` fenced code block (mrkdwn has none)
  escape           unescaped bare & < > outside tokens
                   (& not part of &amp;/&lt;/&gt;)
  section_header_blank  a *Section Header* line (a line that is entirely one
                   *…* bold span) must be preceded by a blank line
  length           char caps by --kind: root > 2,500 fails, reply > 3,900
                   fails, anything > 40,000 (API max) fails

Usage:
    python3 slack_lint.py --input /tmp/release_slack_root.txt --kind root
    python3 slack_lint.py --input /tmp/release_slack_reply_01.txt --kind reply

Exit codes:
    0  PASS — no violations
    1  Configuration error (missing file, bad arg)
    2  FAIL — one or more violations

Output:
    Machine-parseable JSON on stdout (always, even on PASS):
        {"ok", "violation_count", "violations": [{rule, line, column,
                                                  snippet, hint}]}
    Human summary on stderr.
"""

import argparse
import json
import re
import sys
from pathlib import Path


ROOT_CAP = 2500
REPLY_CAP = 3900
API_CAP = 40000

# Tokens are matched per line, leftmost-first, non-overlapping. Order matters:
# a code span containing <…> must win over the angle token inside it, and an
# angle-bracketed link <https://…> must win over the bare-URL alternative.
TOKEN_RE = re.compile(
    r"`[^`\n]+`"              # inline code span
    r"|<[^<>\n]+>"            # any angle token candidate (classified below)
    r"|https?://[^\s<>]+"     # bare auto-linked URL (legal, never flagged)
)

MENTION_BODY_RE = re.compile(r"[UW][A-Z0-9]{7,}")
CHANNEL_BODY_RE = re.compile(r"C[A-Z0-9]{7,}(\|[^|]*)?")
SPECIAL_BODY_RE = re.compile(r"here|channel")
LINK_START_RE = re.compile(r"(https?://|mailto:|ftp://)")
BOLD_RE = re.compile(r"\*\*[^*\n]+\*\*|\*\*")
HEADER_RE = re.compile(r"^#{1,6}\s")
AMP_RE = re.compile(r"&(?!amp;|lt;|gt;)")
SECTION_HEADER_LINE_RE = re.compile(r"\*[^*\n]+\*")


def _violation(rule, line, column, snippet, hint):
    return {
        "rule": rule,
        "line": line,
        "column": column,
        "snippet": snippet[:120],
        "hint": hint,
    }


def _classify_angle_token(body, lineno, column, violations):
    """Classify the inside of an <…> candidate.

    Returns True if the candidate is a token (mask it — even when its shape
    violated a token rule, the violation is already recorded); False if it is
    not a token at all (leave it unmasked so the escape rule flags < and >).
    """
    if body.startswith("@"):
        rest = body[1:]
        if MENTION_BODY_RE.fullmatch(rest):
            return True
        if "|" in rest and MENTION_BODY_RE.fullmatch(rest.split("|", 1)[0]):
            violations.append(_violation(
                "mention", lineno, column, f"<{body}>",
                "user mentions must be bare <@U…>/<@W…> — remove the |label "
                "(Slack renders the display name itself)"))
            return True
        violations.append(_violation(
            "mention", lineno, column, f"<{body}>",
            "malformed user mention — must be exactly <@[UW][A-Z0-9]{7,}>"))
        return True
    if body.startswith("#"):
        if CHANNEL_BODY_RE.fullmatch(body[1:]):
            return True
        violations.append(_violation(
            "channel", lineno, column, f"<{body}>",
            "malformed channel ref — must be <#C…> or <#C…|label>"))
        return True
    if body.startswith("!"):
        if SPECIAL_BODY_RE.fullmatch(body[1:]):
            return True
        violations.append(_violation(
            "special_mention", lineno, column, f"<{body}>",
            "only <!here> and <!channel> are allowed"))
        return True
    if LINK_START_RE.match(body):
        if "|" in body:
            label = body.split("|", 1)[1]
            if not label.strip():
                violations.append(_violation(
                    "link_label", lineno, column, f"<{body}>",
                    "a piped link <url|label> requires a non-empty label — "
                    "use <url> or a bare URL for an unlabeled link"))
        return True
    # Not a recognizable token: the angle brackets are unescaped text and the
    # escape rule will flag them.
    return False


def _tokenize_line(line, lineno, violations):
    """Mask every token in the line (spaces preserve columns).

    Token-shape violations are recorded as a side effect. Returns the masked
    line, on which the outside-token rules run.
    """
    masked = list(line)

    def mask(start, end):
        for i in range(start, end):
            masked[i] = " "

    for m in TOKEN_RE.finditer(line):
        text = m.group(0)
        if text.startswith("`"):
            mask(m.start(), m.end())
        elif text.startswith("<"):
            if _classify_angle_token(text[1:-1], lineno, m.start() + 1,
                                     violations):
                mask(m.start(), m.end())
        else:  # bare URL — legal, mask so its & ? | chars are not flagged
            mask(m.start(), m.end())
    return "".join(masked)


def _is_section_header_line(line):
    """A line that is entirely one *…* bold span (ignoring surrounding space)."""
    return bool(SECTION_HEADER_LINE_RE.fullmatch(line.strip()))


def lint(content, kind):
    """Lint the message text. Returns the list of violations."""
    violations = []
    lines = content.splitlines()

    for lineno, line in enumerate(lines, start=1):
        masked = _tokenize_line(line, lineno, violations)

        # (3) mrkdwn-illegal markdown
        for m in BOLD_RE.finditer(masked):
            violations.append(_violation(
                "bold", lineno, m.start() + 1, line[m.start():m.end()],
                "mrkdwn bold is *single asterisks* — ** does not render"))
        m = HEADER_RE.match(masked)
        if m:
            violations.append(_violation(
                "header", lineno, 1, line[:40],
                "mrkdwn has no # headers — use a *bold* line"))
        fence_at = masked.find("```")
        if fence_at != -1:
            violations.append(_violation(
                "fence", lineno, fence_at + 1, line[fence_at:fence_at + 40],
                "mrkdwn has no ``` fenced blocks — use inline `code` spans"))

        # (4) unescaped bare & < > outside tokens
        for m in AMP_RE.finditer(masked):
            violations.append(_violation(
                "escape", lineno, m.start() + 1, line.strip()[:80],
                "escape bare & as &amp; (Slack renders it back)"))
        for i, ch in enumerate(masked):
            if ch in "<>":
                violations.append(_violation(
                    "escape", lineno, i + 1, line.strip()[:80],
                    f"escape bare {ch} as "
                    f"{'&lt;' if ch == '<' else '&gt;'} — it is not part of "
                    f"a valid Slack token"))

        # (5) blank line required before a *Section Header* line
        if _is_section_header_line(line) and lineno > 1:
            if lines[lineno - 2].strip():
                violations.append(_violation(
                    "section_header_blank", lineno, 1, line.strip()[:80],
                    "insert a blank line before this *…* header line"))

    # (6) char caps by kind
    n = len(content)
    cap = ROOT_CAP if kind == "root" else REPLY_CAP if kind == "reply" else None
    if cap is not None and n > cap:
        violations.append(_violation(
            "length", 1, 1, f"{n} chars (cap {cap} for kind={kind})",
            "shorten the message or spill entries into thread replies — "
            "never a second top-level message"))
    if n > API_CAP:
        violations.append(_violation(
            "length", 1, 1, f"{n} chars (Slack API cap {API_CAP})",
            "the Slack API rejects messages over 40,000 chars — split it"))

    return violations


def human_summary(violations, input_path):
    """Print a human-readable summary to stderr (mirrors ip_guard.py)."""
    if not violations:
        print(f"PASS  {input_path}", file=sys.stderr)
        return
    print(f"FAIL  {input_path}", file=sys.stderr)
    print(f"  violations: {len(violations)}", file=sys.stderr)
    print(file=sys.stderr)
    by_rule = {}
    for v in violations:
        by_rule.setdefault(v["rule"], []).append(v)
    for rule, vs in by_rule.items():
        print(f"  [{rule}]", file=sys.stderr)
        for v in vs[:10]:
            print(f"    L{v['line']:>3}:C{v['column']:<3} '{v['snippet']}'",
                  file=sys.stderr)
            print(f"         ↳ {v['hint']}", file=sys.stderr)
        if len(vs) > 10:
            print(f"    ... and {len(vs) - 10} more in this rule",
                  file=sys.stderr)
    print(file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(
        description="Deterministic Slack mrkdwn lint for composed release messages")
    ap.add_argument("--input", required=True,
                    help="Path to the composed message text file to lint")
    ap.add_argument("--kind", choices=["root", "reply", "any"], default="any",
                    help="Message kind for length caps: root fails > 2,500 "
                         "chars, reply fails > 3,900; anything fails > 40,000 "
                         "(default: any — API cap only)")
    args = ap.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
        return 1

    content = input_path.read_text()
    violations = lint(content, args.kind)

    result = {
        "ok": not violations,
        "violation_count": len(violations),
        "violations": violations,
    }
    print(json.dumps(result, indent=2))
    human_summary(violations, input_path)
    return 0 if not violations else 2


if __name__ == "__main__":
    sys.exit(main())
