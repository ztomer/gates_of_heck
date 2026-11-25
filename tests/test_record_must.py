"""A record with no other home fails loudly (GOH.STATEDB S1, the class of GOH.SHIPNORECORD): a land
record and a ship record are goh.db rows alone, so a row that cannot be written is tried once more
and then raised, said on stderr -- never a land or a ship that ends with no record. Before, both
wrote "no goh.db row" and went on (a land's done/ file held it; a ship's record was simply lost).
"""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path

import _goh_db_fixture as gdb
import goh_db
import land_records
import pytest
import ship_records
import ship_status


@pytest.fixture(autouse=True)
def _quick(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(goh_db, "RETRY_S", 0.0, raising=False)


def _failing(monkeypatch: pytest.MonkeyPatch, fails: int) -> list[int]:
    """goh_db.transaction raises a locked database for its first FAILS opens; the opens counted."""
    real, tries = goh_db.transaction, []

    @contextlib.contextmanager
    def flaky(*a, **kw):
        tries.append(1)
        if len(tries) <= fails:
            raise sqlite3.OperationalError("database is locked")
        with real(*a, **kw) as con:
            yield con

    monkeypatch.setattr(goh_db, "transaction", flaky)
    return tries


def test_a_land_record_that_cannot_be_written_twice_is_raised(tmp_path, monkeypatch, capsys):
    gdb.use(monkeypatch, tmp_path)
    tries = _failing(monkeypatch, 2)
    with pytest.raises(sqlite3.OperationalError):
        land_records.write(tmp_path / "landq", "0001-X.Y", {"state": "landed"})
    assert len(tries) == 2, "one retry, no more"
    assert "0001-X.Y" in capsys.readouterr().err


def test_a_land_record_written_on_its_retry_is_the_row(tmp_path, monkeypatch):
    gdb.use(monkeypatch, tmp_path)
    tries = _failing(monkeypatch, 1)
    rec = {"state": "landed", "at": "2026-11-18T11:06:40Z"}
    land_records.write(tmp_path / "landq", "0001-X.Y", rec)
    assert len(tries) == 2
    assert land_records.get(tmp_path / "landq", "0001-X.Y") == rec


def test_a_ship_record_that_cannot_be_written_twice_is_raised(tmp_path, monkeypatch, capsys):
    gdb.use(monkeypatch, tmp_path)
    tries = _failing(monkeypatch, 2)
    res = {"result": str(tmp_path / "ship" / "r" / "1.json"), "status": "running", "steps": []}
    with pytest.raises(sqlite3.OperationalError):
        ship_status.save(res)
    assert len(tries) == 2, "one retry, no more"
    assert "1.json" in capsys.readouterr().err


def test_a_ship_record_written_on_its_retry_is_the_row(tmp_path, monkeypatch):
    gdb.use(monkeypatch, tmp_path)
    _failing(monkeypatch, 1)
    res = {"result": str(tmp_path / "ship" / "r" / "1.json"), "status": "running", "steps": []}
    ship_status.save(res)
    assert ship_records.get(res["result"]) == res


def test_a_newer_goh_db_refuses_a_ship_record_loudly(tmp_path, monkeypatch):
    """The ship's own record is no side record: a file this copy may not write (db_core.NewerFile)
    is raised, not skipped as goh_db.refusable() skips a pin record."""
    gdb.use(monkeypatch, tmp_path)
    real = goh_db.transaction

    @contextlib.contextmanager
    def newer(*a, **kw):
        raise goh_db.db_core.NewerFile("goh.db holds migration 9999 (by a newer goh)")
        with real(*a, **kw) as con:  # never reached: the shape of a context manager
            yield con

    monkeypatch.setattr(goh_db, "transaction", newer)
    with pytest.raises(goh_db.db_core.NewerFile):
        ship_records.put({"result": str(Path(tmp_path) / "s.json"), "status": "ok"})
