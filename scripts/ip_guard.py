#!/usr/bin/env python3
"""
ip_guard.py — deterministic IP / confidentiality pattern check for
customer-facing release copy: the release-notes markdown AND the composed
Slack messages (root post + every thread reply).

The companion `ip-guard` skill describes the contract. This script implements
the deterministic regex pass. It runs after the storyteller skill has produced
a draft, again over the composed Slack root/replies, and always BEFORE any
Slack delivery.

It checks for tokens that should never appear in customer-facing copy:

  - Internal system / vendor names (issue tracker, source-control host,
    cloud + observability vendors, framework names)
  - Technical-stack jargon (serializer, viewset, model, migration, endpoint,
    queryset, schema, index, foreign key, middleware, route, signal, etc.)
  - MR identifiers (!NNNN, MR NNNN, merge request NNNN)
    NOTE (v0.8.0): ticket identifiers (M1-NNN, GEN-NNNNNN, DEV-NNN, RD-NNN,
    do-NNN) are NO LONGER blocked — they are an intended part of the per-ticket
    release-note nomenclature (ID + verbatim title header, [ID] takeaway chips).
    Markdown heading lines containing a [TICKET-ID], and mrkdwn numbered index
    lines of the form `N. ID · title` (the Slack root-post format), are exempt
    from scanning so the verbatim ticket title can be preserved.
  - File path fragments (src/apps, /api/v\\d+/, .py, .ts, .tsx, .scss, /migrations/)
  - Architecture / mechanism vocabulary (queue, cache, worker, cron, webhook,
    job, daemon)
  - Customer institution names that may have leaked in from upstream tickets
    (loaded from --customer-list if provided; the orchestrator should pass
    its own roster)

The script is intentionally conservative. False positives are preferable to
false negatives — the user can always override (--allow flag) after reviewing.

Usage:
    python3 ip_guard.py --input release-notes-2026-05-18.md
    python3 ip_guard.py --input notes.md --customer-list customers.txt
    python3 ip_guard.py --input notes.md --json-out result.json

Exit codes:
    0  PASS — no violations
    2  FAIL — one or more violations
    1  Configuration error (missing file, bad arg)

Output:
    Human summary on stderr.
    Machine-parseable JSON on stdout (always, even on PASS).
"""

import argparse
import json
import re
import sys
from pathlib import Path


# Category -> list of (pattern, suggested_replacement, severity).
# Severity is "block" by default; "warn" surfaces but does not fail.
# Patterns are compiled with re.IGNORECASE unless they're already case-sensitive
# in the source (the ticket-id regex needs to match M1 but not m1 in prose, so
# we keep that case-sensitive).

PATTERNS = {
    "internal_system": [
        # Vendors / tools we use internally; never to be named in customer copy.
        (r"\bOptimus\b", "issue tracker (omit)", "block"),
        (r"\bGitLab\b", "source-control (omit)", "block"),
        (r"\bgitlab\.com\b", "(omit)", "block"),
        (r"\bGitHub\b", "source-control (omit)", "block"),
        (r"\bgithub\.com\b", "(omit)", "block"),
        (r"\bLinear\b", "issue tracker (omit)", "block"),
        (r"\bSupabase\b", "(omit)", "block"),
        (r"\bSentry\b", "(omit)", "block"),
        (r"\bDatadog\b", "(omit)", "block"),
        (r"\bPagerDuty\b", "(omit)", "block"),
        (r"\bFathom\b", "(omit)", "block"),
        (r"\bMem\b(?!ber|brane|ory(?!\s+leak))", "(omit)", "warn"),
        # Frameworks / runtimes
        (r"\bDjango\b", "(omit)", "block"),
        (r"\bFlask\b", "(omit)", "block"),
        (r"\bFastAPI\b", "(omit)", "block"),
        (r"\bRails\b", "(omit)", "block"),
        (r"\bReact\b", "(omit)", "block"),
        (r"\bVue\b", "(omit)", "block"),
        (r"\bAngular\b", "(omit)", "block"),
        (r"\bNext\.js\b", "(omit)", "block"),
        (r"\bVite\b", "(omit)", "block"),
        # Languages — block when used as tech identifiers, not as ordinary English
        (r"\bPython\b", "(omit)", "block"),
        (r"\bTypeScript\b", "(omit)", "block"),
        (r"\bJavaScript\b", "(omit)", "block"),
        # Datastores / infra
        (r"\bPostgres(?:QL)?\b", "(omit)", "block"),
        (r"\bMySQL\b", "(omit)", "block"),
        (r"\bRedis\b", "(omit)", "block"),
        (r"\bElasticsearch\b", "(omit)", "block"),
        (r"\bCelery\b", "(omit)", "block"),
        (r"\bRabbitMQ\b", "(omit)", "block"),
        (r"\bKafka\b", "(omit)", "block"),
        # Cloud / containers
        (r"\bAWS\b", "(omit)", "block"),
        (r"\bGCP\b", "(omit)", "block"),
        (r"\bAzure\b", "(omit)", "block"),
        (r"\bDocker\b", "(omit)", "block"),
        (r"\bKubernetes\b", "(omit)", "block"),
        (r"\bk8s\b", "(omit)", "block"),
        # Internal product nicknames worth blocking unless added to --allow
        (r"\bMedtrics-?Oracle\b", "(omit)", "warn"),
    ],
    "tech_stack": [
        # Code artifacts and framework concepts.
        (r"\bserializer(s)?\b", "(rewrite without naming the artifact)", "block"),
        (r"\bviewset(s)?\b", "(rewrite without naming the artifact)", "block"),
        (r"\bqueryset(s)?\b", "(rewrite without naming the artifact)", "block"),
        (r"\bmigration(s)?\b", "schema change (avoid)", "block"),
        (r"\bmodel(s)?\b(?!\s+of\s+care|\s+citizen)", "data definition (avoid)", "warn"),
        (r"\bendpoint(s)?\b", "(rewrite as a capability)", "block"),
        (r"\broute(s)?\b(?!\s+of\s+administration|\s+to\s+market)", "(rewrite as a capability)", "block"),
        (r"\bmiddleware\b", "(omit)", "block"),
        (r"\bsignal(s)?\b(?!\s+of)", "internal event (avoid)", "warn"),
        (r"\badmin\.py\b", "(omit)", "block"),
        (r"\bsettings\.py\b", "(omit)", "block"),
        (r"\burls\.py\b", "(omit)", "block"),
        (r"\bviews\.py\b", "(omit)", "block"),
        (r"\bmanage\.py\b", "(omit)", "block"),
        (r"\brequirements\.txt\b", "(omit)", "block"),
        (r"\bDockerfile\b", "(omit)", "block"),
        (r"\bschema\b", "(rewrite as 'data structure' or omit)", "block"),
        (r"\bforeign\s+key\b", "(omit)", "block"),
        (r"\bprimary\s+key\b", "(omit)", "block"),
        (r"\bindex(es)?\b(?!\s+of|\s+finger)", "(omit when referring to a DB index)", "warn"),
        (r"\bN\+1\b", "(omit)", "block"),
    ],
    "ticket_id": [
        # Case-sensitive: M1, GEN, DEV, RD, do prefixes followed by digits.
        # Compiled separately below so re.IGNORECASE doesn't apply.
    ],
    "mr_id": [
        # MR identifiers — !NNNN or "MR NNNN" or "merge request NNNN".
        # (v0.10.0) GitHub PR phrase references — "pull request #42" /
        # "Pull Request 42" — blocked case-insensitively. The "PR#42" short
        # form is case-sensitive and lives in the CASE_SENSITIVE bucket.
        (r"\bpull\s+request\s+#?\d+\b", "(strip PR reference)", "block"),
    ],
    "file_path": [
        (r"\bsrc/apps/[\w/]+", "(omit)", "block"),
        (r"/api/v\d+/[\w/-]+", "(rewrite as a capability)", "block"),
        (r"/migrations/0\d+_[\w-]+\.py", "(omit)", "block"),
        (r"\b[\w.-]+\.py\b", "(omit)", "block"),
        (r"\b[\w.-]+\.tsx?\b", "(omit)", "block"),
        (r"\b[\w.-]+\.scss\b", "(omit)", "block"),
        (r"\b[\w.-]+\.vue\b", "(omit)", "block"),
    ],
    "architecture": [
        (r"\bbackground\s+job(s)?\b", "(rewrite as 'process')", "block"),
        (r"\bcron\b", "(omit)", "block"),
        (r"\bdaemon(s)?\b", "(omit)", "block"),
        (r"\bworker(s)?\b(?!\'s\s+comp|\s+welfare)", "(omit when referring to a process)", "warn"),
        (r"\btask\s+queue\b", "(omit)", "block"),
        (r"\bmessage\s+queue\b", "(omit)", "block"),
        (r"\bwebhook(s)?\b", "(rewrite as 'notification' or 'integration event')", "block"),
        (r"\bcache(d|s)?\b", "(omit when referring to a tech mechanism)", "warn"),
        (r"\brace\s+condition\b", "(omit)", "block"),
        (r"\bdead-?lock\b", "(omit)", "block"),
        (r"\bmemory\s+leak\b", "(omit)", "block"),
        (r"\bload\s+balancer\b", "(omit)", "block"),
    ],
    "marketing_voice": [
        # Soft warnings — these aren't IP leaks but the storyteller skill
        # explicitly bans them. Surfaced as warnings so the storyteller can
        # tighten on a second pass.
        (r"\bwe(\s+are|'re)\s+(thrilled|excited|delighted|proud)\b", "(rewrite without marketing voice)", "warn"),
        (r"\bgame-changing\b", "(rewrite)", "warn"),
        (r"\bnext-generation\b", "(rewrite)", "warn"),
        (r"\brevolutionize\b", "(rewrite)", "warn"),
        (r"\bpowerful\b", "(rewrite or omit)", "warn"),
        (r"\bcutting-edge\b", "(rewrite)", "warn"),
        (r"\bbest-in-class\b", "(rewrite)", "warn"),
    ],
}

# Case-sensitive patterns — ticket IDs and MR refs use specific prefixes
# in canonical case. Putting these in their own bucket keeps the IGNORECASE
# flag on the others without false-matching ordinary prose like "m1 results".
CASE_SENSITIVE = {
    # NOTE (v0.8.0): Ticket identifiers (M1-NNN, GEN-NNNNNN, DEV-NNN, RD-NNN,
    # do-NNN) are NO LONGER blocked. As of the per-ticket release-note format,
    # each item is headed by its ticket identifier + verbatim title and the Key
    # takeaways carry an [ID] reference chip — clients use these to reference a
    # change with support. Ticket IDs are therefore an intended part of the
    # customer-facing nomenclature. MR references stay blocked (internal source-
    # control traces the client cannot act on).
    "mr_id": [
        (r"!\d{3,}", "(strip MR reference)", "block"),
        (r"\bMR\s+\d{3,}\b", "(strip MR reference)", "block"),
        (r"\bmerge\s+request\s+\d+\b", "(strip MR reference)", "block"),
        # (v0.10.0) GitHub PR references — matches "PR#42", "PR #42", "PR 42".
        (r"\bPR\s*#?\d+\b", "(strip PR reference)", "block"),
    ],
}

# Per-ticket title-header lines carry the VERBATIM Optimus ticket title, which
# is an intentional client-facing reference and may legitimately contain words
# the narrative bans (e.g. "API endpoints", module paths, a security class).
# Any markdown heading line that contains a [TICKET-ID] bracket is exempt from
# scanning. The exec-voice body underneath is still fully scanned.
TICKET_HEADER_RE = re.compile(r"^\s{0,3}#{2,6}\s.*\[[A-Za-z]+[0-9]?-\d+\]")

# mrkdwn-aware twin of the exemption above. The composed Slack messages carry
# the same intentional verbatim titles on three line shapes (and mrkdwn has no
# ## headings):
#   root index:        3. M1-1054 · CRITICAL BUG: …
#   reply entry title: *3. M1-1054 · CRITICAL BUG: …*
#   heads-up opener:   *Heads up — M1-1060 · …*
# Those title-carrying lines are exempt from scanning; the exec-voice prose
# beneath them is still fully scanned.
MRKDWN_INDEX_LINE_RE = re.compile(
    r"^\s*\*?(?:\d+\.\s+|Heads up\s+[—-]\s+)[A-Za-z]+[0-9]?-\d+\s+·"
)


def is_exempt_line(line):
    """True for lines whose verbatim ticket title is intentionally client-facing."""
    return bool(TICKET_HEADER_RE.match(line) or MRKDWN_INDEX_LINE_RE.match(line))


def compile_patterns():
    """Compile all patterns once for repeated scans."""
    compiled = []
    for category, items in PATTERNS.items():
        for raw_pattern, suggestion, severity in items:
            compiled.append((
                category,
                re.compile(raw_pattern, re.IGNORECASE),
                suggestion,
                severity,
            ))
    for category, items in CASE_SENSITIVE.items():
        for raw_pattern, suggestion, severity in items:
            compiled.append((
                category,
                re.compile(raw_pattern),  # no IGNORECASE
                suggestion,
                severity,
            ))
    return compiled


def load_customer_patterns(customer_list_path):
    """Compile customer-name patterns from a one-name-per-line file."""
    if not customer_list_path:
        return []
    path = Path(customer_list_path)
    if not path.exists():
        return []
    names = [n.strip() for n in path.read_text().splitlines() if n.strip() and not n.startswith("#")]
    return [
        (
            "customer_name",
            re.compile(r"\b" + re.escape(name) + r"\b", re.IGNORECASE),
            "(abstract to segment, e.g. 'an internal medicine residency')",
            "block",
        )
        for name in names
    ]


def load_allowlist(allow_path):
    """Compile allowlist patterns — substrings whose containing matches are ignored."""
    if not allow_path:
        return []
    path = Path(allow_path)
    if not path.exists():
        return []
    return [
        re.compile(line.strip(), re.IGNORECASE)
        for line in path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]


def scan(content, compiled_patterns, allowlist):
    """Scan content. Return a list of violations."""
    violations = []
    lines = content.splitlines()
    for lineno, line in enumerate(lines, start=1):
        # Per-ticket title headers (markdown `## [ID] Title`) and mrkdwn index
        # lines (`N. ID · title`) carry the verbatim ticket title, an intended
        # client-facing reference — skip IP scanning on those lines.
        if is_exempt_line(line):
            continue
        for category, pattern, suggestion, severity in compiled_patterns:
            for match in pattern.finditer(line):
                matched_text = match.group(0)
                # Skip if any allowlist pattern covers this match's surroundings.
                if any(a.search(line) for a in allowlist):
                    # Allowlist is line-level; finer-grained could be added later.
                    continue
                violations.append({
                    "category": category,
                    "line": lineno,
                    "column": match.start() + 1,
                    "match": matched_text,
                    "context": line.strip()[:160],
                    "severity": severity,
                    "suggestion": suggestion,
                })
    return violations


def human_summary(violations, input_path):
    """Print a human-readable summary to stderr."""
    if not violations:
        print(f"PASS  {input_path}", file=sys.stderr)
        return
    blocks = [v for v in violations if v["severity"] == "block"]
    warns = [v for v in violations if v["severity"] == "warn"]
    print(f"FAIL  {input_path}", file=sys.stderr)
    print(f"  block: {len(blocks)}   warn: {len(warns)}", file=sys.stderr)
    print(file=sys.stderr)

    def group_by_category(items):
        out = {}
        for v in items:
            out.setdefault(v["category"], []).append(v)
        return out

    for label, items in (("BLOCK", blocks), ("WARN", warns)):
        if not items:
            continue
        print(f"  --- {label} ---", file=sys.stderr)
        for cat, vs in group_by_category(items).items():
            print(f"  [{cat}]", file=sys.stderr)
            for v in vs[:10]:
                print(f"    L{v['line']:>3}  '{v['match']}'    {v['suggestion']}", file=sys.stderr)
                print(f"         ↳ {v['context']}", file=sys.stderr)
            if len(vs) > 10:
                print(f"    ... and {len(vs) - 10} more in this category", file=sys.stderr)
        print(file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="IP / confidentiality guard for customer-facing release notes")
    ap.add_argument("--input", required=True, help="Path to markdown file to scan")
    ap.add_argument("--customer-list", default=None,
                    help="Optional path to a file with one customer institution name per line; will be redacted")
    ap.add_argument("--allow", default=None,
                    help="Optional path to an allowlist file (regex per line, IGNORECASE)")
    ap.add_argument("--json-out", default=None,
                    help="Optional path to write the JSON result; otherwise printed to stdout")
    ap.add_argument("--warn-as-block", action="store_true",
                    help="Treat warn-severity violations as block (stricter)")
    args = ap.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
        return 1

    content = input_path.read_text()
    compiled = compile_patterns() + load_customer_patterns(args.customer_list)
    allowlist = load_allowlist(args.allow)
    violations = scan(content, compiled, allowlist)

    blocking = [v for v in violations
                if v["severity"] == "block" or (args.warn_as_block and v["severity"] == "warn")]
    passed = len(blocking) == 0

    result = {
        "input": str(input_path),
        "ok": passed,
        "violation_count": len(violations),
        "blocking_count": len(blocking),
        "violations": violations,
    }

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(result, indent=2))
    else:
        print(json.dumps(result, indent=2))

    human_summary(violations, input_path)

    return 0 if passed else 2


if __name__ == "__main__":
    sys.exit(main())
