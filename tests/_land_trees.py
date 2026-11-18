"""The trees a land's suite carries verdicts from, and their diffs (tests/_land_verdicts.py).

Split from the plugin for its line cap (GOH.COVCARRY); the plugin imports these names, so a fixture
session patches them there (`lv.trees`, `lv.diff`).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ANCESTORS = 20  # first-parent commits of HEAD searched for a recorded tree


def _git(root: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, env=env,
                       stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    return r.stdout.strip() if r.returncode == 0 else ""


def trees(root: Path, pre_sha: str) -> list[tuple[str, str]]:
    """(commit, tree) to carry from, nearest first: the pre-step's commit, then HEAD's first-parent
    ancestors (HEAD's own tree is the pre-step's when nothing rebased)."""
    shas = [pre_sha] if pre_sha else []
    shas += _git(root, "rev-list", "--first-parent", f"--max-count={ANCESTORS}", "HEAD~1").split()
    out = []
    for sha in shas:
        tree = _git(root, "rev-parse", "--verify", "--quiet", f"{sha}^{{tree}}")
        if tree:
            out.append((sha, tree))
    return out


def diff(root: Path, sha: str) -> list[str] | None:
    """The paths whose bytes differ between sha's tree and HEAD's; None when git cannot say."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    r = subprocess.run(["git", "-C", str(root), "diff", "--name-only", "--no-renames", sha, "HEAD"],
                       capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
                       check=False)  # fmt: skip
    return [ln for ln in r.stdout.splitlines() if ln] if r.returncode == 0 else None
