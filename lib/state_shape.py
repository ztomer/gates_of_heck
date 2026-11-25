"""lib/state_shape.py -- the cutover's fence (GOH.ABSDATA), and what a row's `rest` may hold, for
every writer of a table that had a document.

    columns(con, t)      table T's columns
    has(con, t)          whether CON's file has table T
    paused()             why a new land or ship waits: a cutover's fence, up or left by a dead one,
                         or a file from before the cutover
    wait(say, sleep)     until it is down, said once (the lander, before it takes a request)
    fence(con)           the fence's line (`<pid> <start>`, lib/state_cutover.py), or None
    promoted(t)          the paths a row of T's `rest` may not hold, read from its contract's own
                         trigger, so no second list can differ from it
    sift(t, rest)        (REST without them, {path: value} of the ones that held something)
    bury(t, row, why)    a line in the dead-letter file beside goh.db, which `goh state check` names

A VALUE OF THE WRONG TYPE in a field that has a column fits no column and no `rest` (the sealing
trigger). A land or a ship is never refused over one: its row is written without it, and the value
is buried. A hook's row is buried whole (lib/hook_ledgers.py). What an older file left in `rest` the
cutover itself buries, so no such value refuses the contract.
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
        with goh_db.uncut(), goh_db.ro() as con:
            held, old = fence(con), goh_db.not_cut(con)
    except (OSError, sqlite3.Error):
        return None
    if held is None:
        return old
    return (f"goh.db is in a cutover (pid {held}): new lands and ships wait; `goh state "
                     "cutover` goes on with one that died, `--abort` lifts its fence")  # fmt: skip


def wait(say: Callable[[str], None], sleep: Callable[[float], None] | None = None) -> None:
    said = False
    while why := paused():
        if not said:
            say(why)
        said = True
        (sleep or time.sleep)(POLL_S)


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


def sift(table: str, rest: dict) -> tuple[dict, dict[str, Any]]:
    rest, odd = json.loads(json.dumps(rest)), {}
    for path in promoted(table):
        *up, last = path.split(".")
        held: list[Any] = [rest]
        for k in up:
            held.append(held[-1].get(k) if isinstance(held[-1], dict) else None)
        if isinstance(held[-1], dict) and last in held[-1]:
            v = held[-1].pop(last)
            if v not in (None, {}, []):
                odd[path] = v
            for k, parent, child in reversed(list(zip(up, held, held[1:], strict=False))):
                if child == {}:  # it held nothing more
                    del parent[k]
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
