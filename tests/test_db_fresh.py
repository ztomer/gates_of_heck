"""lib/db_schema.py `fresh` (GOH.BODYCOLS): an empty database gets every schema file in one
transaction, its views and triggers last, and is the file the files one by one make."""

from __future__ import annotations

import sqlite3

import db_core
import db_schema
import goh_cache_db
import goh_db
import pytest


def empty() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:", isolation_level=None)
    con.executescript(db_core.BOOT)
    return con


def shape(con: sqlite3.Connection) -> set[tuple]:
    rows = con.execute("SELECT type, name, tbl_name, sql FROM sqlite_master").fetchall()
    return {tuple(r) for r in rows if not str(r[1]).startswith("sqlite_")}


def said(con: sqlite3.Connection) -> list[tuple]:
    return con.execute("SELECT version, name, writer FROM schema_version ORDER BY 1").fetchall()


@pytest.mark.parametrize("seam", [goh_db.SEAM, goh_cache_db.SEAM], ids=["goh", "cache"])
def test_it_is_the_file_one_file_after_another_makes(seam, monkeypatch) -> None:
    files = db_schema.migrations(seam)
    files = {n: files[n] for n in db_schema.on_open(files, set())}
    one, all_at_once, real = empty(), empty(), db_schema.apply
    for n, p in sorted(files.items()):
        real(one, n, p, "w", n == max(files))
    seen: list[int] = []

    def apply(con, n, p, *a) -> None:
        seen.append(n)
        real(con, n, p, *a)
        kinds = {r[0] for r in con.execute("SELECT type FROM sqlite_master")}
        assert not kinds & {"view", "trigger"}  # they cost every later ALTER its whole text

    monkeypatch.setattr(db_schema, "apply", apply)
    assert db_schema.fresh(all_at_once, files, "w", {max(files)})
    assert shape(all_at_once) == shape(one) and said(all_at_once) == said(one)
    assert seen == sorted(files) and not all_at_once.in_transaction
    safe = "SELECT value FROM meta WHERE key = 'old_writers_safe'"
    assert all_at_once.execute(safe).fetchall() == one.execute(safe).fetchall()


def test_a_file_made_meanwhile_is_left_to_the_files_one_by_one() -> None:
    con, files = empty(), db_schema.migrations(goh_cache_db.SEAM)
    db_schema.apply(con, 1, files[1], "other", False)
    was = shape(con)
    assert not db_schema.fresh(con, files, "w", set())
    assert shape(con) == was and not con.in_transaction


def test_a_file_that_fails_leaves_the_database_empty(tmp_path) -> None:
    con, files = empty(), dict(db_schema.migrations(goh_cache_db.SEAM))
    bad = tmp_path / "0099_bad.sql"
    bad.write_text("CREATE TABLE fine(x);\nCREATE TRIGGER t AFTER INSERT ON none BEGIN SELECT 1;"
                   " END;\n")  # fmt: skip
    was = shape(con)
    with pytest.raises(sqlite3.OperationalError):
        db_schema.fresh(con, {**files, 99: bad}, "w", set())
    assert shape(con) == was and said(con) == [] and not con.in_transaction
