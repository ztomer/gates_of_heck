"""An explicit GIT_INDEX_FILE survives goh_bind_hook_index (GOH.HOOKINDEX).

goh_bind_hook_index put the index an OUTER commit hook carried (GOH_HOOK_INDEX_FILE) in place of a
GIT_INDEX_FILE its caller had set on purpose, so a test that ran its own gate over its own index,
inside a commit hook, read the outer commit's index instead (a consumer's lint-gate test,
EST.REDGATES3, 2026-11-13; worked around there by dropping the carried index). A caller's explicit
index wins; with none, the carried one is bound as before.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT, hermetic_env


def _bound(repo: Path, explicit: str | None) -> str:
    gitdir = subprocess.run(["git", "-C", str(repo), "rev-parse", "--absolute-git-dir"],
                            capture_output=True, text=True, check=True,
                            stdin=subprocess.DEVNULL).stdout.strip()  # fmt: skip
    env = hermetic_env(drop_git=True)
    env["GOH_HOOK_INDEX_FILE"] = str(repo / "outer-index")
    env["GOH_HOOK_GIT_DIR"] = str(Path(gitdir).resolve())
    if explicit is not None:
        env["GIT_INDEX_FILE"] = explicit
    script = f'. "{REPO_ROOT}/gates/_git_env.sh"; goh_bind_hook_index; printf %s "$GIT_INDEX_FILE"'
    return subprocess.run(["bash", "-c", script], cwd=repo, env=env, capture_output=True,
                          text=True, check=True, stdin=subprocess.DEVNULL).stdout  # fmt: skip


@pytest.mark.parametrize("explicit", [None, "own-index"])
def test_an_explicit_index_wins_over_a_carried_one(tmp_path, explicit):
    repo = tmp_path / "r"
    fast_init(repo)
    got = _bound(repo, None if explicit is None else str(tmp_path / explicit))
    want = str(repo / "outer-index") if explicit is None else str(tmp_path / explicit)
    assert got == want, (explicit, got)
