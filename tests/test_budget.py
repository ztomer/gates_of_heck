"""`goh budget` (lib/budget.py, GOH.GATEBUDGET): the hysteresis states, each kind's thresholds
at both edges, the relative guard, the ship result's rows, and the three writers' rows (a land,
a ship, a push gate), each calibrated by a plant that turns it red.

Owner 2026-11-15: "once we reach the 10m mark, going above 15m will trigger a self improvement cycle
until we are back below 10m". ARMED at <= 10 min, BREACHED above 15, CLEARED on the next <= 10.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

import _goh_db_fixture as gdb
import pytest
from conftest import REPO_ROOT, git, hermetic_env
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import budget
import reuse
import reuse_audit
import timing_ledger

MIN = 60.0
LAND = REPO_ROOT / "tools" / "land.sh"
SHIP = REPO_ROOT / "tools" / "ship.py"


def run(wall: float, ok: bool = True, **kw: object) -> dict:
    return {"wall_s": wall, "ok": ok, **kw}


def series(kind_name: str, walls: list[float]) -> str:
    """The state after WALLS under KIND_NAME, each wall judged by itself: a median5 kind's window is
    set to one run here, so the thresholds are what is tested (the median has its own test)."""
    with mock.patch.object(budget, "WINDOW", 1):
        return budget.state_of([run(w) for w in walls], budget.KINDS[kind_name])


# -- the table: every kind, both edges -------------------------------------------------------


@pytest.mark.parametrize("name", sorted(budget.KINDS))
def test_every_kind_arms_at_its_arm_edge_and_not_a_second_over(name: str) -> None:
    k = budget.KINDS[name]
    assert series(name, [k.arm_s]) == budget.ARMED
    assert series(name, [k.arm_s + 1]) == budget.UNARMED


@pytest.mark.parametrize("name", sorted(budget.KINDS))
def test_every_kind_breaches_only_above_its_breach_edge(name: str) -> None:
    k = budget.KINDS[name]
    assert series(name, [k.arm_s, k.breach_s]) == budget.ARMED  # AT the line is not over it
    assert series(name, [k.arm_s, k.breach_s + 1]) == budget.BREACHED
    assert (
        series(name, [k.arm_s, (k.arm_s + k.breach_s) / 2]) == budget.ARMED
    )  # the hysteresis band


@pytest.mark.parametrize("name", sorted(budget.KINDS))
def test_every_kind_clears_only_at_or_under_its_arm_edge_and_then_arms(name: str) -> None:
    k = budget.KINDS[name]
    breached = [k.arm_s, k.breach_s + 1]
    assert series(name, [*breached, k.arm_s + 1]) == budget.BREACHED  # back in the band: still over
    assert series(name, [*breached, k.arm_s]) == budget.CLEARED
    assert series(name, [*breached, k.arm_s, k.arm_s]) == budget.ARMED


def test_the_owners_thresholds_are_10_and_15_minutes_and_30_and_60_seconds_for_commits() -> None:
    for name in ("push", "land", "ship", "release"):
        assert (budget.KINDS[name].arm_s, budget.KINDS[name].breach_s) == (10 * MIN, 15 * MIN)
    assert (budget.KINDS["commit"].arm_s, budget.KINDS["commit"].breach_s) == (30, 60)
    assert (budget.KINDS["pre-land"].arm_s, budget.KINDS["pre-land"].breach_s) == (2 * MIN, 3 * MIN)


# -- the hysteresis ---------------------------------------------------------------------------


def test_a_breach_fixture_goes_breached_and_a_9_minute_run_clears_it() -> None:
    k = budget.KINDS["push"]
    assert budget.state_of([run(9 * MIN), run(16 * MIN)], k) == budget.BREACHED
    assert budget.state_of([run(9 * MIN), run(16 * MIN), run(9 * MIN)], k) == budget.CLEARED


def test_a_subject_that_never_reached_10_minutes_cannot_breach() -> None:
    """Nothing to defend until a run has finished inside the budget (ARMED once)."""
    assert budget.state_of([run(12 * MIN), run(20 * MIN)], budget.KINDS["ship"]) == budget.UNARMED


def test_a_fast_red_measures_nothing_and_a_slow_red_proves_a_breach() -> None:
    k = budget.KINDS["push"]
    fast_red = [run(9 * MIN), run(20, ok=False)]
    assert [r for r in fast_red if budget.counts(r, k)] == fast_red[:1]
    slow_red = [run(9 * MIN), run(41 * MIN, ok=False)]
    assert budget.state_of([r for r in slow_red if budget.counts(r, k)], k) == budget.BREACHED


def test_a_median_kind_is_not_breached_by_one_slow_wait() -> None:
    k = budget.KINDS["gate slot wait"]
    base = [run(60.0) for _ in range(5)]
    assert budget.state_of([*base, run(30 * MIN)], k) == budget.ARMED  # one wait behind a long gate
    assert budget.state_of([*base, *[run(30 * MIN)] * 3], k) == budget.BREACHED  # the median moved


# -- the relative guard: cpu-s, not the clock -----------------------------------------------


def _hist(cpu: list[float], last_wall: float = 400.0, **last: object) -> list[dict]:
    runs = [run(400.0, cpu_s=c) for c in cpu[:-1]]
    return [*runs, run(last_wall, cpu_s=cpu[-1], **last)]


def test_cpu_seconds_over_the_median_is_a_regression_on_an_unarmed_subject() -> None:
    k = budget.KINDS["ship"]
    runs = [run(12 * MIN, cpu_s=c) for c in (100, 100, 100, 100, 100, 140)]  # never <= 10 min
    assert budget.state_of(runs, k) == budget.UNARMED
    t = budget.trend(runs, k)
    assert t["verdict"] == "regressed" and t["delta"] == 0.4


def test_a_loaded_box_moves_the_wall_and_not_the_verdict() -> None:
    """Wall over BREACH with cpu-s flat is the box, not the code (528 s at load 148, 1028 at 24)."""
    t = budget.trend(_hist([100, 100, 100, 100, 105], last_wall=16 * MIN), budget.KINDS["push"])
    assert t["verdict"] == "loaded" and t["metric"] == "cpu_s"


def test_a_drifted_control_voids_the_comparison() -> None:
    k = budget.KINDS["push"]
    assert budget.trend(_hist([100, 100, 100, 200], control_drift=0.2), k)["verdict"] == "void"
    assert (
        budget.trend(_hist([100, 100, 100, 200], control_drift=0.05), k)["verdict"] == "regressed"
    )


def test_too_few_runs_before_it_is_new_not_ok() -> None:
    assert budget.trend(_hist([100, 100, 500]), budget.KINDS["push"])["verdict"] == "new"


def test_a_run_without_cpu_falls_back_to_wall() -> None:
    runs = [run(300.0) for _ in range(4)] + [run(480.0)]
    t = budget.trend(runs, budget.KINDS["land"])
    assert t["metric"] == "wall_s" and t["verdict"] == "regressed"


# -- the ship result's rows --------------------------------------------------------------------


def ship_result(
    name: str, started: str, finished: str, release_s: float, status: str = "ok"
) -> dict:
    return {
        "schema": 1,
        "name": name,
        "item": None,
        "sha": "b" * 40,
        "version": "0.1.0",
        "status": status,
        "result": f"/x/{name}/{started.replace(':', '')}-aaaa.json",
        "started": started,
        "finished": finished,
        "load1": 4.5,
        "steps": [
            {"name": "push", "status": "skipped", "why": "kit pushes"},
            {"name": "release", "code": 0, "seconds": release_s, "status": "ok"},
            {"name": "install", "code": 0, "seconds": 20.0, "status": "ok"},
        ],
    }


def test_a_ship_result_is_one_ship_row_and_one_release_row() -> None:
    res = ship_result(
        "gates_of_heck", "2026-11-17T01:13:48+00:00", "2026-11-17T01:21:59+00:00", 214.9
    )
    ship, rel = budget.ship_rows(res)
    assert (ship["subject"], ship["wall_s"], ship["ok"], ship["load1"]) == (
        "goh ship",
        491.0,
        True,
        4.5,
    )
    assert (rel["subject"], rel["wall_s"]) == ("goh release", 214.9)
    assert ship["top_steps"][0] == ["release", 214.9]
    assert budget.ship_rows({**res, "finished": ""}) == []  # still running: no row


def test_the_release_step_alone_breaches_its_own_hysteresis(monkeypatch, tmp_path: Path) -> None:
    """A ship never ARMED (its whole run is over 10 min) cannot breach, but its release step, ARMED
    by earlier runs at 9 min, breaches at 16: two subjects, two states, one result."""
    monkeypatch.setenv("GOH_TIMING_DIR", str(tmp_path / "t"))
    monkeypatch.setenv("GOH_SHIP_DIR", str(tmp_path / "ships"))
    for day, rel in ((10, 9 * MIN), (11, 9 * MIN), (12, 16 * MIN)):
        total = rel + 2.5 * MIN
        end = f"2026-11-{day}T12:{int(total // 60):02d}:00+00:00"
        budget.record_ship(ship_result("ztools", f"2026-11-{day}T12:00:00+00:00", end, rel))
    by = {j["subject"]: j for j in budget.snapshot()["subjects"]}
    assert by["ztools ship"]["state"] == budget.UNARMED
    assert by["ztools release"]["state"] == budget.BREACHED


def test_a_failed_ship_at_41_minutes_is_a_breach_and_a_fast_failure_is_not(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GOH_TIMING_DIR", str(tmp_path / "t"))
    base = dt.datetime(2026, 11, 17, tzinfo=dt.UTC)

    def ship_of(day: int, minutes: float, status: str) -> dict:
        start = base + dt.timedelta(days=day)
        res = ship_result(
            "monitor",
            start.isoformat(),
            (start + dt.timedelta(minutes=minutes)).isoformat(),
            60.0,
            status,
        )
        res["result"] = f"/x/{day}.json"
        return res

    def state() -> str:
        return next(j for j in budget.snapshot()["subjects"] if j["subject"] == "monitor ship")[
            "state"
        ]

    budget.record_ship(ship_of(0, 8, "ok"))
    assert state() == budget.ARMED
    budget.record_ship(ship_of(1, 2, "failed"))  # red in 2 min: it proves nothing about the cost
    assert state() == budget.ARMED
    budget.record_ship(ship_of(2, 41, "failed"))  # red at 41 min: it was slow
    assert state() == budget.BREACHED


def test_recording_a_finished_ship_twice_writes_it_once(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GOH_TIMING_DIR", str(tmp_path))
    res = ship_result("scripts", "2026-11-17T01:00:00+00:00", "2026-11-17T01:05:00+00:00", 100.0)
    budget.record_ship(res)
    budget.record_ship(res)
    assert len(timing_ledger.read()) == 2  # a ship row and a release row


def test_ships_that_predate_the_ledger_are_its_first_rows_and_never_doubled(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GOH_TIMING_DIR", str(tmp_path / "t"))
    monkeypatch.setenv("GOH_SHIP_DIR", str(tmp_path / "ships"))
    gdb.made(monkeypatch, tmp_path)
    d = tmp_path / "ships" / "gates_of_heck"
    res = ship_result(
        "gates_of_heck", "2026-11-17T01:13:48+00:00", "2026-11-17T01:21:59+00:00", 214.9
    )
    gdb.ship(d / "20261117T011348Z-002e818d.json", res)
    (d / "junk.json").write_text("{not json")  # files beside it are read by nothing (S2)
    (d / "20261117T000000Z-0000000a.json").write_text(json.dumps(res))
    assert budget.backfill_ships() == 2 and budget.backfill_ships() == 0
    first = timing_ledger.read()[0]
    assert first["ref"] == "20261117T011348Z-002e818d" and "load_end" not in first  # not 'now'


def test_a_repo_that_ships_with_no_row_is_listed_unmeasured_never_absent(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GOH_TIMING_DIR", str(tmp_path / "t"))
    monkeypatch.setenv("GOH_SHIP_DIR", str(tmp_path / "ships"))
    gdb.made(monkeypatch, tmp_path)
    (tmp_path / "ships" / "koffee_big").mkdir(parents=True)
    names = {u["subject"] for u in budget.snapshot()["unmeasured"]}
    assert {"koffee_big ship", "koffee_big release", "commit", "gate slot wait"} <= names
    assert "push" not in names  # a kind with a writer is not blanket-unmeasured


# -- the report --------------------------------------------------------------------------------


def _cli(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = hermetic_env(GOH_TIMING_DIR=str(tmp_path / "t"), GOH_SHIP_DIR=str(tmp_path / "ships"))
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "lib" / "budget.py"), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=patience(60),
        stdin=subprocess.DEVNULL,
        check=False,
    )


def test_goh_budget_prints_every_state_and_json_carries_the_same(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GOH_TIMING_DIR", str(tmp_path / "t"))
    for wall in (9 * MIN, 16 * MIN):
        timing_ledger.record("push", "ztools", wall, cpu_s=500.0, load1=3.0)
    timing_ledger.record("land", "monitor", 5 * MIN)
    text = _cli(tmp_path)
    assert text.returncode == 0, text.stderr
    assert (
        "✗ ztools push" in text.stdout
        and "BREACHED" in text.stdout
        and "monitor land" in text.stdout
    )
    snap = json.loads(_cli(tmp_path, "--json").stdout)
    by = {j["subject"]: j for j in snap["subjects"]}
    assert (by["ztools push"]["state"], by["monitor land"]["state"]) == ("BREACHED", "ARMED")
    assert by["ztools push"]["last_s"] == 960.0 and by["ztools push"]["load1"] == 3.0


def test_goh_budget_lists_every_reuse_path_with_its_counts(tmp_path: Path) -> None:
    """GOH.REUSEAUDIT2: what the skips stand on, per path, beside the time they save."""
    fields = dict.fromkeys(reuse.PATHS["step"].fields, "v")
    reuse_audit.use_many("step", [fields] * 5, backstop=False)
    reuse_audit.compare("step", fields, "green", "red", lambda: ("green", "green"))
    by = {r["path"]: r for r in json.loads(_cli(tmp_path, "--json").stdout)["reuse"]}
    assert set(by) == set(reuse.PATHS)
    step = by["step"]
    assert (step["uses"], step["sampled"], step["compared"], step["flakes"]) == (5, 1, 1, 1)
    text = _cli(tmp_path).stdout
    [line] = [x for x in text.splitlines() if x[2:].startswith("step ")]
    assert line.split()[1:8] == ["step", "on", "5", "1", "1", "0", "1"], line
    assert "UNSAMPLED" in next(x for x in text.splitlines() if x[2:].startswith("file "))


# -- the writers: land.sh, goh ship's result, the push gate --------------------------------------


def _worktree(repo: Path, tmp_path: Path) -> Path:
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", "-b", "feature", str(wt))
    (wt / "f.txt").write_text("feature\n")
    git(wt, "add", "f.txt")
    git(wt, "commit", "-q", "--no-verify", "-m", "feature")
    return wt


def _land(wt: Path, gate: str, ledger: Path) -> subprocess.CompletedProcess[str]:
    env = hermetic_env(drop_git=True, GOH_LAND_GATE=gate, GOH_TIMING_DIR=str(ledger))
    return subprocess.run(
        ["bash", str(LAND)],
        cwd=wt,
        env=env,
        capture_output=True,
        text=True,
        timeout=patience(120),
        stdin=subprocess.DEVNULL,
        check=False,
    )


def test_a_land_writes_one_row_keyed_by_its_repo_with_the_lock_wait_apart(
    repo: Path, tmp_path: Path
) -> None:
    wt = _worktree(repo, tmp_path)
    r = _land(wt, "sleep 1.2", tmp_path / "ledger")
    assert r.returncode == 0, r.stdout + r.stderr
    (row,) = timing_ledger.read(tmp_path / "ledger" / "ledger.jsonl")
    assert row["subject"] == f"{repo.name} land" and row["ok"] is True
    assert row["wall_s"] >= 1.2 and "wait_lock_s" in row and row["exit"] == 0 and "cpu_s" in row, (
        row
    )


def test_a_red_land_is_a_row_too_with_ok_false(repo: Path, tmp_path: Path) -> None:
    wt = _worktree(repo, tmp_path)
    r = _land(wt, "false", tmp_path / "ledger")
    assert r.returncode == 1
    (row,) = timing_ledger.read(tmp_path / "ledger" / "ledger.jsonl")
    assert row["ok"] is False and row["exit"] == 1


def test_a_land_that_never_took_the_lock_measured_no_gate_and_writes_no_row(
    repo: Path, tmp_path: Path
) -> None:
    r = _land(repo, "true", tmp_path / "ledger")  # run from the main checkout: refused at once
    assert r.returncode == 2
    assert not (tmp_path / "ledger" / "ledger.jsonl").exists()


def test_the_push_gate_writes_one_row_per_full_gate_and_none_under_a_land(tmp_path: Path) -> None:
    from test_push_gate import PUSH_GATE, _repo

    repo = _repo(tmp_path)
    sha = git(repo, "rev-parse", "HEAD").strip()

    def push(**extra: str) -> subprocess.CompletedProcess[str]:
        env = hermetic_env(
            GATE_REPORT=str(tmp_path / "report.txt"),
            GOH_PUSH_LOGS=str(tmp_path / "logs"),
            GOH_PUSH_WORKTREES=str(tmp_path / "exports"),
            GOH_TIMING_DIR=str(tmp_path / "ledger"),
            **extra,
        )
        return subprocess.run(
            ["bash", str(PUSH_GATE), "origin"],
            cwd=repo,
            env=env,
            text=True,
            input=f"refs/heads/main {sha} refs/heads/main {'0' * 40}\n",
            capture_output=True,
            timeout=patience(120),
            check=False,
        )

    assert push(GOH_TIMING_VIA="land").returncode == 0
    assert not (tmp_path / "ledger" / "ledger.jsonl").exists()  # the land's own row holds this gate
    r = push()
    assert r.returncode == 0, r.stdout + r.stderr
    (row,) = timing_ledger.read(tmp_path / "ledger" / "ledger.jsonl")
    assert (row["subject"], row["kind"], row["ok"], row["sha"]) == (
        "repo push",
        "push",
        True,
        sha[:12],
    )
    assert row["wall_s"] >= 0 and "cpu_s" in row


def test_the_ship_pipeline_ends_by_writing_its_rows(monkeypatch, tmp_path: Path) -> None:
    """tools/ship.py's `ledger` is the last act of run_steps on both exits, and never raises."""
    monkeypatch.setenv("GOH_TIMING_DIR", str(tmp_path))
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    sys.path.insert(0, str(REPO_ROOT / "lib"))
    import ship_status

    res = ship_result("routines", "2026-11-17T01:00:00+00:00", "2026-11-17T01:13:30+00:00", 500.0)
    ship_status.record_ship_timing(res)
    assert {r["subject"] for r in timing_ledger.read()} == {"routines ship", "routines release"}
    monkeypatch.setenv("GOH_TIMING_DIR", str(tmp_path / "x" / "y"))
    (tmp_path / "x").write_text("a file, so the ledger directory cannot be made")
    ship_status.record_ship_timing(
        {**res, "result": "/x/other.json"}
    )  # a ledger fault is said, not raised


def test_ship_py_calls_the_ledger_on_both_exits() -> None:
    src = SHIP.read_text(encoding="utf-8")
    body = src[src.index("def run_steps") : src.index("\ndef ", src.index("def run_steps") + 10)]
    assert body.count("ship_status.record_ship_timing(res)") == 2 and 'res["finished"]' in body
