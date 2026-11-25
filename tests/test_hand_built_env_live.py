"""A test that hands a gate a HAND-BUILT environment cannot quietly judge HEAD (GOH.FSCHURN4).

gates/_from_head.sh refuses a gate run under this suite without GOH_LIVE -- keyed on
PYTEST_CURRENT_TEST and GATES_OF_HECK_SUITE, two variables. A test that builds its child's
environment from nothing (`env={"PATH": ..., "HOME": ...}`) drops both with GOH_LIVE, so the
refusal never fired: the gate exported HEAD into the test's own HOME (`head_export_per_test`,
GOH.FSCHURN3) and judged the last commit instead of the tree under test, and said nothing (the
launchagent-fix class). The suite now leaves a mark no scrub can drop: `goh-suite.live` in the
checkout's own git directory, naming the suite's pid, and a gate whose ancestor is that pid is
refused. A test that means HEAD says so with GATES_OF_HECK_HEAD_OK=1.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from _tree_clone import run_head_cache
from conftest import REPO_ROOT
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import pid_owner

TREE_READS = ("gates/",)

PROBE = (
    '. "$1/gates/_from_head.sh"; _goh_head_dir "$1/gates/structural.sh"; '
    'printf "copy=%s\\n" "$_goh_copy"'
)


def _hand_built(tmp_path: Path, **extra: str) -> dict[str, str]:
    """The shape the class has: PATH and HOME and nothing else of the suite's."""
    return {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path / "home"),
        "GOH_HEAD_CACHE": str(tmp_path / "head"),
        **extra,
    }


def _probe(env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", PROBE, "_", str(REPO_ROOT)],
        capture_output=True,
        text=True,
        env=env,
        timeout=patience(120),
        check=False,
        stdin=subprocess.DEVNULL,
    )


def test_a_hand_built_env_that_drops_goh_live_is_refused(tmp_path: Path) -> None:
    r = _probe(_hand_built(tmp_path))
    assert r.returncode == 2, f"the gate ran HEAD's export: {r.stdout}{r.stderr}"
    assert "hand-built env" in r.stderr, r.stderr
    assert not (tmp_path / "head").exists(), "HEAD was exported before the refusal"


def test_a_test_that_means_head_says_so(tmp_path: Path) -> None:
    heads = str(run_head_cache())  # the run's one export of this commit
    r = _probe(_hand_built(tmp_path, GATES_OF_HECK_HEAD_OK="1", GOH_HEAD_CACHE=heads))
    assert r.returncode == 0, r.stderr
    assert "without GOH_LIVE" not in r.stderr, r.stderr


def _ancestor(root: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    ask = '. "$1/gates/_from_head.sh"; _goh_suite_ancestor "$2"'
    return subprocess.run(
        ["bash", "-c", ask, "_", str(REPO_ROOT), str(root)],
        capture_output=True,
        text=True,
        env=env,
        timeout=patience(60),
        check=False,
        stdin=subprocess.DEVNULL,
    )


@pytest.mark.load_sensitive
def test_only_the_suites_own_descendants_are_refused(tmp_path: Path) -> None:
    """A gate a consumer runs from the same checkout while a suite runs there is no child of the
    suite, and gets the export like any other caller; a linked worktree's `.git` file is read."""
    real_gitdir = tmp_path / "gitdir"
    real_gitdir.mkdir()
    root = tmp_path / "wt"
    root.mkdir()
    (root / ".git").write_text(f"gitdir: {real_gitdir}\n")
    env = _hand_built(tmp_path)
    assert _ancestor(root, env).returncode == 1, "no mark: nothing to refuse"
    (real_gitdir / "goh-suite.live").write_text(f"{os.getpid()}\n")
    assert _ancestor(root, env).returncode == 0, "this test's process is the child's ancestor"
    stranger = subprocess.Popen(["cat"], stdin=subprocess.PIPE)  # alive until its stdin closes
    try:
        (real_gitdir / "goh-suite.live").write_text(f"{stranger.pid}\n")
        assert _ancestor(root, env).returncode == 1, "a live pid that is no ancestor"
    finally:
        stranger.stdin.close()
        stranger.wait()


def test_the_running_suite_left_its_mark() -> None:
    gitdir = REPO_ROOT / ".git"
    if gitdir.is_file():
        line = gitdir.read_text().splitlines()[0]
        gitdir = Path(line.removeprefix("gitdir: "))
        if not gitdir.is_absolute():
            gitdir = REPO_ROOT / gitdir
    mark = gitdir / "goh-suite.live"
    assert mark.is_file(), f"no {mark}: a hand-built env would run HEAD unrefused"
    # alive: the mark names this run (or an outer one), never a dead suite
    assert pid_owner.alive_line(mark.read_text()), mark.read_text()
