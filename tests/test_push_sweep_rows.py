"""lib/push_sweep_rows.py (GOH.PINSWEEP, goh.db 0022): a push sweep's record, in memory."""

from __future__ import annotations

import goh_db
import push_sweep_rows as rows
import pytest
from _kv_fixture import Held, memory

A, B, C = (f"/s/{n}.sweep.json" for n in "abc")


@pytest.fixture
def held(monkeypatch) -> Held:
    return Held().lay(monkeypatch)


def sweep(created: float, *repos: str, **more) -> dict:
    return {"run": "r", "status": "pending", "created": created, "quiet": 1.0, "every": 2.0,
            "budget": 9.0, "repos": {r: {"repo": f"/p/{r}", "status": "pending"} for r in repos},
            **more}  # fmt: skip


def status(path: str) -> dict[str, str]:
    return {w: r["status"] for w, r in rows.read(path)["repos"].items()}


def test_a_record_is_read_back_as_it_was_put(held) -> None:
    rec = sweep(5.0, "x", ship="/s/a.json", ship_worker="7 t", log="/s/a.log")
    rec["repos"]["x"] |= {"status": "refused", "said": "no", "log": "/s/x.log", "tip": "t1",
                          "seconds": 4.0, "slot_wait_s": 3.0, "at": 6.0,
                          "steps": {"cargo test": "c1", "b": None}}  # fmt: skip
    rows.put(A, rec)
    assert rows.read(A) == {"path": A, **rec}
    rows.put(A, sweep(7.0, "y"))  # the name again: its rows go first
    assert rows.read(A) == {"path": A, **sweep(7.0, "y")}
    assert held.goh.execute("SELECT count(*) FROM push_sweep_steps").fetchone() == (0,)
    with pytest.raises(ValueError, match="no such column: nope"):
        rows.update(A, nope=1)


def test_no_database_and_one_from_before_the_table_hold_no_sweep(monkeypatch) -> None:
    monkeypatch.setattr(goh_db, "ro", lambda *_: (_ for _ in ()).throw(OSError("none")))
    assert (rows.read(A), rows.live(), rows.anchor(A), rows.verdict("/p/x")) == ({}, [], None, None)
    old = Held.__new__(Held)
    old.goh, old.cache, old.down = memory(goh_db, 21), None, False
    monkeypatch.setattr(goh_db, "ro", old._ro(goh_db, lambda: old.goh))
    assert rows.read(A) == {} and rows.untaken(A) == []


def test_a_newer_sweep_takes_every_repo_it_lists(held) -> None:
    rows.put(A, sweep(1.0, "x", "y"))
    rows.put(B, sweep(2.0, "x"))
    assert rows.untaken(A) == ["y"] and rows.untaken(B) == ["x"]
    assert rows.begin(A, "r", 3.0) == ("go", "")
    assert status(A) == {"x": "superseded", "y": "pending"}
    assert B in rows.read(A)["repos"]["x"]["said"]
    rows.put(C, sweep(3.0, "x"))
    assert rows.begin(B, "r", 4.0) == ("superseded", C)  # nothing left: ended, never run
    assert rows.read(B)["status"] == "done" and rows.read(B).get("started") is None


def test_a_sweep_that_was_lost_takes_nothing(held) -> None:
    rows.put(A, sweep(1.0, "x"))
    rows.put(B, sweep(2.0, "x"))
    assert rows.fail(B, "r", "its supervisor died", 3.0) == [("x", "/p/x")]
    assert rows.untaken(A) == ["x"] and status(B) == {"x": "committed"}
    assert rows.fail(B, "r", "again", 4.0) == []  # ended once


def test_one_sweep_runs_at_a_time_and_only_its_own_start_begins_it(held) -> None:
    rows.put(A, sweep(1.0, "x"))
    rows.put(B, sweep(2.0, "y"))
    assert rows.begin(A, "another start", 3.0) == ("gone", "")
    assert rows.begin(A, "r", 3.0) == ("go", "")
    assert rows.begin(A, "r", 3.5) == ("gone", "")  # begun already
    assert rows.begin(B, "r", 4.0) == ("busy", A) and rows.read(B)["status"] == "pending"
    rows.fail(A, "r", "", 5.0, "done", 0)
    assert rows.begin(B, "r", 6.0) == ("go", "")


def test_the_anchor_is_the_last_sweep_that_ran_else_the_oldest(held) -> None:
    rows.put(A, sweep(1.0, "x"))
    assert rows.anchor(A) is None
    rows.put(B, sweep(2.0, "x"))
    rows.put(C, sweep(3.0, "x"))
    assert rows.anchor(C) == 1.0  # none ran: the oldest
    rows.update(B, started=2.5)
    assert rows.anchor(C) == 2.5 and rows.anchor(A) is None  # never a newer one's


def test_the_verdict_is_the_newest_a_gate_gave(held) -> None:
    for path, at, what in ((A, 1.0, "refused"), (B, 2.0, "held"), (C, 3.0, "committed")):
        rows.put(path, sweep(at, "x"))
        rows.outcome(path, "x", {"cargo test": "c"} if what == "refused" else None, status=what,
                     tip="t" if what == "refused" else None, at=at)  # fmt: skip
    got = rows.verdict("/p/x")
    assert (got["status"], got["tip"], got["steps"]) == ("refused", "t", {"cargo test": "c"})
    rows.outcome(C, "x", status="pushed", at=4.0)
    assert rows.verdict("/p/x")["status"] == "pushed" and rows.verdict("/p/z") is None
