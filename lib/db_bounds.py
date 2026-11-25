"""lib/db_bounds.py -- every table of goh's SQLite files bounded, and each file under a budget
(GOH.DBSEAM; owner 2026-11-23: "need also size limit").

    -- bound: TABLE days=14 rows=5000 bytes=64MB stamp=at     a schema file's header line

    TableBound      days, rows and bytes a table keeps, judged by STAMP (epoch seconds); a later
                    migration's header for the same table replaces an earlier one
    bounds(seam)    {table: TableBound} from the seam's schema files
    sweep(seam)     each table past its bound gives up its OLDEST rows, then the file past its
                    budget (the seam's, goh.db 256 MB, goh-cache.db 2 GB) gives up more from its
                    largest tables; then incremental_vacuum and wal_checkpoint(TRUNCATE)
    unbounded(seam) one line per table with no bound, past its bound, or the file past its budget;
                    on a newer goh's file, not the tables its own migrations created

WHERE THE ROWS GO. A history file (goh.db: lands, ships) loses nothing: its rows, and their `--
with:` children's, move to a yearly archive beside it, `goh-archive-YYYY.db` (the year of each
row's stamp) or its next part, versioned like every file (meta, user_version); the archive commits
first (lib/db_bounds_with.py). A cache file (goh-cache.db) evicts them. A `-- with:` child needs no
bound of its own: it leaves with its parent's rows, and its bytes count as the parent's.

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

import db_bounds_with
import db_core
from db_bounds_with import archive_path, parts  # noqa: F401 -- this module's API too

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


CREATES = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`]?([A-Za-z_][A-Za-z0-9_]*)",
                     re.IGNORECASE)  # fmt: skip


def created(seam: db_core.Seam) -> set[str]:
    """The tables this goh's own migrations create (BOOT's included)."""
    out = set(db_core.BOOT_TABLES)
    for p in db_core.migrations(seam).values():
        out |= set(CREATES.findall(p.read_text(encoding="utf-8")))
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


def _weight(con: sqlite3.Connection, seam: db_core.Seam, table: str) -> int:
    """TABLE's bytes and its `-- with:` children's that have no bound of their own."""
    own = bounds(seam)
    kids = [k for k in db_bounds_with.kids(con, seam, table) if k not in own]
    return sum(table_bytes(con, t) for t in (table, *kids))


def _cutoff(con: sqlite3.Connection, seam: db_core.Seam, table: str, b: TableBound, now: float,
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
        have = _weight(con, seam, table)
        if have > b.bytes:
            drop = max(drop, math.ceil(n * (1 - b.bytes / have)))
    drop = min(n, max(drop, extra))
    if drop:
        cuts.append(con.execute(f'SELECT "{b.stamp_col}" FROM "{table}" ORDER BY "{b.stamp_col}" '
                                "LIMIT 1 OFFSET ?", (drop - 1,)).fetchone()[0])  # fmt: skip
    return max(cuts) if cuts else None


def sweep(seam: db_core.Seam, now: float | None = None, budget: int | None = None) -> dict:
    """Each bounded table's oldest rows past its bound, then the file's past BUDGET (the seam's),
    moved or evicted; {table: rows}. A missing file is never created; one holding a number this
    goh does not ship is left, a safe one's too (GOH.OLDWRITERS)."""
    now = time.time() if now is None else now
    budget = seam.budget if budget is None else budget
    if not db_core.path(seam).is_file():
        return {}
    moved: dict[str, int] = {}
    with db_core.connect(seam) as con:
        if db_core.ahead(con, seam):  # its rows may have children this goh does not know
            return {}
        bound = {t: b for t, b in bounds(seam).items() if t in tables(con)}
        for t, b in bound.items():
            for _ in range(ROUNDS):  # bytes are pages: a pass's estimate can leave one over
                if (cut := _cutoff(con, seam, t, b, now)) is None:
                    break
                moved[t] = moved.get(t, 0) + db_bounds_with.drop(con, seam, t, b.stamp_col, cut)
        for _ in range(ROUNDS):
            over = used_bytes(con) - budget
            sized = sorted(
                ((_weight(con, seam, t), t) for t in bound if _count(con, t)), reverse=True
            )
            if over <= 0 or not sized:
                break
            have, t = sized[0]
            extra = max(1, math.ceil(_count(con, t) * min(1.0, over / max(have, 1))))
            cut = _cutoff(
                con, seam, t, TableBound(None, None, None, bound[t].stamp_col), now, extra
            )
            if cut is None:
                break
            moved[t] = moved.get(t, 0) + db_bounds_with.drop(con, seam, t, bound[t].stamp_col, cut)
        con.execute("PRAGMA incremental_vacuum")
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return {t: n for t, n in moved.items() if n}


def unbounded(seam: db_core.Seam, now: float | None = None) -> list[str]:
    """Each table of the seam's file with no bound or past it, and the file past its budget."""
    now = time.time() if now is None else now
    out: list[str] = []
    with contextlib.suppress(FileNotFoundError), db_core.ro(seam) as con:
        known = bounds(seam)
        with_kids = {k for t in known for k in db_bounds_with.kids(con, seam, t)}
        # A newer goh's file: the tables its migrations created are bounded by headers this goh
        # does not ship, and are that goh's to judge (an older release called 0006's three
        # tables unbounded, GOH.DURATIONSDB).
        mine = created(seam) if db_core.ahead(con, seam) else None
        for t in tables(con):
            if t in db_core.BOOT_TABLES or (mine is not None and t not in mine):
                continue
            b = known.get(t)
            if b is None and t in with_kids:  # it leaves with its parent's rows
                continue
            if b is None:
                out.append(f"{seam.file}: table {t} has no `-- bound:` header")
            elif _cutoff(con, seam, t, b, now) is not None:
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
