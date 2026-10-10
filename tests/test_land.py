"""tools/land.sh: a branch reaches main only through a green gate, by construction.

The house workflow fast-forwards main only after `tools/gate_profile.sh .` is green on the branch
tip. Done by hand, it was `gate; merge` -- and on 2026-10-08 a `;` where `&&` belonged moved main
onto a red gate run. Engineering rule #3: gates are structural, not disciplinary. So the gate and
the merge are one command, and the merge is of the gated SHA, never of whatever the branch says
by then. GOH_LAND_GATE stands in for the gate here.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from conftest import REPO_ROOT, git, hermetic_env

LAND = REPO_ROOT / "tools" / "land.sh"


def _worktree(repo: Path, tmp_path: Path) -> Path:
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", "-b", "feature", str(wt))
    (wt / "f.txt").write_text("feature\n")
    git(wt, "add", "f.txt")
    git(wt, "commit", "-q", "--no-verify", "-m", "feature")
    return wt


def _land(wt: Path, gate: str) -> subprocess.CompletedProcess[str]:
    env = hermetic_env(drop_git=True, GOH_LAND_GATE=gate)
    return subprocess.run(["bash", str(LAND)], cwd=wt, env=env, capture_output=True, text=True,
                          timeout=60)  # fmt: skip


def _main(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").strip()


def test_a_green_gate_lands_the_gated_tip(repo: Path, tmp_path: Path) -> None:
    wt = _worktree(repo, tmp_path)
    tip = git(wt, "rev-parse", "HEAD").strip()
    r = _land(wt, "true")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _main(repo) == tip


def test_a_red_gate_lands_nothing(repo: Path, tmp_path: Path) -> None:
    wt = _worktree(repo, tmp_path)
    before = _main(repo)
    r = _land(wt, "false")
    assert r.returncode != 0 and "gate failed" in r.stderr, r.stdout + r.stderr
    assert _main(repo) == before


def test_a_tip_that_moved_under_the_gate_lands_nothing(repo: Path, tmp_path: Path) -> None:
    wt = _worktree(repo, tmp_path)
    before = _main(repo)
    moved = "echo more >> f.txt && git commit -qam more --no-verify"
    r = _land(wt, moved)
    assert r.returncode != 0 and "moved while it was gated" in r.stderr, r.stdout + r.stderr
    assert _main(repo) == before


def test_a_main_that_moved_on_is_named_not_merged(repo: Path, tmp_path: Path) -> None:
    wt = _worktree(repo, tmp_path)
    (repo / "m.txt").write_text("main moved\n")
    git(repo, "add", "m.txt")
    git(repo, "commit", "-q", "--no-verify", "-m", "main moved")
    before = _main(repo)
    r = _land(wt, "true")
    assert r.returncode != 0 and "main moved on since" in r.stderr, r.stdout + r.stderr
    assert _main(repo) == before


def test_land_refuses_to_run_in_the_main_checkout(repo: Path) -> None:
    env = hermetic_env(drop_git=True, GOH_LAND_GATE="true")
    r = subprocess.run(["bash", str(LAND)], cwd=repo, env=env, capture_output=True, text=True)
    assert r.returncode == 2 and "main checkout" in r.stderr, r.stdout + r.stderr
    assert os.path.exists(repo / ".git")


def test_a_landed_tip_is_recorded_for_the_push_to_read(repo: Path, tmp_path: Path) -> None:
    """The record GOH_LANDED_ONLY reads: the gated SHA, in the common git dir (BACKLOG 1.5)."""
    wt = _worktree(repo, tmp_path)
    tip = git(wt, "rev-parse", "HEAD").strip()
    r = _land(wt, "true")
    assert r.returncode == 0, r.stdout + r.stderr
    common = git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
    assert (Path(common) / "goh-landed").read_text().split() == [tip]
