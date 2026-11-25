"""lib/db_bounds_with.py (GOH.OLDWRITERS, L7): the rows a sweep takes, their `-- with:` children
and the archive part they go to. Rows in equal rows out, whatever the archive's shape, killed or
not. The plan's 0011 and 0012 (tests/fixtures/goh_db_plan) are the shapes; they are not shipped."""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path

import db_bounds
import db_bounds_with
import db_core
import goh_db
import pytest
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

PLAN = Path(__file__).parent / "fixtures" / "goh_db_plan"
NOW = 1_795_000_000.0  # 2026-11-18, UTC
DAY = 86400.0


def seam(tmp_path: Path, monkeypatch, sql: dict[int, str], name: str = "schema",
         over: str = "archive") -> db_core.Seam:  # fmt: skip
    d = tmp_path / name
    d.mkdir()
    for n, text in sql.items():
        (d / f"{n:04d}_m.sql").write_text(text)
    monkeypatch.setenv("GOH_DB", str(tmp_path / "state" / "goh.db"))
    return dataclasses.replace(goh_db.SEAM, schema=d, over=over)


def rows(p: Path, table: str, cols: str = "*") -> list[tuple]:
    con = sqlite3.connect(p)
    try:
        return sorted(con.execute(f"SELECT {cols} FROM {table}"), key=repr)
    except sqlite3.OperationalError:  # a part without the table
        return []
    finally:
        con.close()


def everywhere(s: db_core.Seam, table: str, cols: str = "*") -> list[tuple]:
    """TABLE's rows in the live file and every archive part."""
    files = [db_core.path(s), *sorted(db_core.path(s).parent.glob("goh-archive-*.db"))]
    return sorted((r for p in files for r in rows(p, table, cols)), key=repr)


def part(s: db_core.Seam, tail: str = "") -> Path:
    return db_bounds_with.archive_path(s, "2026", tail)


# -- L7: the plan's own shapes --------------------------------------------------------------------


def land(run: int, **more) -> tuple:
    body = {"item": "A.B", "state": "red", "guards": ["g1", "g2"], "pre_s": 1.5, **more}
    return ("/q", str(run), "A.B", "red", NOW - DAY + run, json.dumps(body))


PUT = "INSERT INTO lands(queue, run, item, state, at, body) VALUES (?, ?, ?, ?, ?, ?)"


def plan(tmp_path: Path, monkeypatch) -> tuple[db_core.Seam, db_core.Seam]:
    """Schema 10 as shipped, and the same with the plan's 0011 and 0012 beside it; lands keep 3."""
    for name in ("ten", "more"):
        shutil.copytree(goh_db.SEAM.schema, tmp_path / name)
        f = tmp_path / name / "0002_lands.sql"
        f.write_text(f.read_text().replace("bound: lands bytes=128MB", "bound: lands rows=3"))
    for p in PLAN.glob("*.sql"):
        shutil.copy(p, tmp_path / "more")
    monkeypatch.setenv("GOH_DB", str(tmp_path / "state" / "goh.db"))
    return (dataclasses.replace(goh_db.SEAM, schema=tmp_path / "ten"),
            dataclasses.replace(goh_db.SEAM, schema=tmp_path / "more"))  # fmt: skip


def test_a_widened_lands_never_goes_into_a_schema_10_archive(tmp_path, monkeypatch) -> None:
    """L7: an archive made at schema 10, then a sweep of lands with 52 more columns and five child
    tables. A part `-b` opens, every row, column and child row is in exactly one file."""
    ten, more = plan(tmp_path, monkeypatch)
    with db_core.transaction(ten) as con:
        con.executemany(PUT, [land(i) for i in range(8)])
    assert db_bounds.sweep(ten, NOW) == {"lands": 5}
    closed = part(ten).read_bytes()
    with db_core.transaction(more) as con:
        con.executemany(PUT, [land(i) for i in range(8, 20)])
        con.execute(PUT.replace("INSERT", "INSERT OR REPLACE"), land(9, why="w"))
    with db_core.ro(more) as con:
        want = {t: set(con.execute(f"SELECT * FROM {t}")) for t in ("lands", "land_lists")}
        assert len(want["land_lists"]) == 24 and db_bounds.unbounded(more, NOW)
    assert db_bounds.sweep(more, NOW) == {"lands": 12}
    assert part(more).read_bytes() == closed and part(more, "b").is_file()
    for t, was in want.items():
        assert set(rows(db_core.path(more), t)) | set(rows(part(more, "b"), t)) == was
        assert not set(rows(db_core.path(more), t)) & set(rows(part(more, "b"), t))
    assert sorted(int(r) for (r,) in everywhere(more, "lands", "run")) == list(range(20))
    got = rows(part(more, "b"), "lands", "run, why, pre_s, rest")
    assert ("9", "w", 1.5, "{}") in got, got
    assert db_bounds.unbounded(more, NOW) == []  # the child tables need no bound of their own
    with db_core.transaction(more) as con:  # a later sweep goes on into -b
        con.executemany(PUT, [land(i) for i in range(20, 24)])
    db_bounds.sweep(more, NOW)
    assert len(rows(part(more, "b"), "lands")) == 16 and not part(more, "c").exists()


def test_the_older_copy_never_sweeps_the_widened_file(tmp_path, monkeypatch) -> None:
    """It may write it (0011 and 0012 are safe); its DELETE would fire lands_gone and take the
    child rows it does not know to archive."""
    ten, more = plan(tmp_path, monkeypatch)
    with db_core.connect(ten):  # the numbers are judged: the file was there before them
        pass
    with db_core.connect(more) as con:
        assert "11, 12" in db_core.version(con)["meta"]["old_writers_safe"]
    with db_core.transaction(ten) as con:
        con.executemany(PUT, [land(i) for i in range(9)])
    assert db_bounds.sweep(ten, NOW) == {}
    assert len(rows(db_core.path(more), "land_lists")) == 18


def test_a_column_the_live_table_dropped(tmp_path, monkeypatch) -> None:
    """606 of 606 rows were lost on a copy of the real file: the archive still called `body` NOT
    NULL, INSERT OR IGNORE skipped each row, and the live rows went."""
    base = ("-- bound: h rows=1 stamp=at\n"
            "CREATE TABLE h(k INTEGER PRIMARY KEY, body TEXT NOT NULL, at REAL);\n")  # fmt: skip
    s = seam(tmp_path, monkeypatch, {1: base})
    with db_core.transaction(s) as con:
        con.executemany("INSERT INTO h(k, body, at) VALUES (?, 'b', ?)",
                        [(i, NOW + i) for i in range(4)])  # fmt: skip
    db_bounds.sweep(s, NOW)
    (s.schema / "0002_m.sql").write_text(
        "-- old-writers: refuse\nALTER TABLE h DROP COLUMN body;\n"
    )
    with db_core.transaction(s) as con:
        con.executemany("INSERT INTO h(k, at) VALUES (?, ?)", [(i, NOW + i) for i in range(4, 611)])
    db_bounds.sweep(s, NOW)
    assert [k for (k,) in sorted(everywhere(s, "h", "k"))] == list(range(611))
    assert len(rows(part(s, "b"), "h")) == 607


@pytest.mark.parametrize("change", [
    "CREATE TABLE kid2(k INTEGER, v TEXT, PRIMARY KEY (k, v));\n-- with: kid2 of h\n",
    "ALTER TABLE kid ADD COLUMN extra TEXT;\n",
    "ALTER TABLE h ADD COLUMN extra TEXT;\n",
], ids=["a new child", "a wider child", "a wider parent"])  # fmt: skip
def test_any_difference_of_shape_opens_a_part(tmp_path, monkeypatch, change) -> None:
    s = seam(tmp_path, monkeypatch, {1: (
        "-- bound: h rows=1 stamp=at\n-- with: kid of h\n"
        "CREATE TABLE h(k INTEGER PRIMARY KEY, at REAL);\n"
        "CREATE TABLE kid(k INTEGER, v TEXT, PRIMARY KEY (k, v));\n")})  # fmt: skip
    with db_core.transaction(s) as con:
        con.executemany("INSERT INTO h(k, at) VALUES (?, ?)", [(i, NOW + i) for i in range(3)])
        con.execute("INSERT INTO kid(k, v) VALUES (0, 'a')")
    db_bounds.sweep(s, NOW)
    assert not part(s, "b").exists()
    (s.schema / "0002_m.sql").write_text(f"-- old-writers: safe\n{change}")
    with db_core.transaction(s) as con:
        con.execute("INSERT INTO kid(k, v) VALUES (2, 'b')")
        con.execute("INSERT INTO h(k, at) VALUES (9, ?)", (NOW + 9,))
    db_bounds.sweep(s, NOW)
    assert rows(part(s, "b"), "h", "k") == [(2,)] and rows(part(s, "b"), "kid", "k, v") == [
        (2, "b")
    ]
    assert len(rows(part(s), "h")) == 2 and rows(part(s), "kid", "k, v") == [(0, "a")]


# -- children, nested and twice claimed -----------------------------------------------------------

NESTED = """-- bound: a rows=2 stamp=at
-- bound: b days=3650 stamp=at
-- with: b of a
-- with: c of b
-- with: c of a
-- with: d of c
CREATE TABLE a(k TEXT PRIMARY KEY, at REAL);
CREATE TABLE b(k TEXT PRIMARY KEY, at REAL);
CREATE TABLE c(k TEXT, r TEXT, PRIMARY KEY (k, r)) WITHOUT ROWID;
CREATE TABLE d(k TEXT, r TEXT, o INTEGER, PRIMARY KEY (k, r, o));
CREATE TRIGGER a_gone AFTER DELETE ON a BEGIN DELETE FROM b WHERE k = OLD.k; END;
"""


def nested(tmp_path: Path, monkeypatch, over: str = "archive", n: int = 10) -> db_core.Seam:
    s = seam(tmp_path, monkeypatch, {1: NESTED}, over=over)
    with db_core.transaction(s) as con:
        for i in range(n):
            con.execute("INSERT INTO a(k, at) VALUES (?, ?)", (f"k{i}", NOW + i))
            if i % 2:
                con.execute("INSERT INTO b(k, at) VALUES (?, ?)", (f"k{i}", NOW + i))
            con.execute("INSERT INTO c(k, r) VALUES (?, 'r')", (f"k{i}",))
            con.execute("INSERT INTO d(k, r, o) VALUES (?, 'r', 0)", (f"k{i}",))
    return s


def test_nested_children_leave_with_their_parent(tmp_path, monkeypatch) -> None:
    s = nested(tmp_path, monkeypatch)
    assert db_bounds.sweep(s, NOW + 60) == {"a": 8}
    left = {"k8", "k9"}
    for t, n in (("a", 10), ("b", 5), ("c", 10), ("d", 10)):
        live = {k for (k,) in rows(db_core.path(s), t, "k")}
        gone = {k for (k,) in rows(part(s), t, "k")}
        assert live <= left and not live & gone and len(live | gone) == n, (t, live, gone)
    assert db_bounds.unbounded(s, NOW + 60) == []


def test_a_cache_evicts_the_children_too(tmp_path, monkeypatch) -> None:
    s = nested(tmp_path, monkeypatch, over="evict")
    assert db_bounds.sweep(s, NOW + 60) == {"a": 8}
    assert len(rows(db_core.path(s), "d")) == 2 and not part(s).exists()


def test_a_parents_bytes_count_the_children_that_have_no_bound(tmp_path, monkeypatch) -> None:
    """Else a child table grows past the file's budget and no sweep takes a row of it."""
    s = seam(tmp_path, monkeypatch, {1: (
        "-- bound: h bytes=64KB stamp=at\n-- with: kid of h\n"
        "CREATE TABLE h(k INTEGER PRIMARY KEY, at REAL);\n"
        "CREATE TABLE kid(k INTEGER, v TEXT, PRIMARY KEY (k, v));\n")})  # fmt: skip
    with db_core.transaction(s) as con:
        for i in range(100):
            con.execute("INSERT INTO h(k, at) VALUES (?, ?)", (i, NOW + i))
            con.execute("INSERT INTO kid(k, v) VALUES (?, ?)", (i, "x" * 4000))
    assert any("table h is past its bound" in x for x in db_bounds.unbounded(s, NOW))
    assert db_bounds.sweep(s, NOW)["h"] > 50
    assert db_bounds.unbounded(s, NOW) == [] and len(everywhere(s, "kid")) == 100


def test_a_child_of_an_unbounded_parent_is_still_unbounded(tmp_path, monkeypatch) -> None:
    s = seam(tmp_path, monkeypatch, {1: (
        "-- with: kid of h\nCREATE TABLE h(k INTEGER PRIMARY KEY, at REAL);\n"
        "CREATE TABLE kid(k INTEGER PRIMARY KEY);\n")})  # fmt: skip
    with db_core.connect(s):
        pass
    assert len(db_bounds.unbounded(s, NOW)) == 2


# -- nothing leaves that is not there --------------------------------------------------------------


def test_a_key_the_archive_holds_with_another_row(tmp_path, monkeypatch) -> None:
    """A table swept empty starts its rowids again: the new row 1 is not the archived row 1."""
    s = seam(tmp_path, monkeypatch, {1: (
        "-- bound: h days=1 stamp=at\nCREATE TABLE h(n INTEGER PRIMARY KEY, v TEXT, at REAL);\n"
    )})  # fmt: skip
    for v in ("first", "second"):
        with db_core.transaction(s) as con:
            con.execute("INSERT INTO h(v, at) VALUES (?, ?)", (v, NOW))
        assert db_bounds.sweep(s, NOW + 5 * DAY) == {"h": 1}
    assert everywhere(s, "h", "n, v") == [(1, "first"), (1, "second")]


def test_a_row_every_part_refuses_stays_live(tmp_path, monkeypatch) -> None:
    s = nested(tmp_path, monkeypatch)
    monkeypatch.setattr(db_bounds_with, "_copy", lambda *_a: False)
    with pytest.raises(sqlite3.IntegrityError, match="kept in the live file"):
        db_bounds.sweep(s, NOW + 60)
    assert len(rows(db_core.path(s), "a")) == 10


def test_no_write_comes_between_the_copy_and_the_delete(tmp_path, monkeypatch) -> None:
    """The live file's write lock is held from before the copy: a row written in between (stamped
    in the swept range) would be deleted without ever having been copied."""
    s = nested(tmp_path, monkeypatch)
    monkeypatch.setenv(s.busy_env, "50")
    real, tried = db_bounds_with.archive, []

    def then_a_writer(*a):
        got = real(*a)
        with pytest.raises(sqlite3.OperationalError, match="locked"), db_core.transaction(s) as con:
            con.execute("INSERT INTO a(k, at) VALUES ('late', ?)", (NOW,))
        tried.append(got)
        return got

    monkeypatch.setattr(db_bounds_with, "archive", then_a_writer)
    db_bounds.sweep(s, NOW + 60)
    assert tried and len(everywhere(s, "a")) == 10


KILLED = """
import os, signal, sys, dataclasses
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import db_bounds, db_bounds_with, goh_db
where, real, seen = sys.argv[3], db_bounds_with.archive, []
def die(): os.kill(os.getpid(), signal.SIGKILL)
def archive(*a):
    got = real(*a)
    if where == "between": die()
    return got
copy = db_bounds_with._copy
def half(*a):
    got = copy(*a)
    if where == "before": die()
    return got
db_bounds_with.archive, db_bounds_with._copy = archive, half
db_bounds.sweep(dataclasses.replace(goh_db.SEAM, schema=Path(sys.argv[2])), float(sys.argv[4]))
"""


@pytest.mark.parametrize("where", ["before", "between"])
def test_a_kill_loses_nothing_and_doubles_nothing(tmp_path, monkeypatch, where) -> None:
    """SIGKILL before the archive's commit, and between it and the live file's: every row is in a
    file, and the next sweep leaves each in one."""
    s = nested(tmp_path, monkeypatch)
    got = subprocess.run([sys.executable, "-c", KILLED, str(REPO_ROOT / "lib"), str(s.schema),
                          where, str(NOW + 60)], capture_output=True, text=True, check=False,
                         env=hermetic_env(GOH_DB=os.environ["GOH_DB"]), stdin=subprocess.DEVNULL,
                         timeout=patience(60))  # fmt: skip
    assert got.returncode == -signal.SIGKILL, got.stderr
    assert len(rows(db_core.path(s), "a")) == 10
    assert len(rows(part(s), "a")) == (8 if where == "between" else 0)
    assert db_bounds.sweep(s, NOW + 60) == {"a": 8}
    for t, n in (("a", 10), ("b", 5), ("c", 10), ("d", 10)):
        assert len(everywhere(s, t)) == n == len(set(everywhere(s, t)))
    assert not part(s, "b").exists()
