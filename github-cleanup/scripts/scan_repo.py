#!/usr/bin/env python3
"""
Read-only scan of the current repo's issues, PRs, branches, and worktrees for the
github-cleanup skill. It never closes, merges, or deletes anything. The only local write is
`git fetch --prune`, which refreshes remote-tracking refs so the branch report isn't stale;
it changes nothing on GitHub or in any branch. Prints one JSON report to stdout; the skill
instructions decide what to do with it and how to present it.

Merge detection uses two independent signals, because `git branch --merged` alone misses the
most common case on GitHub:
  1. Ancestry: the branch tip is contained in the default branch (merge commit or
     fast-forward).
  2. PR identity: a merged PR's head ref has the same name AND its head commit is exactly the
     branch tip. This catches squash and rebase merges, where the branch's commits never enter
     the default branch's history. If the branch has commits after the PR's head, it is NOT
     treated as merged (that newer work would be lost).
"""
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

PROTECTED_BRANCH_NAMES = {
    "main", "master", "develop", "development", "staging", "production", "release",
}

# Soft cap so a huge or ancient repo doesn't dump thousands of items into context.
LIST_LIMIT = 200
STALE_DAYS = 30

MENTION_RE = re.compile(r"#(\d+)")


def run(cmd, cwd=None, check=True):
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if check and result.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd)}\n{result.stderr.strip()}")
    return result.stdout


def run_json(cmd):
    return json.loads(run(cmd))


def repo_info():
    data = run_json(["gh", "repo", "view", "--json",
                     "nameWithOwner,defaultBranchRef,squashMergeAllowed,mergeCommitAllowed,"
                     "rebaseMergeAllowed"])
    methods = [m for m, ok in (("squash", data.get("squashMergeAllowed")),
                               ("merge", data.get("mergeCommitAllowed")),
                               ("rebase", data.get("rebaseMergeAllowed"))) if ok]
    return data["nameWithOwner"], data["defaultBranchRef"]["name"], methods


def pick_remote():
    remotes = run(["git", "remote"]).split()
    if not remotes:
        raise RuntimeError("this repo has no git remote")
    return "origin" if "origin" in remotes else remotes[0]


def collaborator_count(repo):
    try:
        return len(run_json(["gh", "api", f"repos/{repo}/collaborators", "--paginate"]))
    except Exception:
        return None


def days_since(timestamp):
    try:
        then = dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return (dt.datetime.now(dt.timezone.utc) - then).days
    except Exception:
        return None


def trim_issue(issue):
    body = (issue.get("body") or "").strip().replace("\r", "")
    return {
        "number": issue["number"],
        "title": issue["title"],
        "url": issue["url"],
        "updatedAt": issue["updatedAt"],
        "days_since_update": days_since(issue["updatedAt"]),
        "body_excerpt": body[:200] + ("..." if len(body) > 200 else ""),
    }


def ci_status(rollup):
    """Covers both check runs (status/conclusion) and commit statuses (state)."""
    if not rollup:
        return "none"
    states = []
    for c in rollup:
        if c.get("state"):  # StatusContext, e.g. from external CI services
            s = c["state"].upper()
            states.append("success" if s == "SUCCESS" else "pending" if s in ("PENDING", "EXPECTED") else "failure")
            continue
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
    issues = run_json(["gh", "issue", "list", "--state", "open", "--limit", str(LIST_LIMIT),
                       "--json", "number,title,url,updatedAt,createdAt,body"])
    by_number = {i["number"]: i for i in issues}

    keyword_closed = {}
    for pr in prs_merged:
        for ref in pr.get("closingIssuesReferences") or []:
            keyword_closed.setdefault(ref["number"], []).append(pr["number"])
    mention_only = {}
    for pr in prs_merged:
        for m in MENTION_RE.finditer(pr.get("body") or ""):
            n = int(m.group(1))
            if n in by_number and n not in keyword_closed and pr["number"] not in mention_only.get(n, []):
                mention_only.setdefault(n, []).append(pr["number"])

    def with_prs(n, prs, key):
        item = trim_issue(by_number[n])
        item[key] = prs
        return item

    safe = [with_prs(n, prs, "closed_by_merged_prs") for n, prs in keyword_closed.items() if n in by_number]
    inferred = [with_prs(n, prs, "mentioned_in_merged_prs") for n, prs in mention_only.items()]
    handled = set(keyword_closed) | set(mention_only)
    unlinked = [trim_issue(i) for i in issues if i["number"] not in handled]
    for item in unlinked:
        item["stale"] = (item["days_since_update"] or 0) > STALE_DAYS

    return {
        "safe_to_close_slipped_through": safe,
        "inferred_needs_confirmation": inferred,
        "open_no_linked_pr_report_only": unlinked,
    }


def pr_blockers(pr):
    reasons = []
    if pr.get("mergeable") == "CONFLICTING":
        reasons.append("merge conflicts")
    elif pr.get("mergeable") == "UNKNOWN":
        reasons.append("GitHub is still computing mergeability (rerun the scan in a minute)")
    state = pr.get("mergeStateStatus") or ""
    if state == "BLOCKED":
        reasons.append("blocked by branch protection (required reviews or checks)")
    elif state == "BEHIND":
        reasons.append("branch is behind the base branch")
    elif state == "UNSTABLE":
        reasons.append("some non-required checks are failing")
    if pr["ci_status"] == "failing":
        reasons.append("CI failing")
    elif pr["ci_status"] == "pending":
        reasons.append("CI still running")
    review = pr.get("reviewDecision") or ""
    if review == "CHANGES_REQUESTED":
        reasons.append("changes requested in review")
    elif review == "REVIEW_REQUIRED":
        reasons.append("review required")
    return reasons


def scan_prs():
    prs = run_json(["gh", "pr", "list", "--state", "open", "--limit", str(LIST_LIMIT), "--json",
                    "number,title,url,isDraft,mergeable,mergeStateStatus,reviewDecision,"
                    "statusCheckRollup,headRefName"])
    ready, blocked, draft = [], [], []
    for pr in prs:
        pr["ci_status"] = ci_status(pr.pop("statusCheckRollup", None))
        if pr.get("isDraft"):
            draft.append(pr)
            continue
        pr["blocked_reasons"] = pr_blockers(pr)
        pr["no_ci"] = pr["ci_status"] == "none"
        (blocked if pr["blocked_reasons"] else ready).append(pr)
    return {"ready_to_merge": ready, "blocked": blocked, "draft_wip": draft}, {p["headRefName"] for p in prs}


def worktrees():
    """Parse `git worktree list --porcelain`. The first entry is the main worktree."""
    entries, current = [], {}
    for line in run(["git", "worktree", "list", "--porcelain"]).splitlines() + [""]:
        if not line:
            if current:
                entries.append(current)
            current = {}
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            current["path"] = value
        elif key == "HEAD":
            current["head"] = value
        elif key == "branch":
            current["branch"] = value.removeprefix("refs/heads/")
        elif key in ("locked", "prunable", "detached", "bare"):
            current[key] = value or True
    for i, e in enumerate(entries):
        e["is_main"] = i == 0
    return entries


def local_branch_tips():
    out = run(["git", "for-each-ref", "refs/heads", "--format=%(refname:short) %(objectname)"])
    return dict(line.split(" ", 1) for line in out.splitlines() if line.strip())


def remote_branch_tips(remote):
    # Full refname, stripping refs/remotes/<remote>/ ourselves: the :short form turns
    # refs/remotes/origin/HEAD into the bare word "origin", which looks like a branch.
    out = run(["git", "for-each-ref", f"refs/remotes/{remote}", "--format=%(refname) %(objectname)"])
    tips = {}
    for line in out.splitlines():
        ref, sha = line.split(" ", 1)
        name = ref.removeprefix(f"refs/remotes/{remote}/")
        if name != "HEAD":
            tips[name] = sha
    return tips


def merged_by_ancestry(ref_prefix, names, default_ref):
    merged = set()
    for name in names:
        if subprocess.run(["git", "merge-base", "--is-ancestor", f"{ref_prefix}{name}", default_ref],
                          capture_output=True).returncode == 0:
            merged.add(name)
    return merged


def classify(names, tips, ancestry_merged, merged_prs_by_branch):
    """Split branches into merged (how), merged-PR-but-newer-commits, and unmerged."""
    merged, newer_work, unmerged = [], [], []
    for name in sorted(names):
        prs = merged_prs_by_branch.get(name, [])
        if name in ancestry_merged:
            if prs:
                merged.append({"branch": name, "how": "merged_pr", "pr": prs[0]["number"]})
            else:
                # Tip is inside the default branch but no PR ever merged it: either merged
                # locally, or a branch created and never committed to. Nothing is lost by
                # deleting it either way.
                merged.append({"branch": name, "how": "no_unique_commits"})
            continue
        exact = [p for p in prs if p["headRefOid"] == tips[name]]
        if exact:
            merged.append({"branch": name, "how": "merged_pr", "pr": exact[0]["number"]})
        elif prs:
            newer_work.append({"branch": name, "merged_prs": [p["number"] for p in prs],
                               "note": "branch has commits after its merged PR's head; not merged"})
        else:
            unmerged.append(name)
    return merged, newer_work, unmerged


def scan_branches(default_branch, remote, open_pr_branches, merged_prs):
    protected = set(PROTECTED_BRANCH_NAMES) | {default_branch}
    trees = worktrees()
    in_use = {t["branch"] for t in trees if t.get("branch")}

    merged_prs_by_branch = {}
    for pr in merged_prs:
        merged_prs_by_branch.setdefault(pr["headRefName"], []).append(pr)

    local = local_branch_tips()
    candidates = set(local) - protected - in_use
    has_remote_default = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/remotes/{remote}/{default_branch}"],
        capture_output=True).returncode == 0
    default_ref = f"{remote}/{default_branch}" if has_remote_default else default_branch
    local_merged, local_newer, local_unmerged = classify(
        candidates, local, merged_by_ancestry("refs/heads/", candidates, default_ref), merged_prs_by_branch)

    remote_tips = remote_branch_tips(remote)
    remote_candidates = set(remote_tips) - protected - open_pr_branches
    remote_merged, remote_newer, remote_unmerged = classify(
        remote_candidates, remote_tips,
        merged_by_ancestry(f"refs/remotes/{remote}/", remote_candidates, default_ref),
        merged_prs_by_branch)

    merged_names = {b["branch"] for b in local_merged} | {b["branch"] for b in remote_merged}
    tree_report = {"safe_to_remove": [], "prunable": [], "report_only": []}
    for t in trees:
        if t["is_main"]:
            continue
        item = {"path": t["path"], "branch": t.get("branch"), "head": t.get("head")}
        if t.get("prunable"):
            item["reason"] = f"directory is gone ({t['prunable']})" if t["prunable"] is not True else "directory is gone"
            tree_report["prunable"].append(item)
            continue
        if t.get("locked"):
            item["reason"] = "locked (usually an agent is using it right now)"
            tree_report["report_only"].append(item)
            continue
        item["claude_code_managed"] = "/.claude/worktrees/" in t["path"]
        dirty = bool(run(["git", "status", "--porcelain"], cwd=t["path"], check=False).strip()) \
            if Path(t["path"]).exists() else False
        branch = t.get("branch")
        # A worktree is only removable when its work demonstrably landed through a merged PR.
        # A branch with no commits of its own also counts as "merged" to git, but that is exactly
        # what a freshly created workspace looks like, e.g. a running Claude Code session.
        prs = merged_prs_by_branch.get(branch, []) if branch else []
        landed = bool(prs) and (branch in merged_names or any(p["headRefOid"] == t.get("head") for p in prs))
        if dirty:
            item["reason"] = "has uncommitted changes"
        elif branch and branch in open_pr_branches:
            item["reason"] = "its branch has an open PR"
        elif t.get("detached"):
            item["reason"] = "detached HEAD"
        elif not prs:
            item["reason"] = ("no merged PR for its branch: may be an active workspace"
                              + (" (Claude Code session or agent)" if item["claude_code_managed"] else ""))
        elif not landed:
            item["reason"] = "branch has commits after its merged PR"
        if "reason" in item:
            tree_report["report_only"].append(item)
        else:
            tree_report["safe_to_remove"].append(item)

    local_unmerged_has_pr = sorted(set(local_unmerged) & open_pr_branches)
    return {
        "local_merged_safe_to_delete": local_merged,
        "local_newer_than_merged_pr_report_only": local_newer,
        "local_unmerged_no_open_pr": sorted(set(local_unmerged) - open_pr_branches),
        "local_unmerged_has_open_pr": local_unmerged_has_pr,
        "remote_merged_safe_to_delete": remote_merged,
        "remote_newer_than_merged_pr_report_only": remote_newer,
        "remote_unmerged_report_only": remote_unmerged,
        "in_use_by_worktree_excluded": sorted((in_use - protected)),
        "protected_excluded": sorted(protected),
        "worktrees": tree_report,
    }


def main():
    try:
        repo, default_branch, merge_methods = repo_info()
        remote = pick_remote()
    except Exception as e:
        print(json.dumps({"error": f"could not read repo info (are you in a repo with a GitHub "
                                   f"remote, and is gh authenticated?): {e}"}))
        sys.exit(1)

    warnings = []
    fetch = subprocess.run(["git", "fetch", "--prune", "--quiet", remote], capture_output=True, text=True)
    if fetch.returncode != 0:
        warnings.append(f"git fetch failed, remote branch data may be stale: {fetch.stderr.strip()}")

    count = collaborator_count(repo)
    repo_type = "unknown" if count is None else ("solo" if count <= 1 else "shared")

    try:
        merged_prs = run_json(["gh", "pr", "list", "--state", "merged", "--limit", str(LIST_LIMIT),
                               "--json", "number,body,closingIssuesReferences,headRefName,headRefOid"])
    except Exception as e:
        merged_prs = []
        warnings.append(f"could not list merged PRs: {e}")

    try:
        issues_report = scan_issues(merged_prs)
    except Exception as e:
        issues_report = {"error": str(e)}
    try:
        prs_report, open_pr_branches = scan_prs()
    except Exception as e:
        prs_report, open_pr_branches = {"error": str(e)}, set()
    try:
        branches_report = scan_branches(default_branch, remote, open_pr_branches, merged_prs)
    except Exception as e:
        branches_report = {"error": str(e)}

    print(json.dumps({
        "repo": repo,
        "default_branch": default_branch,
        "remote": remote,
        "merge_methods_allowed": merge_methods,
        "repo_type": repo_type,
        "collaborator_count": count,
        "warnings": warnings,
        "issues": issues_report,
        "pull_requests": prs_report,
        "branches": branches_report,
    }, indent=2))


if __name__ == "__main__":
    main()
