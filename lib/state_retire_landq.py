"""lib/state_retire_landq.py -- `goh state retire landq`: an older goh's land queue request files,
the store that moved into goh.db 0007 (GOH.LANDQDB), as lib/state_retire.py retires one.

A lander before GOH.LANDQDB filed each request as `<q>/<state>/<run>.json` (state enqueuing,
pending or running) and each land judged red at enqueue as `<q>/early/<run>.json`. They are rows of
`requests` and `early` now (lib/landq_state.py, the one writer) and no lander reads a file, so one
left on disk is a stray: it goes in as its row (INSERT OR IGNORE, so a row already there, in flight
or not, is never rewritten nor moved), is read back (its run is a row, or its land is recorded),
archived and deleted, and each directory goes once empty. A stray whose land is already recorded
is not queued again: its file is archived with no row. The import runs under `<q>/lander.lock`, the
lock the lander holds while it moves requests: while a lander holds it every file is kept, said.
As every store's, a dry run imports too; only the files stay.

`pre/*.record` is no record of this store: it stays a file beside its kept verdict (0007; land.sh
and lib/pre_reuse.py read it by path), reaped per request by lib/land_prestep.py `reap`. The slot
owners' files are lib/land_slot.py's `import_owners`. In the seam with lib/state_retire.py
(tests/_state_seam_scan.py).

GOH.LANDQDB: `goh state retire landq`, the land queue requests' half of `state_retire.py`: each
stray `<q>/{enqueuing,pending,running}/<run>.json` (an older goh's request) in as its `requests`
row and each `early/<run>.json` as its `early` row, under `lander.lock` (a lander holding it keeps
every file, said; `Locked` is the store's `db`, its import taken under the lock); INSERT OR IGNORE,
so an in-flight row is never rewritten nor moved, and a run whose land is recorded is not queued
again; `check` passes a run that is a row or a recorded land; each emptied directory goes.
`pre/*.record` stays a file (0007), reaped by `land_prestep.reap`. In the seam beside
`state_retire.py`. Invoked by `state_retire.py`.

`lib/state_retire.py`: GOH.STATEDB wave 3: `goh state retire [STORE...] [--dry-run]` imports a
moved store's files (INSERT OR IGNORE), reads every record back from goh.db (equal, or its row
newer than its file), copies them under `<state>/retired/<YYYY-MM-DD>/<store>/{state,cache}/`,
byte-compares each copy, and only then deletes them; one unequal record keeps every file of its
store (exit 1). `goh state stray` names a retired store's file still on disk. The one module that
names the moved files (in the seam, `tests/_state_seam_scan.py`). Invoked by `goh_db.py`.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import sqlite3
import sys
import time
from collections.abc import Iterator
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import goh_db  # lib/, on the path from the line above
import landq_state
import lock_order
import state_paths
import state_retire_lands  # its queue(): GOH_LANDQ_DIR, else the state's landq/

DIRS = (*landq_state.STATES, landq_state.EARLY)
BUSY = "a lander holds lander.lock: retired once it is idle"


def _q() -> Path:
    return state_retire_lands.queue()


def files() -> list[Path]:
    q = _q()
    return [p for d in DIRS for p in sorted((q / d).glob("*.json")) if not p.name.startswith(".")]


def dirs() -> list[Path]:
    return [_q() / d for d in DIRS]


def _lock() -> int | None:
    """lander.lock taken (never waited for: a lander runs for a whole land), else None."""
    q = _q()
    if not q.is_dir():
        return None
    fd = os.open(q / "lander.lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        lock_order.flock(fd, "lander", wait=False)
    except OSError:
        os.close(fd)
        return None
    return fd


def keep(p: Path) -> str | None:
    """Why P stays for now: a lander holds lander.lock."""
    fd = _lock()
    if fd is None:
        return BUSY
    os.close(fd)
    return None


class Locked:
    """goh.db as the Store's `db`: its write (the import) under lander.lock, its reads as any."""

    ro = staticmethod(goh_db.ro)

    @staticmethod
    @contextlib.contextmanager
    def transaction() -> Iterator[sqlite3.Connection]:
        fd = _lock()
        if fd is None:  # a lander took it since `keep` looked: nothing is imported
            raise RuntimeError(f"landq: {BUSY}; run `goh state retire landq` again")
        try:
            with goh_db.transaction() as con:
                yield con
        finally:
            os.close(fd)


def _read(p: Path) -> dict | None:
    return landq_state.read(p)


def _has(con: sqlite3.Connection, table: str, queue: str, run: str) -> bool:
    sql = f"SELECT 1 FROM {table} WHERE queue = ? AND run = ?"
    return con.execute(sql, (queue, run)).fetchone() is not None


def _landed(con: sqlite3.Connection, queue: str, run: str) -> bool:
    return _has(con, "lands", queue, run)


def _row(con: sqlite3.Connection, queue: str, run: str) -> bool:
    return _has(con, "requests", queue, run) or _has(con, "early", queue, run)


def load(con: sqlite3.Connection, paths: list[Path]) -> None:
    """Each whole file in as its row unless its run is a row already or its land is recorded;
    each file's bytes in `imports`."""
    queue, now = landq_state.queue_key(_q()), time.time()
    for p in paths:
        rec, run, state = _read(p), p.stem, p.parent.name
        n = 0
        if rec is not None and not _row(con, queue, run) and not _landed(con, queue, run):
            at = p.stat().st_mtime
            n = int(landq_state.insert(con, queue, run, rec, state, at, ignore=True))
        table = "early" if state == landq_state.EARLY else "requests"
        sha = hashlib.sha256(p.read_bytes()).hexdigest()
        con.execute("INSERT OR IGNORE INTO imports(store, source, sha256, rows, at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (table, state_paths.column(con, str(p)), sha, n, now))  # fmt: skip


def check(con: sqlite3.Connection, p: Path) -> str | None:
    """P's run read back: a row of the queue (the one it went in as, or the one already there), or
    a recorded land; a file not whole is no request."""
    if _read(p) is None:
        return "not a whole request"
    queue = landq_state.queue_key(_q())
    if _row(con, queue, p.stem) or _landed(con, queue, p.stem):
        return None
    return "no requests row"


STORE = {"files": files, "load": load, "check": check, "keep": keep, "db": Locked, "dirs": dirs}
