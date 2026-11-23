"""lib/db_bounds.py (GOH.DBSEAM; owner 2026-11-23: "need also size limit"): every table bounded
by days, rows and bytes, every file by a budget; a history table past its bound moves its oldest
rows to goh-archive-YYYY.db, a cache table evicts them."""

from __future__ import annotations

import dataclasses
import re
import sqlite3
from pathlib import Path

import db_bounds
import db_core
import goh_cache_db
import goh_db
import pytest
import state_bounds

DAY = 86400.0
NOW = 1_795_000_000.0  # 2026-11-18, UTC
LAST_YEAR = 1_760_000_000.0  # 2025-10-09


def seam(tmp_path: Path, monkeypatch, bound: str, over: str = "archive") -> db_core.Seam:
    """A seam whose one table `h` is bounded by BOUND, its file in tmp_path/state/goh.db."""
    d = tmp_path / f"schema-{over}"
    d.mkdir()
    (d / "0001_h.sql").write_text(
        f"-- bound: h {bound} stamp=at\n"
        "CREATE TABLE h(k INTEGER PRIMARY KEY, body BLOB, at REAL);\n"
    )
    monkeypatch.setenv("GOH_DB", str(tmp_path / "state" / "goh.db"))
    return dataclasses.replace(goh_db.SEAM, schema=d, over=over)


def fill(s: db_core.Seam, stamps: list[float], size: int = 1000) -> None:
    with db_core.transaction(s) as con:
        con.executemany("INSERT INTO h VALUES (?, ?, ?)",
                        [(i, b"x" * size, t) for i, t in enumerate(stamps)])  # fmt: skip


def keys(p: Path) -> list[int]:
    con = sqlite3.connect(p)
    try:
        return [r[0] for r in con.execute("SELECT k FROM h ORDER BY k")]
    finally:
        con.close()


def test_an_oversized_history_table_moves_its_oldest_rows_to_the_archive(tmp_path, monkeypatch):
    """A planted 1000-row table of 1 KB rows under a 256 KB budget: the oldest rows move to the
    yearly archive of their stamp, the newest stay, nothing is lost."""
    s = seam(tmp_path, monkeypatch, "bytes=256KB")
    stamps = [LAST_YEAR + i for i in range(200)] + [NOW + i for i in range(800)]
    fill(s, stamps)
    assert db_bounds.unbounded(s, NOW)  # past its bound before the sweep
    moved = db_bounds.sweep(s, NOW)
    live = keys(db_core.path(s))
    a25 = keys(db_bounds.archive_path(s, "2025"))
    a26 = keys(db_bounds.archive_path(s, "2026"))
    assert moved["h"] == len(a25) + len(a26) and a25 == list(range(200)), moved
    assert sorted(live + a25 + a26) == list(range(1000))  # every row in exactly one file
    assert live == list(range(1000 - len(live), 1000)) and 0 < len(live) < 300
    with db_core.ro(s) as con:
        assert db_bounds.table_bytes(con, "h") <= 256 << 10
    arch = sqlite3.connect(db_bounds.archive_path(s, "2025"))
    meta = dict(arch.execute("SELECT key, value FROM meta"))
    assert meta["db"] == "goh-archive-2025" and meta["schema_version"] == "1", meta
    assert arch.execute("PRAGMA user_version").fetchone()[0] == 1
    arch.close()
    assert db_bounds.unbounded(s, NOW) == []


def test_an_oversized_cache_table_evicts(tmp_path, monkeypatch) -> None:
    s = seam(tmp_path, monkeypatch, "rows=100", over="evict")
    fill(s, [NOW + i for i in range(300)], size=10)
    assert db_bounds.sweep(s, NOW) == {"h": 200}
    assert keys(db_core.path(s)) == list(range(200, 300))
    assert not list((tmp_path / "state").glob("*archive*"))


def test_days_and_rows(tmp_path, monkeypatch) -> None:
    s = seam(tmp_path, monkeypatch, "days=14 rows=50", over="evict")
    fill(s, [NOW - 20 * DAY + i for i in range(10)] + [NOW + i for i in range(60)], size=10)
    assert db_bounds.sweep(s, NOW + 100) == {"h": 20}  # 10 by age, then 10 past the newest 50
    assert keys(db_core.path(s)) == list(range(20, 70))
    assert db_bounds.sweep(s, NOW + 100) == {}


def test_the_file_budget_takes_from_its_largest_table(tmp_path, monkeypatch) -> None:
    s = seam(tmp_path, monkeypatch, "days=3650")
    fill(s, [NOW + i for i in range(800)])
    with db_core.ro(s) as con:
        used = db_bounds.used_bytes(con)
    moved = db_bounds.sweep(s, NOW, budget=used // 2)
    with db_core.ro(s) as con:
        assert db_bounds.used_bytes(con) <= used // 2, moved
    assert moved["h"] > 0 and keys(db_core.path(s))[-1] == 799


def test_unbounded_names_a_table_with_no_bound_and_the_file_past_its_budget(tmp_path, monkeypatch):
    s = seam(tmp_path, monkeypatch, "rows=10")
    with db_core.transaction(s) as con:
        con.execute("CREATE TABLE stray(x)")
    got = db_bounds.unbounded(dataclasses.replace(s, budget=1))
    assert any("table stray has no `-- bound:` header" in g for g in got), got
    assert any("past its budget of 1" in g for g in got), got


def test_a_missing_file_is_never_created_by_the_bounds(tmp_path, monkeypatch) -> None:
    s = seam(tmp_path, monkeypatch, "rows=10")
    assert db_bounds.sweep(s) == {} and db_bounds.unbounded(s) == []
    assert not db_core.path(s).exists()


def test_the_headers(tmp_path) -> None:
    assert db_bounds.parse("-- bound: t days=1.5 rows=3 bytes=2MB stamp=at") == (
        "t", db_bounds.TableBound(1.5, 3, 2 << 20, "at"))  # fmt: skip
    assert db_bounds.parse("CREATE TABLE t(x);") is None
    for bad in ("-- bound: t rows=3", "-- bound: t stamp=at", "-- bound: t rows=1 ttl=2 stamp=a",
                "-- bound: t bytes=lots stamp=at"):  # fmt: skip
        with pytest.raises(ValueError):
            db_bounds.parse(bad)


def test_every_shipped_table_is_bounded() -> None:
    """Each table a shipped migration creates has a `-- bound:` header (a new table cannot be
    unbounded), and each seam has its budget."""
    for s in (goh_db.SEAM, goh_cache_db.SEAM):
        sql = "".join(p.read_text() for p in db_core.migrations(s).values())
        made = set(re.findall(r"CREATE TABLE (\w+)", sql))
        bounded = set(db_bounds.bounds(s))
        assert made and made <= bounded, (s.file, made - bounded)
    assert (goh_db.SEAM.budget, goh_cache_db.SEAM.budget) == (256 << 20, 2 << 30)
    assert (goh_db.SEAM.over, goh_cache_db.SEAM.over) == ("archive", "evict")


def test_state_bounds_sweeps_a_file_in_its_root(tmp_path, monkeypatch) -> None:
    """lib/state_bounds.py's sweep and check keep the databases too: every seam for goh's state
    root, a fixture root's own file for a fixture."""
    root = tmp_path / "state"
    root.mkdir()
    monkeypatch.setenv("GOH_DB", str(root / "goh.db"))
    monkeypatch.setenv("GOH_CACHE_DB", str(tmp_path / "elsewhere" / "goh-cache.db"))
    assert db_bounds.under(root, tmp_path / "other") == [goh_db.SEAM]
    assert db_bounds.under(root, root) == [goh_db.SEAM, goh_cache_db.SEAM]
    old = NOW - 900 * DAY
    with goh_db.transaction() as con:
        con.execute("INSERT INTO imports VALUES ('s', 'f', 'h', 1, ?)", (old,))
        con.execute("CREATE TABLE stray(x)")
    assert any("stray" in line for line in state_bounds.unbounded(root, NOW))
    assert root / "goh.db" in state_bounds.sweep(root, NOW)
    with goh_db.ro() as con:
        assert con.execute("SELECT COUNT(*) FROM imports").fetchone()[0] == 0
    assert (root / "goh-archive-2024.db").is_file()  # the year of the row's stamp
