# claude-skills

Personal collection of [Claude Code](https://claude.com/claude-code) skills. Each subdirectory is a self-contained skill — drop it (or symlink it) into `~/.claude/skills/` to install.

## Skills

| Skill | What it does |
|---|---|
| [`github-cleanup`](github-cleanup/) | End-of-session GitHub housekeeping: scans open issues, open PRs, and local/remote branches, then proposes closing finished issues, merging ready PRs, and deleting merged branches — always reports first, never acts without explicit confirmation. |
| [`issues-creator`](issues-creator/) | Deep multi-agent codebase audit (bugs, security, feature gaps, UI/UX, stale docs) via a `Workflow` script — produces a prioritized, deduplicated draft issue list; never files anything on GitHub without explicit approval. |
| [`explain-diff`](explain-diff/) | Turns a diff, branch, or PR into a self-contained HTML explainer — background, intuition, code walkthrough, and an interactive quiz — saved as a dated file outside the repo. |
| [`autopilot`](autopilot/) | Works through a repo's GitHub issues on autopilot: parallel subagents in isolated worktrees, dependency-ordered merges, CI kept green — and stops only when the owner is needed, with an exact to-do list (what to do, how long, what happens next). Modes: `review` (default), `auto-merge`, `plan`. |

## Installing a skill

```bash
git clone https://github.com/<your-username>/claude-skills.git
ln -s "$(pwd)/claude-skills/<skill-name>" ~/.claude/skills/<skill-name>
```

Or just copy the skill's folder into `~/.claude/skills/` directly if you don't want it symlinked to this repo.
