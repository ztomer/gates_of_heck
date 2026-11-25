"""lib/db_safe.py -- is a schema file that says `-- old-writers: safe` safe (GOH.OLDWRITERS)?

    faults(seam, n)    why number N's file is not what its head says, one line each ([]: it is)
    check(seam)        every shipped file's, and a head missing on a number past HEADLESS

SAFE IS ADDITIVE. A copy of goh without the file keeps writing through it, so the file holds only:
  CREATE TABLE, CREATE VIEW; CREATE INDEX (a UNIQUE one only on a table the file creates);
  ALTER TABLE ADD COLUMN whose CHECK reads no other column of its table (an old writer's row never
  fills the new one, so a CHECK over an old column would refuse it), with no REFERENCES, UNIQUE or
  generated value;
  DROP VIEW with a CREATE VIEW of the name later in the file, the ORDERED columns equal;
  an AFTER INSERT or AFTER UPDATE OF body trigger that writes only its own row (keyed by the whole
  primary key, setting only columns the file adds) and child tables of the file, keyed by NEW;
  an AFTER DELETE trigger that deletes only child rows keyed by OLD.
DROP of anything but a same-shape view is never safe. A child table is one the file creates, or
one its `-- with:` lines say leaves with the trigger's table.

THE VIEWS ARE COMPARED IN A DATABASE, never in text (L11): the earlier files are replayed in
memory, each view's columns read in order, this file run, and every view that was there compared:
so a view the file drops, and a `SELECT *` view over a table it widens, are both seen.
Pinned by tests/test_db_safe.py.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import db_schema

if TYPE_CHECKING:
    from db_core import Seam

HEADLESS = {"goh": 10, "goh-cache": 3}  # the last number each file shipped before heads
ID = r'["`\[]?([A-Za-z_][A-Za-z0-9_]*)["`\]]?'
SP = re.compile(r"\s+")
CREATE_TABLE = re.compile(rf"CREATE TABLE (?:IF NOT EXISTS )?{ID}", re.IGNORECASE)
CREATE_INDEX = re.compile(rf"CREATE (UNIQUE )?INDEX (?:IF NOT EXISTS )?{ID} ON {ID} ?\(",
                          re.IGNORECASE)  # fmt: skip
CREATE_VIEW = re.compile(rf"CREATE VIEW (?:IF NOT EXISTS )?{ID}", re.IGNORECASE)
DROP_VIEW = re.compile(rf"DROP VIEW (?:IF EXISTS )?{ID}$", re.IGNORECASE)
ADD = re.compile(rf"ALTER TABLE {ID} ADD (?:COLUMN )?{ID}(.*)$", re.IGNORECASE)
TRIGGER = re.compile(
    rf"CREATE TRIGGER (?:IF NOT EXISTS )?{ID} AFTER (INSERT|DELETE|UPDATE OF body) ON {ID} "
    r"(?:FOR EACH ROW )?(?:WHEN .+? )?BEGIN (.*) END$",
    re.IGNORECASE,
)
INSERT = re.compile(rf"INSERT (?:OR \w+ )?INTO {ID} ?(.*)$", re.IGNORECASE)
UPDATE = re.compile(rf"UPDATE (?:OR \w+ )?{ID} SET (.*)$", re.IGNORECASE)
DELETE = re.compile(rf"DELETE FROM {ID}(.*)$", re.IGNORECASE)


def flat(sql: str) -> str:
    """SQL on one line with its comments gone and each string's text blanked, so a word in either
    is never read as SQL."""
    out, i = [], 0
    while i < len(sql):
        two, c = sql[i : i + 2], sql[i]
        if two in ("--", "/*"):
            end = sql.find("\n" if two == "--" else "*/", i)
            i = len(sql) if end < 0 else end + (1 if two == "--" else 2)
            out.append(" ")
        elif c in "'\"`":
            j = i + 1
            while j < len(sql) and (sql[j] != c or sql[j : j + 2] == c * 2):
                j += 2 if sql[j] == c else 1
            out.append("'s'" if c == "'" else sql[i : j + 1])
            i = j + 1
        else:
            out.append(c)
            i += 1
    return SP.sub(" ", "".join(out)).strip().rstrip(";").strip()


def split(text: str, sep: str) -> list[str]:
    """TEXT split at SEP outside parentheses."""
    out, depth, at = [], 0, 0
    for m in re.finditer(rf"[()]|{re.escape(sep)}", text, re.IGNORECASE):
        if m.group() in "()":
            depth += 1 if m.group() == "(" else -1
        elif depth == 0:
            out.append(text[at : m.start()])
            at = m.end()
    return [*out, text[at:]]


def where_keys(text: str, row: str) -> set[str] | None:
    """The columns TEXT's one top-level WHERE ties to ROW (NEW or OLD): `col = NEW.col` conjuncts.
    None for a WHERE this cannot judge (an OR or a CASE at its top level) or none at all."""
    parts = split(text, " WHERE ")
    if len(parts) != 2 or len(split(parts[1], " OR ")) > 1:
        return None
    if re.search(r"\b(CASE|LIMIT|RETURNING)\b", "".join(split_out(parts[1])), re.IGNORECASE):
        return None
    keys = set()
    for c in split(parts[1], " AND "):
        m = re.fullmatch(rf"(\w+) ?(?:=|IS) ?{row}\.(\w+)", c.strip(), re.IGNORECASE)
        if m and m.group(1) == m.group(2):
            keys.add(m.group(1))
    return keys


def split_out(text: str) -> list[str]:
    """TEXT's characters outside parentheses."""
    out, depth = [], 0
    for c in text:
        depth += (c == "(") - (c == ")")
        if depth == 0:
            out.append(c)
    return out


@dataclass
class File:
    """What one file adds, and the database as it stands before the statement being judged."""

    con: sqlite3.Connection
    withs: list[tuple[str, str]]
    later: list[str] = field(default_factory=list)  # the views created after this statement
    made: set[str] = field(default_factory=set)  # tables the file creates
    added: set[tuple[str, str]] = field(default_factory=set)  # (table, column) it adds

    def key(self, table: str) -> set[str]:
        got = {r[1] for r in self.con.execute(f'PRAGMA table_info("{table}")') if r[5]}
        return got or {"rowid"}

    def columns(self, table: str) -> set[str]:
        return {r[1] for r in self.con.execute(f'PRAGMA table_info("{table}")')}

    def child(self, table: str, parent: str) -> bool:
        return table != parent and (table in self.made or (table, parent) in self.withs)


def _refuses_its_default(raw: str, table: str, col: str) -> str | None:
    """A row that leaves the column out, as an old writer's does: why it is refused, or None."""
    m = re.search(rf"ADD (?:COLUMN )?{ID}(.*?);?\s*$", raw, re.IGNORECASE | re.DOTALL)
    con = sqlite3.connect(":memory:")
    try:
        con.execute(f'CREATE TABLE t(x, "{col}" {m.group(2) if m else ""})')
        con.execute("INSERT INTO t(x) VALUES (1)")
    except sqlite3.Error as e:
        return f"{table}.{col} refuses a row that leaves it out: {e}"
    finally:
        con.close()
    return None


def _add_column(f: File, table: str, col: str, rest: str, raw: str) -> str | None:
    bad = re.search(r"\b(REFERENCES|UNIQUE|GENERATED|AS|PRIMARY)\b", rest, re.IGNORECASE)
    if bad:
        return f"{table}.{col} is added with {bad.group(1).upper()}"
    words = {w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", rest)}
    other = sorted(c for c in f.columns(table) - {col} if c.lower() in words - {"text", "real"})
    if other:
        return f"{table}.{col}'s constraint reads {', '.join(other)}, which an old writer fills"
    f.added.add((table, col))
    return _refuses_its_default(raw, table, col)


def _insert_own(f: File, table: str, rest: str) -> str | None:
    """INSERT of the trigger's own row: named columns, the key's values NEW's."""
    m = re.match(r"\(([^()]*)\) VALUES ?\((.*)\)$", rest)
    cols = [c.strip() for c in m.group(1).split(",")] if m else []
    vals = [v.strip() for v in split(m.group(2), ",")] if m else []
    got = dict(zip(cols, vals, strict=False))
    if len(cols) != len(vals) or any(
        got.get(k, "").lower() != f"new.{k}".lower() for k in f.key(table)
    ):
        return f"it inserts into {table} a row that is not its own"
    return None


def _write(f: File, table: str, event: str, stmt: str) -> str | None:
    """Why a trigger of TABLE on EVENT may not hold STMT, or None."""
    row = "OLD" if event == "DELETE" else "NEW"
    if m := INSERT.match(stmt):
        target, rest = m.group(1), m.group(2)
        if event != "DELETE" and f.child(target, table):
            return None if re.search(r"\bNEW\.", rest) else f"it fills {target} with no NEW value"
        if event != "DELETE" and target == table:
            return _insert_own(f, table, rest)
    elif m := (UPDATE.match(stmt) or DELETE.match(stmt)):
        target, rest = m.group(1), m.group(2)
        keys = where_keys(rest, row)
        if f.child(target, table) and (event != "DELETE" or DELETE.match(stmt)):
            return None if keys else f"it writes {target} rows not keyed by {row}"
        if event != "DELETE" and target == table:
            if keys is None or not f.key(table) <= keys:
                return f"it writes {table} rows not keyed to its own ({', '.join(f.key(table))})"
            if UPDATE.match(stmt):
                sets = [s.split("=")[0].strip() for s in split(split(rest, " WHERE ")[0], ",")]
                old = [c for c in sets if (table, c) not in f.added]
                return f"it sets {', '.join(old)}, which the file does not add" if old else None
            return None
    else:
        return f"it runs {stmt[:60]!r}"
    return f"it writes {target}"


def _trigger(f: File, sql: str) -> str | None:
    m = TRIGGER.match(sql)
    if not m:
        return f"{sql[:80]!r} is no AFTER INSERT / UPDATE OF body / DELETE trigger"
    name, event, table, body = m.group(1), m.group(2).upper(), m.group(3), m.group(4)
    for stmt in (s.strip() for s in split(body, ";")):
        if stmt and (why := _write(f, table, event, stmt)):
            return f"trigger {name}: {why}"
    return None


def statement(f: File, sql: str, raw: str) -> str | None:
    """Why one statement of a safe file (SQL: `flat`; RAW: as written) is not additive, or None."""
    if m := CREATE_TABLE.match(sql):
        if f.columns(m.group(1)):
            return f"CREATE TABLE {m.group(1)}, which is there already"
        f.made.add(m.group(1))
        return None
    if m := CREATE_INDEX.match(sql):
        if m.group(1) and m.group(3) not in f.made:
            return f"a UNIQUE index on {m.group(3)} can refuse an old writer's row"
        return None
    if CREATE_VIEW.match(sql):
        return None
    if m := DROP_VIEW.match(sql):
        return None if m.group(1) in f.later else f"DROP VIEW {m.group(1)}, never created again"
    if m := ADD.match(sql):
        return _add_column(f, m.group(1), m.group(2), m.group(3), raw)
    if sql.upper().startswith("CREATE TRIGGER "):
        return _trigger(f, sql)
    return f"{sql[:60]!r} is not additive"


def shapes(con: sqlite3.Connection) -> dict[str, list[str]]:
    """Each view's column names, in order."""
    names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='view'")]
    return {v: [r[1] for r in con.execute(f'PRAGMA table_info("{v}")')] for v in names}


def _with_faults(con: sqlite3.Connection, pairs: list[tuple[str, str]]) -> list[str]:
    """A `-- with:` whose child does not carry its parent's key: its rows could not follow."""
    out = []
    for child, parent in pairs:
        kid = {r[1] for r in con.execute(f'PRAGMA table_info("{child}")')}
        rows = list(con.execute(f'PRAGMA table_info("{parent}")'))
        key = {r[1] for r in rows if r[5]}
        if not kid or not key or not key <= kid:
            out.append(f"-- with: {child} of {parent}: {child} does not carry {parent}'s key")
    return out


def judged(con: sqlite3.Connection, p: Path) -> list[str]:
    """Why file P is not what its head says ([] when it is); CON, the files before it, gains it."""
    sql = p.read_text(encoding="utf-8")
    safe = db_schema.old_writers(sql) == "safe"
    out: list[str] = []
    if safe and db_schema.by_command(sql):
        out.append("a file the contract command applies is never safe")
    try:
        before, f = shapes(con), File(con, db_schema.withs(sql))
        raws = db_schema.statements(sql)
        todo = [flat(s) for s in raws] if safe else [""] * len(raws)
        for i, (stmt, raw) in enumerate(zip(todo, raws, strict=True)):
            f.later = [m.group(1) for s in todo[i + 1 :] if (m := CREATE_VIEW.match(s))]
            if safe and (why := statement(f, stmt, raw)):
                out.append(why)
            con.execute(raw)
        after = shapes(con)
        for v, cols in before.items():
            if safe and after.get(v) != cols:
                out.append(f"view {v} had the columns {cols} and has {after.get(v)}")
        out += _with_faults(con, f.withs)
    except (sqlite3.Error, ValueError) as e:
        out.append(f"it does not apply after the files before it: {e}")
    return [f"{p.name}: {why}" for why in out]


def faults(seam: Seam, n: int) -> list[str]:
    """Why number N's file is not what its head says ([] when it is)."""
    con = sqlite3.connect(":memory:", isolation_level=None)
    try:
        known = db_schema.migrations(seam)
        for k in sorted(k for k in known if k < n):
            for stmt in db_schema.statements(known[k].read_text(encoding="utf-8")):
                con.execute(stmt)
        return judged(con, known[n])
    finally:
        con.close()


def check(seam: Seam) -> list[str]:
    """Every shipped file's faults, and a number past HEADLESS with no line 1 head."""
    out = []
    con = sqlite3.connect(":memory:", isolation_level=None)
    for n, p in sorted(db_schema.migrations(seam).items()):
        sql = p.read_text(encoding="utf-8")
        if db_schema.old_writers(sql) is None and n > HEADLESS.get(seam.name, 0):
            out.append(f"{p.name}: line 1 is not `-- old-writers: safe` or `refuse`")
        if db_schema.by_command(sql) and db_schema.old_writers(sql) != "refuse":
            out.append(f"{p.name}: applied by command, so line 1 is `-- old-writers: refuse`")
        out += judged(con, p)
    con.close()
    return out
