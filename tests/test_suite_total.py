"""A whole run's total CPU seconds and processes are held under a ceiling that only falls, and no
ship goes out over it (GOH.SUITETOTAL, lib/test_work.py `total_faults`, `ship_refusal`).

The ceilings (tests/test_work_ceiling.json `whole_run`) were set over three whole runs of ~9,480
tests, idle twice and once under tools/load.py's 8 spinners: CPU 3,651.6, 3,362.1 and 3,789.4 s,
processes 32,375, 32,366 and 32,502. A planted whole session over them is red; the same rows under
them are green; a ship reads the newest recorded whole run and refuses over a ceiling.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import ship_item
import test_work
from _kv_fixture import Held
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

TREE_READS = ("tests/test_work_ceiling.json",)
CONF = test_work.config()
CPU_S, PROCS = 4360, 33150  # tests/test_work_ceiling.json whole_run: only ever lowered
NEW = {"cpu_s": 60.0, "procs": 50, "git_repos": 5, "cargo_builds": 5, "tree_walks": 5}
PLANTED = """import subprocess

def test_three(tmp_path):
    for _ in range(3):
        subprocess.run(["true"], check=True, stdin=subprocess.DEVNULL)

def test_four(tmp_path):
    for _ in range(4):
        subprocess.run(["true"], check=True, stdin=subprocess.DEVNULL)
"""


def test_a_planted_rise_in_a_whole_runs_total_is_red(tmp_path: Path) -> None:
    """Seven processes in a WHOLE planted run (no argument narrows it) against a ceiling of 6."""
    (tmp_path / "test_planted.py").write_text(PLANTED)
    conf = {"new": NEW, "named": {}, "whole_run": {"procs": 6, "cpu_s": 600.0}}
    env = hermetic_env(
        GATES_OF_HECK_TEST_WORK="judge", GATES_OF_HECK_TEST_WORK_CEILING=json.dumps(conf),
        PYTHONPATH=f"{REPO_ROOT / 'tests'}:{REPO_ROOT / 'lib'}",
    )  # fmt: skip
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist", "-p",
         "_test_work", "-W", "ignore", "--rootdir", str(tmp_path), str(tmp_path)],
        cwd=tmp_path, env=env, check=False, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, timeout=patience(120),
    )  # fmt: skip
    assert "2 passed" in r.stdout and r.returncode == 1, r.stdout[-2000:]
    assert "a whole run's procs 7, over its ceiling 6" in r.stdout, r.stdout[-2000:]
    assert "only falls" in r.stdout, r.stdout[-2000:]


def _rows(procs: int, cpu: float) -> dict:
    row = dict.fromkeys(test_work.DIMS, 0)
    return {"f.py::a": {**row, "procs": procs, "cpu_s": cpu}, test_work.SHARED: {**row, "procs": 1}}


def test_a_run_under_its_ceiling_is_green_and_far_under_says_the_lower_one() -> None:
    conf = {"whole_run": {"procs": 100}, "whole_run_headroom": {"procs": 1.05}}
    assert test_work.total_faults(_rows(99, 1.0), conf) == ([], [])  # 99 + the shared 1
    over, notes = test_work.total_faults(_rows(100, 1.0), conf)
    assert len(over) == 1 and notes == [], over
    over, notes = test_work.total_faults(_rows(50, 1.0), conf)
    lower = "a whole run's procs 51: lower whole_run procs to 54 (from 100)"
    assert over == [] and notes == [lower]


def test_a_ship_is_refused_while_the_newest_whole_run_is_over_a_ceiling(
    tmp_path, monkeypatch
) -> None:
    Held().lay(monkeypatch)
    conf = {"whole_run": {"procs": 100, "cpu_s": 50.0}}
    assert test_work.ship_refusal(conf) is None  # no whole run recorded: nothing to judge
    assert test_work.record("r1", _rows(200, 1.0), False, 1.0, 1.0)  # a partial run: no reading
    assert test_work.ship_refusal(conf) is None
    assert test_work.record("r2", _rows(200, 1.0), True, 1.0, 1.0)
    why = test_work.ship_refusal(conf)
    assert why and "procs 201 > 100" in why and "no ship" in why, why
    tree = tmp_path / "tests"
    tree.mkdir()
    (tree / "test_work_ceiling.json").write_text(json.dumps(conf))
    assert ship_item.work_refusal(tmp_path) == why  # tools/ship.py's door to it
    assert ship_item.work_refusal(tmp_path / "elsewhere") is None  # a repo with no ceilings
    assert test_work.record("r3", _rows(90, 1.0), True, 1.0, 1.0)
    assert test_work.ship_refusal(conf) is None  # the newest whole run is back under


def test_the_ceilings_stand_over_the_readings_and_only_fall() -> None:
    most = CONF["whole_run"]
    assert most["cpu_s"] <= CPU_S and most["procs"] <= PROCS, most
    assert most["cpu_s"] > 3789.4 and most["procs"] > 32502  # the loaded reading, the highest
