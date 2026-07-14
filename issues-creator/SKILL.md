---
name: issues-creator
description: Deep, multi-agent codebase audit that hunts for bugs, security issues, feature gaps, UI/UX problems, and stale documentation, then produces a prioritized, deduplicated DRAFT issue list for the user to review before anything is filed on GitHub. Use this whenever the user wants a thorough/deep review or audit of a codebase, asks to "find bugs", "find issues", "review this codebase with a fine tooth comb", "what's missing from this project", "find security problems", "audit the UI", "check for stale docs", or wants a comprehensive pass before a release -- even if they don't name the skill directly. This is a heavy, multi-agent operation (it runs a Workflow that fans out several reviewer subagents plus a verification pass) -- not for a quick one-off bug check on a single file, which should just be handled directly. Always presents a draft for approval; never files a GitHub issue without the user explicitly signing off on that specific batch.
---

# Issues Creator

A deep audit of a codebase across five independent lenses -- correctness bugs,
security, feature gaps, UI/UX (both static code review and, when possible,
actually driving the app), and stale documentation -- producing one
prioritized, tagged, numbered draft issue list ready for the user's review.

The core principle, same as `github-cleanup`: **the audit and the draft are
free to run on their own; filing anything on GitHub always requires the
user's explicit go-ahead on that specific batch.** Findings get verified
before they're trusted, and nothing gets filed until a human has seen the
list.

## Step 1: Run the audit workflow

Invoke the `Workflow` tool with:

```
Workflow({
  scriptPath: "~/claude-skills/issues-creator/workflow.js",
  args: { repoPath: "<absolute path to the repo being audited>" }
})
```

(Expand `~` to the real home directory path -- the tool needs an absolute
path.)

The workflow does, in order:

1. **Recon** -- one agent maps the repo, pulls the existing open GitHub
   issues (for dedup later), and checks whether the UI can actually be
   driven right now (device/emulator/dev-server available).
2. **Review** -- up to six reviewers run per dimension (bugs, security,
   feature gaps, UI static, UI driven -- skipped automatically if recon found
   no way to drive it, docs staleness). Each one only reads/inspects, never
   edits anything.
3. **Verify** -- every individual finding gets an adversarial second look
   (an independent check trying to refute it, or for feature ideas, checking
   it isn't already built or already tracked) before it's trusted. Findings
   that don't survive this are dropped silently -- they were never real.
4. **Synthesize** -- a final pass tiers everything (P0-P3, see rubric below),
   drops anything that duplicates an existing open issue, and produces two
   separate numbered lists.

This can take a while and spawns a meaningful number of subagents -- that's
expected for "fine tooth comb" thoroughness. If the user wants a lighter/
faster pass, you can scope it via `args.dimensions` (an array of any of
`bugs`, `security`, `features`, `ui_static`, `ui_driven`, `docs`) or turn off
the driven-UI pass specifically with `args.driveUI: false` (useful when you
already know no device/emulator is available and don't want to pay for the
check).

## Step 2: Present the draft -- don't file anything yet

The workflow returns `{ bugs_and_issues, feature_ideas, dropped_as_duplicate,
skipped_dimensions }`. Present this to the user as two clearly separate
numbered lists (bugs/issues are never mixed with feature ideas -- they're
different kinds of thing and get judged differently):

```
## Bugs & Issues (N)
1. [P0][security] <title>
   <one-line summary, file reference>
2. [P1][bug] <title>
   ...

## Feature Ideas (M)
1. [high value] <title>
   ...

Skipped: <skipped_dimensions, if any, and why>
Dropped as duplicates of existing issues: <dropped_as_duplicate, if any>
```

Then ask which of these to actually file -- e.g. "file all of them", "just
the P0/P1s", or specific numbers. Don't file anything until they answer.

## Step 3: File only what's approved

For each approved item, run `gh issue create --title "..." --body "..."`.
Two details worth getting right:

- **Tag visibly even without labels.** Prefix the title with `[tier]
  [category]` (e.g. `[P1][security] ...`) regardless of whether the repo has
  matching GitHub labels, so the priority/category survives even if labels
  aren't set up.
- **Use existing labels when they match.** Run `gh label list` once; if the
  repo already has labels like `bug`, `security`, `enhancement`,
  `documentation` (case-insensitive match is fine), pass the matching one via
  `--label`. Don't create new labels on the user's repo unprompted -- if
  nothing matches, just skip `--label` and rely on the title prefix.

Report each result (filed as #N / failed because X) rather than running
everything silently.

## What this skill deliberately does not do

- Doesn't fix anything it finds -- this produces issues, not patches. If the
  user wants something fixed, that's a separate, explicit follow-up.
- Doesn't loop indefinitely hunting for more findings -- each dimension gets
  one thorough pass plus verification, not an exhaustive "keep searching
  until nothing new turns up" sweep. Re-running periodically as the codebase
  changes is the expected usage pattern, not one infinitely deep run.
- Doesn't guess at UI bugs from code when it could have actually driven the
  app, and doesn't pretend to drive the app when nothing was actually
  reachable -- recon's `ui_driveable` check gates this honestly either way.
- Doesn't invent findings to pad the list -- an empty findings array from a
  dimension is a valid, expected outcome, not a failure.
