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
import land_records
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
    real_write = land_records.write

    def dwelling_write(qq: Path, run: str, rec: dict, request: Path | None = None) -> None:
        real_write(qq, run, rec, request)
        time.sleep(dwell_s)

    monkeypatch.setattr(land_records, "write", dwelling_write)
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
        # ONE record, its outcome in it, the request gone with it: tools/landq.py record()
        outcome = {"item": "T.S", "mode": "land", "state": "landed", "sha": "b" * 40}
        land_records.write(q, mine.stem, {**outcome, "landed": "c" * 40}, request=mine)

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
    land_records.write(q, "1000000001-T.S",
                      {"item": "T.S", "state": "red", "sha": "b" * 40, "why": "try 2"})  # fmt: skip
    marker = landq_inflight.mark(q, "T.S")
    out: list[int] = []
    t = threading.Thread(
        target=lambda: out.append(landq_look.wait(q, "T.S", patience(60), lambda: None))
    )
    t.start()
    t.join(patience(0.5))  # the old wait returned at its first look, well within this
    assert not out, "the wait returned the previous attempt's record"
    land_records.write(q, "1000000002-T.S",
                      {"item": "T.S", "state": "landed", "sha": "c" * 40})  # fmt: skip
    landq_inflight.clear(marker)
    t.join(patience(30))
    assert out == [0]


def test_a_record_filed_between_a_waits_two_looks_is_the_one_returned(tmp_path, monkeypatch):
    """GOH.WAITORDER, 2026-11-25 (the land of 573a90aa): the wait read the previous red, then the
    new attempt wrote its record and cleared its marker, then the wait saw no marker and returned
    the old red. Whatever lands between the two looks, the old record is not the answer."""
    q = tmp_path / "q"
    land_records.write(q, "1000000001-T.S", {"item": "T.S", "state": "red", "sha": "b" * 40})
    marker = landq_inflight.mark(q, "T.S")
    real, looks = landq_look.find_request, []

    def then_it_lands(*a):
        found = real(*a)
        if not looks:
            looks.append(found)
            land_records.write(q, "1000000002-T.S",
                              {"item": "T.S", "state": "landed", "sha": "c" * 40})  # fmt: skip
            landq_inflight.clear(marker)
        return found

    monkeypatch.setattr(landq_look, "find_request", then_it_lands)
    monkeypatch.setattr(landq_look, "poll", 0.01)
    assert landq_look.wait(q, "T.S", patience(30), lambda: None) == 0


def test_a_red_enqueue_between_its_filing_and_its_record_is_still_its_request(tmp_path):
    """GOH.WAITEARLYGAP: an enqueue red on its pre-step files under `early/` (its marker gone in
    that transaction), then records the red; a wait reading between the two said "no request, no
    enqueue of it running", exit 2."""
    q = tmp_path / "landq"
    filed = _req(q, "early", "00000000000000000001-T.GAP", "T.GAP")
    found = landq_look.unfiled(q, "T.GAP", float("inf"))
    assert isinstance(found, Path), found
    assert found.stem == filed.stem
    land_records.write(q, filed.stem, {"item": "T.GAP", "state": "red"}, filed)
    assert landq_look.find_request(q, "T.GAP") == land_records.ref(q, filed.stem)
