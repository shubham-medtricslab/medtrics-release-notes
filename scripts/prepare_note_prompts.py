#!/usr/bin/env python3
"""
Prepare per-issue prompt files for notes commands, with chunking for large issues.

Reads the gathered JSON data and writes prompt files for agent consumption.
Large issues (total diff chars > CHUNK_THRESHOLD) are split into multiple
chunk prompts so they can be analyzed by parallel agents.

Usage:
    python3 .claude/scripts/prepare_note_prompts.py \
        --data /tmp/release_notes_data.json \
        --instructions /tmp/rn_instructions.txt \
        --prefix rn \
        --output-manifest /tmp/rn_manifest.json

The manifest JSON is written to --output-manifest and describes all prompt files:
{
  "prompts": [
    {"identifier": "M1-123", "type": "single", "file": "/tmp/rn_prompt_M1-123.md"},
    {"identifier": "M1-399", "type": "chunk", "chunk_num": 1, "total_chunks": 8, "file": "..."},
    ...
  ],
  "chunked_issues": ["M1-399"],
  "total_prompts": 55,
  "total_issues": 50
}
"""

import argparse
import json
import sys

CHUNK_THRESHOLD = 50000   # Issues with total diff chars above this get chunked
CHUNK_TARGET_SIZE = 80000  # Target chars per chunk


def log(msg):
    print(msg, file=sys.stderr)


def calc_issue_diff_size(issue):
    """Calculate total diff chars for an issue across all MRs."""
    total = 0
    for mr in issue.get("mrs", []):
        for d in mr.get("diffs", []):
            total += len(d.get("diff", ""))
    return total


def build_issue_header(issue):
    """Build the issue metadata header for a prompt file.

    Additive fields (assignee_name, assignee_email, assignee_slack_id,
    clients, mr author info) are emitted only when present. Older gather
    data without these fields stays unchanged.
    """
    lines = [
        f"# Issue: {issue['identifier']} — {issue['title']}",
        f"**URL:** {issue.get('url', 'N/A')}",
        f"**State:** {issue.get('state', 'N/A')}",
    ]
    # Optional dev-attribution fields. The /release-slack and /oracle-release
    # orchestrators populate `assignee_slack_id`, `clients`, and the code-link
    # fields (`pr_links` on the GitHub/Oracle track, `mr_links` on the
    # GitLab/classic track) after Slack-MCP resolution / client enrichment /
    # code-URL collection; the analyzer copies them verbatim into its block.
    if issue.get("assignee_name"):
        lines.append(f"**Assignee:** {issue['assignee_name']}")
    if issue.get("assignee_email"):
        lines.append(f"**Assignee Email:** {issue['assignee_email']}")
    if issue.get("assignee_slack_id"):
        lines.append(f"**Assignee Slack ID:** {issue['assignee_slack_id']}")
    if issue.get("assignee_name_from_directory"):
        # Oracle shape: the Mem developer directory knows the proper display
        # name behind a GitHub login — surface it so the analyzer can use it.
        lines.append(f"**Assignee (from directory):** "
                     f"{issue['assignee_name_from_directory']}")
    if issue.get("clients"):
        lines.append(f"**Clients:** {issue['clients']}")
    if issue.get("pr_links"):
        lines.append(f"**PR Links:** {issue['pr_links']}")
    if issue.get("mr_links"):
        lines.append(f"**MR Links:** {issue['mr_links']}")
    if issue.get("mrs"):
        authors = []
        seen = set()
        for mr in issue["mrs"]:
            a = mr.get("author_name") or mr.get("author_username") or ""
            if a and a not in seen:
                seen.add(a)
                authors.append(a)
        if authors:
            lines.append(f"**MR Author(s):** {', '.join(authors)}")
    if issue.get("description"):
        lines.append(f"\n## Description\n\n{issue['description']}")
    lines.append("")
    return "\n".join(lines)


def build_diff_entries(issue):
    """Return list of (mr_header, path, diff_text, char_count) for all diffs."""
    entries = []
    for mr in issue.get("mrs", []):
        mr_header = f"MR !{mr['iid']}: {mr.get('title', '')}"
        for d in mr.get("diffs", []):
            path = d.get("new_path") or d.get("old_path", "unknown")
            diff = d.get("diff", "")
            entries.append((mr_header, path, diff, len(diff)))
    return entries


def chunk_entries(entries, target_size):
    """Split diff entries into chunks of approximately target_size chars."""
    chunks = []
    current_chunk = []
    current_size = 0

    for mr_header, path, diff, size in entries:
        if current_size + size > target_size and current_chunk:
            chunks.append(current_chunk)
            current_chunk = []
            current_size = 0
        current_chunk.append((mr_header, path, diff))
        current_size += size

    if current_chunk:
        chunks.append(current_chunk)
    return chunks


def write_single_prompt(issue, instructions, filepath):
    """Write a complete single-agent prompt for a normal-sized issue."""
    with open(filepath, "w") as f:
        f.write(build_issue_header(issue))
        f.write("\n## MR Diffs\n\n")
        for mr in issue.get("mrs", []):
            f.write(f"### MR !{mr['iid']}: {mr.get('title', '')}\n\n")
            for d in mr.get("diffs", []):
                path = d.get("new_path") or d.get("old_path", "unknown")
                diff = d.get("diff", "")
                f.write(f"**{path}**\n```diff\n{diff}\n```\n\n")
        if not issue.get("mrs"):
            f.write("*No MRs found for this issue. Analyze based on the description above.*\n\n")
        f.write(f"\n---\n\n## Instructions\n\n{instructions}\n")


def write_chunk_prompt(issue, chunk, chunk_num, total_chunks, filepath):
    """Write a chunk prompt for a subset of an issue's diffs."""
    chunk_instructions = (
        "You are analyzing a SUBSET of the code diffs for this issue. "
        f"This is chunk {chunk_num} of {total_chunks}. "
        "Other agents are analyzing other parts in parallel.\n\n"
        "Your job is to produce a PARTIAL technical summary covering ONLY the files shown above.\n\n"
        "OUTPUT FORMAT — Return EXACTLY this structure:\n\n"
        f"IDENTIFIER: {issue['identifier']}\n"
        f"CHUNK: {chunk_num} of {total_chunks}\n"
        "FILES_ANALYZED:\n"
        "- {list each file path}\n"
        "CHUNK_ANALYSIS:\n"
        "- {bullet-pointed technical details for the files in this chunk}\n\n"
        "Focus on:\n"
        "- What each modified file does and why it was changed\n"
        "- Notable implementation decisions\n"
        "- Any important side effects or dependencies\n"
        "- If the diffs are sparse or unclear, use codebase context — "
        "read related source files to understand the broader picture."
    )

    with open(filepath, "w") as f:
        f.write(f"# Issue: {issue['identifier']} — {issue['title']}\n")
        f.write(f"**Chunk {chunk_num} of {total_chunks}**\n\n")
        f.write(f"**URL:** {issue.get('url', 'N/A')}\n")
        # Include description only in first chunk
        if chunk_num == 1 and issue.get("description"):
            f.write(f"\n## Description\n\n{issue['description']}\n")
        f.write("\n## MR Diffs (partial)\n\n")
        current_mr = None
        for mr_header, path, diff in chunk:
            if mr_header != current_mr:
                f.write(f"### {mr_header}\n\n")
                current_mr = mr_header
            f.write(f"**{path}**\n```diff\n{diff}\n```\n\n")
        f.write(f"\n---\n\n## Instructions\n\n{chunk_instructions}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Prepare per-issue prompt files for notes commands"
    )
    parser.add_argument("--data", required=True, help="Path to gathered JSON data")
    parser.add_argument("--instructions", required=True,
                        help="Path to file containing analysis instructions")
    parser.add_argument("--prefix", default="rn",
                        help="Prefix for prompt filenames (rn/in/internal)")
    parser.add_argument("--output-manifest", required=True,
                        help="Path to write manifest JSON")
    parser.add_argument("--chunk-threshold", type=int, default=CHUNK_THRESHOLD,
                        help=f"Diff chars threshold for chunking (default: {CHUNK_THRESHOLD})")
    parser.add_argument("--chunk-size", type=int, default=CHUNK_TARGET_SIZE,
                        help=f"Target chars per chunk (default: {CHUNK_TARGET_SIZE})")
    args = parser.parse_args()

    with open(args.data) as f:
        data = json.load(f)

    with open(args.instructions) as f:
        instructions = f.read().strip()

    manifest = {
        "prompts": [],
        "chunked_issues": [],
        "total_issues": len(data.get("issues", [])),
    }

    log("Preparing prompt files...")
    for issue in data.get("issues", []):
        identifier = issue["identifier"]
        safe_id = identifier.replace("/", "_")
        diff_size = calc_issue_diff_size(issue)

        if diff_size <= args.chunk_threshold:
            # Single prompt — includes full diffs + analysis instructions
            filepath = f"/tmp/{args.prefix}_prompt_{safe_id}.md"
            write_single_prompt(issue, instructions, filepath)
            manifest["prompts"].append({
                "identifier": identifier,
                "type": "single",
                "file": filepath,
                "diff_chars": diff_size,
            })
            log(f"  {identifier}: single prompt ({diff_size:,} chars)")
        else:
            # Chunked — split diffs across multiple prompt files
            entries = build_diff_entries(issue)
            chunks = chunk_entries(entries, args.chunk_size)
            manifest["chunked_issues"].append(identifier)

            for i, chunk in enumerate(chunks, 1):
                filepath = f"/tmp/{args.prefix}_prompt_{safe_id}_chunk_{i}.md"
                write_chunk_prompt(issue, chunk, i, len(chunks), filepath)
                manifest["prompts"].append({
                    "identifier": identifier,
                    "type": "chunk",
                    "chunk_num": i,
                    "total_chunks": len(chunks),
                    "file": filepath,
                })

            log(f"  {identifier}: {len(chunks)} chunks ({diff_size:,} chars)")

    manifest["total_prompts"] = len(manifest["prompts"])

    with open(args.output_manifest, "w") as f:
        json.dump(manifest, f, indent=2)

    log(f"\nTotal: {manifest['total_prompts']} prompts for "
        f"{manifest['total_issues']} issues "
        f"({len(manifest['chunked_issues'])} chunked)")
    log(f"Manifest written to {args.output_manifest}")


if __name__ == "__main__":
    main()
