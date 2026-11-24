"""The lander's batch window (GOH.BATCHWINDOW, lib/landq_window.py), on a fake clock.

Cycle 1 of the batched cycles (2026-11-18): GOH.EARLYCONFLICT filed 32 s before GOH.E2EARMED,
both told to enqueue at once; the lander took EARLYCONFLICT alone, and E2EARMED waited a whole
second gate. Here a land is pending while another's enqueue is still in its pre-step (its marker in
`enqueuing/`): the lander waits for it, bounded, and batches the two into one gate run.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import landq_state  # lib/, after its path

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
    for d in ("done", "logs"):  # the requests are goh.db rows (GOH.LANDQDB)
        (q / d).mkdir(parents=True)
    return q


def _file(q: Path, at: float, item: str, mode: str = "land", repo: str = REPO) -> Path:
    """A request as tools/landq.py enqueue files it: named by its filing time."""
    return landq_state.put(q, f"{int(at * 1e9):020d}-{item}",
                           {"mode": mode, "repo": repo, "sha": "a" * 40, "item": item,
                            "worktree": f"/nowhere/wt-{item}"})  # fmt: skip


def _mark(q: Path, item: str, pid: int, until: float, repo: str = REPO) -> Path:
    """An enqueue's pre-step marker, as lib/landq_inflight.py mark writes it."""
    return landq_state.put(q, f"{pid}-{item}", {"pid": pid, "item": item, "repo": repo,
                                                "until": until}, "enqueuing")  # fmt: skip


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
    clock = Clock(T0)  # handed to drain_held, never patched into the time module (below)
    t0 = clock.t
    _file(q, t0, "T.ONE")
    marker = _mark(q, "T.TWO", os.getpid(), t0 + 3600)
    clock.at[t0 + 32] = lambda: (_file(q, t0 + 32, "T.TWO"), landq_state.drop(marker))
    runs: list[list[str]] = []

    def gate(_q, paths, _kit, _run_one, _size=None):
        runs.append([landq_state.request(p)["item"] for p in paths])
        return [{"state": "landed", "sha": "b" * 40, "repo": REPO, "mode": "land"}] * len(paths)

    monkeypatch.setattr(lq.landq_batch, "run", gate)
    monkeypatch.setattr(lq.landq_early, "sweep", lambda *_a: 0)
    monkeypatch.setattr(lq, "record", lambda *_a: None)
    monkeypatch.setattr(lq, "batch_kit", lambda: None)
    assert lq.drain_held(q, clock=clock.time, sleep=clock.sleep) is True
    assert runs == [["T.ONE", "T.TWO"]], runs
    assert 32 <= clock.slept < 32 + 2.5, clock.slept  # one poll past the filing, no more


def test_a_land_that_files_and_exits_between_the_two_reads_joins_the_batch(tmp_path, monkeypatch):
    """GOH.LANDQWINFLAKE: the window listed pending/, THEN read the markers. TWO filing and its
    enqueue exiting between the two reads left it in neither, so the window closed on "no enqueue
    left" with a list that lacked it, and TWO went through a second gate run: `[['T.ONE'],
    ['T.TWO']]` in a pre-step at load 116-138, where the old test's fake `time.sleep`, patched into
    the whole process, could run the filing on another thread between the reads. The markers are
    read first now; a land whose marker is gone by then is already listed."""
    monkeypatch.delenv("GOH_LANDQ_WINDOW", raising=False)
    monkeypatch.delenv("GOH_LANDQ_BATCH", raising=False)
    import landq_inflight
    import landq_window

    q, clock, said = _dirs(tmp_path), Clock(T0), []
    _file(q, clock.t, "T.ONE")
    marker = _mark(q, "T.TWO", os.getpid(), clock.t + 3600)
    real = landq_inflight.live

    def live(qq, item=None):  # TWO files and exits just as the lander reads the markers
        if landq_state.get(marker) is not None:
            _file(q, clock.t + 1, "T.TWO")
            landq_state.drop(marker)
        return real(qq, item)

    monkeypatch.setattr(landq_inflight, "live", live)
    listed = landq_state.listing(q, "pending")
    out = landq_window.hold(q, listed, said.append, clock.time, clock.sleep)
    assert [p.stem.partition("-")[2] for p in out] == ["T.ONE", "T.TWO"], out
    assert (clock.slept, said) == (0.0, [])


@pytest.fixture
def window():
    import landq_window

    return landq_window


@pytest.fixture
def q(tmp_path, monkeypatch) -> Path:
    monkeypatch.delenv("GOH_LANDQ_WINDOW", raising=False)
    monkeypatch.delenv("GOH_LANDQ_WINDOW_MAX", raising=False)
    monkeypatch.delenv("GOH_LANDQ_BATCH", raising=False)
    return _dirs(tmp_path)


def _hold(window, q: Path, clock: Clock, said: list[str]) -> list[str]:
    pending = landq_state.listing(q, "pending")
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
    landq_state.drop(two)
    monkeypatch.setenv("GOH_LANDQ_BATCH", "1")
    assert _hold(window, q, clock, said) == ["T.ONE"]
    monkeypatch.delenv("GOH_LANDQ_BATCH")
    landq_state.drop(one)
    _file(q, clock.t, "T.SHIP", mode="ship")
    assert _hold(window, q, clock, said) == ["T.SHIP"]
    assert (clock.slept, said) == (0.0, [])


def test_a_request_gone_while_the_batch_waits_ends_the_wait(window, q):
    clock, said = Clock(T0), []
    head = _file(q, clock.t, "T.ONE")
    _mark(q, "T.TWO", os.getpid(), clock.t + 3600)
    clock.at[clock.t + 4] = lambda: landq_state.drop(head)
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


# ADAPTIVE, BOUNDED (GOH.ONEVERDICTTREE3): a marker says when its pre-step started, the queue's
# done records how long its repo's pre-steps take; the window stretches to the latest one due.
def _done(q: Path, pre_s: list[float], repo: str = REPO) -> None:
    for n, s in enumerate(pre_s):
        landq_state.write(q / "done" / f"{n:020d}-T.D{n}.json", {"repo": repo, "pre_s": s})


def _started(q: Path, item: str, at: float) -> Path:
    path = _mark(q, item, os.getpid(), at + 3600)
    landq_state.update(path, {**landq_state.get(path), "at": at})
    return path


def test_a_pre_step_due_soon_after_the_base_bound_is_waited_for(window, q):
    """The 8-in-11 shape: the other land files 5 s after the fixed 120 s would have closed."""
    clock, said = Clock(T0), []
    _done(q, [300.0, 290.0, 310.0, 5000.0, 1.0])  # median 300: the bound is no average
    _file(q, clock.t, "T.ONE")
    marker = _started(q, "T.TWO", clock.t - 200)  # due at +130 (200 s in, 300 s typical, 30 slack)
    clock.at[clock.t + 125] = lambda: (_file(q, T0 + 125, "T.TWO"), landq_state.drop(marker))
    assert _hold(window, q, clock, said) == ["T.ONE", "T.TWO"]
    assert 125 <= clock.slept < 127.5, clock.slept
    assert "its pre-steps take 300s" in said[0], said


def test_the_stretch_is_bounded_to_the_marker_due_inside_it(window, q):
    clock, said = Clock(T0), []
    _done(q, [300.0])
    _file(q, clock.t, "T.ONE")
    _started(q, "T.TWO", clock.t - 200)  # due at +130, never files
    assert _hold(window, q, clock, said) == ["T.ONE"]
    assert clock.slept == pytest.approx(130)  # its due, not a minute more


def test_a_marker_due_inside_the_cap_but_past_the_base_bound_is_waited_for(window, q):
    """GOH.WINDOWDUE, the kept half: due at +250 (base 120, cap 300) it holds the lander to 250."""
    clock, said = Clock(T0), []
    _done(q, [220.0])
    _file(q, clock.t, "T.ONE")
    _started(q, "T.TWO", clock.t)  # due at 220 + 30 = +250, never files
    assert _hold(window, q, clock, said) == ["T.ONE"]
    assert clock.slept == pytest.approx(250)
    assert "T.TWO" in said[0] and "bound" in said[1], said


def test_a_marker_due_past_the_cap_is_not_waited_for(window, q):
    """GOH.WINDOWDUE: just started, due at +330 (cap 300): the old code held the base 120 s for it
    and closed on the bound with it still in its pre-step. Nothing is due inside the window, so the
    lander takes its batch at once, and says nothing (no wait began)."""
    clock, said = Clock(T0), []
    _done(q, [300.0])
    _file(q, clock.t, "T.ONE")
    _started(q, "T.THREE", clock.t)
    assert _hold(window, q, clock, said) == ["T.ONE"]
    assert (clock.slept, said) == (0.0, [])
    # one due inside and one past: it waits for the first only, and ends at ITS due
    _started(q, "T.FOUR", clock.t - 200)  # due at +130
    clock = Clock(T0)
    assert _hold(window, q, clock, said) == ["T.ONE"]
    assert clock.slept == pytest.approx(130)
    assert "T.FOUR" in said[0], said


def test_the_most_is_the_named_setting_and_never_below_the_base(window, q, monkeypatch):
    assert (window.most(), window.expected(q, REPO)) == (window.MAX_S, window.PRE_S)
    monkeypatch.setenv(window.MAX_ENV, "60")
    marker = [{"at": T0 - 50}]
    assert window.deadline(T0, marker, 100) == T0 + window.DEFAULT_S  # due +80, base 120 wins
    monkeypatch.setenv(window.MAX_ENV, "soon")
    assert window.most() == window.MAX_S


def test_a_no_pre_land_runs_alone_and_never_waits_for_a_batch(window, q):
    """GOH.NOPREBATCH: a `--no-pre` land's tree went to the lander unchecked by the pre-step, so it
    never joins a batch whose other lands its red could take down: it is taken alone, and a land
    ahead of it takes no batch past it."""
    import landq_batch

    clock, said = Clock(T0), []
    lone = _file(q, clock.t - 1, "T.NOPRE")
    landq_state.update(lone, {**landq_state.get(lone), "no_pre": "the set ran by hand"})
    _file(q, clock.t, "T.TWO")
    _mark(q, "T.THREE", os.getpid(), clock.t + 3600)
    pending = landq_state.listing(q, "pending")
    assert _hold(window, q, clock, said) == ["T.NOPRE", "T.TWO"]
    assert (clock.slept, said) == (0.0, [])  # no window held for it
    assert [p.stem.partition("-")[2] for p in landq_batch.pick(pending).paths] == ["T.NOPRE"]
    landq_state.drop(lone)
    first = _file(q, clock.t - 2, "T.ONE")
    by_hand = {**landq_state.get(first), "item": "T.NOPRE", "no_pre": "by hand"}
    landq_state.put(q, lone.stem, by_hand)
    pending = landq_state.listing(q, "pending")
    names = [p.stem.partition("-")[2] for p in pending]
    assert names == ["T.ONE", "T.NOPRE", "T.TWO"], names
    assert [p.stem.partition("-")[2] for p in landq_batch.pick(pending).paths] == ["T.ONE"]
