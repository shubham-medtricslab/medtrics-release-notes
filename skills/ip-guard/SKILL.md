---
name: ip-guard
description: Validate customer-facing release-notes markdown and the composed Slack messages for leakage of internal-system names, code artifacts, MR references, file paths, or architecture vocabulary. Runs after storyteller and copy-audit. Refuses delivery on violation.
---

# IP Guard

The IP Guard is the deterministic safety net that sits between the Storyteller and the customer-facing Slack post. Its job is to make a yes/no call: does this draft contain anything that should not be exposed outside Medtrics?

If yes, the pipeline stops. If no, the pipeline proceeds.

## What it protects against

Three kinds of leakage:

1. **Internal-system disclosure.** Names of the issue tracker, source-control host, observability stack, cloud provider, framework, language runtimes, datastores, and other vendors that are not part of the Medtrics product surface from a customer's point of view.
2. **Implementation disclosure.** Code artifacts and architecture vocabulary that reveal how something was built — *serializer*, *viewset*, *migration*, *endpoint*, *queue*, *cache*, *worker*, *cron*, *index*, *foreign key*, *middleware*, and so on.
3. **Trace identifiers.** MR references (`!NNNN`, "MR 1234", "merge request NNNN"), branch names, file paths, and URL routes — internal source-control traces a reader cannot act on.

   **Ticket identifiers are NOT blocked (v0.8.0).** In the per-ticket release-note format each item is headed by its ticket identifier and verbatim title, and every Key-takeaways bullet ends with an `[ID]` reference chip. Clients use these identifiers to reference a change with support, so they are an intended part of the customer-facing nomenclature. In addition, **verbatim-title carrier lines are exempt from scanning entirely** so the title can be preserved even when it contains internal-flavored wording (e.g., `CRITICAL BUG:`, a module path, a client code). Exempt line shapes (v0.9.0):
   - markdown heading containing a `[TICKET-ID]` bracket — `### [ID] Title`
   - mrkdwn root-index line — `3. M1-1054 · Title`
   - mrkdwn thread-reply entry title — `*3. M1-1054 · Title*`
   - mrkdwn heads-up opener — `*Heads up — M1-1060 · Title*`

   The exec-voice prose beneath each carrier line is still fully scanned.

It also surfaces (as warnings, not blocks) two adjacent classes:

- **Marketing voice** (*game-changing*, *thrilled to announce*, *revolutionize*) — the Storyteller skill explicitly forbids these, so a hit usually means the Storyteller drifted.
- **Customer institution names** — optional. If the orchestrator passes a `--customer-list` file, those names become block-severity. The default Medtrics roster lives in the team's CRM, not in this plugin; the orchestrator is expected to provide a fresh list at call time.

## What it does NOT protect against

- It does not catch *semantic* leaks. If the Storyteller writes "We replaced our scheduling algorithm with a new constraint solver," the IP Guard will not flag that — there are no forbidden tokens. The Storyteller's own non-negotiables (in `skills/storyteller/SKILL.md`) are the primary defense against semantic leaks. The Guard is the second line.
- It does not check the integration-notes document. Integration notes are intended for technical integration partners and reasonably contain endpoint paths and field names. Only the customer-facing release-notes markdown passes through the Guard.
- The Guard runs over the customer-facing release-notes markdown (`/deploy-notes`, `/notes`) and, on the Oracle track, directly over the composed `/oracle-release` client-communication block and its thread continuation (the only Slack content that is still a forwardable client deliverable). As of 0.11.0 the combined `/release-slack` post is internal-only (a single `#dream-team` message carrying developer mentions, client names, and Optimus URLs) and is **not** guarded — internal vocabulary is the point there; the client-safety of its `_For clients:_` exec ledes is enforced upstream by the analyzer's `EXEC_SUMMARY` voice rules.

## How to invoke

Run the bundled script with the markdown path:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/ip_guard.py \
    --input .claude/outputs/notes/deploy/release-notes-<YYYY-MM-DD>.md \
    --json-out /tmp/ip_guard_result.json
```

Optional flags:

| Flag | Purpose |
|---|---|
| `--customer-list path/to/customers.txt` | One institution name per line. Each becomes a block-severity pattern. |
| `--allow path/to/allow.txt` | One regex per line. Any line in the markdown matching an allow pattern is exempt from all checks. Use sparingly. |
| `--warn-as-block` | Treat soft warnings (marketing voice, ambiguous matches) as blocks. Stricter mode for high-stakes releases. |

## Output contract

- **Exit code 0** — PASS. The markdown is clear.
- **Exit code 2** — FAIL. One or more block-severity violations.
- **Exit code 1** — Configuration error (missing file, bad argument).

Always emits a JSON document with the shape:

```json
{
  "input": "...",
  "ok": true,
  "violation_count": 0,
  "blocking_count": 0,
  "violations": []
}
```

On FAIL, `violations` lists each hit:

```json
{
  "category": "tech_stack",
  "line": 14,
  "column": 23,
  "match": "serializer",
  "context": "We refactored the EvaluationSerializer to support bulk operations",
  "severity": "block",
  "suggestion": "(rewrite without naming the artifact)"
}
```

A human-readable summary also prints to stderr — categorized, line-numbered, with the first 10 examples per category.

## How the orchestrator uses it

The Guard runs wherever customer-facing prose is produced. As of 0.11.0 that is the markdown commands and the Oracle client block — **not** the combined `/release-slack`, which is internal-only and unguarded (its `_For clients:_` ledes are kept safe upstream by the analyzer's `EXEC_SUMMARY` voice rules).

**`commands/deploy-notes.md`** (and the deploy half of `/notes`) — the Guard runs between the Storyteller's markdown output and the final file:

```
1. gather data
2. generate per-ticket exec-voice summaries
3. invoke storyteller -> release-notes-<date>.md draft
4. invoke copy-audit -> deploy markdown
5. invoke ip-guard against the deploy markdown            <-- markdown scan
   - on PASS: done
   - on FAIL: feed violations back to storyteller for up to 3 rewrites
     (standalone /deploy-notes); /notes surfaces the violations instead
```

**`commands/oracle-release.md`** — the Guard runs over the composed client-communication block (and its thread continuation, if any) before the root is assembled — the only Slack content that is still a forwardable client deliverable:

```
... storyteller -> client-section file
    invoke ip-guard against the client-section file(s)    <-- message scan
    - same PASS/FAIL/3-rewrite handling
    assemble root, lint (slack_lint.py), preview, post
```

This means customer-facing prose that contains a true blocker (e.g., a leaked customer name the Storyteller refused to abstract) never reaches a customer's hands. The user gets a clear failure with line offsets and the option to edit manually.

## Pattern coverage philosophy

The patterns are intentionally **conservative — false positives are preferable to false negatives**. It is cheaper to rewrite a sentence that the Guard flagged than to walk back a posted Slack message that leaked an internal system name.

Categories and their rationale:

| Category | Why it's blocked |
|---|---|
| `internal_system` | Names a vendor, framework, datastore, or cloud component. Customer learns nothing useful and we expose stack choices to competitors. |
| `tech_stack` | Names a code artifact concept (*serializer*, *viewset*, *endpoint*, *schema*). Customer cannot act on this and we leak implementation language. |
| `ticket_id` | **Not blocked as of v0.8.0.** Ticket identifiers are an intended part of the per-ticket nomenclature (ID + verbatim title header, `[ID]` takeaway chips) so clients can cite a change with support. |
| `mr_id` | Blocked. Source-control merge traces the customer cannot act on. |
| `file_path` | Discloses internal directory structure and module organization. |
| `architecture` | Names a mechanism (*queue*, *worker*, *cron*, *cache*). Customer cares about outcomes, not mechanisms. |
| `marketing_voice` | Not an IP leak but a voice violation against the Storyteller's own rules. Warning only. |
| `customer_name` | Identifies a specific institution. Block when a roster is supplied. |

## Updating the pattern set

If a legitimately customer-facing term is being false-flagged, add it to a session-scoped `--allow` file rather than removing the pattern from the script. The pattern set is shared across releases; the allow file is per-release. This keeps the conservative default intact for the next deploy.

If a new vendor or framework name needs to be added to the block list, edit `scripts/ip_guard.py` directly and bump the plugin version. New blocks are not backward-compatible — they may surface as violations on previously-published notes if re-scanned, which is expected.

## Known limits

- **Substring traps.** "Index" can mean a database index or a clinical index. The current pattern treats it as a warn-severity term, not a block, and uses negative-lookahead exclusions for the most common false-positive English usages (`index finger`, `index of`). When in doubt, the Storyteller should choose alternate phrasing.
- **Compound proper nouns.** "Next-generation" is flagged as marketing voice. "Next.js" is flagged as a framework. "Next year" is not flagged. The patterns are deliberately tight so legitimate prose passes.
- **Languages used as ordinary English.** "Python" and "JavaScript" are blocked. If a release legitimately must reference a language (rare for our audience), use `--allow` for that one release.

## Failure-handling pattern

When the Guard fails inside the release-slack flow, the orchestrator should:

1. Read `result.violations` from the JSON output.
2. Pass the list back to the Storyteller with a "rewrite these sentences" prompt.
3. Re-run the Storyteller, get a new draft.
4. Re-run the Guard.
5. Loop up to 3 times.
6. If still failing, stop and surface the residual violations to the user. Show the failing lines, the suggested rewrites, and the path to the draft markdown so the user can edit by hand.
7. After a manual edit, the user can rerun the Guard alone (`python3 scripts/ip_guard.py --input ...`) before re-entering the release-slack flow at the message-composition step.

Three iterations is the cap because a fourth almost always indicates a deeper problem in the upstream data (e.g., a customer name baked into a ticket title that the Storyteller cannot abstract without changing meaning).
