"""A pathspec commit passes a gate step reading the index git hands the hook (GOH.PATHSPECCOMMIT).

A private Swift app, 2026-11-16: every `git commit -- <path>` was refused with
"index_tree: git write-tree in ...: fatal: Unable to create .../.git/index.lock: File exists".
With a pathspec (and with `-a`) git holds the repository's own index lock while the hook runs and
commits a temporary index that only GIT_INDEX_FILE names. The app's tools/index_tree.py honoured
GIT_INDEX_FILE, as git's hook contract says; goh's hook had dropped it at entry (contract #12) and
carried it only as GOH_HOOK_INDEX_FILE, so the step ran `git write-tree` on the locked real index.
Sessions sharing a checkout need pathspec commits: an unscoped add+commit in one swept another
session's staged files into its commit.

The fix is not per step: while the hook carries an index, `lib/hookgit/git` is first on PATH and
binds it for an index read in the committing repository (gates/_git_env.sh `_goh_hook_git_shim`).

Each test commits for real through the stock hook, in a main checkout and a linked worktree. The
gate is the app's shape -- a Python step running a plain `git write-tree` -- plus a scratch repo
built from inside the committing one, which must never see the commit's index. The calibration
runs the same step through the real git by absolute path (no shim): the lock error comes back.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT, hermetic_env, native_goh_path
from test_commit_hook_git_env import _consumer, _git
from timing_bounds import patience

# The private app's step, reduced: a plain `git write-tree` in the repository, the hook's env as is.
INDEX_TREE = """import os, subprocess, sys
done = subprocess.run([sys.argv[1], "write-tree"], capture_output=True, text=True,
                      stdin=subprocess.DEVNULL, check=False)
if done.returncode != 0:
    sys.exit("index_tree: git write-tree in " + os.getcwd() + ": " + done.stderr.strip())
open(os.environ["TREE_OUT"], "w").write(done.stdout.strip())
"""

# tools/gate.sh: the step, then a scratch repo (made by the test) driven from INSIDE the committing
# repository (`add`/`commit` there, `ls-files` by -C from here), the shape of a consumer suite.
GATE = """#!/usr/bin/env bash
set -euo pipefail
python3 -I "$GATE_DIR/index_tree.py" "{git}"
t="$GATE_DIR/scratch"
echo scratch > "$t/scratch.txt"
(cd "$t" && git add scratch.txt && git -c user.email=t@t -c user.name=t commit -q -m s)
git -C "$t" ls-files > "$GATE_DIR/scratch_files"
"""


def _repo(tmp_path: Path, git: str, where: str) -> Path:
    main, wt = _consumer(
        tmp_path, GATE.format(git=git), (REPO_ROOT / "hooks" / "pre-commit").read_bytes()
    )
    return main if where == "main" else wt


def _commit(repo: Path, tmp_path: Path, how: str) -> tuple[subprocess.CompletedProcess, Path]:
    """Stage `other.txt`, edit `ours.txt` and README.md, commit HOW; the commit leaves other.txt."""
    gate_dir = tmp_path / "gate"
    gate_dir.mkdir()
    (gate_dir / "index_tree.py").write_text(INDEX_TREE)
    fast_init(gate_dir / "scratch")
    (repo / "ours.txt").write_text("ours\n")
    _git(repo, "add", "ours.txt")
    _git(repo, "commit", "-q", "--no-verify", "-m", "base")
    (repo / "ours.txt").write_text("changed\n")
    if how == "path":
        (repo / "other.txt").write_text("staged, not ours\n")
        _git(repo, "add", "other.txt")
    args = ["--", "ours.txt"] if how == "path" else ["-a"]
    env = hermetic_env(
        GOH_DIR=str(REPO_ROOT),
        GOH_BIN=str(native_goh_path()),
        GATE_DIR=str(gate_dir),
        TREE_OUT=str(tmp_path / "tree"),
    )
    got = subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "ours", *args], env=env,
                         capture_output=True, text=True, timeout=patience(180),
                         stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    return got, gate_dir


@pytest.mark.parametrize("how", ["path", "all"])
@pytest.mark.parametrize("where", ["main", "worktree"])
def test_a_gate_step_writing_the_index_tree_takes_a_pathspec_commit(tmp_path, where, how):
    repo = _repo(tmp_path, "git", where)
    got, gate_dir = _commit(repo, tmp_path, how)
    text = got.stdout + got.stderr
    assert got.returncode == 0, text
    # The tree the step wrote is the one the commit recorded: the temporary index, not the own one.
    assert (tmp_path / "tree").read_text() == _git(repo, "rev-parse", "HEAD^{tree}"), text
    assert _git(repo, "show", "--name-only", "--format=", "HEAD") == "ours.txt", text
    if how == "path":  # staged in the real index, left out of the pathspec commit, still staged
        assert _git(repo, "diff", "--cached", "--name-only") == "other.txt", text
    # The scratch repo built from inside the committing one kept its own index.
    assert (gate_dir / "scratch_files").read_text().split() == ["scratch.txt"], text


@pytest.mark.parametrize("where", ["main", "worktree"])
def test_calibration_a_step_that_bypasses_the_hook_index_hits_the_held_lock(tmp_path, where):
    """The same step through the real git by absolute path: the app's refusal, so the fixture
    holds the lock and the test above can fail."""
    real = shutil.which("git")
    assert real
    repo = _repo(tmp_path, real, where)
    got, _ = _commit(repo, tmp_path, "path")
    text = got.stdout + got.stderr
    assert got.returncode != 0, text
    assert "index.lock" in text and "File exists" in text, text
    assert _git(repo, "log", "--format=%s", "-1") == "base", text
