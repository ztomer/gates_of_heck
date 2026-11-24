"""`goh land --wait` reports where its request is NOW (GOH.LANDWAITSTALE, lib/landq_look.py).

GOH.REUSEOFFLANDED3, 2026-11-19: a wait started during the enqueue's pre-step said "is in its
pre-step" and nothing more while the request was queued and then gated. Each tick re-reads the
request's state dir; a line is printed only when that state changes. The test moves one request
through enqueuing -> pending -> running (stacking, then a batch's gate 1, gate 2) -> done under a
live wait, each move ONE rename or write, gated on the wait having reported the state before it:
events, not clocks. GOH.FLAKY2, 2026-11-19: the mover waited for "running" after writing gate 1,
and "running, stacking of a batch of 2" -- the state between T.S's move and the gate-1 log --
matched it, so a wait that read that gap (load ~79 on 16 cores) was handed gate 2 before it saw
gate 1. So each state is its own exact line to wait for, stacking included.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest
from conftest import REPO_ROOT
from timing_bounds import GiveUp, patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
sys.path.insert(0, str(REPO_ROOT))
import landq_inflight
import landq_look
import landq_state  # the lander's one writer: a copy that truncated tore a read (GOH.CIRED01310)


def _req(q: Path, d: str, name: str, item: str) -> Path:
    assert d != "done", "a done record carries its outcome: the lander files it whole, once"
    return landq_state.put(
        q, name, {"item": item, "repo": "/r", "sha": "a" * 40, "mode": "land"}, d
    )  # a goh.db row (GOH.LANDQDB)


@pytest.mark.load_sensitive
@pytest.mark.parametrize("dwell_s", [0.0, 0.3])
def test_wait_follows_the_request_through_every_state_once(tmp_path, monkeypatch, dwell_s):
    """`dwell_s` holds every done record the mover files for that long, so the wait reads each one:
    a done record the real lander never files (one with no outcome) turns the wait red every time,
    where unheld it did so only when a loaded box read it in a microsecond gap (GOH.FLAKYGITDIR)."""
    q = tmp_path / "landq"
    said: list[str] = []
    real_write = landq_state.write

    def dwelling_write(path: Path, data: dict, indent: int | None = 1) -> None:
        real_write(path, data, indent)
        if path.parent.name == "done":
            time.sleep(dwell_s)

    monkeypatch.setattr(landq_state, "write", dwelling_write)
    monkeypatch.setattr(landq_look, "info", said.append)
    monkeypatch.setattr(landq_look, "ok", said.append)
    monkeypatch.setattr(landq_look, "poll", 0.01)
    marker = landq_inflight.mark(q, "T.S")

    def after(text: str) -> None:
        give_up = GiveUp(30)
        while not any(text in s for s in said):
            assert not give_up.passed(), (text, said)
            time.sleep(0.01)

    def mover() -> None:
        after("pre-step")
        _req(q, "pending", "00-T.AHEAD", "T.AHEAD")
        mine = _req(q, "pending", "01-T.S", "T.S")
        landq_inflight.clear(marker)
        after("queued behind T.AHEAD")
        landq_state.move(q / "pending" / "00-T.AHEAD.json", "running")  # one state at a time
        mine = landq_state.move(mine, "running")
        after("stacking of a batch of 2")  # both running, no gate log yet: a state of its own
        (q / "logs").mkdir(parents=True)  # the queue dir: no state dir made it (GOH.LANDQDB)
        (q / "logs" / "00-T.AHEAD.gate1.log").write_text("")
        after("gate 1 of a batch of 2")
        (q / "logs" / "00-T.AHEAD.gate2.log").write_text("")
        after("gate 2 of a batch of 2")
        landq_state.drop(mine)
        (q / "done").mkdir(
            exist_ok=True
        )  # ONE record, its outcome in it, as tools/landq.py record() files it
        outcome = {"item": "T.S", "mode": "land", "state": "landed", "sha": "b" * 40}
        landq_state.write(q / "done" / mine.name, {**outcome, "landed": "c" * 40})

    t = threading.Thread(target=mover)
    t.start()
    assert landq_look.wait(q, "T.S", patience(60), lambda: None) == 0
    t.join()
    states = [s for s in said if "T.S" in s]
    assert len(states) == len(set(states)), states  # no line twice: it does not flood
    assert any("queued behind T.AHEAD" in s for s in states), states
    assert any("stacking of a batch of 2" in s for s in states), states
    assert any("gate 1 of a batch of 2" in s for s in states), states
    assert any("gate 2 of a batch of 2" in s for s in states), states
    assert "landed" in states[-1] and "cccccccc" in states[-1], states


def test_where_reads_a_request_moving_on_mid_look_once_and_never_as_its_own(tmp_path, monkeypatch):
    """GOH.FLAKY2: where() listed pending's names, then re-read pending for the items ahead; the
    lander's rename of T.AHEAD to running between the two shifted the slice onto T.S itself, and
    the wait said "queued behind T.AHEAD, T.S", then "queued behind T.AHEAD" a second time. The
    rename lands while where() reads T.AHEAD's pending record: one listing decides the order."""
    q = tmp_path / "landq"
    ahead = _req(q, "pending", "00-T.AHEAD", "T.AHEAD")
    _req(q, "pending", "01-T.S", "T.S")
    real_get = landq_state.get

    def lander_moves_it_now(path: Path) -> dict | None:
        if path == ahead and real_get(ahead) is not None:
            landq_state.move(ahead, "running")  # the lander takes it, mid-look
        return real_get(path)

    monkeypatch.setattr(landq_state, "get", lander_moves_it_now)
    assert landq_look.where(q, "01-T.S.json") == "queued behind T.AHEAD"
    assert landq_look.where(q, "01-T.S.json") == "queued behind T.AHEAD"


@pytest.mark.load_sensitive  # a thread, its join bounded by patience
def test_wait_after_a_re_enqueue_waits_for_that_attempt_not_the_last_red(tmp_path, monkeypatch):
    # GOH.WAITSTALE, 2026-11-21 (GOH.TOOLTIMEOUT try 3): the item's previous attempt is red in
    # done/ and its re-enqueue is still in its pre-step; the wait returned try 2's red at once.
    monkeypatch.setattr(landq_look, "poll", 0.01)
    q = tmp_path / "q"
    (q / "done").mkdir(parents=True)
    landq_state.write(q / "done" / "1000000001-T.S.json",
                      {"item": "T.S", "state": "red", "sha": "b" * 40, "why": "try 2"})  # fmt: skip
    marker = landq_inflight.mark(q, "T.S")
    out: list[int] = []
    t = threading.Thread(
        target=lambda: out.append(landq_look.wait(q, "T.S", patience(60), lambda: None))
    )
    t.start()
    t.join(patience(0.5))  # the old wait returned at its first look, well within this
    assert not out, "the wait returned the previous attempt's record"
    landq_state.write(q / "done" / "1000000002-T.S.json",
                      {"item": "T.S", "state": "landed", "sha": "c" * 40})  # fmt: skip
    landq_inflight.clear(marker)
    t.join(patience(30))
    assert out == [0]
