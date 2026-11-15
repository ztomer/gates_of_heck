"""index_tree names the tree a commit records, from inside its own hook (GOH.REFRESHINDEXLOCK).

`goh hooks --refresh` in a private Swift app made a commit whose gate step wrote the index's tree
with a plain `git write-tree`: the outer `git commit` held `.git/index.lock` while its hook ran, and
the step died "Unable to create .git/index.lock: File exists". The helper (`lib/run_path.sh`
goh_index_tree, `lib/run_path.py` index_tree) writes from a private copy; what is pinned here is
that the copy is of the right index. A hook drops its git variables at entry (`goh_hook_unbind`)
and carries a commit's TEMPORARY index (`commit -a`, `commit -- path`) only as
GOH_HOOK_INDEX_FILE, so a helper that reads GIT_INDEX_FILE alone copied the repository's own index
there, and named the tree of the state before the commit, not the one it records.

Each test commits for real. The staged bytes differ from the working tree's, so a tree written from
the worktree, or from the wrong index, names something the commit did not record.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from conftest import REPO_ROOT, git, hermetic_env
from timing_bounds import patience

SH_HOOK = """#!/usr/bin/env bash
. "{goh}/gates/_git_env.sh"
. "{goh}/lib/run_path.sh"
goh_hook_unbind
if test -e "$(git rev-parse --git-dir)/index.lock"; then echo held > "{out}.lock"; fi
goh_index_tree > "{out}" || exit 1
"""
PY_HOOK = """#!/usr/bin/env bash
. "{goh}/gates/_git_env.sh"
goh_hook_unbind
if test -e "$(git rev-parse --git-dir)/index.lock"; then echo held > "{out}.lock"; fi
exec python3 -I -c '
import sys
sys.path.insert(0, "{goh}/lib")
import run_path
open("{out}", "w").write(run_path.index_tree("."))
'
"""
HOOKS = {"shell": SH_HOOK, "python": PY_HOOK}


def _commit(repo: Path, hook: str, out: Path, mode: str, run: Path) -> str:
    """Stage `staged`, leave a different worktree byte, commit in MODE, return the hook's tree."""
    h = repo / ".git" / "hooks" / "pre-commit"
    h.write_text(hook.format(goh=REPO_ROOT, out=out))
    h.chmod(0o755)
    tracked = git(repo, "ls-files").splitlines()[0]
    (repo / "other.txt").write_text("other\n")
    git(repo, "add", "other.txt")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--no-verify", "-m", "o")
    (repo / tracked).write_text("staged\n")
    if mode != "all":
        git(repo, "add", tracked)
    if mode == "plain":
        (repo / tracked).write_text("worktree\n")
    args = {"plain": [], "all": ["-a"], "path": ["--", tracked]}[mode]
    env = hermetic_env(drop_git=True, GOH_RUN_DIR=str(run))
    done = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                           "-m", "x", *args], cwd=repo, env=env, capture_output=True, text=True,
                          timeout=patience(120), stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert done.returncode == 0, done.stdout + done.stderr
    return git(repo, "rev-parse", "HEAD^{tree}").strip()


@pytest.mark.parametrize("mode", ["plain", "all", "path"])
@pytest.mark.parametrize("face", sorted(HOOKS))
def test_index_tree_in_a_commit_hook_names_the_tree_the_commit_records(
    repo: Path, tmp_path: Path, face: str, mode: str
) -> None:
    out = tmp_path / "tree"
    recorded = _commit(repo, HOOKS[face], out, mode, tmp_path / "run")
    assert out.read_text().strip() == recorded
    # `commit -a` and `commit -- path` hold the shared lock while the hook runs: the case at risk.
    assert (out.parent / "tree.lock").exists() == (mode != "plain")
