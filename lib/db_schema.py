"""lib/db_schema.py -- one schema file of goh.db or goh-cache.db: its number, its statements, its
head lines and the one way it is applied (GOH.OLDWRITERS; split from lib/db_core.py).

    -- old-writers: safe | refuse         line 1: may a copy of goh without this file go on writing
    -- applied-by: goh state contract     in the head: no open applies it (lib/db_contract.py does)
    -- with: CHILD of PARENT              CHILD's rows leave with their PARENT row (nested ones too)

    migrations(seam)   every file the seam ships, by number
    statements(sql)    its statements, whole
    old_writers(sql), by_command(sql), withs(sql)   its head lines
    fresh(con, ...)    every file of an empty database, in one transaction, its views and triggers
                       last: each ALTER reads the whole schema again, so 0016's 226 of them after
                       0011's triggers cost 0.8 s of CPU, and 0.02 s before them (GOH.BODYCOLS)
    apply(con, ...)    the statements, the schema_version row and, when safe, the number in
                       meta.old_writers_safe, inside the caller's transaction
    version(con), top(con), ahead(con, seam), stamp_rows(con, ...)   what the file says it holds
    safe_numbers(v)    the numbers a copy that does not ship them may write beside

SAFE IS JUDGED, never believed: lib/db_safe.py. A file with no line 1 of that form is `refuse`.
Pinned by tests/test_db_old_writers.py and tests/test_db_core.py.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from db_core import Seam

MIGRATION = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")
HEAD = re.compile(r"-- old-writers: (safe|refuse)")
WITH = re.compile(r"-- with: ([a-z_][a-z0-9_]*) of ([a-z_][a-z0-9_]*)")
BY_COMMAND = "-- applied-by: goh state contract"
SAFE_KEY = "old_writers_safe"


def migrations(seam: Seam) -> dict[int, Path]:
    """Every migration the seam ships, by number; a malformed name or a number twice raises."""
    out: dict[int, Path] = {}
    for p in sorted(seam.schema.glob("*.sql")):
        m = MIGRATION.match(p.name)
        if not m:
            raise ValueError(f"{p}: a migration is named NNNN_name.sql")
        n = int(m.group(1))
        if n in out:
            raise ValueError(f"{p}: migration {n} is also {out[n].name}")
        out[n] = p
    return out


def statements(sql: str) -> list[str]:
    """SQL's statements, whole (a `;` inside a trigger body or a string stays in its statement)."""
    out, buf = [], ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip():
                out.append(buf.strip())
            buf = ""
    if buf.strip() and not all(ln.lstrip().startswith("--") for ln in buf.strip().splitlines()):
        raise ValueError(f"an unterminated statement: {buf.strip()[:80]!r}")
    return out


def old_writers(sql: str) -> str | None:
    """`safe` or `refuse` as line 1 says it, None without such a line 1."""
    m = HEAD.fullmatch(sql.split("\n", 1)[0].rstrip())
    return m.group(1) if m else None


def by_command(sql: str) -> bool:
    """Whether the head (the comment lines before the first statement) names the command."""
    for line in sql.splitlines():
        if not line.startswith("--"):
            return False
        if line.rstrip() == BY_COMMAND:
            return True
    return False


def withs(sql: str) -> list[tuple[str, str]]:
    """The (child, parent) pairs SQL declares; a malformed `-- with:` raises."""
    out = []
    for line in sql.splitlines():
        if line.startswith("-- with:"):
            m = WITH.fullmatch(line.rstrip())
            if not m:
                raise ValueError(f"{line!r}: the form is `-- with: CHILD of PARENT`")
            out.append((m.group(1), m.group(2)))
    return out


def on_open(known: dict[int, Path], have: set[int]) -> list[int]:
    """The numbers an open applies: shipped, not applied, and not a by-command contract."""
    return [n for n in sorted(set(known) - have)
            if not by_command(known[n].read_text(encoding="utf-8"))]  # fmt: skip


def safe_numbers(meta: dict[str, str]) -> set[int]:
    """meta.old_writers_safe as numbers; a value that is no list of integers is none of them."""
    try:
        got = json.loads(meta.get(SAFE_KEY, "[]"))
    except ValueError:
        return set()
    return {n for n in got if type(n) is int} if isinstance(got, list) else set()


LATE = re.compile(r"(?:--[^\n]*\n|\s)*(?:CREATE|DROP)\s+(?:VIEW|TRIGGER)\b", re.IGNORECASE)


def fresh(con: sqlite3.Connection, files: dict[int, Path], stamp: str, safe: set[int]) -> bool:
    """FILES, all of them, into a file that holds no number; False when it holds one by now."""
    con.execute("BEGIN IMMEDIATE")
    try:
        if con.execute("SELECT 1 FROM schema_version").fetchone():
            con.execute("ROLLBACK")  # another process made it while this one waited
            return False
        late: list[str] = []
        for n, p in sorted(files.items()):
            apply(con, n, p, stamp, n in safe, late)
        for stmt in late:
            con.execute(stmt)
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    return True


def apply(con: sqlite3.Connection, n: int, p: Path, stamp: str, safe: bool,
          late: list[str] | None = None) -> None:  # fmt: skip
    """File P as number N, inside the caller's transaction; its views and triggers onto LATE, when
    the caller runs them after every file (no row is there for a trigger to act on)."""
    for stmt in statements(p.read_text(encoding="utf-8")):
        if late is not None and LATE.match(stmt):
            late.append(stmt)
        else:
            con.execute(stmt)
    con.execute("INSERT INTO schema_version(version, name, applied_at, writer) VALUES (?, ?, ?, ?)",
                (n, p.stem, time.time(), stamp))  # fmt: skip
    if safe:
        meta = dict(con.execute("SELECT key, value FROM meta WHERE key = ?", (SAFE_KEY,)))
        con.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET "
                    "value=excluded.value",
                    (SAFE_KEY, json.dumps(sorted(safe_numbers(meta) | {n}))))  # fmt: skip


def version(con: sqlite3.Connection) -> dict:
    """{user_version, applied, meta} of an open file (an empty file: 0, [], {})."""
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    applied = sorted(r[0] for r in con.execute("SELECT version FROM schema_version")) if (
        "schema_version" in names) else []  # fmt: skip
    meta = dict(con.execute("SELECT key, value FROM meta")) if "meta" in names else {}
    return {"user_version": con.execute("PRAGMA user_version").fetchone()[0],
            "applied": applied, "meta": meta}  # fmt: skip


def ahead(con: sqlite3.Connection, seam: Seam) -> bool:
    """The file holds a number this code does not ship (a safe one or not): its rows may be more
    than this code knows, so it neither sweeps them nor judges their bounds."""
    return bool(set(version(con)["applied"]) - set(migrations(seam)))


def top(con: sqlite3.Connection) -> int:
    """The highest number with every lower one applied."""
    have = {r[0] for r in con.execute("SELECT version FROM schema_version")}
    n = 0
    while n + 1 in have:
        n += 1
    return n


def stamp_rows(con: sqlite3.Connection, seam: Seam, stamp: str, created_by: str) -> None:
    """user_version and meta as the applied rows say, inside the caller's transaction."""
    at = top(con)
    con.execute(f"PRAGMA user_version={at}")
    rows = {"db": seam.name, "schema_version": str(at)}
    if stamp:
        rows["last_migrated_by"] = stamp
    con.executemany("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET "
                    "value=excluded.value", rows.items())  # fmt: skip
    con.executemany(
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        [("created_by", created_by), ("created_at", f"{time.time():.3f}")],
    )
