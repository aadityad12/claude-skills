# claude-skills

Personal collection of [Claude Code](https://claude.com/claude-code) skills. Each subdirectory is a self-contained skill — drop it (or symlink it) into `~/.claude/skills/` to install.

## Skills

| Skill | What it does |
|---|---|
| [`github-cleanup`](github-cleanup/) | End-of-session GitHub housekeeping: scans open issues, open PRs, and local/remote branches, then proposes closing finished issues, merging ready PRs, and deleting merged branches — always reports first, never acts without explicit confirmation. |
| [`issues-creator`](issues-creator/) | Deep multi-agent codebase audit (bugs, security, feature gaps, UI/UX, stale docs) via a `Workflow` script — produces a prioritized, deduplicated draft issue list; never files anything on GitHub without explicit approval. |

## Installing a skill

```bash
git clone https://github.com/<your-username>/claude-skills.git
ln -s "$(pwd)/claude-skills/<skill-name>" ~/.claude/skills/<skill-name>
```

Or just copy the skill's folder into `~/.claude/skills/` directly if you don't want it symlinked to this repo.
