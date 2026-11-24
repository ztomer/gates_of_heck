"""lib/db_core.py, the mechanics of goh.db and goh-cache.db (GOH.DBSEAM): migrations by set
difference, the version kept in the file (user_version and meta), an older goh read-only on a
newer file, the real home refused under a suite, and the lock-wait instrument."""

from __future__ import annotations

import dataclasses
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import db_core
import goh_cache_db
import goh_db
import lock_order
import pytest
from conftest import REPO_ROOT
from timing_bounds import patience


def seam(tmp_path: Path, monkeypatch, *migrations: str, name: str = "schema") -> db_core.Seam:
    """goh.db's seam over a temp schema directory holding MIGRATIONS (NNNN_name), its file in
    tmp_path (GOH_DB)."""
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    for m in migrations:
        n = int(m[:4])
        (d / f"{m}.sql").write_text(f"CREATE TABLE t{n}(x INTEGER, at REAL);\n")
    monkeypatch.setenv("GOH_DB", str(tmp_path / "goh.db"))
    return dataclasses.replace(goh_db.SEAM, schema=d)


def test_an_out_of_order_migration_is_applied(tmp_path, monkeypatch) -> None:
    """Lane B's 0003 lands before lane A's 0002: 0002 still applies (set difference), and
    user_version waits for it -- `MAX(version)` would have skipped 0002 for good."""
    s = seam(tmp_path, monkeypatch, "0001_a", "0003_c")
    with db_core.connect(s) as con:
        assert db_core.version(con)["applied"] == [1, 3]
        assert con.execute("PRAGMA user_version").fetchone()[0] == 1  # 2 is missing
    (s.schema / "0002_b.sql").write_text("CREATE TABLE t2(x INTEGER);\n")
    with db_core.connect(s) as con:
        v = db_core.version(con)
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert v["applied"] == [1, 2, 3] and v["user_version"] == 3, v
    assert v["meta"]["schema_version"] == "3" and v["meta"]["db"] == "goh", v
    assert {"t1", "t2", "t3"} <= tables


def test_the_file_carries_its_version(tmp_path, monkeypatch) -> None:
    """Owner 2026-11-23: "please keep a version field somewhere for the dbs"."""
    s = seam(tmp_path, monkeypatch, "0001_a")
    with db_core.connect(s) as con:
        v = db_core.version(con)
        rows = con.execute("SELECT version, name, writer FROM schema_version").fetchall()
    assert set(v["meta"]) == {"db", "schema_version", "created_by", "last_migrated_by",
                              "created_at"}, v  # fmt: skip
    assert v["meta"]["created_by"].startswith("goh ") and rows == [(1, "0001_a", rows[0][2])]
    raw = (tmp_path / "goh.db").read_bytes()[60:64]  # the header's user_version, no table parse
    assert int.from_bytes(raw, "big") == 1


def test_an_older_goh_opens_a_newer_file_read_only(tmp_path, monkeypatch, capsys) -> None:
    """A revert or an older install against a file a newer goh migrated: it reads, says so, and
    never writes or migrates down."""
    newer = seam(tmp_path, monkeypatch, "0001_a", "0002_b", name="new")
    with db_core.connect(newer):
        pass
    older = seam(tmp_path, monkeypatch, "0001_a", name="old")
    with db_core.connect(older) as con:
        assert con.execute("SELECT COUNT(*) FROM t2").fetchone()[0] == 0  # it reads
        with pytest.raises(sqlite3.OperationalError):
            con.execute("INSERT INTO t1 VALUES (1, 0)")
    err = capsys.readouterr().err
    assert "holds migration 0002_b (by goh " in err and "ships [1]): opened read-only" in err, err
    with pytest.raises(db_core.NewerFile), db_core.transaction(older) as con:
        con.execute("INSERT INTO t1 VALUES (1, 0)")
    with db_core.connect(newer) as con:
        assert db_core.version(con)["user_version"] == 2
        assert con.execute("SELECT COUNT(*) FROM t1").fetchone()[0] == 0


def test_a_number_the_code_does_not_ship_is_newer_too(tmp_path, monkeypatch) -> None:
    """A newer goh applied 0003 while 0002 is still missing: user_version is 1, but the file is
    still not this goh's to write."""
    newer = seam(tmp_path, monkeypatch, "0001_a", "0003_c", name="new")
    with db_core.connect(newer):
        pass
    older = seam(tmp_path, monkeypatch, "0001_a", name="old")
    with db_core.connect(older) as con:
        assert db_core.newer(con, older)
        assert con.execute("PRAGMA query_only").fetchone()[0] == 0  # mode=ro, not a pragma
        with pytest.raises(sqlite3.OperationalError):
            con.execute("INSERT INTO t1 VALUES (1, 0)")


def test_newer_names_the_migration_it_does_not_ship(tmp_path, monkeypatch) -> None:
    """2026-11-23 it read "version 4 (applied [4]), newer than this goh's 6": a lane's 0004 on a
    file main (shipping up to 6, without 4) opened. It names the migration and who applied it."""
    lane = seam(tmp_path, monkeypatch, "0001_a", "0004_small", name="lane")
    with db_core.connect(lane):
        pass
    main = seam(tmp_path, monkeypatch, "0001_a", "0006_d", name="main")
    with db_core.connect(main) as con:
        why = db_core.newer(con, main)
    assert why and "holds migration 0004_small (by goh " in why and "ships [1, 6])" in why, why
    assert "newer than" not in why


def test_a_real_home_open_raises(tmp_path, monkeypatch) -> None:
    """Under a suite, the real home's goh.db and goh-cache.db are refused before any open (here a
    stand-in real home: a failing guard must never create the real file)."""
    real = tmp_path / "real"
    monkeypatch.setenv(db_core.REAL_HOME_ENV, str(real))
    for env, rel in (("GOH_DB", ".local/state/goh/goh.db"),
                     ("GOH_CACHE_DB", ".cache/goh/goh-cache.db")):  # fmt: skip
        monkeypatch.setenv(env, str(real / rel))
    for s in (goh_db.SEAM, goh_cache_db.SEAM):
        with pytest.raises(db_core.RealHomeOpen), db_core.connect(s):
            pass
        with pytest.raises(db_core.RealHomeOpen), db_core.ro(s):
            pass
    assert not (real / ".local").exists() and not (real / ".cache").exists()
    monkeypatch.setenv("GOH_DB", str(real / ".cache" / "goh" / "suite" / "h" / "goh.db"))
    with db_core.connect(goh_db.SEAM):  # a suite home under the real cache root is the suite's
        pass


def test_the_suite_points_both_files_at_its_own_home() -> None:
    """The defaults under a suite are the suite home's, never the real home's."""
    for s in (goh_db.SEAM, goh_cache_db.SEAM):
        db_core.guard(db_core.path(s))


def test_a_read_never_creates_the_file(tmp_path, monkeypatch) -> None:
    s = seam(tmp_path, monkeypatch, "0001_a")
    with pytest.raises(OSError), db_core.ro(s):
        pass
    assert not (tmp_path / "goh.db").exists()


def test_the_paths(tmp_path, monkeypatch) -> None:
    env = {"HOME": "/h", "XDG_STATE_HOME": "/xs"}
    assert db_core.path(goh_db.SEAM, env) == Path("/xs/goh/goh.db")
    assert db_core.path(goh_cache_db.SEAM, env) == Path("/h/.cache/goh/goh-cache.db")
    assert db_core.path(goh_db.SEAM, {"HOME": "/h"}) == Path("/h/.local/state/goh/goh.db")
    assert db_core.path(goh_db.SEAM, {**env, "GOH_DB": "/x/g.db"}) == Path("/x/g.db")
    with pytest.raises(ValueError, match="absolute"):
        db_core.path(goh_db.SEAM, {"GOH_DB": "g.db"})


def test_a_transaction_commits_rolls_back_and_times_its_wait(tmp_path, monkeypatch) -> None:
    s = seam(tmp_path, monkeypatch, "0001_a")
    timings = tmp_path / "t.jsonl"
    monkeypatch.setenv("GOH_TIMINGS", str(timings))
    with db_core.transaction(s) as con:
        con.execute("INSERT INTO t1 VALUES (1, 0)")
    with pytest.raises(RuntimeError), db_core.transaction(s) as con:
        con.execute("INSERT INTO t1 VALUES (2, 0)")
        raise RuntimeError("rolled back")
    with db_core.ro(s) as con:
        assert con.execute("SELECT x FROM t1").fetchall() == [(1,)]
        assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    rows = [json.loads(ln) for ln in timings.read_text().splitlines()]
    assert len(rows) == 2 and {r["db"] for r in rows} == {"goh"}, rows
    assert all(r["wait_s"] >= 0 and r["label"] == "db wait goh" and "ms" in r for r in rows)


def test_the_instrument_reaches_a_suite_child_and_counts_a_busy_timeout(tmp_path, monkeypatch):
    """GOH.VERDICTDB step 5's instrument: a suite drops GOH_TIMINGS from every worker and gate
    child (tests/conftest.py), so the waits that matter -- workers sharing the suite home's
    goh-cache.db -- were never seen. GATES_OF_HECK_DB_WAITS (no GOH_ prefix: hermetic_env keeps
    it) receives the same rows; a write that gave up at its busy timeout is a row too, `busy`."""
    s = seam(tmp_path, monkeypatch, "0001_a")
    monkeypatch.delenv("GOH_TIMINGS", raising=False)
    waits = tmp_path / "w.jsonl"
    monkeypatch.setenv(db_core.WAITS_ENV, str(waits))
    with db_core.transaction(s) as con:
        con.execute("INSERT INTO t1 VALUES (1, 0)")
    monkeypatch.setenv(s.busy_env, "50")
    with db_core.transaction(s) as con:
        con.execute("INSERT INTO t1 VALUES (2, 0)")
        with pytest.raises(sqlite3.OperationalError, match="locked"), db_core.transaction(s):
            pass
    rows = [json.loads(ln) for ln in waits.read_text().splitlines()]
    assert [r.get("busy", False) for r in rows] == [False, False, True], rows
    assert rows[2]["wait_s"] >= 0.04 and rows[2]["db"] == "goh", rows


def test_the_seams_take_their_innermost_locks(tmp_path, monkeypatch) -> None:
    """goh_db's transaction waits on lock `goh_db`: the table ranks it above every other lock, so
    it may be taken under any of them, and a write already holding it refuses a nested wait."""
    monkeypatch.setenv("GOH_DB", str(tmp_path / "goh.db"))
    table = lock_order.table()
    assert table["goh_db"].rank > max(
        lk.rank for n, lk in table.items() if not n.startswith("goh_")
    )
    monkeypatch.setenv(lock_order.ENV, lock_order.with_held("land", 1, where=REPO_ROOT))
    with goh_db.transaction() as con:
        con.execute("INSERT INTO imports VALUES ('s', 'f', 'h', 1, 0)")
    monkeypatch.setenv(lock_order.ENV, lock_order.with_held("goh_db", os.getpid()))
    with pytest.raises(lock_order.OrderError), goh_db.transaction():
        pass


def test_the_statements_of_a_migration() -> None:
    sql = "-- c\nCREATE TABLE a(x);\nCREATE TRIGGER t AFTER INSERT ON a BEGIN SELECT 1; END;\n"
    assert len(db_core.statements(sql)) == 2
    with pytest.raises(ValueError, match="unterminated"):
        db_core.statements("CREATE TABLE a(x)")


def test_a_malformed_migration_name_or_a_number_twice_raises(tmp_path, monkeypatch) -> None:
    s = seam(tmp_path, monkeypatch, "0001_a")
    (s.schema / "0001_b.sql").write_text("SELECT 1;\n")
    with pytest.raises(ValueError, match="also"):
        db_core.migrations(s)
    (s.schema / "0001_b.sql").unlink()
    (s.schema / "2_x.sql").write_text("SELECT 1;\n")
    with pytest.raises(ValueError, match="NNNN_name"):
        db_core.migrations(s)


def test_the_cli(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GOH_DB", str(tmp_path / "goh.db"))
    run = [sys.executable, str(REPO_ROOT / "lib" / "goh_db.py")]

    def goh_db_cli(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run([*run, *args], capture_output=True, text=True, check=False,
                              stdin=subprocess.DEVNULL, timeout=patience(60))  # fmt: skip

    assert goh_db_cli("path").stdout.strip() == str(tmp_path / "goh.db")
    assert json.loads(goh_db_cli("migrate").stdout)["user_version"] >= 1
    assert json.loads(goh_db_cli("version").stdout)["meta"]["db"] == "goh"
    assert "goh\n" in goh_db_cli("dump", "meta").stdout.replace("\t", "\n")
    assert goh_db_cli("check").returncode == 0
    assert goh_db_cli("bogus").returncode == 2


def test_a_cache_error_is_a_miss(capsys) -> None:
    got = None
    with goh_cache_db.missable():
        raise sqlite3.OperationalError("database is locked")
    assert got is None and "a miss" in capsys.readouterr().err
