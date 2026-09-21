# claude-skills

Personal collection of [Claude Code](https://claude.com/claude-code) skills. Each subdirectory is a self-contained skill — drop it (or symlink it) into `~/.claude/skills/` to install.

## Skills

| Skill | What it does | Model tier |
|---|---|---|
| [`github-cleanup`](github-cleanup/) | End-of-session GitHub housekeeping: scans open issues, open PRs, branches, and git worktrees, then proposes closing finished issues, merging ready PRs, deleting merged branches (including squash-merged ones), and removing finished worktrees — always reports first, never acts without explicit confirmation. | **Balanced** (pinned: `sonnet`) |
| [`issues-creator`](issues-creator/) | Deep multi-agent codebase audit (bugs, security, feature gaps, UI/UX, stale docs, optional test gaps) via a `Workflow` script — adversarially verified, deduplicated, tiered draft issues, written so a person or agent can act on them; never files anything without explicit approval. | **Deep** (pinned: `opus`; recon runs on `sonnet`) |
| [`explain-diff`](explain-diff/) | Turns a diff, branch, or PR into a self-contained HTML explainer — background, intuition, code walkthrough, and an interactive quiz — saved as a dated file outside the repo. | **Balanced**, **Deep** for large or subtle changes (not pinned) |
| [`autopilot`](autopilot/) | Works through a repo's GitHub issues on autopilot: parallel subagents in isolated worktrees, dependency-ordered merges, CI kept green — and stops only when the owner is needed, with an exact to-do list (what to do, how long, what happens next). Modes: `review` (default), `auto-merge`, `plan`. | **Balanced** orchestrator and workers, **Deep** for issues labelled `hard` (pinned, configurable per repo) |

The skills chain: `issues-creator` writes issues with `Depends on:` lines →
`autopilot` works through them → `github-cleanup` tidies up afterwards.

## Choosing a model

Each skill is tagged with the **tier** of model it needs, based on what the task demands,
not on a specific model version:

| Tier | Needs | Current alias |
|---|---|---|
| **Fast** | Mechanical, fully scripted steps with little judgment | `haiku` |
| **Balanced** | Real judgment, but a script or a clear procedure does the heavy lifting | `sonnet` |
| **Deep** | Open-ended reasoning where a subtle miss is expensive: audits, adversarial verification, tricky code | `opus` |

**How this stays current.** Where a skill's tier is clear-cut it's **pinned** in the
skill's frontmatter (`model: sonnet`) using a tier *alias*, never a versioned model ID.
Claude Code resolves an alias to the newest model in that tier, so nothing here needs
editing when new models ship. The pin applies only while the skill runs; your session
model comes back on your next message. Workflow and subagent steps inside a skill can
choose their own tier too (e.g. `autopilot` sends `hard` issues to `opus`).

**Overriding.** Edit the `model:` line in a skill's `SKILL.md` (or delete it to use
whatever your session is on). `autopilot` also reads `worker-model` and `careful-model`
from a repo's `## Autopilot` section in `CLAUDE.md`.

If a new tier appears, or the aliases change, only this table and the `model:` lines need
updating.

## Installing a skill

```bash
git clone https://github.com/<your-username>/claude-skills.git
ln -s "$(pwd)/claude-skills/<skill-name>" ~/.claude/skills/<skill-name>
```

Or just copy the skill's folder into `~/.claude/skills/` directly if you don't want it symlinked to this repo.
