"""A goh.db is in one shape (GOH.OLDFORMS): a new file is born cut over, and one from before the
cutover is refused by name, never read as empty."""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path

import _body_fixture as bf
import db_core
import db_schema
import goh_db
import hook_ledgers
import pytest
import state_paths
import state_shape
from _contract_fixture import NUMBERS, contracts


@pytest.fixture
def db(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("GOH_DB", str(tmp_path / "goh.db"))
    return tmp_path / "goh.db"


def uncut(db: Path) -> None:
    with contextlib.closing(bf.memory(bf.UNCUT)) as con:
        db.write_bytes(con.serialize())


def schema(con: sqlite3.Connection) -> dict[str, str]:
    said = con.execute("SELECT name, sql FROM sqlite_master WHERE sql NOT NULL").fetchall()
    return {name: " ".join(sql.split()) for name, sql in said}


def test_a_new_file_is_born_as_the_cutover_leaves_one() -> None:
    con = sqlite3.connect(":memory:", isolation_level=None)
    con.executescript(db_core.BOOT)
    db_core.migrate(con, goh_db.SEAM)
    assert db_schema.version(con)["applied"] == sorted(db_schema.migrations(goh_db.SEAM))
    assert state_paths.tokens(con)
    assert [n for (n,) in con.execute("SELECT m.name FROM sqlite_master m, pragma_table_xinfo("
            "m.name) c WHERE m.type = 'table' AND c.name = 'body'")] == ["kv"]  # fmt: skip
    cut = bf.memory(bf.UNCUT)
    contracts(cut, *NUMBERS)
    db_core.migrate(cut, goh_db.SEAM)  # what came after the last contract
    assert schema(con) == schema(cut)


def test_a_file_made_meanwhile_by_an_older_goh_gets_no_contract(monkeypatch) -> None:
    con = sqlite3.connect(":memory:", isolation_level=None)
    con.executescript(db_core.BOOT)
    real = db_schema.fresh

    def raced(con, files, *a):  # the older copy's create commits first
        assert real(con, {n: p for n, p in files.items() if n <= 16}, "older", set())
        return real(con, files, *a)

    monkeypatch.setattr(db_schema, "fresh", raced)
    db_core.migrate(con, goh_db.SEAM)
    rest = sorted(set(db_schema.migrations(goh_db.SEAM)) - set(NUMBERS))
    assert db_schema.version(con)["applied"] == rest and not state_paths.tokens(con)


@pytest.mark.parametrize("door", ["connect", "ro", "known", "transaction"])
def test_every_door_refuses_an_uncut_file_by_name(db: Path, door: str) -> None:
    uncut(db)
    with pytest.raises(goh_db.Uncut, match=r"goh state cutover"), getattr(goh_db, door)():
        pass
    with goh_db.uncut(), getattr(goh_db, door)() as con:  # what the cutover itself opens
        assert con.execute("SELECT count(*) FROM lands").fetchone() == (0,)
    with pytest.raises(goh_db.Uncut), getattr(goh_db, door)():  # the leave is the block's alone
        pass


def test_a_refusal_is_no_sqlite_error_a_reader_takes_for_no_rows() -> None:
    assert not issubclass(goh_db.Uncut, (sqlite3.Error, OSError))


def test_a_cut_file_and_one_with_no_schema_are_not_refused(db: Path) -> None:
    with goh_db.transaction() as con:
        assert state_paths.tokens(con)
    with goh_db.ro() as con:
        assert con.execute("SELECT count(*) FROM lands").fetchone() == (0,)
    db.unlink()
    db.touch()
    with goh_db.ro() as con, pytest.raises(sqlite3.OperationalError, match="no such table"):
        con.execute("SELECT 1 FROM lands")


def test_a_hook_spills_and_lands_wait_until_it_is_cut(db: Path) -> None:
    uncut(db)
    row = {"event": "spawn", "lane": "l1", "time": "2026-11-18T11:06:40+00:00"}
    assert "goh state cutover" in (hook_ledgers.append(hook_ledgers.SPAWNS, row) or "")
    assert "goh state cutover" in (state_shape.paused() or "")
    db.unlink()
    assert hook_ledgers.append(hook_ledgers.SPAWNS, {**row, "lane": "l2"}) is None
    assert [r["lane"] for r in hook_ledgers.spawns()] == ["l1", "l2"]
    assert state_shape.paused() is None
