"""lib/ship_records.py -- goh ship's records in goh.db (GOH.STATEDB S2, goh.db 0003).

A `goh ship` result was one JSON file per run under ship/<repo>/, rewritten at each step, and its
install step's pins a `<result>.pins.json` sidecar. Both are rows now: `ships` and `ship_pins`,
columns and child rows (lib/ship_columns.py, GOH.STEPTIMES),
keyed by the RESULT path every caller already names a record by (GOH_SHIP_RESULT, `goh ship
--status RESULT`, loopctl's --shipped), with the stem as `run`, the join key. The step logs, the
sweep's record, the suite's profiles and the coverage summary stay files in the ship directory.

THE FILES THEY WERE are read by nothing since the database plan's wave 3: `goh state retire
ships` (lib/state_retire.py) imported each, read it back equal and archived it. What this
module still names under ship_dir() is where the step logs stay (handover plan (e)). No goh.db
reads as no record for one result's lookup and raises goh_db.Unknown for a reader of many
(goh_db.known: unknown, never none). Pinned by tests/test_ship_records.py.

GOH.STATEDB S2: goh ship's records in goh.db (0003), replacing the per-run result JSON under
`ship/<repo>/` and its `<result>.pins.json` sidecar. `ships` and `ship_pins` are keyed by the
RESULT path every caller names a record by (`GOH_SHIP_RESULT`, `goh ship --status RESULT`), with
its stem as `run` (the join key); `ships_v1` is the view other readers use. `put`/`put_pins` write
a row; `get`, `get_pins`, `in_dir`, `every` and `live` (every repo's running records, which
`ship_status.settle_all` settles at each ship start) read the rows alone; with no goh.db a reader
of many raises `goh_db.Unknown`, one result's lookup reads None (the files are retired by
`state_retire_ships.py`, which first settles a result whose worker is gone). `ship_dir()` is the
one spelling of the ship directory (`GOH_SHIP_DIR`). Invoked by `tools/ship.py`.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import goh_db  # lib/, beside this file: the one door to goh.db
import ship_columns
import state_paths
import state_shape

PINS = ".pins.json"


def ship_dir() -> Path:
    """Where ship results and their step logs live: GOH_SHIP_DIR, else the state root's ship/."""
    return Path(os.environ.get("GOH_SHIP_DIR") or state_paths.state_root() / "ship")


def pins_path(result: Path | str) -> Path:
    """The name wf-pin's own pin record goes by when no ship runs it (its goh.db kv row,
    lib/consumer_pins.py): the path a sidecar beside RESULT once had, never opened."""
    return Path(result).with_suffix(PINS)


def _repo(con: sqlite3.Connection, res: dict) -> str:
    """The repo column of RES: its key in a file of tokens, or with no document to hold its path;
    else its directory's name."""
    keyed = state_paths.tokens(con) or state_shape.closing(con, "ships")
    key = str(state_paths.repo_key(res.get("repo")) or "") if keyed else ""
    return key or Path(str(res["result"])).parent.name


def put(res: dict) -> None:
    """Write RES (a whole ship record) as its result's row; a file this copy may not write is
    raised, never skipped (the ship's one record is no side record: lib/ship_status.py save)."""
    with goh_db.transaction() as con:
        ship_columns.put(con, state_paths.put(con, "ships", res),
                         state_paths.column(con, str(res["result"])), _repo(con, res),
                         time.time())  # fmt: skip


def put_pins(result: Path | str, body: dict) -> None:
    with goh_db.refusable(), goh_db.transaction() as con:
        ship_columns.put_pins(con, state_paths.put(con, "ship_pins", body),
                              state_paths.column(con, str(result)), time.time())  # fmt: skip


@contextlib.contextmanager
def _read(one: bool = False) -> Iterator[sqlite3.Connection | None]:
    """goh.db read-only, or None for a file without the tables (one from before 0003): no record.
    No goh.db at all raises goh_db.Unknown for a reader of MANY records (the ships are unknown
    then, never none); for ONE result's lookup (ONE) it is None: that result has no record."""
    with contextlib.ExitStack() as stack:
        con: sqlite3.Connection | None = None
        with contextlib.suppress(goh_db.Unknown) if one else contextlib.nullcontext():
            con = stack.enter_context(goh_db.known())
        try:
            if con is not None:
                con.execute("SELECT 1 FROM ships LIMIT 0")
        except sqlite3.OperationalError:
            con = None
        yield con


def get(result: Path | str) -> dict | None:
    """RESULT's record (its row), else None."""
    at, args = state_paths.where("x.result", state_paths.forms(result))
    with _read(one=True) as con:
        rows = ship_columns.read(con, at, args) if con else []
    return state_paths.got("ships", rows[-1][1]) if rows else None


def get_pins(result: Path | str) -> dict | None:
    at, args = state_paths.where("x.result", state_paths.forms(result))
    with _read(one=True) as con:
        rows = ship_columns.read_pins(con, at, args) if con else []
    return state_paths.got("ship_pins", rows[-1][1]) if rows else None


def in_dir(directory: Path) -> list[tuple[Path, dict]]:
    """Every record of one ship directory (a repo's), by result path: the rows whose result lives
    there."""
    directory = Path(directory)
    out: dict[str, dict] = {}
    ends = [f + end for f in state_paths.forms(directory) for end in (os.sep, chr(ord(os.sep) + 1))]
    span = " OR ".join(["x.result >= ? AND x.result < ?"] * (len(ends) // 2))
    with _read() as con:
        for result, rec in ship_columns.read(con, f"({span})", ends) if con else ():
            full = state_paths.decode(result)
            if Path(full).parent == directory:
                out[full] = state_paths.got("ships", rec)
    return [(Path(k), out[k]) for k in sorted(out)]


def live() -> list[tuple[Path, dict]]:
    """Every record, in ANY repo's directory, that still says running or bisecting (its worker
    may be gone: lib/ship_status.py settle_all)."""
    with _read() as con:
        rows = ship_columns.read(con, "x.status IN ('running', 'bisecting')") if con else []
    return [(Path(state_paths.decode(r)), state_paths.got("ships", rec)) for r, rec in rows]


def repo_dirs(base: Path | None = None) -> list[Path]:
    """Each repo's ship directory under BASE: the ones on disk and the ones rows name."""
    base = base or ship_dir()
    dirs = {p for p in base.iterdir() if p.is_dir()} if base.is_dir() else set()
    with _read() as con:
        for (result,) in con.execute("SELECT result FROM ships") if con else ():
            full = Path(state_paths.decode(result))
            if full.parent.parent == base:
                dirs.add(full.parent)
    return sorted(dirs)


def every(base: Path | None = None) -> list[tuple[Path, dict]]:
    """Every record under BASE (the ship directory), each repo's in turn."""
    return [rec for d in repo_dirs(base) for rec in in_dir(d)]


# A TEST'S SHIP ROW (GOH.FAKESHIPROWS). A test that ran goh ship against the live goh.db left its
# row there (.loop's test_ship_done wrote 30+, item A.0, before it was made hermetic), and every
# ship statistic counted it. Such a row's result sits under the system's temp roots; a real ship's
# under the ship directory (ship_dir()), which is never a test's, wherever it is.
TEMP_ROOTS = ("/tmp", "/private/tmp", "/var/folders", "/private/var/folders")


def temp_roots() -> tuple[str, ...]:
    """The roots a test's result path sits under: the platform's and the temp dir this process
    names, as named and resolved."""
    here = tempfile.gettempdir()
    return tuple(dict.fromkeys((*TEMP_ROOTS, here, str(Path(here).resolve()))))


def _under(path: str, root: str) -> bool:
    root = root.rstrip(os.sep)
    return path == root or path.startswith(root + os.sep)


def test_rows(roots: tuple[str, ...] | None = None) -> list[str]:
    """Every ship row's result path under one of ROOTS (temp_roots()), none in the ship
    directory: the rows a test left behind, sorted."""
    roots = roots or temp_roots()
    keep = (str(ship_dir()), str(ship_dir().resolve()))
    with _read() as con:
        rows = con.execute("SELECT result FROM ships").fetchall() if con else []
    found = (state_paths.decode(r) for (r,) in rows)
    return sorted(r for r in found if any(_under(r, t) for t in roots)
                  and not any(_under(r, k) for k in keep))  # fmt: skip


def drop_test_rows(roots: tuple[str, ...] | None = None) -> int:
    """test_rows(ROOTS) deleted with their pin rows, in one write; how many."""
    gone = test_rows(roots)
    with goh_db.transaction() as con:
        for table in ("ships", "ship_pins"):
            con.executemany(f"DELETE FROM {table} WHERE result=?",
                            [(f,) for r in gone for f in state_paths.forms(r)])  # fmt: skip
    return len(gone)
