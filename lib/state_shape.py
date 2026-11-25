"""lib/state_shape.py -- which shape goh.db is in while its documents leave it (GOH.ABSDATA), as one
open file says it, for every writer of a table that had a `body`.

    columns(con, t)      table T's columns
    paused()             why a new land or ship waits: a cutover's fence, up or left by a dead one
    wait(say, sleep)     until it is down, said once (the lander, before it takes a request)
    closing(con, t)      T's documents are leaving or gone: a cutover's fence is up, or no `body`
    document(con, t, text)   {'body': TEXT}; {} as its text under a fence; nothing with no `body`
    fence(con)           the fence's line (`<pid> <start>`, lib/state_cutover.py), or None
    promoted(t)          the paths a row's `rest` may not hold once T's contract is applied, read
                         from that contract's own trigger, so no second list can differ from it
    sift(con, t, rest)   (REST without them, {path: value} of the ones that held something), once
                         T is closing; before that a wrong value stays in `rest` and is named
    bury(t, row, why)    a line in the dead-letter file beside goh.db, which `goh state check` names

THE FENCE IS WHERE WRITERS STOP KEEPING DOCUMENTS. It is read inside the writer's own transaction,
so every document is either committed before the cutover's strip or never written. Every reader of
this goh reads the columns, so a row without one reads the same.

A VALUE OF THE WRONG TYPE in a field that has a column fits no column, and from the contract on no
`rest` (the sealing trigger). A land or a ship is never refused over one: its row is written without
it, and the value is buried. A hook's row is buried whole (lib/hook_ledgers.py). Both from the fence
on: what was left in `rest` before it, the cutover itself buries after its strip, so no such value
refuses the contract.
Pinned by tests/test_state_shape.py.
"""

from __future__ import annotations

import functools
import json
import os
import re
import sqlite3
import sys
import time
from collections.abc import Callable
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import goh_db  # lib/, on the path from the line above

POLL_S = 2.0
FENCE = "fence"  # its key in `meta`
DEAD = "hook-ledgers.dead.jsonl"  # beside goh.db: {table, row, why, at}, what its table refuses
SEAL = re.compile(r"CREATE TRIGGER (\w+)_rest_insert BEFORE INSERT ON \w+ WHEN (.*) BEGIN")
PATH = re.compile(r"json_type\(NEW\.rest, '\$\.([^']+)'\)")


def columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]


def has(con: sqlite3.Connection, table: str) -> bool:
    return bool(con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                            (table,)).fetchone())  # fmt: skip


def fence(con: sqlite3.Connection) -> str | None:
    if not has(con, "meta"):
        return None
    row = con.execute("SELECT value FROM meta WHERE key = ?", (FENCE,)).fetchone()
    return row[0] if row else None


def paused() -> str | None:
    """None too when goh.db cannot be read: its own reader says that."""
    try:
        with goh_db.ro() as con:
            held = fence(con)
    except (OSError, sqlite3.Error):
        return None
    return held and (f"goh.db is in a cutover (pid {held}): new lands and ships wait; `goh state "
                     "cutover` goes on with one that died, `--abort` lifts its fence")  # fmt: skip


def wait(say: Callable[[str], None], sleep: Callable[[float], None] | None = None) -> None:
    said = False
    while why := paused():
        if not said:
            say(why)
        said = True
        (sleep or time.sleep)(POLL_S)


def closing(con: sqlite3.Connection, table: str) -> bool:
    return "body" not in columns(con, table) or fence(con) is not None


def document(con: sqlite3.Connection, table: str, text: str) -> dict[str, str]:
    if "body" not in columns(con, table):
        return {}
    return {"body": text if fence(con) is None else "{}"}


@functools.lru_cache(maxsize=1)
def _promoted() -> dict[str, tuple[str, ...]]:
    out = {}
    for p in sorted(goh_db.SEAM.schema.glob("*.sql")):
        if "_rest_insert" in (sql := p.read_text(encoding="utf-8")):
            out |= {
                t: tuple(p.replace('"', "") for p in PATH.findall(when))
                for t, when in SEAL.findall(sql)
            }
    return out


def promoted(table: str) -> tuple[str, ...]:
    return _promoted().get(table, ())


def sift(con: sqlite3.Connection, table: str, rest: dict) -> tuple[dict, dict[str, Any]]:
    if not rest or not closing(con, table):
        return rest, {}
    return _sifted(table, rest)


def _sifted(table: str, rest: dict) -> tuple[dict, dict[str, Any]]:
    rest, odd = json.loads(json.dumps(rest)), {}
    for path in promoted(table):
        *up, last = path.split(".")
        at: Any = rest
        for k in up:
            at = at.get(k) if isinstance(at, dict) else None
        if isinstance(at, dict) and last in at:
            v = at.pop(last)
            if v not in (None, {}, []):
                odd[path] = v
    return rest, odd


def bury(table: str, row: dict, why: str) -> None:
    p = goh_db.path().with_name(DEAD)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"table": table, "row": row, "why": why, "at": time.time()}, sort_keys=True)
    fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:  # one write: O_APPEND keeps the line whole beside another
        os.write(fd, (line + "\n").encode())
    finally:
        os.close(fd)
