"""Every item a land's range names has its changelog fragment (GOH.FRAGMENTATLAND, 2026-11-22).

Two lanes landed with no changelog/ fragment, and the omission surfaced only when `goh ship` cut a
release and refused on an empty Unreleased (a coordinator worktree, commit and extra land later).
In a repository whose tip tracks a `changelog/` directory, `goh land --check` names each item id a
commit subject of HEAD's own range starts with (`GOH.X: ...`) that has no `changelog/<ID>.md` at
the tip and whose commits carry no `Changelog: none` trailer (work no user sees).

Every item a land's range names has its changelog fragment (GOH.FRAGMENTATLAND): in a repository
whose tip tracks `changelog/`, `goh land --check` names each item id a commit subject of HEAD's own
range starts with (`GOH.X: ...`) with no `changelog/<ID>.md` at the tip and no `Changelog: none`
trailer, refusing the check where the lane can still add it rather than at the release. Invoked by
`lib/land_check.py`.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gitrun  # readers under a deadline that never strand a lock (GOH.GITKILL)

ITEM = re.compile(r"^([A-Z][A-Z0-9]*(?:[.\-][A-Z0-9]+)+):")
NONE = re.compile(r"^Changelog:\s*none\s*$", re.IGNORECASE | re.MULTILINE)
DIR = "changelog/"


def _git(top: str | Path, *args: str) -> subprocess.CompletedProcess:
    return gitrun.run_reader(args, repo=top, timeout=60)


def missing(top: str | Path, base: str = "main") -> list[str]:
    """One refusal per item id of HEAD's range (since its merge-base with BASE) with no fragment
    and no `Changelog: none`; [] when the tip keeps no changelog/ or the base is not found."""
    tracked = _git(top, "ls-tree", "--name-only", "HEAD", DIR)
    if tracked.returncode != 0 or not tracked.stdout.strip():
        return []
    mb = _git(top, "merge-base", "HEAD", base)
    if mb.returncode != 0:
        return []
    log = _git(top, "log", "--no-merges", "--format=%s%x00%B%x1e", f"{mb.stdout.strip()}..HEAD")
    items: dict[str, bool] = {}
    for rec in log.stdout.split("\x1e"):
        subject, _, body = rec.strip("\n").partition("\x00")
        m = ITEM.match(subject)
        if m:
            items[m.group(1)] = items.get(m.group(1), False) or bool(NONE.search(body))
    have = set(_git(top, "ls-tree", "--name-only", "HEAD", DIR).stdout.split())
    return [
        f"{item} has no {DIR}{item}.md fragment at the tip -- add one (what a user of it "
        f"sees), or say `Changelog: none` in its commit's trailers"
        for item, exempt in sorted(items.items())
        if not exempt and f"{DIR}{item}.md" not in have
    ]
