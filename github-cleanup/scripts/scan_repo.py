#!/usr/bin/env python3
"""
Read-only scan of the current repo's issues, PRs, and branches for the
github-cleanup skill. Every call in this script is a read (gh list/view,
git branch --merged) -- it never closes, merges, or deletes anything.
Prints a single JSON report to stdout; the skill instructions decide what
to do with it and how to present it to the user.
"""
import json
import re
import subprocess
import sys

PROTECTED_BRANCH_NAMES = {
    "main", "master", "develop", "development", "staging", "production", "release",
}

# Soft cap so a huge/ancient repo doesn't dump thousands of items into context.
LIST_LIMIT = 200

KEYWORD_RE = re.compile(r"\b(close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)", re.IGNORECASE)
MENTION_RE = re.compile(r"#(\d+)")


def run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd)}\n{result.stderr.strip()}")
    return result.stdout


def run_json(cmd):
    return json.loads(run(cmd))


def gh_repo_info():
    data = run_json(["gh", "repo", "view", "--json", "nameWithOwner,defaultBranchRef"])
    return data["nameWithOwner"], data["defaultBranchRef"]["name"]


def collaborator_count(repo):
    try:
        data = run_json(["gh", "api", f"repos/{repo}/collaborators", "--paginate"])
        return len(data)
    except Exception:
        return None


def trim_issue(issue):
    body = (issue.get("body") or "").strip().replace("\r", "")
    excerpt = body[:200] + ("..." if len(body) > 200 else "")
    return {
        "number": issue["number"],
        "title": issue["title"],
        "url": issue["url"],
        "updatedAt": issue["updatedAt"],
        "body_excerpt": excerpt,
    }


def ci_status(status_check_rollup):
    if not status_check_rollup:
        return "none"
    states = []
    for c in status_check_rollup:
        conclusion = (c.get("conclusion") or "").upper()
        status = (c.get("status") or "").upper()
        if status and status != "COMPLETED":
            states.append("pending")
        elif conclusion in ("SUCCESS", "NEUTRAL", "SKIPPED"):
            states.append("success")
        elif conclusion:
            states.append("failure")
        else:
            states.append("pending")
    if "failure" in states:
        return "failing"
    if "pending" in states:
        return "pending"
    return "passing"


def scan_issues(prs_merged):
    issues = run_json([
        "gh", "issue", "list", "--state", "open", "--limit", str(LIST_LIMIT),
        "--json", "number,title,url,updatedAt,createdAt,body",
    ])
    issues_by_number = {i["number"]: i for i in issues}

    keyword_closed_numbers = set()
    mention_only = {}
    for pr in prs_merged:
        body = pr.get("body") or ""
        for ref in pr.get("closingIssuesReferences") or []:
            keyword_closed_numbers.add(ref["number"])
        for m in MENTION_RE.finditer(body):
            n = int(m.group(1))
            if n in issues_by_number and n not in keyword_closed_numbers:
                mention_only.setdefault(n, []).append(pr["number"])

    safe_to_close = [trim_issue(issues_by_number[n]) for n in keyword_closed_numbers if n in issues_by_number]

    inferred = []
    for n, pr_numbers in mention_only.items():
        item = trim_issue(issues_by_number[n])
        item["mentioned_in_merged_prs"] = pr_numbers
        inferred.append(item)

    handled = keyword_closed_numbers | set(mention_only.keys())
    stale = [trim_issue(i) for i in issues if i["number"] not in handled]

    return {
        "safe_to_close_slipped_through": safe_to_close,
        "inferred_needs_confirmation": inferred,
        "stale_report_only": stale,
    }


def scan_prs():
    prs = run_json([
        "gh", "pr", "list", "--state", "open", "--limit", str(LIST_LIMIT),
        "--json", "number,title,url,isDraft,mergeable,mergeStateStatus,reviewDecision,statusCheckRollup,headRefName",
    ])
    ready, blocked, draft = [], [], []
    for pr in prs:
        pr["ci_status"] = ci_status(pr.pop("statusCheckRollup", None))
        if pr.get("isDraft"):
            draft.append(pr)
            continue
        mergeable = pr.get("mergeable")
        review = pr.get("reviewDecision") or ""
        if mergeable == "MERGEABLE" and pr["ci_status"] in ("passing", "none") and review in ("", "APPROVED"):
            ready.append(pr)
        else:
            blocked.append(pr)
    return {"ready_to_merge": ready, "blocked": blocked, "draft_wip": draft}, {pr["headRefName"] for pr in prs}


def branch_list(args):
    out = run(["git", "branch", *args, "--format=%(refname:short)"])
    return {b.strip() for b in out.splitlines() if b.strip() and "->" not in b}


def remote_branch_set(extra_args):
    # Use the full refname (not :short) and strip the refs/remotes/<remote>/ prefix
    # ourselves -- git's :short form collapses "refs/remotes/origin/HEAD" down to the
    # bare remote name "origin", which would otherwise look like a real branch to delete.
    out = run(["git", "branch", "-r", *extra_args, "--format=%(refname)"])
    names = set()
    for line in out.splitlines():
        line = line.strip()
        if not line or line.endswith("/HEAD"):
            continue
        parts = line.split("/", 3)
        if len(parts) == 4 and parts[0] == "refs" and parts[1] == "remotes":
            names.add(parts[3])
    return names


def scan_branches(default_branch, open_pr_branches):
    protected = set(PROTECTED_BRANCH_NAMES) | {default_branch}

    all_local = branch_list([]) - protected
    merged_local = branch_list(["--merged", default_branch])
    local_merged_candidates = sorted(all_local & merged_local)
    local_unmerged = all_local - merged_local
    local_unmerged_no_pr = sorted(local_unmerged - open_pr_branches)
    local_unmerged_has_pr = sorted(local_unmerged & open_pr_branches)

    all_remote = remote_branch_set([]) - protected
    merged_remote = remote_branch_set(["--merged", f"origin/{default_branch}"])
    remote_merged_candidates = sorted(all_remote & merged_remote)
    remote_unmerged = sorted(all_remote - merged_remote)

    return {
        "local_merged_safe_to_delete": local_merged_candidates,
        "local_unmerged_no_open_pr": local_unmerged_no_pr,
        "local_unmerged_has_open_pr": local_unmerged_has_pr,
        "remote_merged_safe_to_delete": remote_merged_candidates,
        "remote_unmerged_report_only": remote_unmerged,
        "protected_excluded": sorted(protected),
    }


def main():
    try:
        repo, default_branch = gh_repo_info()
    except Exception as e:
        print(json.dumps({"error": f"could not read repo info (are you in a repo, and is gh authenticated?): {e}"}))
        sys.exit(1)

    collab_count = collaborator_count(repo)
    repo_type = "unknown"
    if collab_count is not None:
        repo_type = "solo" if collab_count <= 1 else "shared"

    try:
        prs_merged = run_json([
            "gh", "pr", "list", "--state", "merged", "--limit", str(LIST_LIMIT),
            "--json", "number,body,closingIssuesReferences",
        ])
        issues_report = scan_issues(prs_merged)
    except Exception as e:
        issues_report = {"error": str(e)}

    try:
        prs_report, open_pr_branches = scan_prs()
    except Exception as e:
        prs_report, open_pr_branches = {"error": str(e)}, set()

    try:
        branches_report = scan_branches(default_branch, open_pr_branches)
    except Exception as e:
        branches_report = {"error": str(e)}

    report = {
        "repo": repo,
        "default_branch": default_branch,
        "repo_type": repo_type,
        "collaborator_count": collab_count,
        "issues": issues_report,
        "pull_requests": prs_report,
        "branches": branches_report,
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
