#!/usr/bin/env python3
"""A Claude Code SessionStart hook (source `compact`): re-read the repo state after a compaction.

A compaction summary keeps what the summariser judged important, and the working tree's exact
state is the kind of detail it drops. SessionStart stdout is added to the context, so after a
compaction this prints the branch, the uncommitted paths and the last commits of the session's
directory, read from git rather than from the summary's memory of them. Any other source, or a
directory outside a repository, prints nothing (tests/test_claude_compact_hooks.py).
"""

from __future__ import annotations

import json
import subprocess
import sys

GIT_TIMEOUT_S = 5
MAX_STATUS_LINES = 40
RECENT_COMMITS = 5


def _git(cwd: str, *args: str) -> str | None:
    try:
        r = subprocess.run(
            ["git", "-C", cwd, *args],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.rstrip("\n") if r.returncode == 0 else None


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    cwd = event.get("cwd")
    if event.get("source") != "compact" or not cwd:
        return 0
    if _git(cwd, "rev-parse", "--is-inside-work-tree") != "true":
        return 0
    branch = _git(cwd, "branch", "--show-current") or "(detached)"
    status = (_git(cwd, "status", "--short") or "").splitlines()
    commits = _git(cwd, "log", "--oneline", f"-{RECENT_COMMITS}") or "(no commits)"
    shown = status[:MAX_STATUS_LINES]
    if len(status) > len(shown):
        shown.append(f"… and {len(status) - len(shown)} more")
    print("Repository state re-read from git after the compaction (the summary may predate it):")
    print(f"branch: {branch}")
    print("uncommitted:" if shown else "uncommitted: none")
    for line in shown:
        print(f"  {line}")
    print("recent commits:")
    for line in commits.splitlines():
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
