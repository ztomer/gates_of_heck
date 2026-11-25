"""`this_tree(dest)`: a clone of this checkout AS IT STANDS -- HEAD, then its uncommitted edits and
untracked files, uncommitted -- for a test that judges the whole tree (GOH.FSEVQUIET).

The clone-diff-apply-untar was made by hand in two modules, five times a run, ~2,000 files written
each. Built once a run and clonefile'd: no bytes, and three file events to make (what it leaves to
remove is still one a file, so a test that needs only the gates takes todays-gates).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from _tree_clone import clone_tree, process_template
from timing_bounds import patience

REPO_ROOT = Path(__file__).resolve().parent.parent


def _env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _git(tree: Path, *args: str, data: bytes | None = None) -> bytes:
    return subprocess.run(["git", "-C", str(tree), *args], input=data, capture_output=True,
                          check=True, timeout=patience(120), env=_env(),
                          ).stdout  # fmt: skip


def _build(tree: Path) -> None:
    subprocess.run(["git", "clone", "-q", "--shared", str(REPO_ROOT), str(tree)], check=True,
                   capture_output=True, stdin=subprocess.DEVNULL, timeout=patience(120),
                   env=_env())  # fmt: skip
    # A clonefile keeps size and mtime and gives a new inode: without this, the first git of every
    # clone reads all ~2,000 files again.
    _git(tree, "config", "core.checkStat", "minimal")
    _git(tree, "config", "core.trustctime", "false")
    diff = _git(REPO_ROOT, "diff", "HEAD", "--binary")
    if diff:
        _git(tree, "apply", "--whitespace=nowarn", data=diff)
    others = _git(REPO_ROOT, "ls-files", "-z", "--others", "--exclude-standard")
    if others:  # as the lane's next commit holds them; whole, by tar, never a part by hand
        pack = subprocess.run(["tar", "-C", str(REPO_ROOT), "--null", "-T", "-", "-cf", "-"],
                              input=others, capture_output=True, check=True,
                              timeout=patience(120)).stdout  # fmt: skip
        subprocess.run(["tar", "-C", str(tree), "-xf", "-"], input=pack, capture_output=True,
                       check=True, timeout=patience(120))  # fmt: skip
    _git(tree, "add", "-A")  # the edits in the index: each clone's `add -A` finds nothing to hash


def tree_template() -> Path:
    """The run's one, to clone parts of, never to write in."""
    return process_template("this-tree", _build)


def this_tree(dest: Path) -> Path:
    clone_tree(tree_template(), dest)
    return dest
