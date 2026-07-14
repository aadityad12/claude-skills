---
name: github-cleanup
description: End-of-session GitHub housekeeping for the current repo -- scans open issues, open PRs, and local/remote branches, then proposes closing finished issues, merging ready PRs, and deleting merged branches. Use this whenever the user wants to "clean up" a repo, "wrap up" or "close out" a session, tidy up issues/PRs/branches before stepping away, or asks things like "anything to merge or close before I go?" -- even if they don't name the skill directly. Always scan and report before touching anything; never close an issue, merge a PR, or delete a branch without the user's explicit go-ahead on that specific batch of actions.
---

# GitHub Cleanup

A repo accumulates loose ends during a working session: issues that got fixed
but never got closed, PRs that are green and ready but just sitting there,
branches that were merged weeks ago and are still cluttering `git branch`.
This skill's job is to sweep all three, tell the user exactly what it found
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

This is entirely read-only (`gh issue list`, `gh pr list`, `git branch
--merged`, plus a `gh api .../collaborators` call to gauge whether the repo
looks solo or shared) and prints one JSON report. Requires `gh` to be
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
`branches` -- each already bucketed by confidence/safety. Understand what
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
- `stale_report_only` -- open issues with no merged-PR link at all. **Never
  propose closing these.** Staleness is not completion; an issue with no
  recent activity might just be low-priority, not done. Report them (e.g.
  "these haven't moved in a while, might be worth triaging") but keep them
  out of the confirmation batch entirely.

**Pull requests**
- `ready_to_merge` -- not a draft, no conflicts, CI passing (or no CI
  configured), and review requirements satisfied. These are your merge
  candidates.
- `blocked` -- failing CI, conflicts, or review not yet satisfied. Report
  why each one is blocked; don't try to fix or resolve the blocker yourself
  (e.g. don't push commits to resolve conflicts) -- that's separate work
  from cleanup.
- `draft_wip` -- leave these alone entirely, they're not report material
  unless the user asks.

**Branches**
- `local_merged_safe_to_delete` / `remote_merged_safe_to_delete` -- fully
  merged into the default branch. Genuinely safe, but still confirm --
  deleting is only reversible if the reflog hasn't expired.
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
- feature/issue-38-... (merged)
  ...

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
gh pr merge <number> --squash   # or whatever merge method the user/repo prefers -- ask if unclear
git branch -d <branch>                      # local, merged
git push origin --delete <branch>           # remote, merged
```

A few things worth getting right here:
- Use `-d` (safe delete) for local branches, never `-D` -- if git refuses
  because it doesn't think the branch is merged, that's worth surfacing to
  the user rather than force-deleting, since it means the script's merge
  check and git's own check disagree (possible if the branch was merged via
  squash/rebase rather than a fast-forward or ordinary merge commit, which
  `--merged` doesn't always detect). Don't reach for `-D` to make the
  problem go away.
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
  isn't something this skill tries to do.
- Doesn't try to fix a blocked PR (resolve conflicts, push CI fixes, chase
  down a review) -- it reports the blocker and stops there.
- Doesn't force anything (`-D`, `--force`, bypassing branch protection) --
  if the safe path fails, report why instead of escalating to a riskier one.
