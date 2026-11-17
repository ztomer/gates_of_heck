"""A land's pre-step judges the tip main will fast-forward to, rebased first (GOH.PREPARITY).

tools/land.sh ran the pre-step on the branch as the lane left it and rebased onto main only after,
under the land lock: PREPARITY's own land went green in its pre-step at 3709f571, was rebased onto
da1d14f0 (which held gates/_proven.sh at exactly 500 lines) and then went red in the full gate on a
501-line file, and every in-flight lane older than a ship failed changelog_tags the same way
(REUSEAUDIT2 dd8403da). Now a branch behind main is rebased BEFORE its pre-step, and one whose main
moved again while the pre-step ran is rebased and pre-stepped again under the lock, before the gate.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from conftest import git, git_ok, hermetic_env
from test_affected_tests import _declare
from test_land import LAND, _main, _worktree
from timing_bounds import patience


def _land(wt: Path, pre: str, seen: Path) -> subprocess.CompletedProcess[str]:
    _declare(wt, f"GOH_LAND_GATE='echo gate >> {seen}'\nGOH_LAND_PRE='{pre}'\n")
    env = hermetic_env(drop_git=True)
    env.pop("GATES_OF_HECK_SUITE_RUNNER", None)
    return subprocess.run(["bash", str(LAND)], cwd=wt, env=env, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=patience(120), check=False)  # fmt: skip


def _main_moves(repo: Path, name: str) -> str:
    (repo / name).write_text("main's\n")
    git(repo, "add", name)
    git(repo, "commit", "-q", "--no-verify", "-m", f"main adds {name}")
    return _main(repo)


def test_a_branch_behind_main_is_rebased_before_its_pre_step(repo: Path, tmp_path: Path) -> None:
    wt = _worktree(repo, tmp_path)
    moved = _main_moves(repo, "big.txt")
    seen, judged = tmp_path / "seen", tmp_path / "judged"
    r = _land(wt, f"git rev-parse HEAD >> {judged}", seen)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    landed = _main(repo)
    assert judged.read_text().split() == [landed], out  # the pre-step judged what landed
    assert git_ok(repo, "merge-base", "--is-ancestor", moved, landed), out


def test_a_red_only_the_rebased_tree_has_is_refused_before_the_gate(
    repo: Path, tmp_path: Path
) -> None:
    """The tree the lane left passes; the one main would hold (main's file and the branch) fails."""
    wt = _worktree(repo, tmp_path)
    _main_moves(repo, "big.txt")
    before = _main(repo)
    seen = tmp_path / "seen"
    r = _land(wt, "test ! -f big.txt", seen)
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "the pre-land tests failed" in out, out
    assert not seen.exists(), out  # no gate ran
    assert _main(repo) == before


def test_a_main_that_moves_during_the_pre_step_gets_a_second_one(
    repo: Path, tmp_path: Path
) -> None:
    """Another lane lands while this pre-step runs: rebased again under the lock, judged again."""
    wt = _worktree(repo, tmp_path)
    seen, judged, once = tmp_path / "seen", tmp_path / "judged", tmp_path / "once"
    commit = f"git -C {repo} add late.txt; git -C {repo} commit -q --no-verify -m late"
    move = f"[ -e {once} ] || {{ : > {once}; echo x > {repo}/late.txt; {commit}; }}"
    r = _land(wt, f"git rev-parse HEAD >> {judged}; {move}", seen)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    runs = judged.read_text().split()
    assert len(runs) == 2 and runs[1] == _main(repo) != runs[0], out
    assert seen.read_text() == "gate\n", out
