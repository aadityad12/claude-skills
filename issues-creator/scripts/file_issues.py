#!/usr/bin/env python3
"""
File approved draft issues on GitHub for the issues-creator skill.

Usage:
    python3 file_issues.py approved.json [--dry-run]

approved.json is a list of items, in the order they should be filed:
    [{"key": "B3", "title": "...", "body": "...", "category": "security",
      "depends_on": ["B1"]}, ...]

Why a script instead of `gh issue create --title "..." --body "..."` in the shell: titles and
bodies come from audit findings and routinely contain backticks, `$(...)`, quotes, and code.
In a shell string those get mangled or, worse, executed. Here every value goes to `gh` as a
separate argv entry (no shell), and bodies go through a temporary file.

It also:
  - uses an existing repo label when one matches the item's category (never creates labels);
  - files dependencies first, then appends "Depends on: #N, #M" with the real issue numbers,
    so tools like the autopilot skill can order the work;
  - prints one JSON result per item and never stops early on a single failure.
"""
import json
import os
import subprocess
import sys
import tempfile

CATEGORY_LABELS = {
    "bug": ["bug"],
    "security": ["security", "bug"],
    "ui": ["ui", "ux", "accessibility", "design"],
    "docs": ["documentation", "docs"],
    "feature": ["enhancement", "feature"],
    "tests": ["tests", "testing"],
}


def existing_labels():
    out = subprocess.run(["gh", "label", "list", "--limit", "500", "--json", "name"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        return {}
    return {l["name"].lower(): l["name"] for l in json.loads(out.stdout)}


def pick_label(category, labels):
    for candidate in CATEGORY_LABELS.get((category or "").lower(), [(category or "").lower()]):
        if candidate in labels:
            return labels[candidate]
    return None


def order_by_dependencies(items):
    by_key = {i["key"]: i for i in items}
    ordered, seen = [], set()

    def visit(item, stack):
        if item["key"] in seen:
            return
        if item["key"] in stack:
            raise ValueError(f"dependency cycle involving {item['key']}")
        for dep in item.get("depends_on") or []:
            if dep in by_key:
                visit(by_key[dep], stack | {item["key"]})
        seen.add(item["key"])
        ordered.append(item)

    for item in items:
        visit(item, set())
    return ordered


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: file_issues.py approved.json [--dry-run]")
    dry_run = "--dry-run" in sys.argv
    with open(sys.argv[1], encoding="utf-8") as f:
        items = json.load(f)
    for i, item in enumerate(items):
        item.setdefault("key", f"item{i + 1}")

    labels = existing_labels()
    filed = {}  # key -> issue number
    results = []
    for item in order_by_dependencies(items):
        body = item["body"].rstrip()
        deps = [filed[d] for d in item.get("depends_on") or [] if d in filed]
        missing = [d for d in item.get("depends_on") or [] if d not in filed]
        if deps:
            body += "\n\nDepends on: " + ", ".join(f"#{n}" for n in deps)
        cmd = ["gh", "issue", "create", "--title", item["title"]]
        label = pick_label(item.get("category"), labels)
        if label:
            cmd += ["--label", label]
        result = {"key": item["key"], "title": item["title"], "label": label}
        if missing:
            result["unfiled_dependencies"] = missing
        if dry_run:
            result.update(status="dry-run", depends_on=deps)
            results.append(result)
            filed[item["key"]] = f"<{item['key']}>"
            continue
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as tmp:
            tmp.write(body + "\n")
        try:
            out = subprocess.run(cmd + ["--body-file", tmp.name], capture_output=True, text=True)
        finally:
            os.unlink(tmp.name)
        if out.returncode == 0:
            url = out.stdout.strip().splitlines()[-1]
            filed[item["key"]] = int(url.rsplit("/", 1)[-1])
            result.update(status="filed", number=filed[item["key"]], url=url)
        else:
            result.update(status="failed", error=out.stderr.strip())
        results.append(result)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
