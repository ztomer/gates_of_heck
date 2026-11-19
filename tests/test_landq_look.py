"""`goh land --wait` reports where its request is NOW (GOH.LANDWAITSTALE, lib/landq_look.py).

GOH.REUSEOFFLANDED3, 2026-11-19: a wait started during the enqueue's pre-step said "is in its
pre-step" and nothing more while the request was queued and then gated. Each tick re-reads the
request's state dir; a line is printed only when that state changes. The test moves one request
through enqueuing -> pending -> running (a batch's gate) -> done under a live wait, each move
gated on the wait having reported the previous state: events, not clocks.
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
    (q / d).mkdir(parents=True, exist_ok=True)
    path = q / d / f"{name}.json"
    landq_state.write(path, {"item": item, "repo": "/r", "sha": "a" * 40, "mode": "land"})
    return path


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
        (q / "pending" / "00-T.AHEAD.json").replace(_req(q, "running", "00-T.AHEAD", "T.AHEAD"))
        mine.replace(q / "running" / mine.name)
        (q / "logs").mkdir()
        (q / "logs" / "00-T.AHEAD.gate1.log").write_text("")
        after("running")
        (q / "logs" / "00-T.AHEAD.gate2.log").write_text("")
        after("gate 2")
        (q / "running" / mine.name).unlink()
        (q / "done").mkdir()  # ONE record, its outcome in it, as tools/landq.py record() files it
        outcome = {"item": "T.S", "mode": "land", "state": "landed", "sha": "b" * 40}
        landq_state.write(q / "done" / mine.name, {**outcome, "landed": "c" * 40})

    t = threading.Thread(target=mover)
    t.start()
    assert landq_look.wait(q, "T.S", patience(60), lambda: None) == 0
    t.join()
    states = [s for s in said if "T.S" in s]
    assert len(states) == len(set(states)), states  # no line twice: it does not flood
    assert any("queued behind T.AHEAD" in s for s in states), states
    assert any("gate 1 of a batch of 2" in s for s in states), states
    assert any("gate 2 of a batch of 2" in s for s in states), states
    assert "landed" in states[-1] and "cccccccc" in states[-1], states
