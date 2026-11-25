"""lib/state_columns.py -- `goh state check`: the rows of goh.db that keep a document beside the
columns it became (GOH.LANDCOLS), each compared with what its columns give back.

    TABLES             lands, ship_pins, ships, agent_spawns (schema files 0011 to 0013)
    rendered(con, t)   (rowid, key, document, the document its columns render, rest) per row of T
                       with a document, from lib/state_sql/doc_T.sql
    same(t, doc, again)   the two say the same: what section 0 of the data plan counts as equal
    check(con)         one line per thing wrong; [] is the state every later step needs
    main()             `goh state check`: exit 1 with any, 2 when there is nothing to read

WHAT IT NAMES. A row with `rest` NULL: no writer's trigger filled it (`goh state backfill` does),
or its document is not JSON. A `rest` that holds a field: a key no column has, or a value of the
wrong type, kept there until it is looked at. A document its columns do not give back. A red cause
not folded into its land. A child row whose parent is gone. A running ship is not compared: its
steps write its record and its pins in turn.

EQUAL, BUT SPELLED ANOTHER WAY: null, {} and [] are an absent key; a land's `at` and a spawn's
`time` are one instant in any spelling; a ship's `repo` is its checkout's path in the document and
the repo's name in the column; `schema` of ships and pins is a constant, not stored.

TWO LANGUAGES: the SQL is files (lib/state_sql), so the Rust binary can read the same ones.
Pinned by tests/test_state_columns.py.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db_bounds_with  # lib/, on the path from the line above
import db_schema
import goh_db

SQL = Path(__file__).resolve().with_name("state_sql")
TABLES = ("lands", "ship_pins", "ships", "agent_spawns")
INSTANT = {"lands": "at", "agent_spawns": "time"}
IN_FLIGHT = "status IN ('running', 'bisecting')"  # a ship its worker still writes
# a red cause no document of its land has, which the fold would put there (backfill.sql's second)
UNFOLDED = """SELECT l.rowid FROM red_causes r JOIN lands l ON l.queue = r.queue AND l.run = r.run
WHERE l.red_class IS NULL AND l.body <> '{}' AND CASE WHEN json_valid(r.body) THEN
json_type(r.body) = 'object' END AND CASE WHEN json_valid(l.body) THEN
coalesce(json_type(l.body, '$.red_cause'), 'null') = 'null' END"""


def columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]


def key(con: sqlite3.Connection, table: str) -> list[str]:
    """TABLE's primary key columns, in the key's order."""
    got = [(r[5], r[1]) for r in con.execute(f'PRAGMA table_info("{table}")') if r[5]]
    return [name for _, name in sorted(got)]


def migrated(con: sqlite3.Connection) -> bool:
    """Whether every table has its columns (an older file has none of them)."""
    return all("rest" in columns(con, t) for t in TABLES)


def rendered(
    con: sqlite3.Connection, table: str, where: str = "1", args: tuple = ()
) -> Iterator[tuple[int, str, str, str, str | None]]:
    """(rowid, key, document, the one its columns render, rest) per row with a document that is
    JSON and WHERE (over alias `x`) takes, in rowid order."""
    doc = (SQL / f"doc_{table}.sql").read_text(encoding="utf-8")
    names = " || ' ' || ".join(f"x.{k}" for k in key(con, table))
    head, _, tail = doc.partition("x.body,")
    sql = (f"SELECT x.rowid, {names}, x.body,{tail}\nWHERE x.body <> '{{}}' AND json_valid(x.body) "
           f"AND {where} ORDER BY x.rowid")  # fmt: skip
    if not head.startswith("SELECT "):
        raise ValueError(f"doc_{table}.sql: not SELECT key, x.body, its rendering AS doc")
    sql = sql.replace(" AS doc\nFROM", " AS doc, x.rest\nFROM", 1)
    yield from con.execute(sql, args)


def _bare(v: Any) -> Any:
    """V without the keys that say nothing (null, {}, [])."""
    if isinstance(v, dict):
        kept = {k: _bare(x) for k, x in v.items()}
        return {k: x for k, x in kept.items() if x is not None and x != {} and x != []}
    return [_bare(x) for x in v] if isinstance(v, list) else v


def _instant(v: Any) -> Any:
    if isinstance(v, str):
        with contextlib.suppress(ValueError):
            at = datetime.fromisoformat(v)  # no offset is UTC, as SQLite reads it
            return int((at if at.tzinfo else at.replace(tzinfo=UTC)).timestamp() // 1)
    return v


def _spelled(table: str, doc: Any) -> Any:
    doc = _bare(json.loads(doc))
    if isinstance(doc, dict):
        if table in INSTANT and INSTANT[table] in doc:
            doc[INSTANT[table]] = _instant(doc[INSTANT[table]])
        if table in ("ships", "ship_pins"):
            doc.pop("schema", None)
    return doc


def _typed(a: Any, b: Any) -> bool:
    """A and B are equal, and a true is no 1 (Python's == says it is)."""
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_typed(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(map(_typed, a, b))
    return isinstance(a, bool) == isinstance(b, bool) and bool(a == b)


def differs(table: str, doc: str, again: str) -> list[str]:
    """The top keys the document and its rendering differ in; [] when they say the same."""
    a, b = _spelled(table, doc), _spelled(table, again)
    if not (isinstance(a, dict) and isinstance(b, dict)):
        return [] if _typed(a, b) else ["the whole document"]
    if table == "ships" and isinstance(a.get("repo"), str) and isinstance(b.get("repo"), str):
        path, name = a["repo"].replace("/", "_"), b["repo"].replace("/", "_")
        if name and (path == name or path.endswith(f"_{name}")):
            a, b = ({k: v for k, v in d.items() if k != "repo"} for d in (a, b))
    return sorted(k for k in a.keys() | b.keys() if k not in a or k not in b
                  or not _typed(a[k], b[k]))  # fmt: skip


def same(table: str, doc: str, again: str) -> bool:
    return not differs(table, doc, again)


def unfilled(con: sqlite3.Connection, table: str) -> tuple[int, list[str]]:
    """(rows the backfill will fill, the keys of rows whose document is not JSON)."""
    names = " || ' ' || ".join(key(con, table))
    n = con.execute(f"SELECT count(*) FROM {table} WHERE rest IS NULL AND json_valid(body)")
    bad = con.execute(f"SELECT {names} FROM {table} WHERE NOT json_valid(body)")
    return n.fetchone()[0], [str(k) for (k,) in bad]


def orphans(con: sqlite3.Connection) -> list[str]:
    """One line per child table holding rows whose parent row is gone (no foreign keys, R10)."""
    out, kin = [], db_bounds_with.withs(goh_db.SEAM)
    have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for parent, child in sorted((p, c) for p, kids in kin.items() for c in kids):
        if {parent, child} <= have:
            join = " AND ".join(f"p.{k} = c.{k}" for k in key(con, parent))
            n = con.execute(f"SELECT count(*) FROM {child} c WHERE NOT EXISTS "
                            f"(SELECT 1 FROM {parent} p WHERE {join})").fetchone()[0]  # fmt: skip
            out += [f"{child}: {n} of its rows have no {parent} row"] * bool(n)
    return out


def check(con: sqlite3.Connection) -> list[str]:
    """Every line `goh state check` says (module doc)."""
    if not migrated(con):
        return ["goh.db has no columns for its documents yet: no schema file 0011 to 0013"]
    out: list[str] = []
    for t in TABLES:
        n, bad = unfilled(con, t)
        out += [f"{t}: {n} rows not filled (`rest` NULL): goh state backfill"] * bool(n)
        out += [f"{t}: {k}: its document is not JSON" for k in bad]
        names = " || ' ' || ".join(key(con, t))
        held = con.execute(f"SELECT {names}, rest FROM {t} WHERE rest <> '{{}}' ORDER BY rowid")
        out += [f"{t}: {k}: `rest` holds {rest[:120]}" for k, rest in held]
        flight = f"NOT coalesce(x.{IN_FLIGHT}, 0) AND " if t == "ships" else ""
        for _, k, doc, again, _ in rendered(con, t, f"{flight}x.rest IS NOT NULL"):
            if ks := differs(t, doc, again):
                out.append(f"{t}: {k}: its columns do not give back {', '.join(ks)}")
    if n := len(con.execute(UNFOLDED).fetchall()):
        out.append(f"lands: {n} red causes not folded in: goh state backfill")
    return out + orphans(con)


def main() -> int:
    from tui.lib import err, ok

    try:
        with goh_db.known() as con:
            con.execute("BEGIN")  # one snapshot: a writer between two reads is no mismatch
            found = check(con)
            n = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in TABLES}
    except (OSError, sqlite3.Error) as e:
        err(f"state check: not read: {e}")
        return 2
    for line in found:
        err(f"state check: {line}")
    if not found:
        ok("state check: " + ", ".join(f"{v} {t}" for t, v in n.items())
           + ": every one's columns give its document back")  # fmt: skip
    return 1 if found else 0


def writable() -> None:
    """Raise unless this copy may move rows in the file (a shared one: main's code alone)."""
    import db_core

    p = goh_db.path()
    if not p.is_file():
        raise goh_db.Unknown(f"no goh.db at {p}")
    if db_core.shared(p) and not db_core.of_record():
        raise db_core.NotOfRecord(f"{p}: only the code of record moves the shared file's rows "
                                  f"({db_core.why_not_of_record()})")  # fmt: skip
    with goh_db.connect() as con:  # migrates; a newer goh's file is read-only
        if not migrated(con):
            raise sqlite3.OperationalError(f"goh.db holds {db_schema.version(con)['applied']}: "
                                           "no schema file 0011 to 0013")  # fmt: skip
