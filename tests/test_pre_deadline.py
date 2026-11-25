"""The land pre-step's deadline follows the worker share it was admitted with (GOH.PRESTEPSHARE).

A 368-file set took 650 s at a 5-worker share and 347 s at 12 (2026-11-17); the fixed 600 s
deadline ended the 5-worker runs with no verdict and the lander gated an unverified tip.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pre_deadline as pd
import pytest
from conftest import REPO_ROOT, git, hermetic_env
from test_affected_tests import _declare
from test_land import LAND, _main, _worktree
from timing_bounds import patience


def _row(**kw: object) -> dict:
    return {"v": 1, "subject": "goh land", "repo": "goh", "kind": "land", **kw}


def test_the_rule_meets_todays_368_file_set_at_a_5_worker_share() -> None:
    assert pd.work([], "goh") == 347 * 12  # the seed: the 368-file set at 12 workers
    at5 = pd.deadline(pd.work([], "goh"), 5, 600)
    assert at5 == 1250 and at5 > 650  # 650 s measured at 5 workers
    assert pd.deadline(pd.work([], "goh"), 12, 600) == 600  # a wide share keeps the floor
    assert pd.deadline(pd.work([], "goh"), 1, 600) == pd.CEILING_S  # never past the ceiling
    assert pd.deadline(10, 1, 600, ceiling_s=60) == 600  # a floor above the ceiling wins


def test_work_is_the_largest_recent_verdict_of_this_repo() -> None:
    rows = [_row(pre_run_s=100, pre_workers=4), _row(pre_run_s=50, pre_workers=16),
            _row(pre_s=601), {**_row(pre_run_s=999, pre_workers=16), "repo": "other"},
            _row(pre_run_s=9, pre_workers=0)]  # fmt: skip
    assert pd.work(rows, "goh") == 800
    old = [_row(pre_run_s=1000, pre_workers=16)] + [_row(pre_run_s=1, pre_workers=1)] * pd.WINDOW
    assert pd.work(old, "goh") == 1  # an old, costlier set ages out


def _land(
    tmp_path: Path, repo: Path, cores: int, pre: str = "sleep 4", run_s: int = 2
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """A pre-step of 4 s whose ledger says the set costs 2 s at 12 workers (24 worker-s): 8 s at
    a 5-worker share, 3 s at 12; GOH_LAND_PRE_MAX (the floor) is RUN_S, the whole deadline once.
    The gate runs beside it (GOH.GATEPARALLEL), admitted before or after it: the pre-step's share
    is the whole of CORES or half of it, and each test holds for both."""
    wt = _worktree(repo, tmp_path)
    timing = tmp_path / "timing"
    timing.mkdir()
    (timing / "ledger.jsonl").write_text(json.dumps(_row(pre_run_s=run_s, pre_workers=12)) + "\n")
    _declare(wt, f"GOH_LAND_GATE='echo gate >> {tmp_path / 'seen'}'\nGOH_LAND_PRE='{pre}'\n")
    env = hermetic_env(drop_git=True, GOH_LAND_PRE_MAX=f"{run_s}s", GOH_TIMING_DIR=str(timing),
                       GOH_GATE_CORES=str(cores),
                       GOH_GATE_SLOT_DIR=str(tmp_path / "slots"))  # fmt: skip
    env.pop("GATES_OF_HECK_SUITE_RUNNER", None)
    r = subprocess.run(["bash", str(LAND)], cwd=wt, env=env, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=patience(120), check=False)  # fmt: skip
    return r, wt


def test_a_5_worker_share_gets_the_time_its_set_needs_and_a_verdict(repo, tmp_path) -> None:
    r, wt = _land(tmp_path, repo, cores=5)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "pre-land green" in out, out
    assert "of 8 s at 5 workers" in out or "of 18 s at 2 workers" in out, out
    assert "no verdict" not in out, out
    assert _main(repo) == git(wt, "rev-parse", "HEAD").strip()
    rows = [json.loads(x) for x in (tmp_path / "timing" / "ledger.jsonl").read_text().splitlines()]
    assert rows[-1]["pre_workers"] in (5, 2) and rows[-1]["pre_run_s"] >= 4, rows[-1]


def test_a_wide_share_keeps_the_short_deadline(repo, tmp_path) -> None:
    r, _ = _land(tmp_path, repo, cores=12, pre="sleep 9", run_s=1)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "pre-land: no verdict after " in out, out
    assert any(f"of {pd.deadline(12, n, 1)} s at {n} workers" in out for n in (12, 6)), out


@pytest.mark.load_sensitive
def test_the_wrapper_kills_its_command_whole_at_the_deadline(tmp_path: Path) -> None:
    mark, child = tmp_path / "mark", tmp_path / "child.pid"
    env = hermetic_env(GOH_GATE_WORKERS="16", GOH_TIMING_DIR=str(tmp_path))
    r = subprocess.run(
        [sys.executable, str(REPO_ROOT / "lib" / "pre_deadline.py"), "run", "--mark", str(mark),
         "--floor", "1s", "--ceiling", "1s", "--",
         "bash", "-c", f"sleep 60 & echo $! > {child}; wait"],
        env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=patience(30),
        check=False,
    )  # fmt: skip
    assert r.returncode == pd.EXIT_DEADLINE, r.stderr
    _, workers, limit = mark.read_text().split()
    assert (workers, limit) == ("16", "1")
    pid = int(child.read_text())
    alive = subprocess.run(
        ["kill", "-0", str(pid)], stdin=subprocess.DEVNULL, capture_output=True, check=False
    ).returncode
    assert alive != 0, f"the deadline left {pid} running"
