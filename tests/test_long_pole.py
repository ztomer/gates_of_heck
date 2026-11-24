"""No test every enqueue runs reads over 60 s, or says why (GOH.PRELONGPOLE, lib/long_pole.py).

30 s is the target `report` holds it to; 60 s is the ratchet, because the table is a last run's.

The enqueue's pre-step cannot finish before its longest test does, at any worker count: on
2026-11-21 tests/test_self_walk.py (147 s) and tests/test_land_check.py (98 s) ran at every
enqueue, so no land's pre-step took under 2.5 minutes. The standing set is what the selector
chooses for an empty diff; the judge here is the ratchet over it, calibrated on synthetic tables
(each way to go red, and the stale ids of the duration table, which prunes by file only), then run
over the real tree against the suite's own long-pole table: each test's QUIET seconds, read from
its wall, its CPU and the stretch measured as it ended, so a land never goes red because a test it
did not touch ran long under load, and one grown by waiting or by CPU still does (GOH.LONGPOLELOAD).
A machine that has measured nothing (a hosted runner) has nothing to judge: the table is local
state, written by every top-level suite run.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import _drift_guard
import _kept_store
import _schedule
import pytest
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

from lib import durations, fast_tests

sys.path.insert(0, str(REPO_ROOT / "lib"))
import long_pole as lp

SLOW = "tests/test_slow.py::test_a_walk"


def _tree(tmp_path: Path) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_t.py").write_text(
        "def test_a(): pass\n\n\ndef test_p(x): pass\n\n\n"
        "class TestK:\n    def test_m(self): pass\n"
    )
    return tmp_path


def test_a_test_over_the_limit_is_red_and_one_under_it_is_green() -> None:
    assert lp.judge({SLOW: 59.9}, {}) == []
    (red,) = lp.judge({SLOW: 60.1}, {})
    assert SLOW in red and "60.1 s over 60 s" in red and "declare its reads" in red
    assert lp.judge({SLOW: 31.0}, {}) == [], "30-60 s is the report's, not the ratchet's"


def test_an_allowance_holds_its_file_to_its_own_limit() -> None:
    allow = {"tests/test_slow.py": {"limit_s": 150, "why": "two walks"}}
    assert lp.judge({SLOW: 100.0}, allow) == []
    assert len(lp.judge({SLOW: 150.5}, allow)) == 1, "an allowance is a ceiling, not a licence"
    assert len(lp.judge({SLOW: 100.0, "tests/test_other.py::test_b": 61.0}, allow)) == 1


def test_an_allowance_for_a_file_the_set_no_longer_runs_is_red_and_a_fast_run_is_not() -> None:
    allow = {"tests/test_slow.py": {"limit_s": 150, "why": "two walks"}}
    (red,) = lp.judge({"tests/test_fast.py::test_c": 1.0}, allow)
    assert "no longer runs it: delete the entry" in red
    # The table is a last run's, under that run's load: the land gate's own run read a file at 8 s
    # that a 16-worker pre-step read at 33 s (the first land of this ratchet went red on it).
    assert lp.judge({SLOW: 12.0}, allow) == []
    assert lp.judge({SLOW: 20.0}, allow) == []


def test_an_allowance_for_a_file_the_set_runs_but_the_table_has_not_measured_is_not_stale() -> None:
    """The long-pole table fills as top-level runs merge into it: the first one after it existed
    held 34 entries, none of tests/test_self_walk.py, and its allowance read stale
    (GOH.LONGPOLELOAD)."""
    allow = {"tests/test_slow.py": {"limit_s": 150, "why": "two walks"}}
    cases = {"tests/test_fast.py::test_c": 1.0}
    assert lp.judge(cases, allow, running={"tests/test_fast.py", "tests/test_slow.py"}) == []
    (red,) = lp.judge(cases, allow, running={"tests/test_fast.py"})
    assert "no longer runs it: delete the entry" in red


def test_the_report_names_an_allowance_that_may_go() -> None:
    allow = {"tests/test_slow.py": {"limit_s": 150, "why": "w"}}
    assert lp.report({SLOW: 12.0}, allow=allow)[-1].startswith("  allowance for tests/test_slow.py")
    assert not any("allowance" in line for line in lp.report({SLOW: 20.0}, allow=allow))


def test_an_allowance_with_no_why_or_no_room_is_red() -> None:
    reds = lp.judge({SLOW: 40.0}, {"tests/test_slow.py": {"limit_s": 90}})
    assert reds == ["tests/test_slow.py: the allowance carries no why"]
    reds = lp.judge({SLOW: 40.0}, {"tests/test_slow.py": {"limit_s": 60, "why": "w"}})
    assert any("no allowance over 60 s" in r for r in reds), reds


def test_the_cases_of_a_parametrized_test_are_judged_one_by_one() -> None:
    cases = {f"tests/test_p.py::test_x[{n}]": 20.0 for n in range(4)}  # 80 s of cases, 20 s each
    assert lp.judge(cases, {}) == []


def test_measured_keeps_what_the_standing_set_runs_and_the_tree_defines(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    table = {
        "tests/test_t.py::test_a": 4.0,
        "tests/test_t.py::test_gone": 99.0,  # a function the file no longer defines
        "tests/test_t.py::test_p": 80.0,  # from before the test was parametrized
        "tests/test_t.py::test_p[1]": 5.0,
        "tests/test_t.py::test_p[2]": 6.0,
        "tests/test_t.py::TestK::test_m": 2.0,
        "tests/test_t.py::test_a@grp": 3.0,  # an xdist group's suffix is the same test
        "tests/test_other.py::test_z": 50.0,  # not in the standing set
        "tests/test_deleted.py::test_y": 50.0,  # a file that is gone
    }
    whole = lp.measured(root, ["tests/test_t.py", "tests/test_deleted.py"], table)
    assert whole == {
        "tests/test_t.py::test_a": 4.0,
        "tests/test_t.py::test_p[1]": 5.0,
        "tests/test_t.py::test_p[2]": 6.0,
        "tests/test_t.py::TestK::test_m": 2.0,
        "tests/test_t.py::test_a@grp": 3.0,
    }
    nodes = lp.measured(root, ["tests/test_t.py::test_p", "tests/test_t.py::TestK::test_m"], table)
    assert set(nodes) == {"tests/test_t.py::test_p[1]", "tests/test_t.py::test_p[2]",
                          "tests/test_t.py::TestK::test_m"}  # fmt: skip


def test_the_report_names_the_longest_test_and_the_serial_sum() -> None:
    lines = lp.report({"a.py::t": 10.0, "b.py::t": 45.0, "c.py::t": 5.0})
    assert lines[0] == "standing set: 3 measured cases, serial sum 60 s"
    assert lines[1] == "longest single test: 45.0 s  b.py::t"
    assert lines[2:] == ["  over 30 s: 45.0 s  b.py::t"]
    assert lp.report({}) == ["no measured test in the standing set"]


def test_a_test_that_waits_past_the_line_is_red_at_any_load() -> None:
    """A wait is not stretched by the run queue: 70 s of sleep reads 70 s at any load factor, so a
    test grown by waiting stays red however busy the box was (GOH.LONGPOLELOAD)."""
    for factor in (1.0, 3.0, 8.0):
        seconds = lp.pole(70.0, 0.05, factor)
        assert 69.0 < seconds <= 70.0, factor
        assert lp.judge({SLOW: seconds}, {}), factor


def test_a_cpu_bound_test_stretched_by_load_reads_its_quiet_seconds() -> None:
    """50 s of CPU on one thread at three times the cores in the run queue is 150 s of wall: the
    reading that went red on lands whose diff never touched the test. Its quiet time is 50 s, and
    one that truly burns 70 s is red under the same load."""
    assert lp.pole(150.0, 50.0, 3.0) == 50.0
    assert lp.judge({SLOW: lp.pole(150.0, 50.0, 3.0)}, {}) == []
    assert lp.judge({SLOW: lp.pole(210.0, 70.0, 3.0)}, {})
    assert lp.judge({SLOW: lp.pole(70.0, 70.0, 1.0)}, {}), "a quiet box's reading is the wall"


def test_the_quiet_estimate_is_within_the_wall_and_the_wall_over_the_factor() -> None:
    assert lp.pole(60.0, 120.0, 2.0) == 30.0  # two children of 60 s CPU each: 30 s on a quiet box
    assert lp.pole(90.0, 20.0, 3.0) == 50.0  # 20 s of CPU (60 s stretched) and 30 s of waiting
    assert lp.pole(40.0, 10.0, 1.0) == 40.0 and lp.pole(40.0, 10.0, 0.5) == 40.0
    assert lp.pole(40.0, -1.0, 2.0) == 40.0  # a clock that stepped back is no CPU


def test_a_reading_taken_under_load_is_unmeasured_and_leaves_the_entry_unchanged() -> None:
    """GOH.LONGPOLEUNMEASURED: test_self_hosted_hook read 61.9 s at load 47-72 on 16 cores, its
    quiet pole 21.8-26 s and its CPU identical on main and branch. The table is shared by every
    land, so that one loaded reading turned every land after it red. A reading taken above the
    load bound is no measurement: the entry stays what the last quiet run read."""
    assert lp.settle((25.0, 30.0), (61.9, 33.0), loaded=True) is None
    assert lp.settle((25.0, 30.0), (61.9, 30.0 * lp.LOAD_CPU_DRIFT), loaded=True) is None
    assert lp.settle(None, (61.9, 33.0), loaded=True) is None, "a new test waits for a quiet run"
    assert lp.settle((25.0, None), (61.9, 90.0), loaded=True) is None, "no CPU baseline: no figure"
    assert lp.judge({"tests/test_self_hosted_hook.py::test_s": 25.0}, {}) == []
    assert lp.settle((25.0, 30.0), (27.0, 31.0), loaded=False) == (27.0, 31.0), "quiet: measured"
    assert lp.settle(None, (70.0, 1.0), loaded=False) == (70.0, 1.0)


def test_a_real_growth_is_red_at_any_load() -> None:
    """CPU seconds grow under load on Apple silicon (+37% at 10x load, efficiency cores), never
    double: a test whose CPU doubled grew, whatever the load. Its quiet seconds grow by the CPU it
    added over the cores it spread over (calibrate-the-instrument, load-invariant figures)."""
    one_core = lp.settle((40.0, 40.0), (40.0, 80.0), loaded=True)  # wall pole moot when loaded
    assert one_core == (80.0, 80.0)
    assert lp.judge({SLOW: one_core[0]}, {}), "a 40 s CPU test grown to 80 s is red under load"
    four_cores = lp.settle((40.0, 160.0), (40.0, 320.0), loaded=True)
    assert four_cores == (80.0, 320.0) and lp.judge({SLOW: four_cores[0]}, {})
    waiting = lp.settle((25.0, 0.5), (25.0, 1.5), loaded=True)  # a waiting test's CPU tripled
    assert waiting == (26.0, 1.5) and not lp.judge({SLOW: waiting[0]}, {})
    assert lp.judge({SLOW: lp.settle((40.0, 40.0), (80.0, 80.0), loaded=False)[0]}, {})
    assert lp.GROWTH > lp.LOAD_CPU_DRIFT and lp.GROWTH < 2.0


def test_a_cold_cache_spike_is_no_growth_and_never_raises_the_entry() -> None:
    """GOH.LONGPOLECOLD, the reading of 2026-11-24 04:55:56: test_shellcheck_follows_every_source_
    in_this_tree, entry 2.6 s on 2.7 s of CPU, read 28.1 s of wall and 75.0 s of CPU at load 27/16
    with its kept parse findings missed (a cold store re-ran shellcheck over every file, spread
    over cores). `settle` scaled the 72 s of added CPU by the entry's one core and wrote 72.1 s,
    above the reading's own wall, and every land after it went red. A cold store's reading never
    raises an entry, and no reading settles above its own wall: load only ever adds to it."""
    entry, wall = (2.616, 2.722), 28.06
    assert lp.settle(entry, (14.0, 75.03), loaded=True, wall=wall, cold=True) is None
    blind = lp.settle(entry, (14.0, 75.03), loaded=True, wall=wall)  # a store the guard cannot see
    assert blind is not None and blind[0] <= wall and not lp.judge({SLOW: blind[0]}, {})
    assert lp.settle(entry, (14.07, 72.3), loaded=False, cold=True) is None, "quiet, cold: no rise"
    assert lp.settle(entry, (2.5, 2.6), loaded=False, cold=True) == (2.5, 2.6), "a cold run lowers"
    assert lp.settle(None, (14.07, 72.3), loaded=False, cold=True) == (14.07, 72.3), "first entry"


def test_a_warm_growth_still_raises_the_entry_under_load_and_cold_ones_wait() -> None:
    """The other way: a warm reading whose CPU grew raises the entry, by the CPU it added over the
    cores the reading itself spread it over, and a real growth past the line is red at any load,
    since its wall under load is at least its quiet seconds."""
    grown = lp.settle((40.0, 40.0), (40.0, 80.0), loaded=True, wall=95.0)
    assert grown == (80.0, 80.0) and lp.judge({SLOW: grown[0]}, {})
    spread = lp.settle((2.6, 2.7), (20.0, 75.0), loaded=True, wall=70.0)  # 4 cores: 72 s / 1.07
    assert spread is not None and 60.0 < spread[0] <= 70.0 and lp.judge({SLOW: spread[0]}, {})
    assert lp.settle((40.0, 40.0), (40.0, 80.0), loaded=True, wall=95.0, cold=True) is None


def test_a_loaded_reading_lowers_an_entry_above_its_own_wall() -> None:
    """The 72.1 s entry heals itself: a run of the test, at any load, that took less wall than
    the entry proves the entry too high (a quiet box is never slower), so it falls to that wall."""
    assert lp.settle((72.108, 75.03), (2.5, 2.7), loaded=True, wall=3.1) == (3.1, 2.7)
    assert lp.settle((25.0, 30.0), (20.0, 31.0), loaded=True, wall=30.0) is None, "wall above it"


def test_the_pole_table_reads_no_entry_above_its_own_runs_wall(tmp_path: Path) -> None:
    """An entry settled above the wall its own run read is no quiet time any run could have had;
    the view the ratchet judges reads it at that wall (the 72.1 s entry read 28.06 s of wall)."""
    db = tmp_path / "goh.db"
    node = "tests/test_s.py::test_w"
    durations.record(
        {"pole": {node: 2.616}, "pole_cpu": {node: 2.722}, "dur": {node: 3.064}}, db=db
    )
    durations.record({"pole": {node: 72.108}, "pole_cpu": {node: 75.03}, "dur": {node: 28.06}},
                     loaded={node}, db=db)  # fmt: skip
    assert fast_tests.load("pole", db) == {node: 28.06}
    durations.record({"dur": {node: 2.5}}, db=db)  # a later run's wall: not the entry's own run
    assert fast_tests.load("pole", db) == {node: 28.06}
    durations.record({"pole": {node: 2.5}, "pole_cpu": {node: 2.4}, "dur": {node: 2.6}}, db=db)
    assert fast_tests.load("pole", db) == {node: 2.5}, "a pole under its wall reads as written"


FIXTURE = """import time


def test_waits():
    time.sleep(0.4)


def test_burns():
    start = time.process_time()
    while time.process_time() - start < 0.4:
        pass
"""


def _child_run(
    tmp_path: Path, run_queue: float, fixture: str = FIXTURE, conftest: str = "", **env: str
) -> Path:
    """The suite's own wiring (the drift guard's reading, the schedule's table) in a child run,
    through an xdist worker, at a pinned stretch of 3 and a pinned run queue; its duration table."""
    (tmp_path / "conftest.py").write_text(
        f"import sys\nsys.path.insert(0, {str(REPO_ROOT / 'tests')!r})\n{conftest}"
        "import _schedule, _drift_guard\n"
        "from _drift_guard import pytest_runtest_makereport, pytest_runtest_setup  # noqa: F401\n"
        "_schedule.take()\n"
        "_drift_guard.POLE_PROBE_AFTER_S = 0.2\n"
        "_drift_guard.pole_stretch = lambda: 3.0\n"
        f"_drift_guard.run_queue = lambda start, stop: {run_queue!r}\n"
        "def pytest_configure(config):\n"
        "    config.pluginmanager.register(_schedule, 'goh-longest-first')\n"
    )
    (tmp_path / "test_x.py").write_text(fixture)
    table = tmp_path / "state" / "goh.db"
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-n", "1", str(tmp_path)],
        capture_output=True, text=True, cwd=tmp_path, check=False, stdin=subprocess.DEVNULL,
        env=hermetic_env(GOH_TEST_DURATIONS=str(table), **env), timeout=patience(120),
    )  # fmt: skip
    ok = r.returncode == 0 and " passed" in r.stdout and "failed" not in r.stdout
    assert ok, r.stdout + r.stderr
    return table


def test_a_run_records_each_tests_quiet_seconds_beside_its_wall(tmp_path: Path) -> None:
    """A quiet run, at a pinned stretch of 3 above one. Asserts only what no load moves
    (GOH.LONGPOLEFLAKE: a fraction of the wall such as 0.6 held only while the box's real stretch
    stayed under the pinned one; under load 1.485 <= 0.6 x 2.286 failed): no estimate exceeds its
    wall, the waiting test keeps its whole wall, and the burning one reads below its wall."""
    table = _child_run(tmp_path, run_queue=1.0)
    walls = fast_tests.load(db=table)
    poles = fast_tests.load("pole", table)
    waits, burns = "test_x.py::test_waits", "test_x.py::test_burns"
    assert set(poles) == {waits, burns}, poles
    assert poles[waits] >= 0.38 and poles[waits] >= 0.95 * walls[waits], (poles, walls)
    assert all(poles[t] <= walls[t] for t in (waits, burns)), (poles, walls)
    assert poles[burns] < walls[burns], "a burning test's quiet seconds fall below its wall"
    assert lp.judge({waits: poles[waits]}, {}, limit=0.3), "the wait is judged at its wall"
    cpus = fast_tests.load("pole_cpu", table)
    assert set(cpus) == {waits, burns} and cpus[burns] >= 0.38, cpus


def test_a_loaded_run_leaves_the_table_as_the_quiet_run_read_it_but_a_real_growth_moves_it(
    tmp_path: Path,
) -> None:
    """GOH.LONGPOLEUNMEASURED through the suite's own wiring: a run whose run queue is over the
    quiet bound writes nothing into the long-pole table for a test whose CPU did not grow, and
    raises the entry of one whose CPU did, by the CPU it added."""
    waits, burns = "test_x.py::test_waits", "test_x.py::test_burns"
    table = tmp_path / "state" / "goh.db"
    # The waiting test's entry sits under the 0.4 s it sleeps: one above a run's wall is lowered
    # to it at any load (GOH.LONGPOLECOLD), which is not what this test pins.
    durations.record({"pole": {waits: 0.35, burns: 0.3}, "pole_cpu": {waits: 0.5, burns: 0.1}},
                     db=table)  # fmt: skip
    _child_run(tmp_path, run_queue=lp.QUIET_LOAD * 4)
    poles = fast_tests.load("pole", table)
    cpus = fast_tests.load("pole_cpu", table)
    walls = fast_tests.load(db=table)
    assert poles[waits] == 0.35 and cpus[waits] == 0.5, "a loaded reading is unmeasured"
    cores = max(1.0, cpus[burns] / walls[burns])  # the reading's own spread, the sampler's CPU in
    grown = min(0.3 + (cpus[burns] - 0.1) / cores, walls[burns])  # never above the wall it read
    assert cpus[burns] >= 0.38 and poles[burns] == pytest.approx(grown, abs=1e-3)
    with sqlite3.connect(table) as con:
        loads = {r[0] for r in con.execute("SELECT load FROM readings WHERE node=?", (burns,))}
    assert loads >= {lp.QUIET_LOAD * 4}, "each reading carries the load it was taken at"


COLD = (
    FIXTURE
    + """

def test_cold():
    import sys, types  # a kept store this phase missed (tests/_kept_store.py `missed`)
    store = sys.modules.setdefault("_kept_store", types.ModuleType("_kept_store"))
    store.n = getattr(store, "n", 0) + 1
    store.missed = lambda: store.n
    start = time.process_time()
    while time.process_time() - start < 0.4:
        pass
"""
)


def test_a_loaded_run_that_missed_a_kept_store_leaves_the_entry_where_a_warm_one_raises_it(
    tmp_path: Path,
) -> None:
    """GOH.LONGPOLECOLD through the suite's own wiring: the same CPU growth under the same load
    raises the warm test's entry and leaves the cold one's, whose phase missed a kept store."""
    burns, cold = "test_x.py::test_burns", "test_x.py::test_cold"
    table = tmp_path / "state" / "goh.db"
    durations.record({"pole": {burns: 0.3, cold: 0.3}, "pole_cpu": {burns: 0.1, cold: 0.1}},
                     db=table)  # fmt: skip
    _child_run(tmp_path, run_queue=lp.QUIET_LOAD * 4, fixture=COLD)
    poles = fast_tests.load("pole", table)
    assert poles[cold] == 0.3, "a cold reading is no growth"
    assert poles[burns] > 0.3, "the warm one's growth still moves its entry"


COLD_SHELL = (
    FIXTURE
    + """

def test_cold():
    import sys  # the goh binary missed its shell verdicts: it wrote them back, as it does
    store = sys.modules["_kept_store"]  # (crates/goh/src/verdict_cache.rs put_many)
    store.put_shell(store._cache().path(), [("shell-lint", "k", "{}")])
    start = time.process_time()
    while time.process_time() - start < 0.4:
        pass
"""
)


def test_a_run_whose_binary_missed_its_shell_verdicts_is_read_cold(tmp_path: Path) -> None:
    """GOH.COLDRUSTMISS: the goh binary's shell-verdict lookups (V2, in-process in Rust) are a kept
    store a phase can miss too: its misses are the rows it writes back into the kept file, so a
    phase that refilled `shell_verdicts` reads cold and leaves its entry where a warm one moves."""
    burns, cold = "test_x.py::test_burns", "test_x.py::test_cold"
    table, kept = tmp_path / "state" / "goh.db", tmp_path / "cache" / "goh-cache.db"
    durations.record({"pole": {burns: 0.3, cold: 0.3}, "pole_cpu": {burns: 0.1, cold: 0.1}},
                     db=table)  # fmt: skip
    kept.parent.mkdir(parents=True)
    _kept_store.put_shell(kept, [])  # a planted cold table: migrated, no verdict in it
    conftest = "import _kept_store  # noqa: F401  (the suite loads it as a plugin)\n"
    _child_run(tmp_path, run_queue=lp.QUIET_LOAD * 4, fixture=COLD_SHELL, GOH_CACHE_DB=str(kept),
               conftest=conftest)  # fmt: skip
    poles = fast_tests.load("pole", table)
    assert poles[cold] == 0.3, "a phase whose binary refilled its verdicts is no growth"
    assert poles[burns] > 0.3, "the warm one's growth still moves its entry"


def test_remeasure_corrects_an_entry_through_the_suites_runner_and_says_its_reading(
    tmp_path: Path, monkeypatch
) -> None:
    """`long_pole.py --remeasure NODE` is the one way to correct an entry: the suite's own runner
    reads the test afresh and writes it with its load and CPU; the entry said before and after."""
    db, node = tmp_path / "goh.db", "tests/test_s.py::test_w"
    monkeypatch.setenv(durations.ENV, str(db))
    durations.record({"pole": {node: 72.108}, "pole_cpu": {node: 75.03}, "dur": {node: 72.5}},
                     loaded={node}, db=db)  # fmt: skip
    ran: list[list[str]] = []

    def runner(argv, **kw):
        ran.append(argv)
        durations.record({"pole": {node: 2.9}, "pole_cpu": {node: 2.7}, "dur": {node: 3.1}},
                         loaded={node}, loads={node: 1.8}, db=db)  # fmt: skip
        return subprocess.CompletedProcess(argv, 0)

    said: list[str] = []
    assert lp.remeasure(node, REPO_ROOT, run=runner, say=said.append) == 0
    assert ran == [["bash", str(REPO_ROOT / "tools" / "pytest.sh"), node]]
    assert said[0].startswith("before: entry 72.108 s") and said[1].startswith("after:  entry 2.9")
    assert "run queue 1.80/core, loaded" in said[1] and "pole_cpu 2.700 s" in said[1], said


def test_the_stretch_is_the_median_of_the_probes_samples_not_one_of_them() -> None:
    """One sample preempted for ten loops, or one that ran in a lull, must not set the stretch."""
    quiet = _drift_guard.REFERENCE_CPU_S * _drift_guard.speed_factor()
    assert _drift_guard.stretch_of([quiet * 3] * 5) == pytest.approx(3.0)
    assert _drift_guard.stretch_of(
        [quiet * 3, quiet * 3, quiet * 40, quiet * 3, quiet * 3]
    ) == pytest.approx(3.0)
    assert _drift_guard.stretch_of(
        [quiet * 0.5, quiet * 3, quiet * 3, quiet * 3, quiet * 3]
    ) == pytest.approx(3.0)
    assert _drift_guard.stretch_of([quiet * 2, quiet * 4]) == pytest.approx(3.0)
    assert _drift_guard.stretch_of([quiet * 0.2] * 3) == 1.0, "never below a quiet core"
    assert _drift_guard.POLE_PROBE_SAMPLES >= 3


def test_the_allowance_file_is_well_formed() -> None:
    allow = lp.load_allow()
    assert all(isinstance(e.get("limit_s"), int | float) for e in allow.values())
    assert list(allow) == sorted(allow)
    assert set(json.loads(lp.ALLOW.read_text())) == set(allow)


def test_no_test_every_enqueue_runs_takes_longer_than_60_s_but_the_allowed() -> None:
    """The real tree: the standing set (the selector's, on an empty diff) against the suite's own
    long-pole table. Red here is a test that every land's pre-step now waits for."""
    # The table this run read at its start (tests/_schedule.py): tools/pytest.sh hands its path to
    # the top-level run alone, and a test's own HOME holds none.
    table = _schedule._read.get(_schedule._POLE_KEY) or {}
    if not table:
        pytest.skip("no long-pole table on this machine: a suite run writes it (tools/pytest.sh)")
    reds = lp.check(table=table)
    assert reds == [], "\n".join(reds)
