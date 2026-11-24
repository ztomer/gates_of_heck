"""The hook and cache ledgers in goh.db 0005 (GOH.STATEDBS4): goh.db alone since wave 3 (the files'
import and round trip: tests/test_state_retire.py); a write goh.db does not take within its busy
timeout lets the spawn through, spilled and counted, drained in order by the next write; and no
goh.db reads as unknown, never as an empty ledger."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import hook_ledgers as hl


def _spawn(i: int, desc: str = "GOH.STATEDBS4: the hook ledgers") -> dict:
    return {"event": "spawn", "time": f"2026-11-2{i % 4}T0{i % 9}:00:00+00:00", "description": desc,
            "tool_use_id": f"toolu_{i}", "session_id": "s", "model": "opus"}  # fmt: skip


def _outcome(i: int) -> dict:
    return {"event": "outcome", "time": f"2026-11-23T0{i % 9}:30:00+00:00", "agent_id": f"a{i}",
            "status": "landed", "tool_use_id": f"toolu_{i}"}  # fmt: skip


def _lane(i: int) -> dict:
    return {"at": f"2026-11-2{i % 4}T01:00:0{i % 9}+00:00", "verdict": "room", "reading": {"n": i}}


@pytest.fixture
def home(monkeypatch, tmp_path: Path) -> Path:
    for var, sub in (("XDG_CACHE_HOME", "cache"), ("XDG_STATE_HOME", "state")):
        (tmp_path / sub / "goh").mkdir(parents=True)
        monkeypatch.setenv(var, str(tmp_path / sub))
    monkeypatch.setenv("GOH_DB", str(tmp_path / "state" / "goh" / "goh.db"))
    monkeypatch.delenv("GOH_DB_BUSY_MS", raising=False)
    return tmp_path


def _db(home: Path) -> sqlite3.Connection:
    return sqlite3.connect(home / "state" / "goh" / "goh.db")


def test_a_busy_database_lets_the_spawn_through_spilled_and_counted(
    home: Path, monkeypatch
) -> None:
    monkeypatch.setattr(hl, "HOOK_BUSY_MS", 50)
    assert hl.append(hl.SPAWNS, _spawn(1)) is None
    holder = _db(home)
    holder.execute("BEGIN IMMEDIATE")  # another writer holds goh.db past the hook's wait
    warning = hl.append(hl.SPAWNS, _outcome(1))
    assert warning is not None and "spilled, counted" in warning, warning
    assert hl.spawns() == [_spawn(1), _outcome(1)]  # the reader sees the spilled row
    holder.execute("ROLLBACK")
    assert hl.append(hl.SPAWNS, _spawn(2)) is None  # the next write drains the spill first
    assert not hl.spill_file().exists()
    assert hl.spawns() == [_spawn(1), _outcome(1), _spawn(2)]
    assert hl.busy()["count"] == 1
    assert _db(home).execute("SELECT COUNT(*) FROM agent_spawns").fetchone() == (3,)


def test_the_hook_busy_timeout_is_restored_after_the_write(home: Path, monkeypatch) -> None:
    import os

    monkeypatch.setenv("GOH_DB_BUSY_MS", "30000")
    assert hl.append(hl.LANES, _lane(1)) is None
    assert os.environ["GOH_DB_BUSY_MS"] == "30000"
    monkeypatch.delenv("GOH_DB_BUSY_MS")
    assert hl.append(hl.LANES, _lane(2)) is None
    assert os.environ.get("GOH_DB_BUSY_MS") is None


def test_no_goh_db_is_unknown_never_an_empty_ledger(home: Path) -> None:
    """Handover plan (d): with no goh.db every reader raises Unknown (a missing database never
    reads as zero spawns), and a read creates no file; the audit says so and exits 2."""
    import goh_db

    for read in (hl.spawns, hl.lanes, hl.watch, hl.candidates):
        with pytest.raises(goh_db.Unknown):
            read()
    assert hl.busy() == {"count": 0}
    assert not (home / "state" / "goh" / "goh.db").exists()  # a read never creates it
    assert hl.main(["watch"]) == 2
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import agent_audit

    assert agent_audit.main(["--offline"]) == 2


def test_the_weekly_record_and_the_newest_runs_candidates(home: Path) -> None:
    hl.record_watch({"ran_at": "r1"}, [{"id": "FAULTS.a"}, {"id": "FAULTS.b"}])
    assert hl.watch() == {"ran_at": "r1"}
    assert hl.candidates() == [{"id": "FAULTS.a"}, {"id": "FAULTS.b"}]
    hl.record_watch({"ran_at": "r2"}, None)  # no SPEND section: the candidates stand
    assert hl.watch() == {"ran_at": "r2"}
    assert hl.candidates() == [{"id": "FAULTS.a"}, {"id": "FAULTS.b"}]


def test_a_lock_order_refusal_lets_the_spawn_through_spilled(home: Path) -> None:
    """An ancestor holding goh.db's lock (GOH_HELD_LOCKS) makes the write a lock-order refusal: the
    hook fails open, the row spilled and drained by the next write, never an unjudged spawn."""
    import goh_db
    import lock_order

    def refuse() -> None:
        raise lock_order.OrderError("goh_db is held by an ancestor")

    order, goh_db._order = goh_db._order, refuse
    try:
        warning = hl.append(hl.SPAWNS, _spawn(1))
    finally:
        goh_db._order = order
    assert warning is not None and "spilled, counted" in warning, warning
    assert hl.append(hl.SPAWNS, _spawn(2)) is None
    assert hl.spawns() == [_spawn(1), _spawn(2)]
