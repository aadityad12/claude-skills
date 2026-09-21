---
name: github-cleanup
description: End-of-session GitHub housekeeping for the current repo -- scans open issues, open PRs, local/remote branches, and git worktrees, then proposes closing finished issues, merging ready PRs, deleting merged branches (including squash-merged ones), and removing finished worktrees. Use this whenever the user wants to "clean up" a repo, "wrap up" or "close out" a session, tidy up issues/PRs/branches before stepping away, or asks things like "anything to merge or close before I go?" -- even if they don't name the skill directly. Always scan and report before touching anything; never close an issue, merge a PR, or delete a branch without the user's explicit go-ahead on that specific batch of actions.
model: sonnet
---

# GitHub Cleanup

A repo accumulates loose ends during a working session: issues that got fixed
but never got closed, PRs that are green and ready but just sitting there,
branches that were merged weeks ago and are still cluttering `git branch`,
worktrees left behind by agents after their PRs merged.
This skill's job is to sweep all of it, tell the user exactly what it found
and how confident it is about each item, and only act once they say go.

The core principle: **scanning is read-only and can happen freely; closing,
merging, and deleting are shared-state or destructive actions and always
require the user's explicit confirmation on that specific list of items.**
Don't soften this even if the user has run the skill many times before --
every run's proposed actions are a fresh batch to confirm, because the repo
state (and what's safe to touch) changes every time.

## Step 1: Run the scanner

From the repo the user is working in (the skill operates on the current
working directory's repo -- confirm with the user if it's ambiguous which
repo they mean), run:

```bash
python3 ~/.claude/skills/github-cleanup/scripts/scan_repo.py
```

This is read-only as far as GitHub and your branches are concerned (`gh issue
list`, `gh pr list`, `git for-each-ref`, `git worktree list`, plus a `gh api
.../collaborators` call to gauge whether the repo looks solo or shared). The
one local write is `git fetch --prune`, so remote-branch data isn't stale; it
changes nothing on GitHub or in any branch. It prints one JSON report. If
`warnings` is non-empty (e.g. the fetch failed offline), mention it. Requires `gh` to be
installed and authenticated (`gh auth status`) and the cwd to be inside a
git repo with a GitHub remote -- if either isn't true, the script fails with
an explanatory error; surface that to the user rather than trying to work
around it silently.

If `collaborator_count` comes back `null`/`"repo_type": "unknown"` (the API
call failed, e.g. no permission to list collaborators), treat the repo as
**shared** for the rest of this workflow -- when you can't tell if you're the
only one working on something, the conservative assumption is that you're not.

## Step 2: Read the report, don't just relay it

The JSON has three top-level sections -- `issues`, `pull_requests`,
`branches` (which includes `worktrees`) -- each already bucketed by
confidence/safety. Understand what
each bucket means before you write the user-facing summary:

**Issues**
- `safe_to_close_slipped_through` -- a merged PR used a closing keyword
  (Closes/Fixes/Resolves #N) for this issue, but it's still open. GitHub
  normally auto-closes these, so an item landing here usually means the
  keyword linked to the wrong issue, or the PR merged before some edge case.
  Still confirm before closing -- don't skip the gate just because the
  signal is strong.
- `inferred_needs_confirmation` -- a merged PR's body mentions this issue's
  number (`#N`) without a closing keyword. This is a weak signal (could be
  "related to #N", "see #N for context", etc.) -- read the actual PR title
  and issue title together and use judgment before even proposing it as a
  candidate. If it's not clearly the same piece of work, drop it rather than
  presenting a bad guess.
- `open_no_linked_pr_report_only` -- open issues with no merged-PR link at
  all. **Never propose closing these.** Staleness is not completion; an issue
  with no recent activity might just be low-priority, not done. Each has
  `days_since_update` and a `stale` flag (untouched 30+ days): mention the
  stale ones as worth triaging, summarise the rest as a count, and keep all
  of them out of the confirmation batch.

**Pull requests**
- `ready_to_merge` -- not a draft, and nothing in `blocked_reasons`: no
  conflicts, CI passing (or no CI), branch protection satisfied, review not
  pending. These are your merge candidates. If `no_ci` is true, say so next
  to the item -- nothing automated has checked it.
- `blocked` -- each has `blocked_reasons` (conflicts, CI failing or still
  running, behind base, branch protection, review required, or GitHub still
  computing mergeability). Report the reasons; don't try to fix or resolve
  the blocker yourself (e.g. don't push commits to resolve conflicts) --
  that's separate work from cleanup.
- `draft_wip` -- leave these alone entirely, they're not report material
  unless the user asks.

**Branches**
- `local_merged_safe_to_delete` / `remote_merged_safe_to_delete` -- each
  item says `how` it's known to be merged:
  - `merged_pr`: a merged PR's head is exactly this branch's tip. This is
    how squash and rebase merges (GitHub's usual flow) are detected, which
    `git branch --merged` alone misses entirely. Say which PR.
  - `no_unique_commits`: the tip is already inside the default
    branch; deleting loses nothing.
  Genuinely safe, but still confirm.
- `local_newer_than_merged_pr_report_only` / `remote_newer_...` -- a PR from
  this branch merged, but the branch has commits **after** it. That newer
  work isn't on the default branch. Report it; never propose deleting.
- `local_unmerged_no_open_pr` -- not merged, and no open PR is using this
  branch. Could be abandoned work, could be something the user meant to
  come back to. Report it, do not propose deleting it.
- `local_unmerged_has_open_pr` -- this is just informational context (the
  branch is alive and tracked by an open PR); no action needed.
- `remote_unmerged_report_only` -- same idea as the local unmerged case, on
  the remote side. Report only.
- `protected_excluded` -- branch names that are always off-limits (default
  branch plus common names like `develop`/`staging`/`production`/`release`).
  These never appear as delete candidates regardless of merge status --
  that's enforced by the script, not something you need to double-check.
- `in_use_by_worktree_excluded` -- branches checked out in some worktree
  (including the current one). Excluded from deletion; the worktree section
  decides what happens to them.

**Worktrees** (`branches.worktrees`)
- `safe_to_remove` -- clean, not locked, and its branch landed through a
  merged PR. Propose `git worktree remove <path>`, then deleting its branch.
- `prunable` -- git already knows the directory is gone. Propose
  `git worktree prune`.
- `report_only` -- locked (an agent is probably using it right now), has
  uncommitted changes, its branch has an open PR, or there's no merged PR
  (which is also what a live Claude Code session's worktree looks like --
  `claude_code_managed` flags those). **Never propose removing these.**

If `repo_type` is `"shared"` (or `"unknown"`, per Step 1), raise your bar for
anything in the `inferred_needs_confirmation` bucket -- on a repo other
people also work in, a wrong guess about "this issue looks done" is more
costly (you might close something a teammate is still relying on, or that
they closed deliberately-not). When in doubt on a shared repo, downgrade an
inferred item to "reporting only" instead of including it in the proposed
batch.

## Step 3: Present one combined report

Summarize all three categories together in a single message, grouped by
confidence, before asking for confirmation. Something like:

```
## github-cleanup: owner/repo

**Issues -- proposing to close (3)**
- #42 "..." -- closed by merged PR #58 (keyword slipped through)
- #39 "..." -- PR #55 looks like it resolved this (no closing keyword, inferred)
  ...

**PRs -- ready to merge (2)**
- #61 "..." -- CI passing, approved, no conflicts
  ...

**Branches -- safe to delete (4 local, 2 remote)**
- feature/issue-38-... (squash-merged in PR #58)
  ...

**Worktrees -- safe to remove (1)**
- .claude/worktrees/agent-... (branch 12-parser, merged in PR #61)

**Reporting only, not proposing action:**
- 3 stale issues with no linked PR (oldest: #12, untouched 40 days)
- 1 unmerged branch with no open PR: experiment/old-idea
- 1 blocked PR: #59 (failing CI)
```

Then ask a single yes/no-style question covering the whole proposed batch
(the combined-confirmation flow the user prefers): something like "Want me
to go ahead with all of this, or should I skip/adjust any of it?" Let them
strike out individual items rather than making them reconfirm category by
category.

## Step 4: Execute only what was confirmed

Once the user gives the go-ahead (on the whole batch, or the adjusted
subset they specify), execute with plain `gh`/`git` commands:

```bash
gh issue close <number> --comment "Closed via github-cleanup: resolved by PR #<n>"
gh pr merge <number> --squash      # first method in merge_methods_allowed, unless the user says otherwise
git worktree remove <path>                   # never --force
git worktree prune
git branch -d <branch>                       # local, how == no_unique_commits
git branch -D <branch>                       # local, how == merged_pr only (see below)
git push <remote> --delete <branch>          # remote, merged
```

A few things worth getting right here:
- Use `-d` (safe delete) for local branches. The **one** exception is a
  branch whose `how` is `merged_pr`: git's `-d` refuses squash-merged
  branches because their commits aren't in the default branch's history,
  even though the PR merged exactly that tip. Right before `-D`, re-check
  that `git rev-parse <branch>` still equals the merged PR's head
  (`gh pr view <pr> --json headRefOid`). If it differs, stop and report --
  new commits appeared. Never use `-D` for anything else, and never to make
  a refusal go away.
- Remove a worktree before deleting its branch (git won't delete a branch
  that's checked out). If `git worktree remove` refuses, report it rather
  than adding `--force`.
- If `gh pr merge` reports the repo requires a specific merge method (merge
  commit vs. squash vs. rebase), just use what the repo enforces; don't
  fight it.
- Do the confirmed items one at a time and report each result (closed /
  merged / deleted / failed-because-X) rather than running everything
  silently and only reporting failures.

## What this skill deliberately does not do

- Doesn't guess at issues resolved outside GitHub (e.g. fixed via a hotfix
  pushed directly, or closed by conversation elsewhere) -- if there's no
  trace in gh's data, it won't surface as a candidate at all.
- Doesn't touch unmerged branches, ever, regardless of staleness -- that's
  someone's in-progress or abandoned work, and telling those two apart
  isn't something this skill tries to do. Same for worktrees that are
  locked, dirty, or have no merged PR.
- Doesn't try to fix a blocked PR (resolve conflicts, push CI fixes, chase
  down a review) -- it reports the blocker and stops there.
- Doesn't force anything (`--force`, bypassing branch protection, `-D`
  outside the verified squash-merge case) -- if the safe path fails, report
  why instead of escalating to a riskier one.
