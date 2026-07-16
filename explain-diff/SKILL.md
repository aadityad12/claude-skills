---
name: explain-diff
description: Produces a rich, interactive, self-contained HTML explainer for a code change -- diff, branch, or PR -- with background, intuition, a code walkthrough, and an interactive multiple-choice quiz to check understanding. Use whenever the user asks to "explain this diff/PR/branch", wants a deep-dive writeup of a change, asks for a walkthrough with a quiz, or wants to understand a change well enough to explain it to someone else -- even if they don't name the skill directly. Saves a single dated HTML file outside the repo rather than modifying anything in it.
---

# Explain Diff

Turns a diff, branch, or PR into a self-contained HTML explainer: enough
background to orient a reader, the core intuition behind the change, a
guided walkthrough of the actual code, and a short quiz so the reader can
check they really understood it.

Unlike `github-cleanup` and `issues-creator`, this skill has no side effects
on GitHub or git state -- it only reads code and writes one new file outside
the repo. There's no confirmation gate to run it; the judgment calls are all
about quality -- exploring enough surrounding code to get the background
right, and getting the HTML output format exactly right so the file actually
renders well.

## Step 1: Understand the change

Identify what's being explained (a diff, a branch vs. its base, or a PR) and
read it in full. Then broadly explore the surrounding code -- don't limit
yourself to the changed lines. You need enough context to write both a
beginner-level background section and an accurate code walkthrough.

## Step 2: Write the four sections

- **Background** -- explain the existing system relevant to this change. We
  don't know how much the reader already knows, so include a deep background
  for beginners (note that it can be skipped if the reader is already
  familiar), then a narrower background directly relevant to the change.
- **Intuition** -- explain the core intuition for the change. Focus on the
  essence, not full detail. Use concrete examples with toy data. Use figures
  and diagrams liberally.
- **Code** -- a high-level walkthrough of the changes, grouped/ordered in an
  understandable way, not just file-by-file in diff order.
- **Quiz** -- five medium-difficulty questions that test whether the reader
  actually understood the substance of the change (not gotchas). Present
  these as interactive multiple-choice: clicking an answer should tell the
  reader whether they were right and explain why.

Write with the clarity and flow of Martin Kleppmann -- engaging, classic
style, smooth transitions between sections.

Diagram guidance: pick a small number of diagram families and reuse them
throughout rather than inventing a new visual language per section. Two
that work well:
- A simplified version of the UI the user sees in the app, for UI changes.
- A system diagram showing data flow or communication between components,
  always with example data included.

Never use ASCII diagrams -- always build diagrams as simple HTML (divs,
lists, etc.), not text art.

## Step 3: Build the single HTML file

- One self-contained HTML file: inline CSS and JavaScript, no external
  dependencies.
- One long page with section headers and a table of contents -- don't use
  tabs for the top-level structure.
- Basic responsive styling so it's readable on a phone.
- Use callouts for key concepts, definitions, and important edge cases.
- For code blocks, always use `<pre>` tags. If a custom-styled div is used
  instead, it **must** include `white-space: pre-wrap` in its CSS, or the
  browser will collapse all newlines into one line. Before saving, scan
  every code block in the generated HTML and confirm each one has
  `white-space: pre` or `pre-wrap`.

## Step 4: Save it

Save the file in a global place on the user's computer, outside the code
repo -- this is a personal reading artifact, not something that belongs in
version control. Name it starting with today's date in `YYYY-MM-DD-` format
so files sort chronologically and are easy to spot, e.g.:

```
/tmp/2026-01-12-explanation-<slug>.html
```

Report the file path back to the user when done.

## What this skill deliberately does not do

- Doesn't touch the repo -- it never commits, edits tracked files, or writes
  inside version control.
- Doesn't post or publish anywhere (Notion, Slack, GitHub, etc.) -- output is
  a single local HTML file only.
- Doesn't skip the quiz or make it trivial -- if the questions don't require
  actually understanding the change, rewrite them.
- Doesn't reach for ASCII art or unstyled `<div>` code blocks -- both break
  the "actually readable" bar this skill is meant to clear.
