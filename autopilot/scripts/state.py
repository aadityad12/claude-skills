#!/usr/bin/env python3
"""Read-only snapshot of a GitHub repo's issue/PR state for the autopilot skill.

Prints one JSON object: repo info, project config (from an "## Autopilot" section in
CLAUDE.md or AGENTS.md), every open issue with its dependencies and readiness, and every open
PR with CI / mergeability / linked issues. Never modifies anything.

Dependency convention (in an issue body): a line starting with "Depends on", "Blocked by", or
"Requires" (optionally bold, optionally followed by ':'). Every #N on that line is a hard
dependency, except references inside parentheses, which are soft: reported, but they don't
block readiness (e.g. "Depends on: #24 (and #27 if it landed)").
"""

import json
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_CONFIG = {
    "max-parallel": "3",
    "owner-label": "needs-owner",
    "careful-label": "hard",
    "skip-labels": "stretch, tracking",
    "merge-method": "",  # empty: squash if allowed, else the repo's first allowed method
    "worker-model": "sonnet",
    "careful-model": "opus",
}

DEP_LINE = re.compile(r"^[\s*_>-]*(depends on|blocked by|requires)\b[\s*_:]*(.*)$", re.IGNORECASE)
REF = re.compile(r"#(\d+)")
CLOSING = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+#(\d+)", re.IGNORECASE)


def gh(*args):
    result = subprocess.run(["gh", *args], capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"autopilot state: `gh {' '.join(args)}` failed:\n{result.stderr.strip()}")
    return json.loads(result.stdout) if result.stdout.strip() else None


def git(*args):
    result = subprocess.run(["git", *args], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def read_config():
    root = git("rev-parse", "--show-toplevel")
    config = dict(DEFAULT_CONFIG)
    rules, source = [], None
    if root is None:
        return config, rules, source
    for name in ("CLAUDE.md", "AGENTS.md"):
        path = Path(root) / name
        if not path.exists():
            continue
        in_section = False
        for line in path.read_text(encoding="utf-8").splitlines():
            if re.match(r"^#{1,6}\s", line):
                in_section = bool(re.match(r"^#{1,6}\s+autopilot\b", line, re.IGNORECASE))
                if in_section:
                    source = name
                continue
            if not in_section or not line.strip():
                continue
            m = re.match(r"^\s*[-*]\s*([a-z-]+)\s*:\s*(.+)$", line)
            if m and m.group(1).lower() in DEFAULT_CONFIG:
                config[m.group(1).lower()] = m.group(2).strip()
            else:
                rules.append(line.rstrip())
        if source:
            break
    return config, rules, source


def split_refs(text):
    """Hard refs outside parentheses, soft refs inside them."""
    soft = set()
    for group in re.findall(r"\(([^)]*)\)", text):
        soft.update(int(n) for n in REF.findall(group))
    outside = re.sub(r"\([^)]*\)", "", text)
    hard = {int(n) for n in REF.findall(outside)}
    return sorted(hard), sorted(soft - hard)


def dependencies(body):
    hard, soft = set(), set()
    for line in (body or "").splitlines():
        m = DEP_LINE.match(line.strip())
        if m:
            h, s = split_refs(m.group(2))
            hard.update(h)
            soft.update(s)
    return sorted(hard), sorted(soft - hard)


def ci_state(rollup):
    if not rollup:
        return "none"
    states = []
    for check in rollup:
        state = (check.get("conclusion") or check.get("state") or check.get("status") or "").upper()
        states.append(state)
    if any(s in ("FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED") for s in states):
        return "failing"
    if all(s in ("SUCCESS", "NEUTRAL", "SKIPPED") for s in states):
        return "passing"
    return "pending"


def main():
    repo = gh("repo", "view", "--json",
              "nameWithOwner,defaultBranchRef,squashMergeAllowed,mergeCommitAllowed,rebaseMergeAllowed")
    config, rules, source = read_config()
    skip = {s.strip() for s in config["skip-labels"].split(",") if s.strip()}

    issues = gh("issue", "list", "--state", "all", "--limit", "1000",
                "--json", "number,title,state,labels,body,url")
    state_of = {i["number"]: i["state"] for i in issues}

    prs = gh("pr", "list", "--state", "open", "--limit", "200", "--json",
             "number,title,url,headRefName,isDraft,mergeable,mergeStateStatus,reviewDecision,"
             "statusCheckRollup,body,labels")
    pr_for_issue = {}
    pr_out = []
    for pr in prs:
        linked = sorted({int(n) for n in CLOSING.findall(pr.get("body") or "")})
        branch_issue = re.match(r"^(\d+)-", pr["headRefName"])
        if branch_issue and int(branch_issue.group(1)) not in linked:
            linked.append(int(branch_issue.group(1)))
        for n in linked:
            pr_for_issue.setdefault(n, pr["number"])
        linked_labels = sorted({l["name"] for i in issues if i["number"] in linked for l in i["labels"]})
        pr_out.append({
            "number": pr["number"], "title": pr["title"], "url": pr["url"],
            "branch": pr["headRefName"], "draft": pr["isDraft"],
            "ci": ci_state(pr.get("statusCheckRollup")),
            "mergeable": pr.get("mergeable"), "merge_state": pr.get("mergeStateStatus"),
            "review_decision": pr.get("reviewDecision"),
            "linked_issues": linked, "linked_issue_labels": linked_labels,
            "careful": config["careful-label"] in linked_labels,
        })

    open_out, any_deps = [], False
    for issue in issues:
        if issue["state"] != "OPEN":
            continue
        labels = sorted(l["name"] for l in issue["labels"])
        hard, soft = dependencies(issue["body"])
        any_deps = any_deps or bool(hard or soft)
        unmet = [n for n in hard if state_of.get(n) != "CLOSED"]
        reasons = []
        if unmet:
            reasons.append("waiting on " + ", ".join(f"#{n}" for n in unmet))
        if issue["number"] in pr_for_issue:
            reasons.append(f"has open PR #{pr_for_issue[issue['number']]}")
        skipped = sorted(skip.intersection(labels))
        if skipped:
            reasons.append("skipped label: " + ", ".join(skipped))
        open_out.append({
            "number": issue["number"], "title": issue["title"], "url": issue["url"],
            "labels": labels, "depends_on": hard, "soft_depends_on": soft,
            "unmet_dependencies": unmet, "open_pr": pr_for_issue.get(issue["number"]),
            "needs_owner": config["owner-label"] in labels,
            "careful": config["careful-label"] in labels,
            "ready": not reasons, "not_ready_because": reasons,
        })
    open_out.sort(key=lambda i: i["number"])

    allowed = [m for m, ok in (("squash", repo["squashMergeAllowed"]),
                               ("merge", repo["mergeCommitAllowed"]),
                               ("rebase", repo["rebaseMergeAllowed"])) if ok]
    method = config["merge-method"] or (allowed[0] if allowed else "merge")

    print(json.dumps({
        "repo": repo["nameWithOwner"],
        "default_branch": (repo.get("defaultBranchRef") or {}).get("name"),
        "merge_method": method,
        "config": config, "config_source": source, "project_rules": rules,
        "dependency_info_found": any_deps,
        "ready_issues": [i["number"] for i in open_out if i["ready"]],
        "open_issues": open_out,
        "open_prs": sorted(pr_out, key=lambda p: p["number"]),
    }, indent=1))


if __name__ == "__main__":
    main()
