"""The enqueue pre-step's width (GOH.PREWIDTH, lib/gate_lanes.py PRE_SLOTS, lib/land_share.py).

2026-11-21: the pre-step ran its selected set (median 2,314 test-seconds) at a median 5 workers in
867 s; at load 144 one ran at 4 workers until its 1,500 s deadline with no verdict
(enqueue-01795334788734698000-GOH.FLAKEREGISTER: two `pre` holders and two `ship` holders, 16 // 4).
Pinned here:

  * at most two `pre` runs are admitted at once, in a lane of their own: a third waits in line
    instead of starting narrow, and a commit gate no longer waits behind two pre-steps;
  * a gate that runs no suite (its suite carried, tools/pytest.sh) is counted at its REAL width --
    measured from the ledger's suite-carried land rows -- not at an equal share;
  * the land record's `pre_run` always carries workers, run_s and cpu_s (the pytest tree's CPU):
    a pre-step that reached no verdict wrote none, because its figures were read back from the
    green line only (lib/land_spd.py PRE_TOOK), so 10 of the 40 newest done records had null.
"""

from __future__ import annotations

import fcntl
import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env
from test_affected_tests import _declare
from test_gate_slot_fair import Waiter, _cleanup
from test_land import LAND, _worktree
from test_landq import (
    _land_wt,
    _pending,
    enq,
    env as env,  # noqa: PLC0414  -- a fixture, by name
)
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import gate_lanes
import land_prestep
import land_share
import landq_state
import pre_cpu
import proc_table
import worker_share

# --- at most two pre-steps ------------------------------------------------------------------------


@pytest.mark.load_sensitive
def test_a_third_pre_step_waits_in_line_and_takes_the_first_freed_place(tmp_path):
    """Four slots configured, so the old short lane had room for a third: it waits now."""
    pres = [Waiter(tmp_path, f"pre-{i}", 4, 0.05, klass="pre") for i in range(2)]
    third = None
    try:
        for p in pres:
            p.wait_for("TAKEN")
        third = Waiter(tmp_path, "pre-third", 4, 0.05, klass="pre")
        third.queued()
        assert not third.taken(), third.out()
        pres[0].finish()
        third.wait_for("TAKEN")
    finally:
        _cleanup(third, *pres)


@pytest.mark.load_sensitive
def test_a_commit_gate_is_admitted_while_two_pre_steps_run(tmp_path):
    pres = [Waiter(tmp_path, f"pre-{i}", 2, 0.05, klass="pre") for i in range(2)]
    short = None
    try:
        for p in pres:
            p.wait_for("TAKEN")
        short = Waiter(tmp_path, "commit", 2, 0.05, klass="short")
        short.wait_for("TAKEN", 10)
    finally:
        _cleanup(short, *pres)


def test_the_pre_lane_holds_two_and_never_more_than_the_configured_slots() -> None:
    assert gate_lanes.lane_of("pre") == ("pre", 0)
    assert [gate_lanes.lane_slots("pre", n) for n in (1, 2, 4, 8)] == [1, 2, 2, 2]
    assert [gate_lanes.lane_slots(lane, 4) for lane in ("long", "short")] == [4, 4]


# --- a gate that runs no suite, at its real width -------------------------------------------------


def test_two_pre_steps_split_what_a_suiteless_gate_leaves() -> None:
    """Counted at an equal share, a suite-less land gate cost each pre-step 3 workers (16 // 3)."""
    assert land_share.weighted(16, ["pre", "pre", "land"], "pre") == 5  # the old count
    assert land_share.weighted(16, ["pre", "pre"], "pre", fixed=0) == 8
    assert land_share.weighted(16, ["pre", "pre"], "pre", fixed=2) == 7
    assert land_share.weighted(16, ["pre", "pre"], "pre", fixed=3) == 6
    assert land_share.weighted(16, ["pre", "pre", "lane"], "lane", fixed=4) == 2


@pytest.mark.parametrize("fixed", [0, 1, 3, 8])
def test_the_bound_holds_with_real_widths(fixed: int) -> None:
    for cores in (4, 16):
        for land in range(4):
            for rest in range(4):
                classes = ["pre"] * land + ["lane"] * rest
                if not classes or len(classes) + fixed > cores:
                    continue
                shares = [land_share.weighted(cores, classes, c, fixed=fixed) for c in classes]
                assert min(shares) >= 1, (cores, classes, fixed, shares)
                assert sum(shares) + fixed <= cores, (cores, classes, fixed, shares)


def _hold(slots: Path, name: str, klass: str, pid: int = 999999):
    slots.mkdir(exist_ok=True)
    f = open(slots / name, "w")  # noqa: SIM115  -- held until closed
    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    f.write(f"{pid}\tprobe\t0\tkey\t{klass}\n")
    f.flush()
    return f


def _live(slots: Path) -> int:
    return worker_share.live({"GOH_GATE_CORES": "16", "GOH_GATE_SLOT_DIR": str(slots),
                              "GOH_SHARE_OWN": "0", "GOH_SHARE_CLASS": "pre"})  # fmt: skip


def test_a_declared_suiteless_gate_counts_at_its_width_in_the_live_share(tmp_path) -> None:
    slots = tmp_path / "slots"
    held = [_hold(slots, "pre-0", "pre"), _hold(slots, "pre-1", "pre"),
            _hold(slots, "slot-0", "land")]  # fmt: skip
    try:
        assert _live(slots) == 5  # three land-path runs: 16 // 3
        gate = {"GOH_GATE_SLOT": f"{slots / 'slot-0'} 999999"}
        assert land_share.declare(0, gate) is True
        assert _live(slots) == 8  # the gate waits on nothing: the two pre-steps take the cores
        assert land_share.declare(3, gate) is True
        assert _live(slots) == 6
        held[2].close()
        held[2] = _hold(slots, "slot-0", "land", pid=999998)  # a new holder: the old word is stale
        assert _live(slots) == 5
    finally:
        for f in held:
            f.close()


def test_declaring_outside_a_slot_is_a_no_op(tmp_path) -> None:
    assert land_share.declare(3, {}) is False
    assert land_share.declare(3, {"GOH_GATE_SLOT": f"{tmp_path / 'slot-0'} 999999"}) is False


def test_the_real_width_is_measured_from_the_suite_carried_land_rows() -> None:
    def row(cpu: float, wall: float, reused: str | None = "a:b") -> dict:
        return {"kind": "land", "repo": "goh", "cpu_s": cpu, "wall_s": wall, "suite_reused": reused}

    assert land_share.real_width([]) == land_share.SUITELESS_SEED
    rows = [row(836.6, 195.9), row(587.8, 145.0), row(263.1, 109.1), row(9000, 100, None)]
    assert land_share.real_width(rows) == 4  # median of 4.27, 4.05, 2.41: the run's busy cores
    assert land_share.real_width([row(10, 100)]) == 1  # never under one core


def test_the_suites_carried_exit_declares_the_gates_real_width() -> None:
    text = (REPO_ROOT / "tools" / "pytest.sh").read_text(encoding="utf-8")
    carried = text[text.index("ONE RUN PER LAND") : text.index('the suite runs"')]
    assert 'lib/land_share.py" suiteless' in carried, carried


# --- pre_run: workers, run_s and cpu_s, always ----------------------------------------------------


def _proc(pid: int, ppid: int, cpu: float, child: float | None, script: str | None = None):
    return proc_table.Proc(pid, ppid, "python3", "/usr/bin/python3", cpu, child, 0, None, script)


def test_the_pytest_trees_cpu_is_its_live_members_own_and_reaped() -> None:
    snap = {
        10: _proc(10, 1, 50.0, 7.0),  # the pre-step's bash: not pytest
        11: _proc(11, 10, 2.0, 100.0, "affected_tests.py"),  # the structural gates it reaped
        12: _proc(12, 11, 3.0, 4.0, "pytest"),  # pytest's main, a worker it already reaped
        13: _proc(13, 12, 20.0, 1.0),  # two live xdist workers
        14: _proc(14, 12, 30.0, None),
        20: _proc(20, 1, 999.0, 0.0, "pytest"),  # another tree's pytest
    }
    assert pre_cpu.tree_cpu(10, snap) == pytest.approx(3 + 4 + 20 + 1 + 30)
    assert pre_cpu.tree_cpu(99, snap) == 0.0


def test_a_reaped_run_is_counted_into_the_named_file(tmp_path) -> None:
    f = tmp_path / "cpu"
    with pre_cpu.counted({pre_cpu.ENV: str(f)}):
        subprocess.run([sys.executable, "-c", "sum(range(5 * 10**6))"], check=True,
                       stdin=subprocess.DEVNULL)  # fmt: skip
    assert (pre_cpu.read(f) or 0.0) > 0.01
    with pre_cpu.counted({}):  # no file named: nothing written anywhere
        pass


def test_the_cpu_file_accumulates_and_reads_absent_as_none(tmp_path) -> None:
    f = tmp_path / "cpu"
    assert pre_cpu.read(f) is None
    pre_cpu.add(f, 1.25)
    pre_cpu.add(f, 2.5)
    assert pre_cpu.read(f) == pytest.approx(3.75)


@pytest.mark.load_sensitive
def test_the_deadline_counts_the_pytest_run_it_kills(tmp_path) -> None:
    """A killed run has no reaper left to count it: the wrapper reads the live tree first."""
    (tmp_path / "pytest.py").write_text("sum(range(2 * 10**7))\nimport time\ntime.sleep(60)\n")
    cpu, mark = tmp_path / "cpu", tmp_path / "mark"
    seams = hermetic_env(GOH_GATE_WORKERS="16", GOH_TIMING_DIR=str(tmp_path),
                         GOH_LAND_PRE_CPU=str(cpu))  # fmt: skip
    r = subprocess.run(
        [sys.executable, str(REPO_ROOT / "lib" / "pre_deadline.py"), "run", "--mark", str(mark),
         "--floor", "2s", "--ceiling", "2s", "--", "bash", "-c", f"{sys.executable} -m pytest"],
        cwd=tmp_path, env=seams, capture_output=True, text=True, stdin=subprocess.DEVNULL,
        timeout=patience(30), check=False,
    )  # fmt: skip
    assert r.returncode == 124, r.stderr
    got = pre_cpu.read(cpu)
    assert got is not None and got > 0.05, (got, r.stderr)


def test_the_land_pre_step_writes_its_figures_on_no_verdict(repo, tmp_path) -> None:
    took = tmp_path / "took.json"
    wt = _worktree(repo, tmp_path)
    _declare(wt, "GOH_LAND_GATE=true\nGOH_LAND_PRE='sleep 30'\n")
    seams = hermetic_env(drop_git=True, GOH_LAND_PRE_MAX="2s", GOH_LAND_PRE_CEIL="2s",
                         GOH_LAND_PRE_ONLY="1", GOH_LAND_PRE_TOOK=str(took))  # fmt: skip
    seams.pop("GATES_OF_HECK_SUITE_RUNNER", None)
    r = subprocess.run(["bash", str(LAND)], cwd=wt, env=seams, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=patience(120), check=False)  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    got = json.loads(took.read_text())
    assert got["verdict"] == "none", got
    assert got["run_s"] >= 2 and got["workers"] >= 1 and got["cpu_s"] >= 0, got
    assert {"wait_s", "limit_s"} <= got.keys(), got


def test_the_enqueue_keeps_the_figures_of_a_pre_step_with_no_verdict(tmp_path) -> None:
    script = tmp_path / "land.sh"
    figures = {"wait_s": 3, "run_s": 1500, "limit_s": 1500, "workers": 4, "cpu_s": 4210.5,
               "verdict": "none"}  # fmt: skip
    script.write_text(
        f"printf '%s' '{json.dumps(figures)}' >\"$GOH_LAND_PRE_TOOK\"\n"
        "echo '→ pre-land: no verdict after 1500 s at load 68.74 -- the gate decides "
        "(in 1503 s: waited 3 s, ran 1500 s of 1500 s at 4 workers)'\n"
    )
    took: dict = {}
    land_prestep.run(tmp_path, "0" * 40, script, (), took=took)
    assert took == {k: v for k, v in figures.items() if k != "verdict"}, took


def test_an_enqueue_with_no_pre_step_records_zero_figures_and_why(repo, tmp_path, env) -> None:
    wt, _ = _land_wt(repo, tmp_path)
    env = {**env, "GOH_LAND_GATE": "true", "GOH_LAND_PRE": "exit 1"}
    done = enq(env, "--land", str(wt), "--item", "T.X", "--no-start", "--no-pre", "by hand")
    assert done.returncode == 0, done.stdout + done.stderr
    (pending,) = _pending(tmp_path)
    got = landq_state.get(pending)["pre_run"]  # a goh.db row, not a file (GOH.LANDQDB)
    assert got == {"wait_s": 0, "run_s": 0, "limit_s": 0, "workers": 0, "cpu_s": 0,
                   "skipped": "by hand"}, got  # fmt: skip


def test_the_lander_pre_step_passes_the_cpu_file_to_its_run() -> None:
    land = (REPO_ROOT / "tools" / "land.sh").read_text(encoding="utf-8")
    assert "GOH_LAND_PRE_CPU=" in land
    assert subprocess.run(["bash", "-n", str(REPO_ROOT / "tools" / "land.sh")],
                          check=False, stdin=subprocess.DEVNULL).returncode == 0  # fmt: skip


def test_the_enqueue_keeps_the_remote_shares_outcome_beside_the_figures(tmp_path) -> None:
    """GOH.REMOTEMUX: lib/remote_lane.py writes the share's outcome beside the figures
    (`$GOH_LAND_PRE_TOOK.remote`); the land record's `pre_run.remote` carries it, both removed."""
    script = tmp_path / "land.sh"
    figures = {"wait_s": 0, "run_s": 90, "limit_s": 1500, "workers": 4, "cpu_s": 300.0}
    remote = {"tests": 40, "outcome": "fallback", "cause": "mux", "fresh": True}
    script.write_text(f"printf '%s' '{json.dumps(figures)}' >\"$GOH_LAND_PRE_TOOK\"\n"
                      f"printf '%s' '{json.dumps(remote)}' >\"$GOH_LAND_PRE_TOOK.remote\"\n"
                      f"echo \"$GOH_LAND_PRE_TOOK\" > {tmp_path / 'named'}\n")  # fmt: skip
    took: dict = {}
    land_prestep.run(tmp_path, "0" * 40, script, (), took=took)
    assert took == {**figures, "remote": remote}, took
    named = (tmp_path / "named").read_text().strip()
    assert not Path(named).exists() and not Path(f"{named}.remote").exists()
