"""lib/db_bounds.py -- every table of goh's SQLite files bounded, and each file under a budget
(GOH.DBSEAM; owner 2026-11-23: "need also size limit").

    -- bound: TABLE days=14 rows=5000 bytes=64MB stamp=at     a schema file's header line

    TableBound      days, rows and bytes a table keeps, judged by STAMP (epoch seconds); a later
                    migration's header for the same table replaces an earlier one
    bounds(seam)    {table: TableBound} from the seam's schema files
    sweep(seam)     each table past its bound gives up its OLDEST rows, then the file past its
                    budget (the seam's, goh.db 256 MB, goh-cache.db 2 GB) gives up more from its
                    largest tables; then incremental_vacuum and wal_checkpoint(TRUNCATE)
    unbounded(seam) one line per table with no bound, past its bound, or the file past its budget

WHERE THE ROWS GO. A history file (goh.db: lands, ships) loses nothing: its rows move to a yearly
archive beside it, `goh-archive-YYYY.db` (the year of each row's stamp), versioned like every
file (meta, user_version), and ATTACHed read-only by the stats queries. The archive is written and
committed first, then the rows leave the live file: a kill between the two leaves a row in both
(INSERT OR IGNORE on the table's key), never in neither. A cache file (goh-cache.db) evicts them.

BYTES are the table's pages and its indexes' (the dbstat table), or, where SQLite was built
without it, the sum of its columns' lengths; the FILE's are its used pages (page_count minus the
free list), so a sweep's deletions count before the vacuum hands the pages back.
Pinned by tests/test_db_bounds.py.
"""

from __future__ import annotations

import contextlib
import math
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import db_core

HEADER = re.compile(r"^--\s*bound:\s*([A-Za-z_][A-Za-z0-9_]*)\s+(.*)$")
UNITS = {"": 1, "KB": 1 << 10, "MB": 1 << 20, "GB": 1 << 30}
ROUNDS = 6  # passes over a table past its bytes, and over the file's largest tables


@dataclass(frozen=True)
class TableBound:
    days: float | None  # a row older than this goes
    rows: int | None  # the newest ROWS stay
    bytes: int | None  # the table's pages, indexes included
    stamp_col: str  # the column a row's age is read from (epoch seconds)


def size(raw: str) -> int:
    m = re.fullmatch(r"(\d+)\s*([KMG]B)?", raw.strip().upper())
    if not m:
        raise ValueError(f"{raw!r} is not a size (an integer, or N KB / MB / GB)")
    return int(m.group(1)) * UNITS[m.group(2) or ""]


def parse(line: str) -> tuple[str, TableBound] | None:
    """A `-- bound:` header as (table, TableBound), None for another line; a bad one raises."""
    m = HEADER.match(line.strip())
    if not m:
        return None
    kv = dict(part.split("=", 1) for part in m.group(2).split())
    unknown = set(kv) - {"days", "rows", "bytes", "stamp"}
    if unknown or "stamp" not in kv or not {"days", "rows", "bytes"} & set(kv):
        raise ValueError(f"{line.strip()!r}: a bound is days=, rows= and/or bytes=, and stamp=")
    return m.group(1), TableBound(float(kv["days"]) if "days" in kv else None,
                                  int(kv["rows"]) if "rows" in kv else None,
                                  size(kv["bytes"]) if "bytes" in kv else None,
                                  kv["stamp"])  # fmt: skip


def bounds(seam: db_core.Seam) -> dict[str, TableBound]:
    out: dict[str, TableBound] = {}
    for _, p in sorted(db_core.migrations(seam).items()):
        for line in p.read_text(encoding="utf-8").splitlines():
            if got := parse(line):
                out[got[0]] = got[1]
    return out


def tables(con: sqlite3.Connection, schema: str = "main") -> list[str]:
    return [r[0] for r in con.execute(
        f"SELECT name FROM {schema}.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        " ORDER BY name")]  # fmt: skip


def table_bytes(con: sqlite3.Connection, table: str) -> int:
    try:
        got = con.execute("SELECT SUM(pgsize) FROM dbstat WHERE name=? OR name IN (SELECT name "
                          "FROM sqlite_master WHERE type='index' AND tbl_name=?)",
                          (table, table)).fetchone()[0]  # fmt: skip
    except sqlite3.OperationalError:  # no dbstat in this build: the columns' own lengths
        cols = [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]
        expr = " + ".join(f'IFNULL(LENGTH(CAST("{c}" AS BLOB)), 0)' for c in cols) or "0"
        got = con.execute(f'SELECT SUM({expr}) FROM "{table}"').fetchone()[0]
    return int(got or 0)


def used_bytes(con: sqlite3.Connection) -> int:
    pages = con.execute("PRAGMA page_count").fetchone()[0]
    free = con.execute("PRAGMA freelist_count").fetchone()[0]
    return (pages - free) * con.execute("PRAGMA page_size").fetchone()[0]


def _count(con: sqlite3.Connection, table: str) -> int:
    return con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]


def _cutoff(con: sqlite3.Connection, table: str, b: TableBound, now: float,
            extra: int = 0) -> float | None:  # fmt: skip
    """The stamp at or below which TABLE's rows go (its oldest first), None when none go."""
    n, cuts = _count(con, table), []
    if b.days is not None:
        old = now - b.days * 86400 - 1e-6
        if con.execute(f'SELECT 1 FROM "{table}" WHERE "{b.stamp_col}" <= ? LIMIT 1',
                       (old,)).fetchone():  # fmt: skip
            cuts.append(old)
    drop = max(0, n - b.rows) if b.rows is not None else 0
    if b.bytes is not None and n:
        have = table_bytes(con, table)
        if have > b.bytes:
            drop = max(drop, math.ceil(n * (1 - b.bytes / have)))
    drop = min(n, max(drop, extra))
    if drop:
        cuts.append(con.execute(f'SELECT "{b.stamp_col}" FROM "{table}" ORDER BY "{b.stamp_col}" '
                                "LIMIT 1 OFFSET ?", (drop - 1,)).fetchone()[0])  # fmt: skip
    return max(cuts) if cuts else None


def archive_path(seam: db_core.Seam, year: str) -> Path:
    return db_core.path(seam).with_name(f"{seam.name}-archive-{year}.db")


def _archive(con: sqlite3.Connection, seam: db_core.Seam, table: str, year: str) -> Path:
    """The year's archive with TABLE in it (created versioned, columns added as the live grew)."""
    p = archive_path(seam, year)
    db_core.guard(p)
    live = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                       (table,)).fetchone()[0]  # fmt: skip
    arch = sqlite3.connect(p, isolation_level=None, timeout=db_core.busy_ms(seam) / 1000)
    try:
        arch.executescript(db_core.BOOT)
        arch.execute(re.sub(r"^CREATE TABLE\s+(IF NOT EXISTS\s+)?", "CREATE TABLE IF NOT EXISTS ",
                            live, flags=re.IGNORECASE))  # fmt: skip
        have = {r[1] for r in arch.execute(f'PRAGMA table_info("{table}")')}
        for col in [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]:
            if col not in have:
                arch.execute(f'ALTER TABLE "{table}" ADD COLUMN "{col}"')
        uv = con.execute("PRAGMA user_version").fetchone()[0]
        if arch.execute("PRAGMA user_version").fetchone()[0] < uv:
            arch.execute(f"PRAGMA user_version={uv}")
        meta = {"db": f"{seam.name}-archive-{year}", "schema_version": str(uv)}
        arch.executemany("INSERT INTO meta VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET "
                         "value=excluded.value", meta.items())  # fmt: skip
        arch.executemany(
            "INSERT OR IGNORE INTO meta VALUES (?, ?)",
            [("created_by", db_core.writer()), ("created_at", f"{time.time():.3f}")],
        )
    finally:
        arch.close()
    return p


def _drop(con: sqlite3.Connection, seam: db_core.Seam, table: str, stamp: str, cut: float) -> int:
    """TABLE's rows stamped at or before CUT: moved to their year's archive (a history file) or
    evicted (a cache); how many left."""
    where = f'"{stamp}" <= ?'
    if seam.over == "archive":
        years = [
            r[0]
            for r in con.execute(
                f"SELECT DISTINCT strftime('%Y', \"{stamp}\", "
                f"'unixepoch') FROM \"{table}\" WHERE {where}",
                (cut,),
            )
        ]
        for year in years:
            p = _archive(con, seam, table, year)
            cols = ", ".join(f'"{r[1]}"' for r in con.execute(f'PRAGMA table_info("{table}")'))
            con.execute("ATTACH DATABASE ? AS arch", (str(p),))
            try:
                con.execute("BEGIN IMMEDIATE")
                con.execute(f'INSERT OR IGNORE INTO arch."{table}" ({cols}) SELECT {cols} FROM '
                            f"main.\"{table}\" WHERE {where} AND strftime('%Y', \"{stamp}\", "
                            "'unixepoch') = ?", (cut, year))  # fmt: skip
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
            finally:
                con.execute("DETACH DATABASE arch")
    con.execute("BEGIN IMMEDIATE")
    gone = con.execute(f'DELETE FROM "{table}" WHERE {where}', (cut,)).rowcount
    con.execute("COMMIT")
    return gone


def sweep(seam: db_core.Seam, now: float | None = None, budget: int | None = None) -> dict:
    """Each bounded table's oldest rows past its bound, then the file's past BUDGET (the seam's),
    moved or evicted; {table: rows}. A missing file is never created; a newer goh's is left."""
    now = time.time() if now is None else now
    budget = seam.budget if budget is None else budget
    if not db_core.path(seam).is_file():
        return {}
    moved: dict[str, int] = {}
    with db_core.connect(seam) as con:
        if db_core.newer(con, seam):
            return {}
        bound = {t: b for t, b in bounds(seam).items() if t in tables(con)}
        for t, b in bound.items():
            for _ in range(ROUNDS):  # bytes are pages: a pass's estimate can leave one over
                if (cut := _cutoff(con, t, b, now)) is None:
                    break
                moved[t] = moved.get(t, 0) + _drop(con, seam, t, b.stamp_col, cut)
        for _ in range(ROUNDS):
            over = used_bytes(con) - budget
            sized = sorted(
                ((table_bytes(con, t), t) for t in bound if _count(con, t)), reverse=True
            )
            if over <= 0 or not sized:
                break
            have, t = sized[0]
            extra = max(1, math.ceil(_count(con, t) * min(1.0, over / max(have, 1))))
            cut = _cutoff(con, t, TableBound(None, None, None, bound[t].stamp_col), now, extra)
            if cut is None:
                break
            moved[t] = moved.get(t, 0) + _drop(con, seam, t, bound[t].stamp_col, cut)
        con.execute("PRAGMA incremental_vacuum")
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return {t: n for t, n in moved.items() if n}


def unbounded(seam: db_core.Seam, now: float | None = None) -> list[str]:
    """Each table of the seam's file with no bound or past it, and the file past its budget."""
    now = time.time() if now is None else now
    out: list[str] = []
    with contextlib.suppress(FileNotFoundError), db_core.ro(seam) as con:
        known = bounds(seam)
        for t in tables(con):
            if t in db_core.BOOT_TABLES:
                continue
            b = known.get(t)
            if b is None:
                out.append(f"{seam.file}: table {t} has no `-- bound:` header")
            elif _cutoff(con, t, b, now) is not None:
                out.append(f"{seam.file}: table {t} is past its bound {b} ({_count(con, t)} rows,"
                           f" {table_bytes(con, t)} bytes)")  # fmt: skip
        if (used := used_bytes(con)) > seam.budget:
            out.append(f"{seam.file}: {used} bytes used, past its budget of {seam.budget}")
    return out


def seams() -> list[db_core.Seam]:
    import goh_cache_db
    import goh_db

    return [goh_db.SEAM, goh_cache_db.SEAM]


def under(root: Path, state_root: Path) -> list[db_core.Seam]:
    """The seams lib/state_bounds.py keeps for ROOT: every one for goh's state root, else those
    whose file lives in ROOT (a fixture's)."""
    if os.path.realpath(root) == os.path.realpath(state_root):
        return seams()
    return [s for s in seams()
            if os.path.realpath(db_core.path(s).parent) == os.path.realpath(root)]  # fmt: skip
