"""lib/hook_ledgers.py -- the hook and cache ledgers in goh.db (GOH.STATEDBS4, goh.db 0005).

    hook_ledgers.py watch

WHY. Four stores were files: the spawn ledger agent_guard appends one line to per allowed spawn
and per outcome ($XDG_CACHE_HOME/goh/agent-spawns.jsonl, ~10 MB), lane admission's ledger
($XDG_STATE_HOME/goh/lane-starts.jsonl), and the weekly model-choice watch's record and fault
candidates (beside the spawn ledger). They are the
tables agent_spawns, lane_starts and fault_candidates and the kv row `model-choice-watch`
(lib/goh_db_schema/0005_hook_ledgers.sql), in the goh.db lib/goh_db.py names (GOH_DB, else
$XDG_STATE_HOME/goh/goh.db).

THE HOT PATH. The two hooks write on every agent spawn in every repo, so a write waits at most
HOOK_BUSY_MS for the file and then lets the spawn through: the row goes to the SPILL file beside
goh.db (one O_APPEND line), the next write drains it in order and counts it (kv BUSY), and the
caller gets a warning to show.

THE FILES THEY WERE. goh.db alone holds them since the database plan's wave 3: `goh state retire
hook-ledgers` (lib/state_retire.py) imported each file, read it back equal and archived it. No
goh.db reads as unknown (goh_db.Unknown), never as an empty ledger.

A SPAWN'S FIELDS are columns and child rows (lib/spawn_columns.py, GOH.SPAWNKEY), its items among
them; a lane start's too (lib/body_columns.py, GOH.BODYCOLS). A row its table refuses is kept in
DEAD beside goh.db: never retried, so one such row holds no other, and named by `goh state check`.
Pinned by tests/test_hook_ledgers.py.

GOH.STATEDBS4: the hook and cache ledgers in goh.db (0005): `agent_spawns` (the model-choice hook's
spawn and outcome rows, was `agent-spawns.jsonl`), `lane_starts` (lane admission's verdicts, was
`lane-starts.jsonl`), `fault_candidates` (the weekly watch's, newest run) and `kv`
`model-choice-watch` (the watch's record). `append(table, row)` is the hooks' one write: goh.db's
busy timeout drops to 5 s (`HOOK_BUSY_MS`), and past it the row goes to `hook-ledgers.spill.jsonl`
beside goh.db, the spawn goes on, and the next write drains it in order and counts it (`kv`
`hook-ledgers.busy`). The files they were are retired (`state_retire.py`);
`rows`/`spawns`/`lanes`/`watch`/`candidates` read goh.db alone, and no goh.db raises
`goh_db.Unknown` (unknown, never an empty ledger). `hook_ledgers.py watch`.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # outside any checkout: nothing to re-run from
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import contextlib
import json
import os
import sqlite3
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import body_columns  # the lib directory, on the path from the line above
import body_tables
import candidate_columns
import goh_db
import kv_rows
import lock_order
import spawn_columns
import state_paths
import state_shape

STORE = "hook ledgers"  # the imports ledger's store
SPAWNS, LANES, CANDIDATES = "agent_spawns", "lane_starts", "fault_candidates"
WATCH = "model-choice-watch"  # kv: the weekly watch's record
BUSY = "hook-ledgers.busy"  # kv: {count, last}: rows a write spilled past HOOK_BUSY_MS
SPILL = "hook-ledgers.spill.jsonl"  # beside goh.db: {table, row} lines a busy write left
DEAD = state_shape.DEAD  # beside goh.db: {table, row, why, at}, a row its table refuses
HOOK_BUSY_MS = 5000  # a hook's write waits this long for goh.db, then lets the spawn through
# what one row of the spill can meet that no other row would: it is dead-lettered, never retried
REFUSED = (spawn_columns.Refused, sqlite3.IntegrityError, sqlite3.DataError, OverflowError)


def cache_dir() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "goh"


def _dump(row: object) -> str:
    return json.dumps(row, sort_keys=True)


def _lines(data: bytes) -> list[dict]:
    out = []
    for line in data.decode("utf-8", errors="replace").splitlines():
        with contextlib.suppress(ValueError):
            if isinstance(row := json.loads(line), dict):
                out.append(row)
    return out


def _kv(con: sqlite3.Connection, name: str) -> Any:
    row = con.execute("SELECT body FROM kv WHERE name=?", (name,)).fetchone()
    return state_paths.got(name, json.loads(row[0])) if row else None


def _kv_put(con: sqlite3.Connection, name: str, value: object) -> None:
    con.execute("INSERT INTO kv(name, body, at) VALUES (?, ?, ?) ON CONFLICT(name) DO UPDATE SET "
                "body=excluded.body, at=excluded.at",
                (name, _dump(state_paths.put(con, name, value)), time.time()))  # fmt: skip


def insert(con: sqlite3.Connection, table: str, row: dict) -> None:
    """ROW as TABLE's next row (SPAWNS or LANES; lib/state_retire.py's import uses it too)."""
    row = state_paths.put(con, table, row)
    if table == SPAWNS:
        spawn_columns.write(con, row)
    else:
        at = spawn_columns.instant(row.get("at")) or 0.0
        body_columns.write(con, body_tables.LANES, row, {"at": at})


# -- the spill: a write that waited HOOK_BUSY_MS ------------------------------------------------


def spill_file() -> Path:
    return goh_db.path().with_name(SPILL)


def _line(p: Path, rec: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:  # one write: O_APPEND keeps the line whole beside another
        os.write(fd, (json.dumps(rec, sort_keys=True) + "\n").encode())
    finally:
        os.close(fd)


def _spill(table: str, row: dict) -> None:
    _line(spill_file(), {"table": table, "row": row})


def _spilled(p: Path) -> list[tuple[str, dict]]:
    with contextlib.suppress(OSError):
        return [(r["table"], r["row"]) for r in _lines(p.read_bytes())
                if r.get("table") in (SPAWNS, LANES)
                and isinstance(r.get("row"), dict)]  # fmt: skip
    return []


def _taken(con: sqlite3.Connection, table: str, row: dict) -> str | None:
    """ROW inserted whole, or why its table refuses it (nothing of it written)."""
    con.execute("SAVEPOINT hook_row")
    why = None
    try:
        insert(con, table, row)
    except REFUSED as e:
        con.execute("ROLLBACK TO hook_row")
        why = str(e)
    con.execute("RELEASE hook_row")
    return why


def _bury(letters: list[tuple[str, dict, str]]) -> None:
    for letter in letters:
        state_shape.bury(*letter)


def dead() -> list[dict]:
    """The rows their tables refused, as {table, row, why, at}: `goh state check` names each."""
    with contextlib.suppress(OSError):
        return _lines(goh_db.path().with_name(DEAD).read_bytes())
    return []


def _drain(
    con: sqlite3.Connection, claimed: list[Path], letters: list[tuple[str, dict, str]]
) -> None:
    """The spill's rows into their tables, before this write's row, counted in kv BUSY; a row its
    table refuses joins LETTERS. The file is claimed by a rename (another spill starts a new one);
    the caller removes it after COMMIT."""
    p = spill_file()
    if not p.exists():
        return
    mine = p.with_name(f"{SPILL}.{os.getpid()}.{time.monotonic_ns()}")
    try:
        os.rename(p, mine)
    except OSError:
        return  # another write drained it
    claimed.append(mine)
    rows = _spilled(mine)
    for table, row in rows:
        if why := _taken(con, table, row):
            letters.append((table, row, why))
    busy = _kv(con, BUSY) or {"count": 0}
    _kv_put(con, BUSY, {"count": int(busy.get("count") or 0) + len(rows), "last": time.time()})


@contextlib.contextmanager
def _hook_busy() -> Iterator[None]:
    """goh.db's busy timeout at HOOK_BUSY_MS for this write (a caller's lower setting stands)."""
    key, old = goh_db.SEAM.busy_env, os.environ.get(goh_db.SEAM.busy_env)
    if old is None or not old.isdigit() or int(old) > HOOK_BUSY_MS:
        os.environ[key] = str(HOOK_BUSY_MS)
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old


def append(table: str, row: dict) -> str | None:
    """One hook ledger row (SPAWNS or LANES), after the spill's;
    the warning to show when goh.db could not take it (the row is spilled, the spawn goes on)."""
    claimed: list[Path] = []
    letters: list[tuple[str, dict, str]] = []
    try:
        with _hook_busy(), goh_db.transaction() as con:
            _drain(con, claimed, letters)
            if why := _taken(con, table, row):
                letters.append((table, row, why))
        _bury(letters)  # the COMMIT is done: until here the spill still holds them
    except (sqlite3.Error, OSError, lock_order.OrderError) as e:  # an ancestor holding goh.db: open
        for mine in claimed:  # rolled back: its rows go back to the spill
            for t, r in _spilled(mine):
                _spill(t, r)
            mine.unlink(missing_ok=True)
        try:
            _spill(table, row)
        except OSError as e2:
            return f"hook_ledgers: the {table} row was not written ({e}; spill: {e2})."
        return f"hook_ledgers: goh.db did not take the {table} row ({e}); spilled, counted."
    for mine in claimed:
        mine.unlink(missing_ok=True)
    if letters:
        said = "; ".join(f"{t}: {why}" for t, _, why in letters)
        return f"hook_ledgers: not written, kept in {DEAD}: {said}"
    return None


# -- readers -------------------------------------------------------------------------------------


@contextlib.contextmanager
def _read(db: Path | None = None) -> Iterator[sqlite3.Connection | None]:
    """goh.db (DB when named) read-only; None when it predates 0005. No file: goh_db.Unknown."""
    with contextlib.ExitStack() as stack:
        con = stack.enter_context(goh_db.known(db))
        try:
            con.execute("SELECT 1 FROM agent_spawns LIMIT 1")
        except sqlite3.Error:
            con = None
        yield con


def rows(table: str, db: Path | None = None) -> list[dict]:
    """TABLE's rows (SPAWNS or LANES) in the order written, then the spill's (DB: that file's rows
    alone, a run's own goh.db). No goh.db raises goh_db.Unknown: unknown, never empty."""
    with _read(db) as con:
        got: list[dict] = []
        if con is not None and table == SPAWNS:
            con.execute("BEGIN")  # a spawn and its child rows are one snapshot
            got = spawn_columns.read(con)
        elif con is not None:
            got = [doc for _, doc in body_columns.read(con, body_tables.LANES)]
        got = [state_paths.got(table, r) for r in got]
    if db is not None:
        return got
    late = [r for t, r in _spilled(spill_file()) if t == table]
    return got + ([spawn_columns.normal(r) for r in late] if table == SPAWNS else late)


def spawns(db: Path | None = None) -> list[dict]:
    return rows(SPAWNS, db)


def lanes() -> list[dict]:
    return rows(LANES)


def busy() -> dict:
    """{count, last}: the hook rows a busy goh.db spilled (drained since), else {count: 0}."""
    with contextlib.suppress(goh_db.Unknown), _read() as con:
        return (_kv(con, BUSY) if con is not None else None) or {"count": 0}
    return {"count": 0}


# -- the weekly watch's record and fault candidates ------------------------------------------------


def watch() -> object:
    """The weekly watch's last record (its kv row), or None before its first run."""
    with _read() as con:
        if con is None:
            return None
        con.execute("BEGIN")  # the report and its rows are one snapshot
        return kv_rows.first_done(con, _kv(con, WATCH))


def record_watch(state: dict, candidates: list[dict] | None) -> None:
    """One weekly run: its record, and (when the SPEND section ran) its fault candidates."""
    with goh_db.transaction() as con:
        _kv_put(con, WATCH, state)
        kv_rows.put_first_done(con, state)
        at = time.time()
        for row in candidates if candidates is not None else []:
            try:
                candidate_columns.write(con, row, at)
            except REFUSED as e:
                _bury([(CANDIDATES, row, str(e))])


def candidates() -> list[dict]:
    """The newest run's fault candidates."""
    with _read() as con:
        if con is None:
            return []
        con.execute("BEGIN")  # a candidate and its child rows are one snapshot
        return candidate_columns.read(con, "at = (SELECT MAX(at) FROM fault_candidates)")


def main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="hook_ledgers.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=("watch",))
    ap.parse_args(argv)
    try:  # the weekly record, for a reader that used to read its file
        print(json.dumps(watch(), sort_keys=True))
    except goh_db.Unknown as e:
        print(f"hook_ledgers: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
