"""lib/db_bounds_with.py -- the rows a bound takes from a table, and where they go: the table's
`-- with:` children with them, into an archive part of the same shape (GOH.OLDWRITERS; lib/
db_bounds.py decides which rows).

    family(con, seam, t)     T and the child tables that leave with it, nested ones too, deepest
                             first, each with the WHERE that finds its leaving rows
    kids(con, seam, t)       those children's names
    parts(seam, year)        goh-archive-YYYY.db, then -b, -c, ...: every path a year may use
    drop(con, seam, t, ...)  the rows at or before CUT, archived (a history file) or evicted

ONE WRITE LOCK, TWO COMMITS. The live file's write lock is held throughout, so no row is added or
changed between the copy and the delete. The archive commits first: a kill between the two leaves
a row in both files, never in neither, and the next sweep finds it equal and deletes it.

NOTHING LEAVES THAT IS NOT THERE. Until GOH.OLDWRITERS the copy was INSERT OR IGNORE, which also
skips a row the archive's table REFUSES (a NOT NULL column the live table dropped), and the live
row was deleted all the same: 606 of 606 on a copy of the real file. Now the copy is ON CONFLICT
DO NOTHING (a refusal raises), each leaving row is looked up in the archive, whole, before the
part commits, and a part that lacks one is rolled back and the next part tried.

A PART'S SHAPE IS FIXED (L7). A part holding the table with any other column, or without one of
the table's children as the live file has it, is closed for that table; the next part opens with
the live shape. So a file that gained columns or child tables never drops them into an older part.
Pinned by tests/test_db_bounds_with.py.
"""

from __future__ import annotations

import contextlib
import re
import sqlite3
import string
import time
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING

import db_core
import db_schema

if TYPE_CHECKING:
    from db_core import Seam


OF_C = re.compile(r"\bc\.")  # a column of the alias `c`


@dataclass(frozen=True)
class Member:
    table: str
    where: str  # over alias `c` of this table, `{live}` the schema it is read from


def withs(seam: Seam) -> dict[str, list[str]]:
    """{parent: its children}, as the shipped files declare them."""
    out: dict[str, list[str]] = {}
    for _, p in sorted(db_schema.migrations(seam).items()):
        for child, parent in db_schema.withs(p.read_text(encoding="utf-8")):
            if child not in out.setdefault(parent, []):
                out[parent].append(child)
    return out


def _tables(con: sqlite3.Connection, schema: str = "main") -> set[str]:
    return {r[0] for r in con.execute(
        f"SELECT name FROM {schema}.sqlite_master WHERE type='table'")}  # fmt: skip


def _paths(con: sqlite3.Connection, seam: Seam, table: str) -> list[tuple[str, ...]]:
    """Every chain from TABLE down its declared children that this file has."""
    have, kin, out = _tables(con), withs(seam), []
    todo: list[tuple[str, ...]] = [(table,)]
    while todo:
        path = todo.pop()
        out.append(path)
        todo += [(*path, k) for k in kin.get(path[-1], []) if k in have and k not in path]
    return out


def kids(con: sqlite3.Connection, seam: Seam, table: str) -> list[str]:
    return sorted({p[-1] for p in _paths(con, seam, table)} - {table})


def _key(con: sqlite3.Connection, parent: str, child: str) -> list[str]:
    """PARENT's primary key columns, all of which CHILD carries."""
    key = [r[1] for r in con.execute(f'PRAGMA table_info("{parent}")') if r[5]]
    kid = {r[1] for r in con.execute(f'PRAGMA table_info("{child}")')}
    if not key or not set(key) <= kid:
        raise ValueError(f"-- with: {child} of {parent}: {child} does not carry {parent}'s key")
    return key


def _where(con: sqlite3.Connection, path: tuple[str, ...], root: str) -> str:
    """The WHERE over alias `c` of PATH's last table: its PATH's first row satisfies ROOT (over
    alias `c` too)."""
    out = root
    for i, (up, down) in enumerate(pairwise(path)):
        join = " AND ".join(f'p{i}."{k}" = c."{k}"' for k in _key(con, up, down))
        out = (f'EXISTS (SELECT 1 FROM {{live}}."{up}" p{i} WHERE {join} AND '
               f"({OF_C.sub(f'p{i}.', out)}))")  # fmt: skip
    return out


def family(con: sqlite3.Connection, seam: Seam, table: str, root: str) -> list[Member]:
    """TABLE's leaving rows (ROOT, over alias `c`) and its children's, deepest table first."""
    paths = _paths(con, seam, table)
    depth = {t: max(len(p) for p in paths if p[-1] == t) for t in {p[-1] for p in paths}}
    return [Member(t, " OR ".join(f"({_where(con, p, root)})" for p in paths if p[-1] == t))
            for t in sorted(depth, key=lambda t: (-depth[t], t))]  # fmt: skip


def archive_path(seam: Seam, year: str, part: str = "") -> Path:
    tail = f"-{part}" if part else ""
    return db_core.path(seam).with_name(f"{seam.name}-archive-{year}{tail}.db")


def parts(seam: Seam, year: str) -> list[Path]:
    return [archive_path(seam, year, p) for p in ("", *string.ascii_lowercase[1:])]


def shape(con: sqlite3.Connection, table: str) -> frozenset[tuple]:
    """TABLE's columns, whatever their order; empty when there is no such table."""
    return frozenset(tuple(r[1:]) for r in con.execute(f'PRAGMA main.table_xinfo("{table}")'))


def _columns(con: sqlite3.Connection, table: str) -> str:
    return ", ".join(f'"{r[1]}"' for r in con.execute(f'PRAGMA main.table_xinfo("{table}")')
                     if not r[6])  # fmt: skip


def _fits(arch: sqlite3.Connection, live: sqlite3.Connection, root: str, names: list[str]) -> bool:
    """Whether a part may take ROOT's family: each member it holds has the live columns, and one
    that holds ROOT already holds every member."""
    has = {t: shape(arch, t) for t in names}
    if any(s and s != shape(live, t) for t, s in has.items()):
        return False
    return not has[root] or all(has.values())


def _boot(arch: sqlite3.Connection, live: sqlite3.Connection, names: list[str], name: str,
          stamp: str) -> None:  # fmt: skip
    """The members the part lacks, as the live file has them, and its version rows."""
    have = _tables(arch)
    new = [t for t in names if t not in have]
    for t in new:
        arch.execute(live.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                                  (t,)).fetchone()[0])  # fmt: skip
    if not new:
        return
    uv = live.execute("PRAGMA user_version").fetchone()[0]
    uv = max(uv, arch.execute("PRAGMA user_version").fetchone()[0])
    arch.execute(f"PRAGMA user_version={uv}")
    arch.executemany("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET "
                     "value=excluded.value",
                     [("db", name), ("schema_version", str(uv))])  # fmt: skip
    arch.executemany("INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
                     [("created_by", stamp), ("created_at", f"{time.time():.3f}")])  # fmt: skip


@contextlib.contextmanager
def _part(seam: Seam, p: Path) -> Iterator[sqlite3.Connection]:
    """Part P read-write, the live file attached read-only as `live`."""
    db_core.guard(p)
    arch = sqlite3.connect(p, isolation_level=None, timeout=db_core.busy_ms(seam) / 1000, uri=True)
    try:
        arch.execute("ATTACH DATABASE ? AS live", (f"{db_core.path(seam).as_uri()}?mode=ro",))
        arch.executescript(db_core.BOOT)
        yield arch
    finally:
        arch.close()


def _copy(arch: sqlite3.Connection, live: sqlite3.Connection, fam: list[Member],
          args: dict) -> bool:  # fmt: skip
    """FAM's leaving rows into the part; False when one of them is not there afterwards."""
    for m in fam:
        cols, where = _columns(live, m.table), m.where.format(live="live")
        rows = f'SELECT {cols} FROM live."{m.table}" c WHERE {where}'
        arch.execute(f'INSERT INTO main."{m.table}" ({cols}) {rows} ON CONFLICT DO NOTHING', args)
        if arch.execute(f'{rows} EXCEPT SELECT {cols} FROM main."{m.table}" LIMIT 1',
                        args).fetchone():  # fmt: skip
            return False
    return True


def archive(con: sqlite3.Connection, seam: Seam, table: str, fam: list[Member], args: dict,
            stamp: str) -> Path:  # fmt: skip
    """FAM's rows of one year committed to the first part that holds them all afterwards."""
    names = [m.table for m in fam]
    for p in parts(seam, args["year"]):
        with _part(seam, p) as arch:
            if not _fits(arch, con, table, names):
                continue
            arch.execute("BEGIN")
            try:
                _boot(arch, con, names, p.name.removesuffix(".db"), stamp)
                kept = _copy(arch, con, fam, args)
            except sqlite3.IntegrityError:  # the part refuses a row (a constraint the live lost)
                kept = False
            except BaseException:
                arch.execute("ROLLBACK")
                raise
            arch.execute("COMMIT" if kept else "ROLLBACK")
            if kept:
                return p
    raise sqlite3.IntegrityError(f"{seam.file}: no archive part of {args['year']} takes {table}'s "
                                 "rows: kept in the live file")  # fmt: skip


def drop(con: sqlite3.Connection, seam: Seam, table: str, stamp_col: str, cut: float) -> int:
    """TABLE's rows stamped at or before CUT, and their children: moved to their year's archive (a
    history file) or evicted (a cache); how many of TABLE's left."""
    root = f'c."{stamp_col}" <= :cut'
    year = f"strftime('%Y', c.\"{stamp_col}\", 'unixepoch')"
    by = db_core.writer() if seam.over == "archive" else ""  # a subprocess: before the lock
    con.execute("BEGIN IMMEDIATE")
    try:
        if seam.over == "archive":
            fam = family(con, seam, table, f"{root} AND {year} = :year")
            for (y,) in con.execute(f'SELECT DISTINCT {year} FROM "{table}" c WHERE {root}',
                                    {"cut": cut}).fetchall():  # fmt: skip
                archive(con, seam, table, fam, {"cut": cut, "year": y}, by)
        gone = 0
        for m in family(con, seam, table, root):
            gone = con.execute(f'DELETE FROM main."{m.table}" AS c WHERE '
                               f"{m.where.format(live='main')}", {"cut": cut}).rowcount  # fmt: skip
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    return gone
