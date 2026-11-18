"""The lander's batch window (GOH.BATCHWINDOW, lib/landq_window.py), on a fake clock.

Cycle 1 of the batched cycles (2026-11-18): GOH.EARLYCONFLICT filed 32 s before GOH.E2EARMED,
both told to enqueue at once; the lander took EARLYCONFLICT alone, and E2EARMED waited a whole
second gate. Here a land is pending while another's enqueue is still in its pre-step (its marker in
`enqueuing/`): the lander waits for it, bounded, and batches the two into one gate run.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
LANDQ = REPO_ROOT / "tools" / "landq.py"
REPO = "/nowhere/repo"
T0 = 2_000_000_000.0  # the fake clock's start: after now, so a marker's `until` past it is live


class Clock:
    """Fake time: SLEEP advances it; AT[t] runs once the clock reaches t (an enqueue filing)."""

    def __init__(self, t: float) -> None:
        self.t, self.slept, self.at = t, 0.0, {}

    def time(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        assert s > 0, s
        self.t += s
        self.slept += s
        for when in sorted(w for w in self.at if w <= self.t):
            self.at.pop(when)()


def _dirs(tmp_path: Path) -> Path:
    q = tmp_path / "landq"
    for d in ("pending", "running", "done", "logs", "enqueuing"):
        (q / d).mkdir(parents=True)
    return q


def _file(q: Path, at: float, item: str, mode: str = "land", repo: str = REPO) -> Path:
    """A request as tools/landq.py enqueue files it: named by its filing time."""
    path = q / "pending" / f"{int(at * 1e9):020d}-{item}.json"
    path.write_text(json.dumps({"mode": mode, "repo": repo, "sha": "a" * 40, "item": item,
                                "worktree": f"/nowhere/wt-{item}"}))  # fmt: skip
    return path


def _mark(q: Path, item: str, pid: int, until: float, repo: str = REPO) -> Path:
    """An enqueue's pre-step marker, as lib/landq_inflight.py mark writes it."""
    path = q / "enqueuing" / f"{pid}-{item}.json"
    path.write_text(json.dumps({"pid": pid, "item": item, "repo": repo, "until": until}))
    return path


def _dead_pid() -> int:
    """A pid no process has: past every pid_max (macOS 99998, Linux at most 2**22)."""
    import landq_inflight

    pid = 2**31 - 2
    assert not landq_inflight.alive(pid)
    return pid


def _landq():
    spec = importlib.util.spec_from_file_location("landq_window_probe", LANDQ)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_two_lands_filed_within_the_window_go_through_one_gate_run(tmp_path, monkeypatch):
    """The cycle-1 shape: ONE files, TWO's pre-step is still running and files 32 s later."""
    monkeypatch.delenv("GOH_LANDQ_WINDOW", raising=False)
    monkeypatch.delenv("GOH_LANDQ_BATCH", raising=False)
    lq, q = _landq(), _dirs(tmp_path)
    clock = Clock(T0)
    monkeypatch.setattr(time, "time", clock.time)
    monkeypatch.setattr(time, "sleep", clock.sleep)
    t0 = clock.t
    _file(q, t0, "T.ONE")
    marker = _mark(q, "T.TWO", os.getpid(), t0 + 3600)
    clock.at[t0 + 32] = lambda: (_file(q, t0 + 32, "T.TWO"), marker.unlink())
    runs: list[list[str]] = []

    def gate(_q, paths, _kit, _run_one):
        runs.append([json.loads(p.read_text())["item"] for p in paths])
        return [{"state": "landed", "sha": "b" * 40, "repo": REPO, "mode": "land"}] * len(paths)

    monkeypatch.setattr(lq.landq_batch, "run", gate)
    monkeypatch.setattr(lq.landq_early, "sweep", lambda *_a: 0)
    monkeypatch.setattr(lq, "record", lambda *_a: None)
    monkeypatch.setattr(lq, "batch_kit", lambda: None)
    assert lq.drain_held(q) is True
    assert runs == [["T.ONE", "T.TWO"]], runs
    assert 32 <= clock.slept < 32 + 2.5, clock.slept  # one poll past the filing, no more


@pytest.fixture
def window():
    import landq_window

    return landq_window


@pytest.fixture
def q(tmp_path, monkeypatch) -> Path:
    monkeypatch.delenv("GOH_LANDQ_WINDOW", raising=False)
    monkeypatch.delenv("GOH_LANDQ_BATCH", raising=False)
    return _dirs(tmp_path)


def _hold(window, q: Path, clock: Clock, said: list[str]) -> list[str]:
    pending = sorted((q / "pending").glob("*.json"))
    out = window.hold(q, pending, said.append, clock.time, clock.sleep)
    return [p.stem.partition("-")[2] for p in out]


def test_a_lone_land_with_nothing_enqueuing_does_not_wait(window, q):
    clock, said = Clock(T0), []
    _file(q, clock.t - 1, "T.ONE")
    assert _hold(window, q, clock, said) == ["T.ONE"]
    assert (clock.slept, said) == (0.0, [])


def test_its_own_marker_and_a_dead_ones_never_hold_the_lander(window, q):
    """An enqueue files, starts the lander, THEN exits: its own marker is no other land; a marker
    whose pid is dead is no enqueue at all, and it is deleted."""
    clock, said = Clock(T0), []
    _file(q, clock.t - 1, "T.ONE")
    _mark(q, "T.ONE", os.getpid(), clock.t + 3600)
    dead = _mark(q, "T.TWO", _dead_pid(), clock.t + 3600)
    assert _hold(window, q, clock, said) == ["T.ONE"]
    assert (clock.slept, said) == (0.0, [])
    assert not dead.exists()


def test_a_marker_of_another_repo_does_not_hold_the_batch(window, q):
    clock, said = Clock(T0), []
    _file(q, clock.t - 1, "T.ONE")
    _mark(q, "T.TWO", os.getpid(), clock.t + 3600, repo="/nowhere/other")
    assert _hold(window, q, clock, said) == ["T.ONE"]
    assert clock.slept == 0.0


def test_a_live_other_enqueue_that_never_files_holds_no_longer_than_the_bound(window, q):
    clock, said = Clock(T0), []
    filed = clock.t - 5
    _file(q, filed, "T.ONE")
    _mark(q, "T.TWO", os.getpid(), clock.t + 3600)
    assert _hold(window, q, clock, said) == ["T.ONE"]
    assert clock.t == pytest.approx(filed + window.DEFAULT_S)  # 120 s past ONE's filing, no more
    assert clock.slept == pytest.approx(window.DEFAULT_S - 5)
    assert len(said) == 2, said
    assert "T.TWO" in said[0] and "GOH.BATCHWINDOW" in said[0] and window.ENV in said[0]
    assert "bound" in said[1] and "T.TWO" in said[1], said[1]


def test_the_bound_is_the_named_setting(window, q, monkeypatch):
    monkeypatch.setenv("GOH_LANDQ_WINDOW", "30")
    clock, said = Clock(T0), []
    _file(q, clock.t, "T.ONE")
    _mark(q, "T.TWO", os.getpid(), clock.t + 3600)
    _hold(window, q, clock, said)
    assert clock.slept == pytest.approx(30)
    monkeypatch.setenv("GOH_LANDQ_WINDOW", "0")
    clock = Clock(clock.t)
    _hold(window, q, clock, said)
    assert clock.slept == 0.0
    monkeypatch.setenv("GOH_LANDQ_WINDOW", "soon")
    assert window.window() == window.DEFAULT_S == 120


def test_a_head_filed_longer_ago_than_the_window_does_not_wait(window, q):
    """It waited behind a running gate already: the window is past its filing, not past now."""
    clock, said = Clock(T0), []
    _file(q, clock.t - window.DEFAULT_S - 1, "T.ONE")
    _mark(q, "T.TWO", os.getpid(), clock.t + 3600)
    assert _hold(window, q, clock, said) == ["T.ONE"]
    assert clock.slept == 0.0


def test_a_full_batch_a_ship_head_and_a_one_by_one_queue_do_not_wait(window, q, monkeypatch):
    clock, said = Clock(T0), []
    monkeypatch.setenv("GOH_LANDQ_BATCH", "2")
    one, two = _file(q, clock.t, "T.ONE"), _file(q, clock.t + 1, "T.TWO")
    _mark(q, "T.THREE", os.getpid(), clock.t + 3600)
    assert _hold(window, q, clock, said) == ["T.ONE", "T.TWO"]
    two.unlink()
    monkeypatch.setenv("GOH_LANDQ_BATCH", "1")
    assert _hold(window, q, clock, said) == ["T.ONE"]
    monkeypatch.delenv("GOH_LANDQ_BATCH")
    one.unlink()
    _file(q, clock.t, "T.SHIP", mode="ship")
    assert _hold(window, q, clock, said) == ["T.SHIP"]
    assert (clock.slept, said) == (0.0, [])


def test_a_request_gone_while_the_batch_waits_ends_the_wait(window, q):
    clock, said = Clock(T0), []
    head = _file(q, clock.t, "T.ONE")
    _mark(q, "T.TWO", os.getpid(), clock.t + 3600)
    clock.at[clock.t + 4] = head.unlink
    assert _hold(window, q, clock, said) == []
    assert "left pending/" in said[-1], said
    assert clock.slept == pytest.approx(4)


def test_a_name_that_is_no_filing_time_is_read_by_its_mtime(window, tmp_path):
    odd = tmp_path / "by-hand.json"
    odd.write_text("{}")
    os.utime(odd, (T0, T0))
    assert window.filed(odd) == T0
    assert window.filed(tmp_path / "gone.json") == 0.0
    assert window.filed(tmp_path / f"{int(T0 * 1e9):020d}-T.ONE.json") == pytest.approx(T0)
