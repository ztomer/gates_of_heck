"""A land's pre-step judges the tip main will fast-forward to, rebased first (GOH.PREPARITY).

tools/land.sh ran the pre-step on the branch as the lane left it and rebased onto main only after,
under the land lock: PREPARITY's own land went green in its pre-step at 3709f571, was rebased onto
da1d14f0 (which held gates/_proven.sh at exactly 500 lines) and then went red in the full gate on a
501-line file, and every in-flight lane older than a ship failed changelog_tags the same way
(REUSEAUDIT2 dd8403da). Now a branch behind main is rebased BEFORE its pre-step, which runs once,
under the lock, on the rebased tip and beside the gate (GOH.GATEPARALLEL).
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


def test_a_red_only_the_rebased_tree_has_is_refused(repo: Path, tmp_path: Path) -> None:
    """The tree the lane left passes; the one main would hold (main's file and the branch) fails.
    The gate runs beside the pre-step (GOH.GATEPARALLEL), so whether it ran is not the claim."""
    wt = _worktree(repo, tmp_path)
    _main_moves(repo, "big.txt")
    before = _main(repo)
    seen = tmp_path / "seen"
    r = _land(wt, "test ! -f big.txt", seen)
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "the pre-land tests failed" in out, out
    assert _main(repo) == before


def test_a_main_moved_outside_land_sh_during_the_land_lands_nothing(
    repo: Path, tmp_path: Path
) -> None:
    """The pre-step runs ONCE, under the lock, on the tip main will hold (GOH.GATEPARALLEL), so no
    other land moves main under it; a main moved by hand meanwhile is named and nothing lands."""
    wt = _worktree(repo, tmp_path)
    seen, judged = tmp_path / "seen", tmp_path / "judged"
    commit = f"git -C {repo} add late.txt; git -C {repo} commit -q --no-verify -m late"
    r = _land(wt, f"git rev-parse HEAD >> {judged}; echo x > {repo}/late.txt; {commit}", seen)
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert len(judged.read_text().split()) == 1, out
    assert "while" in out and "outside land.sh" in out, out
    assert git(repo, "log", "-1", "--format=%s").strip() == "late", out


def test_the_pre_step_is_the_one_the_rebased_tree_declares(repo: Path, tmp_path: Path) -> None:
    """Main changes GOH_LAND_PRE while the lane is out: the land runs main's, not the lane's.

    land.sh read the declaration once, before rebasing, and ran it on the rebased tree: when
    GOH.COVOFFLAND dropped `--fast-only` from goh's GOH_LAND_PRE and from tools/affected_tests.py,
    the next land (GOH.CADOVERRIDE, 2026-11-19) ran the old command on the new tool, which refused
    the flag, and landed nothing.
    """
    seen, judged = tmp_path / "seen", tmp_path / "judged"
    gate = f"GOH_LAND_GATE='echo gate >> {seen}'\n"
    _declare(repo, gate + f"GOH_LAND_PRE='echo lane >> {judged}'\n")
    wt = _worktree(repo, tmp_path)
    _declare(repo, gate + f"GOH_LAND_PRE='echo main >> {judged}'\n")
    env = hermetic_env(drop_git=True)
    env.pop("GATES_OF_HECK_SUITE_RUNNER", None)
    r = subprocess.run(["bash", str(LAND)], cwd=wt, env=env, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=patience(120), check=False)  # fmt: skip
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert judged.read_text().split() == ["main"], out
