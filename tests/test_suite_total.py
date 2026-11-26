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


SLOW = {"norm_runs": 5, "min_runs": 2, "factor": 2.0, "min_s": 0.05}
SLOW_PLANT = {
    "test_slow_work.py": """import hashlib, subprocess

def test_slow_and_busier():
    hashlib.pbkdf2_hmac("sha256", b"x", b"y", 2_000_000)
    for _ in range(4):
        subprocess.run(["true"], check=True, stdin=subprocess.DEVNULL)
""",
    "test_slow_box.py": """import hashlib

def test_slow_alone():
    hashlib.pbkdf2_hmac("sha256", b"x", b"y", 2_000_000)
""",
    "test_steady.py": "def test_quick():\n    pass\n",
}
NORM = {**dict.fromkeys(test_work.DIMS, 0.0), "cpu_s": 0.01, "procs": 1.0}


def test_the_land_names_a_file_over_its_norm_and_blocks_only_when_its_counts_grew(
    tmp_path: Path,
) -> None:
    """Three planted files against a norm of 0.01 CPU s and 1 process a test: the slow one whose
    processes grew blocks, the slow one alone warns, the steady one says nothing."""
    for name, body in SLOW_PLANT.items():
        (tmp_path / name).write_text(body)
    conf = {"new": NEW, "named": {}, "slow": SLOW}
    nodes = ("test_slow_work.py::test_slow_and_busier", "test_slow_box.py::test_slow_alone",
             "test_steady.py::test_quick")  # fmt: skip
    norms = dict.fromkeys(nodes, NORM)
    env = hermetic_env(
        GATES_OF_HECK_TEST_WORK="judge", GATES_OF_HECK_TEST_WORK_CEILING=json.dumps(conf),
        GATES_OF_HECK_TEST_WORK_NORMS=json.dumps(norms),
        PYTHONPATH=f"{REPO_ROOT / 'tests'}:{REPO_ROOT / 'lib'}",
    )  # fmt: skip
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist", "-p",
         "_test_work", "-W", "ignore", "--rootdir", str(tmp_path), *SLOW_PLANT],
        cwd=tmp_path, env=env, check=False, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, timeout=patience(120),
    )  # fmt: skip
    out = r.stdout
    assert "3 passed" in out and r.returncode == 1, out[-2000:]
    block = [ln for ln in out.splitlines() if ln.startswith("✗ test work")]
    warn = [ln for ln in out.splitlines() if ln.startswith("⚠ test work (GOH.SUITETOTAL)")]
    assert len(block) == 1 and "test_slow_work.py read" in block[0], out[-2000:]
    assert "its work grew: procs 4.0 a test (norm 1.0)" in block[0], block
    assert len(warn) == 1 and "test_slow_box.py read" in warn[0], out[-2000:]
    assert "counts did not grow" in warn[0] and "test_steady.py" not in out, out[-2000:]


def test_a_tests_norm_is_the_median_of_its_own_readings_in_other_changes_runs(monkeypatch) -> None:
    Held().lay(monkeypatch)
    conf = {"slow": {**SLOW, "norm_runs": 3}}
    row = dict.fromkeys(test_work.DIMS, 0)
    for i, (cpu, head) in enumerate([(9.0, "a"), (1.0, "b"), (2.0, None), (3.0, "c"), (5.0, "d")]):
        rows = {"f.py::t": {**row, "cpu_s": cpu}, "f.py::u": {**row, "cpu_s": cpu}}
        assert test_work.record(f"r{i}", rows, False, 1.0, 1.0, head=head)
    norms = test_work.file_norms(conf)  # the newest 3 of a commit: 5, 3, 1 -- not 2 (no head)
    assert norms["f.py::t"]["cpu_s"] == 3.0, norms
    assert test_work.file_norms(conf, exclude="d")["f.py::t"]["cpu_s"] == 3.0  # c, b, a: 3
    assert test_work.file_norms({"slow": {**SLOW, "min_runs": 9}}) == {}  # too few readings
    assert test_work.file_norms({}) == {}  # no window configured: no norm


def test_a_stage_that_ran_one_cheap_test_of_a_file_makes_no_norm_for_the_rest(monkeypatch) -> None:
    """The red of 2026-11-26: earlier stages of the same land ran 1 of a file's 12 tests (0.001 s);
    by file that was a norm of 0.00 for all 12. By node the others have none, and a zero norm,
    where it is a test's own, is named and never judged."""
    Held().lay(monkeypatch)
    row = dict.fromkeys(test_work.DIMS, 0)
    for i in range(2):
        assert test_work.record(f"p{i}", {"f.py::cheap": {**row, "cpu_s": 0.0}}, False, 1.0, 1.0,
                                head=f"earlier{i}")  # fmt: skip
    conf = {"slow": {**SLOW, "min_runs": 2}}
    norms = test_work.file_norms(conf)
    assert set(norms) == {"f.py::cheap"}, norms
    now = {f"f.py::t{i}": {**row, "cpu_s": 0.5, "procs": 1} for i in range(12)}
    assert test_work.slow_files(now, conf, norms) == ([], [])  # the 12 have no norm: nothing judged
    blocks, warns = test_work.slow_files({**now, "f.py::cheap": {**row, "cpu_s": 0.5, "procs": 1}},
                                         {"slow": {**SLOW, "min_s": 0.1}}, norms)  # fmt: skip
    assert blocks == [] and len(warns) == 1 and "a norm of no CPU" in warns[0], (blocks, warns)
