"""The landing queue's state, written and read ONE way (GOH.CIRED01310, GOH.LANDQDB).

THE REQUESTS ARE ROWS of goh.db's `requests` (lib/goh_db_schema/0007_landq.sql), one per request,
its `state` one of enqueuing, pending, running. A request is still NAMED by the path it had as a
file, `<q>/<state>/<run>.json` (what `goh land --enqueue` prints, `--wait` takes and every caller
passes around), but the name is a key, never a file: (the queue's realpath, the state, the run).

    name(q, state, run)        a request's name
    put(q, run, req, state, replaces=None)   a new row; REPLACES, a marker's run, deleted with it
    get(path)                  its body while it is in PATH's state, else None (moved on, gone)
    listing(q, state)          the names in STATE, oldest first
    move(path, to)             one UPDATE from PATH's state to TO; FileNotFoundError when it is
                               not there (another mover won), the rename's own error
    update(path, req)          its body rewritten in place, same state
    drop(path)                 the row gone; False when it was not there
    finish(path, outcome)      DELETE the request and INSERT its land record, ONE transaction
    (an older goh's request files are no request: `goh state retire landq` imports them,
    lib/state_retire_landq.py)

Every state change is one transaction, so a SIGKILL anywhere leaves a request in exactly one
state: two renames (running/ to done/, then the unlink) left it in two (tests/test_landq_db.py).

THE OTHER STATE FILES (`write`/`read`, GOH.CIRED01310): every other record is a whole JSON object
or absent: `write` puts it in place by one rename, `read` answers None for a file not there or not
whole, so a reader re-reads it next tick and never crashes on one. Pinned by
tests/test_landq_state.py.

The landing queue's requests as goh.db rows (GOH.LANDQDB, migration 0007 `requests`): one row per
request, its `state` enqueuing, pending or running, still named `<q>/<state>/<run>.json` (a key,
never a file). `put` files one (replacing the enqueue's pre-step marker in the same transaction),
`move` is one UPDATE (FileNotFoundError when another mover won), `get`/`listing`/`rows`/`any_in`
read, `drop` removes, `finish` deletes it inside the land record's own transaction
(`land_records.write(..., request=)`), so a SIGKILL anywhere leaves a request in exactly one state;
an older goh's request files are no request (`state_retire_landq.py` imports them). `early`
requests (red at enqueue) are rows of `early`. The other state records keep `write` (one rename)
and `read` (None for a record moved on or not whole) (GOH.CIRED01310). Invoked by
`lib/land_prestep.py`.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import sys
import time
from collections.abc import Iterator
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import goh_db  # lib/, on the path from the line above
import state_paths

STATES = ("enqueuing", "pending", "running")
EARLY = "early"  # a land judged red at enqueue: filed only to be recorded, never queued (`early`)


def write(path: Path, data: dict, indent: int | None = 1) -> None:
    """DATA at PATH by one rename: a reader sees the old file, none, or this one whole."""
    tmp = path.with_name(f".{path.name}.tmp")  # a dot name: no `*.json` glob sees it
    tmp.write_text(json.dumps(data, indent=indent) + "\n")
    tmp.replace(path)


def read(path: Path) -> dict | None:
    """PATH's record; None when it is gone, not whole, or not an object: read it again later."""
    try:
        rec = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) else None


def queue_key(q: Path) -> str:
    return os.path.realpath(q)


def _ours(queue: str) -> tuple[str, tuple[str, ...]]:
    """(clause, args) naming QUEUE's rows, under either form of its path (lib/state_paths.py)."""
    return state_paths.where("queue", state_paths.forms(queue))


def name(q: Path, state: str, run: str) -> Path:
    return q / state / f"{run}.json"


def key(path: Path) -> tuple[str, str, str]:
    """(queue, state, run) of a request's name."""
    state = path.parent.name
    if state not in (*STATES, EARLY):
        raise ValueError(f"{path}: not a request's name (<q>/<state>/<run>.json)")
    return queue_key(path.parent.parent), state, path.name.removesuffix(".json")


def _cols(req: dict) -> tuple:
    pid = req.get("pid")
    until = req.get("until")
    return (str(req.get("item") or ""), str(req.get("repo") or ""), str(req.get("sha") or ""),
            str(req.get("mode") or ""), pid if isinstance(pid, int) else None,
            req.get("pid_stamp") if isinstance(req.get("pid_stamp"), str) else None,
            float(until) if isinstance(until, (int, float)) else None)  # fmt: skip


def _filed(run: str) -> float:
    ns = run.partition("-")[0]
    return int(ns) / 1e9 if ns.isdigit() and len(ns) > 12 else time.time()


INSERT = ("INSERT INTO requests (queue, run, item, repo, sha, mode, pid, pid_stamp, until, state, "
          "filed_at, body) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")  # fmt: skip


def _insert(con: sqlite3.Connection, queue: str, run: str, req: dict, state: str, how: str = ""
            ) -> int:  # fmt: skip
    sql = INSERT.replace("INSERT", f"INSERT {how}") if how else INSERT
    queue, req = state_paths.column(con, queue), state_paths.put(con, "requests", req)
    cur = con.execute(sql, (queue, run, *_cols(req), state, _filed(run), json.dumps(req)))
    return cur.rowcount


def put(q: Path, run: str, req: dict, state: str = "pending", replaces: str | None = None) -> Path:
    """REQ filed as RUN in STATE (a new row; an existing run raises); REPLACES, the run of this
    enqueue's marker, deleted in the same transaction: a reader sees the marker or the request."""
    if state not in (*STATES, EARLY):
        raise ValueError(f"{state!r}: a request's state is one of {STATES}, or {EARLY}")
    queue = queue_key(q)
    with goh_db.transaction() as con:
        if replaces:
            ours, args = _ours(queue)
            con.execute(f"DELETE FROM requests WHERE {ours} AND run = ?", (*args, replaces))
        if state == EARLY:
            con.execute("INSERT INTO early (queue, run, at, body) VALUES (?, ?, ?, ?)",
                        (state_paths.column(con, queue), run, time.time(),
                         json.dumps(state_paths.put(con, "requests", req))))  # fmt: skip
        else:
            _insert(con, queue, run, req, state)
    return name(q, state, run)


@contextlib.contextmanager
def _reader() -> Iterator[sqlite3.Connection | None]:
    """goh.db read-only, or None when there is no file or no requests table yet."""
    try:
        with goh_db.ro() as con:
            has = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='requests'")
            yield con if has.fetchone() else None
    except (FileNotFoundError, sqlite3.OperationalError):
        yield None


def _body(raw: str) -> dict | None:
    try:
        rec = json.loads(raw)
    except ValueError:
        return None
    return state_paths.got("requests", rec) if isinstance(rec, dict) else None


def get(path: Path) -> dict | None:
    """The request named PATH while it is in PATH's state; None once it moved on or is gone."""
    queue, state, run = key(path)
    ours, args = _ours(queue)
    with _reader() as con:
        if con is None:
            return None
        if state == EARLY:
            got = con.execute(f"SELECT body FROM early WHERE {ours} AND run = ?",
                              (*args, run)).fetchone()  # fmt: skip
        else:
            got = con.execute(f"SELECT body FROM requests WHERE {ours} AND run = ? AND "
                              "state = ?", (*args, run, state)).fetchone()  # fmt: skip
    return _body(got[0]) if got else None


def state_of(q: Path, run: str) -> str | None:
    """RUN's state now, None when queue Q holds no such request."""
    with _reader() as con:
        if con is None:
            return None
        ours, args = _ours(queue_key(q))
        got = con.execute(f"SELECT state FROM requests WHERE {ours} AND run = ?",
                          (*args, run)).fetchone()  # fmt: skip
    return got[0] if got else None


def rows(q: Path, state: str) -> list[tuple[Path, dict]]:
    """(name, body) of every request of queue Q in STATE, oldest first, in one read."""
    with _reader() as con:
        if con is None:
            return []
        ours, args = _ours(queue_key(q))
        got = con.execute(f"SELECT run, body FROM requests WHERE {ours} AND state = ? "
                          "ORDER BY run", (*args, state)).fetchall()  # fmt: skip
    return [(name(q, state, run), b) for run, raw in got if (b := _body(raw)) is not None]


def listing(q: Path, state: str) -> list[Path]:
    """The names of queue Q's requests in STATE, oldest first; EARLY's have their own table."""
    with _reader() as con:
        if con is None:
            return []
        ours, args = _ours(queue_key(q))
        if state == EARLY:
            got = con.execute(f"SELECT run FROM early WHERE {ours} ORDER BY run", args).fetchall()
            return [name(q, state, run) for (run,) in got]
        got = con.execute(f"SELECT run FROM requests WHERE {ours} AND state = ? ORDER BY run",
                          (*args, state)).fetchall()  # fmt: skip
    return [name(q, state, run) for (run,) in got]


def any_in(q: Path, *states: str) -> bool:
    """Queue Q holds a request in any of STATES."""
    with _reader() as con:
        if con is None:
            return False
        marks = ", ".join("?" * len(states))
        ours, args = _ours(queue_key(q))
        got = con.execute(f"SELECT 1 FROM requests WHERE {ours} AND state IN ({marks}) LIMIT 1",
                          (*args, *states)).fetchone()  # fmt: skip
    return got is not None


def move(path: Path, to: str) -> Path:
    """The request named PATH into state TO, one UPDATE; its new name. FileNotFoundError when it is
    no longer in PATH's state (as a rename of a file another mover took)."""
    if to not in STATES:
        raise ValueError(f"{to!r}: a request's state is one of {STATES}")
    queue, state, run = key(path)
    ours, args = _ours(queue)
    with goh_db.transaction() as con:
        cur = con.execute(f"UPDATE requests SET state = ? WHERE {ours} AND run = ? AND state = ?",
                          (to, *args, run, state))  # fmt: skip
        if cur.rowcount != 1:
            raise FileNotFoundError(f"{path}: no request {run} in {state}")
    return path.parent.parent / to / path.name


def update(path: Path, req: dict) -> bool:
    """The body of the request named PATH is REQ (same state); False when it is not there."""
    queue, state, run = key(path)
    ours, args = _ours(queue)
    with goh_db.transaction() as con:
        req = state_paths.put(con, "requests", req)
        cur = con.execute(
            "UPDATE requests SET item = ?, repo = ?, sha = ?, mode = ?, pid = ?, pid_stamp = ?, "
            f"until = ?, body = ? WHERE {ours} AND run = ? AND state = ?",
            (*_cols(req), json.dumps(req), *args, run, state),
        )
        return cur.rowcount == 1


def drop(path: Path) -> bool:
    """The request named PATH gone; False when it was not in PATH's state."""
    queue, state, run = key(path)
    ours, args = _ours(queue)
    with goh_db.transaction() as con:
        if state == EARLY:
            return con.execute(f"DELETE FROM early WHERE {ours} AND run = ?",
                               (*args, run)).rowcount == 1  # fmt: skip
        cur = con.execute(f"DELETE FROM requests WHERE {ours} AND run = ? AND state = ?",
                          (*args, run, state))  # fmt: skip
        return cur.rowcount == 1


def finish(con: sqlite3.Connection, path: Path) -> None:
    """Inside the caller's transaction (lib/land_records.py write): the request named PATH gone,
    in whatever state, in the same commit as its land record."""
    queue, _, run = key(path)
    ours, args = _ours(queue)
    con.execute(f"DELETE FROM requests WHERE {ours} AND run = ?", (*args, run))
    con.execute(f"DELETE FROM early WHERE {ours} AND run = ?", (*args, run))


def request(path: Path) -> dict:
    """The request named PATH ({} once it is gone): a queue state's or an early one's row."""
    return get(path) or {}
