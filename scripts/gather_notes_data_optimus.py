#!/usr/bin/env python3
"""
Shared data gathering script for notes commands (deploy-notes, integration-notes, release-slack).

Reads Optimus tasks and GitLab MR diffs, outputs structured JSON.

Usage:
    python3 .claude/scripts/gather_notes_data_optimus.py status [options]
    python3 .claude/scripts/gather_notes_data_optimus.py milestone "Project Name" [options]
    python3 .claude/scripts/gather_notes_data_optimus.py mr 6567 [options]

Options:
    --output PATH           Output JSON file path (default: /tmp/notes_data.json)
    --include-description   Include Optimus task descriptions in output
    --filter-integration    Filter diffs to integration-relevant files only
    --dry-run               Print summary table and exit without writing JSON
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DIFF_TRUNCATE_LIMIT = 15000

INTEGRATION_PATTERNS = [
    r"src/apps/api/.*/serializers\.py$",
    r"src/apps/api/.*/minimal_serializers\.py$",
    r"src/apps/api/.*/urls\.py$",
    r"src/apps/api/.*/viewsets\.py$",
    r"src/apps/.*/migrations/0.*\.py$",
    r"src/apps/api/.*/permissions\.py$",
    r"src/apps/api/.*/filters\.py$",
    r"src/apps/api/.*/authentication\.py$",
    r"src/apps/api/pagination\.py$",
]

MIGRATION_SCHEMA_OPS = [
    "AddField", "RemoveField", "CreateModel", "AlterField",
    "RenameField", "RunPython", "DeleteModel", "AddIndex", "RemoveIndex",
]

OPTIMUS_BASE_URL = "https://uyrrqbieymrcsecmxsfb.supabase.co/functions/v1/integration-tasks-api/v1"

NOTES_STATUSES = ["qa1", "qa2", "code_review", "needs_changes", "pipeline_issues"]


def log(msg):
    print(msg, file=sys.stderr)


def load_env(env_path=".env"):
    """Read .env file and return dict of relevant variables."""
    env = {}
    if not os.path.exists(env_path):
        log(f"ERROR: {env_path} not found")
        sys.exit(1)
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip("'\"")
                if key in ("GITLAB_TOKEN", "GITLAB_URL", "GITLAB_PROJECT_ID", "OPTIMUS_API_KEY"):
                    env[key] = value
    env.setdefault("GITLAB_URL", "https://gitlab.com")
    for required in ("GITLAB_TOKEN", "GITLAB_PROJECT_ID", "OPTIMUS_API_KEY"):
        if required not in env:
            log(f"ERROR: {required} not found in {env_path}")
            sys.exit(1)
    return env


HTTP_TIMEOUT = 30  # seconds — applies to every urlopen call

OPTIMUS_MAX_RETRIES = 5
OPTIMUS_INITIAL_BACKOFF = 2  # seconds


class OptimusError(Exception):
    """Raised when the Optimus API fails after retries or with a non-retryable error."""


def _retry_wait(headers, attempt, initial_backoff):
    """Wait time from a Retry-After header, else exponential backoff."""
    retry_after = ""
    if headers is not None:
        retry_after = headers.get("Retry-After", "") or ""
    if retry_after.strip().isdigit():
        return int(retry_after.strip())
    return initial_backoff * (2 ** (attempt - 1))


def optimus_api(env, path, params=None):
    """Execute an Optimus API GET request. Returns parsed JSON.

    Retries 429 and 5xx responses (honoring Retry-After, else exponential
    backoff) as well as network errors and timeouts, up to
    OPTIMUS_MAX_RETRIES attempts. Raises OptimusError on exhaustion or a
    non-retryable HTTP error; main() catches it and exits non-zero.
    """
    url = f"{OPTIMUS_BASE_URL}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    last_error = None
    for attempt in range(1, OPTIMUS_MAX_RETRIES + 1):
        req = urllib.request.Request(url, headers={"x-api-key": env["OPTIMUS_API_KEY"]})
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if e.code == 429 or 500 <= e.code <= 599:
                last_error = f"Optimus API returned {e.code}: {body[:200]}"
                if attempt < OPTIMUS_MAX_RETRIES:
                    wait = _retry_wait(e.headers, attempt, OPTIMUS_INITIAL_BACKOFF)
                    log(f"  Optimus {e.code} on {path}, retrying in {wait}s "
                        f"(attempt {attempt}/{OPTIMUS_MAX_RETRIES})...")
                    time.sleep(wait)
                    continue
            else:
                raise OptimusError(f"Optimus API returned {e.code} for {path}: {body[:200]}")
        except (urllib.error.URLError, TimeoutError) as e:
            # Network failure or socket timeout — retryable.
            last_error = f"Optimus API network error for {path}: {e}"
            if attempt < OPTIMUS_MAX_RETRIES:
                wait = OPTIMUS_INITIAL_BACKOFF * (2 ** (attempt - 1))
                log(f"  Optimus network error on {path} ({e}), retrying in {wait}s "
                    f"(attempt {attempt}/{OPTIMUS_MAX_RETRIES})...")
                time.sleep(wait)
                continue
    raise OptimusError(
        f"{last_error} (exhausted {OPTIMUS_MAX_RETRIES} attempts)")


GITLAB_MAX_RETRIES = 5
GITLAB_INITIAL_BACKOFF = 2  # seconds
GITLAB_MIN_REMAINING = 5  # proactively pause when remaining requests drop below this


def gitlab_api(env, path, params=None):
    """Execute a GitLab API GET request with rate-limit handling.

    Retries on 429 responses using Retry-After header or exponential backoff.
    Proactively pauses when RateLimit-Remaining drops below GITLAB_MIN_REMAINING.

    Returns (parsed_json, response_headers).
    """
    base = env["GITLAB_URL"].rstrip("/")
    project_id = env["GITLAB_PROJECT_ID"]
    url = f"{base}/api/v4/projects/{project_id}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    for attempt in range(1, GITLAB_MAX_RETRIES + 1):
        req = urllib.request.Request(url, headers={"PRIVATE-TOKEN": env["GITLAB_TOKEN"]})
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                headers = {k.lower(): v for k, v in resp.getheaders()}
                data = json.loads(resp.read().decode("utf-8"))
                # Proactive throttle: pause if we're close to the rate limit
                remaining = headers.get("ratelimit-remaining", "")
                if remaining.isdigit() and int(remaining) < GITLAB_MIN_REMAINING:
                    reset_at = headers.get("ratelimit-reset", "")
                    if reset_at.isdigit():
                        wait = max(0, int(reset_at) - int(time.time())) + 1
                    else:
                        wait = GITLAB_INITIAL_BACKOFF
                    log(f"  Rate limit low ({remaining} remaining), pausing {wait}s...")
                    time.sleep(wait)
                return data, headers
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if e.code == 429:
                # Determine wait time from Retry-After header or exponential backoff
                retry_after = e.headers.get("Retry-After", "")
                if retry_after and retry_after.strip().isdigit():
                    wait = int(retry_after.strip())
                else:
                    wait = GITLAB_INITIAL_BACKOFF * (2 ** (attempt - 1))
                if attempt < GITLAB_MAX_RETRIES:
                    log(f"  Rate limited (429) on {path}, retrying in {wait}s (attempt {attempt}/{GITLAB_MAX_RETRIES})...")
                    time.sleep(wait)
                    continue
                else:
                    log(f"  Rate limited (429) on {path}, exhausted all {GITLAB_MAX_RETRIES} retries")
                    return None, {}
            else:
                log(f"WARNING: GitLab API {e.code} for {path}: {body[:200]}")
                return None, {}
        except (urllib.error.URLError, TimeoutError) as e:
            # Network failure or socket timeout — retry with backoff.
            wait = GITLAB_INITIAL_BACKOFF * (2 ** (attempt - 1))
            if attempt < GITLAB_MAX_RETRIES:
                log(f"  GitLab network error on {path} ({e}), retrying in {wait}s "
                    f"(attempt {attempt}/{GITLAB_MAX_RETRIES})...")
                time.sleep(wait)
                continue
            log(f"WARNING: GitLab network error for {path} after "
                f"{GITLAB_MAX_RETRIES} attempts: {e}")
            return None, {}
    return None, {}


# --- Optimus query helpers ---


PAGE_LIMIT = 50


def fetch_tasks_paginated(env, params):
    """Fetch every page of /tasks matching params. Always terminates.

    Terminator rules (robust to an empty page returned while meta.count is
    still greater than the offset, and to a missing/zero meta.count on a
    non-empty page):
      - empty page -> stop (nothing more to read, whatever meta claims)
      - meta.count present and truthy -> stop once offset + len(page) >= count
      - meta.count missing/zero -> continue only while the page is full
        (len == PAGE_LIMIT); a short page stops with a truncation warning.
    """
    all_tasks = []
    offset = 0
    while True:
        page_params = dict(params)
        page_params["limit"] = str(PAGE_LIMIT)
        page_params["offset"] = str(offset)
        result = optimus_api(env, "/tasks", page_params)
        tasks = result.get("data", [])
        if not tasks:
            break
        all_tasks.extend(tasks)
        meta = result.get("meta") or {}
        count = meta.get("count")
        if count:
            if offset + len(tasks) >= count:
                break
            offset += len(tasks)
        elif len(tasks) == PAGE_LIMIT:
            offset += len(tasks)
        else:
            log(f"  WARNING: stopping pagination after a short page "
                f"({len(tasks)} < {PAGE_LIMIT}) with no meta.count at "
                f"offset {offset}; results may be truncated")
            break
    return all_tasks


def query_tasks_status(env):
    """Query Optimus for tasks in QA1, QA2, Code Review, Needs Changes, Pipeline Issues lanes."""
    all_tasks = []
    for status in NOTES_STATUSES:
        log(f"  Querying {status}...")
        all_tasks.extend(fetch_tasks_paginated(env, {
            "scope": "product",
            "product_status": status,
        }))
    return all_tasks


def query_tasks_project(env, project_name):
    """Query Optimus for tasks in a specific project (replaces milestone mode)."""
    # Step 1: Find the project by name
    log(f"Finding project '{project_name}'...")
    result = optimus_api(env, "/projects")
    projects = result.get("data", [])
    match = None
    for p in projects:
        if p["title"].lower() == project_name.lower():
            match = p
            break
    if not match:
        # Fallback: partial match
        for p in projects:
            if project_name.lower() in p["title"].lower():
                match = p
                break
    if not match:
        log(f"ERROR: Project '{project_name}' not found")
        log(f"  Available: {', '.join(p['title'] for p in projects)}")
        sys.exit(1)
    project_id = match["id"]
    log(f"  Found project: {match['title']} ({project_id[:8]}...)")

    # Step 2: Fetch all tasks in the project
    return fetch_tasks_paginated(env, {
        "scope": "product",
        "project_id": project_id,
    })


def query_tasks_by_identifiers(env, identifiers):
    """Query Optimus for tasks matching a list of identifiers (e.g., M1-583, GEN-128592)."""
    all_tasks = []
    for identifier in identifiers:
        log(f"  Querying {identifier}...")
        result = optimus_api(env, "/tasks", {
            "scope": "product",
            "search": identifier,
            "limit": "50",
        })
        tasks = result.get("data", [])
        # Match exact identifier
        for t in tasks:
            task_id = t.get("task_identifier") or t.get("linear_identifier") or ""
            if task_id.upper() == identifier.upper():
                all_tasks.append(t)
                break
        else:
            # If no exact match, take first result if search returned something
            if tasks:
                all_tasks.append(tasks[0])
                log(f"    No exact match for {identifier}, using best match: {resolve_identifier(tasks[0])}")
            else:
                log(f"    WARNING: No tasks found for {identifier}")
    return all_tasks


def resolve_identifier(task):
    """Resolve the best identifier for a task: task_identifier > linear_identifier > UUID prefix."""
    return task.get("task_identifier") or task.get("linear_identifier") or task["id"][:8]


def _module_name(task):
    """Optimus has exposed the module as a string or an object; '' when absent."""
    m = task.get("module") or task.get("module_name") or ""
    if isinstance(m, dict):
        m = m.get("name") or m.get("title") or ""
    return str(m)


def task_to_issue(task, include_description=False):
    """Convert an Optimus task to the normalized issue format."""
    identifier = resolve_identifier(task)
    task_id = task.get("task_identifier")
    # Use Optimus URL for native tasks, Linear URL for migrated tasks
    if task_id:
        url = f"https://optimus.medtricslab.com/tasks/{task_id}"
    elif task.get("linear_url"):
        url = task["linear_url"]
    else:
        url = f"https://optimus.medtricslab.com/tasks/{identifier}"
    # For migrated tasks, include the linear identifier for MR branch matching
    linear_id = task.get("linear_identifier") or ""
    # Extract assignee info — additive, used by /release-slack for dev attribution.
    # Optimus task payloads expose assignee under a few different shapes depending
    # on the endpoint variant; try the common ones in order.
    assignee = task.get("assignee") or task.get("assigned_to") or task.get("owner") or {}
    if isinstance(assignee, dict):
        assignee_name = (
            assignee.get("full_name")
            or assignee.get("name")
            or (f"{assignee.get('first_name','').strip()} {assignee.get('last_name','').strip()}".strip())
            or assignee.get("email")
            or ""
        )
        assignee_email = assignee.get("email") or ""
        assignee_id = assignee.get("id") or assignee.get("user_id") or ""
    else:
        assignee_name = str(assignee)
        assignee_email = ""
        assignee_id = ""
    return {
        "id": task["id"],
        "identifier": identifier,
        "linear_identifier": linear_id,
        "title": task["title"],
        "description": (task.get("description") or "") if include_description else "",
        "branchName": identifier.lower(),  # Used for MR branch matching
        "url": url,
        "state": task.get("product_status") or "",
        # Creation timestamp (additive). Optimus payloads have been seen with
        # both spellings; None when absent — the Oracle gatherer's
        # no_pr_in_window decision then simply never fires.
        "created_at": task.get("created_at") or task.get("createdAt") or None,
        "assignee_name": assignee_name,
        "assignee_email": assignee_email,
        "assignee_id": assignee_id,
        # The integration API names the classification `product_type`; `type` /
        # `task_type` are kept for older payload shapes.
        "task_type": task.get("product_type") or task.get("type") or task.get("task_type") or "",
        # Product module (additive) — /kb-articles places tickets by it.
        "module": _module_name(task),
        "mrs": [],
    }


# --- GitLab MR helpers ---


def mr_matches_identifier(mr, identifier):
    """Check if an MR actually belongs to the given identifier (not a substring match).

    GitLab search for 'M1-59' also returns MRs for 'M1-597' because it's a substring.
    This filters by checking the source branch and title for an exact identifier match,
    meaning the identifier must not be followed by additional digits.

    Hyphens and spaces are treated as interchangeable to handle MR titles that use
    spaces (e.g., 'M1 808') while identifiers use hyphens (e.g., 'M1-808').
    """
    # Replace hyphens with a pattern that matches either hyphen or space
    escaped = re.escape(identifier)
    flexible = escaped.replace(r"\-", r"[\s\-]")
    pattern = re.compile(flexible + r"(?!\d)", re.IGNORECASE)
    branch = mr.get("source_branch", "")
    title = mr.get("title", "")
    return bool(pattern.search(branch) or pattern.search(title))


def find_mrs_for_issue(env, issue):
    """Find GitLab MRs for an Optimus issue using identifier search."""
    mrs_by_iid = {}

    # Build list of identifiers to search: primary + linear (for migrated tasks)
    search_ids = [issue["identifier"]]
    linear_id = issue.get("linear_identifier", "")
    if linear_id and linear_id != issue["identifier"]:
        search_ids.append(linear_id)

    for identifier in search_ids:
        # Build search variants: hyphenated (M1-808), space-separated (M1 808),
        # and lowercased versions of each. GitLab search only matches title and
        # description text, not source_branch, so we need the space variant to
        # catch MRs whose titles use spaces instead of hyphens.
        variants = {identifier}
        space_id = identifier.replace("-", " ")
        if space_id != identifier:
            variants.add(space_id)
        lower_id = identifier.lower()
        if lower_id != identifier:
            variants.add(lower_id)

        for search_term in variants:
            data, _ = gitlab_api(
                env, "merge_requests",
                {"search": search_term, "state": "all", "per_page": "20"},
            )
            if data:
                for mr in data:
                    if mr_matches_identifier(mr, identifier):
                        mrs_by_iid[mr["iid"]] = mr

    return list(mrs_by_iid.values())


def fetch_mr_by_iid(env, iid):
    """Fetch a single MR by IID."""
    data, _ = gitlab_api(env, f"merge_requests/{iid}")
    return data


def fetch_mr_diffs(env, iid):
    """Fetch all diffs for an MR, handling pagination."""
    all_diffs = []
    page = 1
    while True:
        data, headers = gitlab_api(
            env, f"merge_requests/{iid}/diffs",
            {"per_page": "100", "page": str(page)},
        )
        if not data:
            break
        all_diffs.extend(data)
        next_page = headers.get("x-next-page", "").strip()
        if next_page and next_page.isdigit():
            page = int(next_page)
        else:
            break
    return all_diffs


def is_integration_file(path):
    """Check if a file path matches integration-relevant patterns."""
    return any(re.search(p, path) for p in INTEGRATION_PATTERNS)


def is_meaningful_migration(diff_text):
    """Check if a migration diff contains actual schema operations."""
    return any(op in diff_text for op in MIGRATION_SCHEMA_OPS)


def filter_integration_diffs(diffs):
    """Filter diffs to only integration-relevant files."""
    filtered = []
    for d in diffs:
        path = d.get("new_path") or d.get("old_path", "")
        if not is_integration_file(path):
            continue
        # For migration files, skip whitespace-only changes
        if "/migrations/" in path:
            diff_text = d.get("diff", "")
            if not is_meaningful_migration(diff_text):
                continue
        filtered.append(d)
    return filtered


def truncate_diffs(diffs):
    """Truncate individual file diffs at DIFF_TRUNCATE_LIMIT characters."""
    for d in diffs:
        if len(d.get("diff", "")) > DIFF_TRUNCATE_LIMIT:
            d["diff"] = d["diff"][:DIFF_TRUNCATE_LIMIT] + "\n... [truncated]"
    return diffs


# --- Main ---


def main():
    parser = argparse.ArgumentParser(description="Gather data from Optimus and GitLab for notes generation")
    parser.add_argument("mode", choices=["status", "milestone", "mr", "tickets"], help="Query mode")
    parser.add_argument("parameter", nargs="?", help="Project name, MR IID, or comma-separated ticket IDs")
    parser.add_argument("--output", default="/tmp/notes_data.json", help="Output JSON path")
    parser.add_argument("--include-description", action="store_true", help="Include Optimus task descriptions")
    parser.add_argument("--filter-integration", action="store_true", help="Filter diffs to integration-relevant files")
    parser.add_argument("--dry-run", action="store_true", help="Print summary and exit")
    parser.add_argument("--task-types", default="",
                        help="Comma-separated Optimus task types to keep (e.g. feature,critical_feature); "
                             "others are dropped BEFORE any GitLab lookup")
    args = parser.parse_args()

    if args.mode in ("milestone", "mr", "tickets") and not args.parameter:
        log(f"ERROR: '{args.mode}' mode requires a parameter")
        sys.exit(1)

    env = load_env()
    log(f"Mode: {args.mode}, Parameter: {args.parameter or 'N/A'}")

    # --- Gather issues ---
    issues = []
    try:
        if args.mode == "status":
            log("Querying Optimus for tasks in QA1, QA2, Code Review, Needs Changes, Pipeline Issues...")
            tasks = query_tasks_status(env)
            log(f"  Found {len(tasks)} tasks")
            for t in tasks:
                issues.append(task_to_issue(t, args.include_description))

        elif args.mode == "milestone":
            log(f"Querying Optimus for project '{args.parameter}'...")
            tasks = query_tasks_project(env, args.parameter)
            log(f"  Found {len(tasks)} tasks")
            for t in tasks:
                issues.append(task_to_issue(t, args.include_description))

        elif args.mode == "tickets":
            ticket_ids = [t.strip() for t in args.parameter.split(",") if t.strip()]
            log(f"Querying Optimus for {len(ticket_ids)} tickets...")
            tasks = query_tasks_by_identifiers(env, ticket_ids)
            log(f"  Found {len(tasks)} tasks")
            for t in tasks:
                issues.append(task_to_issue(t, args.include_description))
    except OptimusError as e:
        log(f"ERROR: Optimus gather failed: {e}")
        sys.exit(1)

    if args.task_types and args.mode != "mr":
        keep = {t.strip().lower() for t in args.task_types.split(",") if t.strip()}
        before = len(issues)
        issues = [i for i in issues if (i.get("task_type") or "").lower() in keep]
        log(f"  --task-types kept {len(issues)} of {before} tasks")

    if args.mode == "mr":
        # MR mode: no Optimus query, create a synthetic issue from the MR
        mr_iid = args.parameter
        log(f"Fetching MR !{mr_iid}...")
        mr_data = fetch_mr_by_iid(env, mr_iid)
        if not mr_data:
            log(f"ERROR: MR !{mr_iid} not found")
            sys.exit(1)
        issues.append({
            "id": "",
            "identifier": f"MR-{mr_iid}",
            "title": mr_data.get("title", ""),
            "description": mr_data.get("description", ""),
            "branchName": mr_data.get("source_branch", ""),
            "url": mr_data.get("web_url", ""),
            "state": mr_data.get("state", ""),
            "mrs": [],
        })

    # --- Find MRs for each issue ---
    def _mr_entry(mr):
        """Build the normalized MR dict with author info for internal attribution."""
        author = mr.get("author") or {}
        if not isinstance(author, dict):
            author = {}
        return {
            "iid": mr["iid"],
            "title": mr.get("title", ""),
            "web_url": mr.get("web_url", ""),
            "author_name": author.get("name", "") or "",
            "author_username": author.get("username", "") or "",
        }

    if args.mode != "mr":
        log("Finding GitLab MRs for each issue...")
        for issue in issues:
            mrs = find_mrs_for_issue(env, issue)
            issue["mrs"] = [_mr_entry(mr) for mr in mrs]
            mr_desc = ", ".join(f"!{mr['iid']}" for mr in issue["mrs"]) if issue["mrs"] else "none"
            log(f"  {issue['identifier']}: {mr_desc}")
    else:
        # mr_data was already fetched (and validated non-empty) above — reuse it
        # rather than fetching again, so a transient failure here can't crash.
        issues[0]["mrs"] = [_mr_entry(mr_data)]

    # --- Dry run: print summary and exit ---
    if args.dry_run:
        print(f"\n{'='*80}")
        print(f"DRY RUN — {len(issues)} issues found")
        print(f"{'='*80}")
        print(f"{'Identifier':<15} {'MRs':<20} {'Title'}")
        print(f"{'-'*15} {'-'*20} {'-'*40}")
        for issue in issues:
            mr_str = ", ".join(f"!{mr['iid']}" for mr in issue["mrs"]) or "none"
            title = issue["title"][:50] + "..." if len(issue["title"]) > 50 else issue["title"]
            print(f"{issue['identifier']:<15} {mr_str:<20} {title}")
        print(f"\nTotal issues: {len(issues)}")
        total_mrs = sum(len(i["mrs"]) for i in issues)
        no_mr = sum(1 for i in issues if not i["mrs"])
        print(f"Total MRs: {total_mrs}")
        print(f"Issues without MRs: {no_mr}")
        sys.exit(0)

    # --- Fetch diffs for all MRs ---
    log("Fetching MR diffs...")
    seen_iids = set()
    for issue in issues:
        for mr in issue["mrs"]:
            iid = mr["iid"]
            if iid in seen_iids:
                # Diffs already fetched for another issue sharing this MR
                continue
            seen_iids.add(iid)
            log(f"  Fetching diffs for !{iid}...")
            diffs = fetch_mr_diffs(env, iid)
            diffs = truncate_diffs(diffs)
            if args.filter_integration:
                diffs = filter_integration_diffs(diffs)
            mr["diffs"] = [
                {"old_path": d.get("old_path", ""), "new_path": d.get("new_path", ""), "diff": d.get("diff", "")}
                for d in diffs
            ]

    # For shared MRs, copy diffs to all references
    diffs_cache = {}
    for issue in issues:
        for mr in issue["mrs"]:
            if "diffs" in mr:
                diffs_cache[mr["iid"]] = mr["diffs"]
            elif mr["iid"] in diffs_cache:
                mr["diffs"] = diffs_cache[mr["iid"]]
            else:
                mr["diffs"] = []

    # For integration filtering: remove MRs with zero relevant diffs
    if args.filter_integration:
        for issue in issues:
            issue["mrs"] = [mr for mr in issue["mrs"] if mr.get("diffs")]

    # --- Write output ---
    output = {
        "mode": args.mode,
        "parameter": args.parameter or "",
        "issues": issues,
    }
    with open(args.output, "w") as f:
        json.dump(output, f, indent=2)
    log(f"\nData written to {args.output}")
    log(f"  {len(issues)} issues, {sum(len(i['mrs']) for i in issues)} MRs with diffs")


if __name__ == "__main__":
    main()
