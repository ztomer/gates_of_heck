"""lib/durations.py -- the suite's timing tables in goh.db (GOH.DURATIONSDB, goh.db 0006).

    durations.py path | dump [dur|pole|pole_cpu]

WHY. The duration table, the long-pole table and its CPU twin were whole-file JSON
(test-durations.json, .poles.json, .poles-cpu.json) that every top-level run read, merged and
replaced with no lock: two runs ending together kept one run's readings, and one run's write
decided every later land (the long-pole flap of 2026-11-23). A run now INSERTs one row per reading
(`record`), in one transaction with its prunes; the table a reader sees (`load`) is the
`durations` view, each (kind, node)'s newest reading no later prune hid.

THE FILE is goh.db, named by GOH_TEST_DURATIONS when a caller sets it (lib/fast_tests.py `cache`,
the one spelling: tools/pytest.sh names it for its top-level run, the only writer,
GOH.DURATIONSSTALE), else lib/goh_db.py `path()`.

    kinds     dur (seconds per node id), pole (quiet seconds), pole_cpu (their CPU seconds)
    keep      the newest KEEP readings per (kind, node); the schema's bound holds the 30 days
    pruned    a prune (a node the run's whole-file collection did not hold, or whose file is gone)
              is a `pruned` row in the run's transaction; a later reading brings the node back
    kv        duration_kv: the split's `remote_*` paces, one number each (lib/remote_lane.py); its
              ledger linux_unsafe is a table of its own (lib/kv_rows.py), its document kept beside

THE JSON FILES THEY WERE (test-durations*.json, remote-rates.json, linux-unsafe.json) are read by
nothing since the database plan's wave 3: `goh state retire durations` (lib/state_retire.py)
imported each, read it back and archived it.

GOH.DURATIONSDB: the suite's timing tables in goh.db (0006), replacing `test-durations.json` and
its `.poles` / `.poles-cpu` siblings, which every top-level run read, merged and replaced with no
lock (two runs ending together kept one run's readings). `record` INSERTs one row per reading
(`readings`: run, node, kind `dur`/`pole`/`pole_cpu`, seconds, loaded, at) in ONE transaction with
its prunes (`pruned` rows, lifted by a later reading) and keeps the newest `KEEP` (5) per (kind,
node); `load(kind)` reads the `durations` view (each newest unpruned reading). `get`/`update` hold
the split's ledgers (`linux_unsafe`, rows and document; `duration_kv`'s `remote_*` paces). The
file is `GOH_TEST_DURATIONS` when named, else goh.db. No JSON file beside it is read (retired by
`state_retire.py`). `durations.py path|dump [kind]`. Invoked by `tests/_schedule.py`,
`long_pole.py`.
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
from collections.abc import Callable, Iterable, Iterator, Mapping
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import goh_db  # the lib directory, on the path from the line above
import kv_rows

ENV = "GOH_TEST_DURATIONS"
KINDS = ("dur", "pole", "pole_cpu")
KEEP = 5


def path(environ: Mapping[str, str] | None = None) -> Path:
    """GOH_TEST_DURATIONS when a caller names the database, else goh.db's own path."""
    named = (os.environ if environ is None else environ).get(ENV)
    return Path(named) if named else goh_db.path()


def _where(db: Path | None, named: bool) -> tuple[Path, bool]:
    """(the file, whether its runner named it): no DB is `path()`, and a file GOH_TEST_DURATIONS
    names is the runner's by contract (tools/pytest.sh names it for its top-level run; the
    controller hands it back for a call, lib/remote_gate.py `table`)."""
    if db is not None:
        return Path(db), named
    return path(), named or bool(os.environ.get(ENV))


@contextlib.contextmanager
def _read(db: Path, named: bool) -> Iterator[sqlite3.Connection | None]:
    """The database read-only, or None when there is none (or it predates 0006)."""
    with contextlib.ExitStack() as stack:
        try:
            con = stack.enter_context(goh_db.ro(db, named))
            con.execute("SELECT 1 FROM durations LIMIT 1")
        except (OSError, sqlite3.Error):
            con = None
        yield con


# A `pole` entry read at no more than the wall its own run read (GOH.LONGPOLECOLD): a quiet time
# above the wall the same run of the test took is no quiet time at all (load only adds to a wall),
# and the 72.1 s entry of 2026-11-24, settled from a cold store's CPU over 28.06 s of wall, turned
# every land red until a quiet run replaced it. lib/long_pole.py `settle` no longer writes one.
OWN_WALL = (
    "SELECT d.node, MIN(d.seconds, COALESCE((SELECT r.seconds FROM readings AS r WHERE r.kind='dur'"
    " AND r.node=d.node AND r.run=d.run ORDER BY r.n DESC LIMIT 1), d.seconds)) "
    "FROM durations AS d WHERE d.kind=?"
)


def load(kind: str = "dur", db: Path | None = None, named: bool = False) -> dict[str, float]:
    """{node id: seconds} of KIND: the view; {} for none."""
    db, named = _where(db, named)
    with _read(db, named) as con:
        if con is None:
            return {}
        rows = con.execute(OWN_WALL if kind == "pole" else
                           "SELECT node, seconds FROM durations WHERE kind=?", (kind,))  # fmt: skip
        return {k: float(v) for k, v in rows}


def newest(node: str, db: Path | None = None) -> dict[str, dict[str, object]]:
    """{kind: its newest reading of NODE} as {seconds, load, loaded, run, at}: what a run read and
    under what load (lib/long_pole.py `remeasure`); {} for none."""
    db, named = _where(db, False)
    out: dict[str, dict[str, object]] = {}
    with _read(db, named) as con:
        if con is None:
            return out
        for kind, seconds, load, loaded, run, at in con.execute(
            "SELECT kind, seconds, load, loaded, run, at FROM readings WHERE node=? "
            "ORDER BY at, n", (node,)):  # fmt: skip
            out[kind] = {"seconds": seconds, "load": load, "loaded": bool(loaded), "run": run,
                         "at": at}  # fmt: skip
    return out


def get(key: str, db: Path | None = None) -> object:
    """duration_kv's KEY (linux_unsafe: its rows, lib/kv_rows.py), else None."""
    db, named = _where(db, False)
    with _read(db, named) as con:
        if con is not None:
            con.execute("BEGIN")  # a document and its rows are one snapshot
            return _value(con, key)
    return None


def _value(con: sqlite3.Connection, key: str) -> object:
    row = con.execute("SELECT value FROM duration_kv WHERE key=?", (key,)).fetchone()
    doc = json.loads(row[0]) if row else None
    return kv_rows.unsafe(con, doc) if key == kv_rows.UNSAFE else doc


def scalars(keys: Iterable[str], db: Path | None = None) -> dict[str, float]:
    """The numbers duration_kv has under KEYS (the split's paces, lib/remote_lane.py)."""
    got = {k: get(k, db) for k in keys}
    return {k: float(v) for k, v in got.items() if isinstance(v, (int, float))}


def written(key: str, db: Path | None = None) -> float:
    """When duration_kv's KEY was last written; 0 for never."""
    db, named = _where(db, False)
    with _read(db, named) as con:
        row = con and con.execute("SELECT at FROM duration_kv WHERE key=?", (key,)).fetchone()
    return float(row[0]) if row else 0.0


def run_id() -> str:
    return f"{os.uname().nodename}:{os.getpid()}:{time.time():.3f}"


def record(
    seen: Mapping[str, Mapping[str, float]],
    keep: Callable[[str], bool] | None = None,
    loaded: frozenset[str] | set[str] = frozenset(),
    db: Path | None = None,
    named: bool = False,
    loads: Mapping[str, float] | None = None,
) -> None:
    """One run's readings, {kind: {node id: seconds}}, written in ONE transaction with its prunes:
    every node of the view KEEP refuses gets a `pruned` row, and each (kind, node) keeps its
    newest KEEP readings. LOADS, {node id: runnable threads per core over the test}, fills each
    reading's `load` (GOH.LONGPOLECOLD: a reading is judged by the load it was taken at). A failed
    write costs the next run its order, never its verdict."""
    (db, named), now, run = _where(db, named), time.time(), run_id()
    loads = loads or {}
    with contextlib.suppress(OSError, sqlite3.Error), goh_db.transaction(db, named) as con:
        con.executemany(
            "INSERT INTO readings(run, node, kind, seconds, load, loaded, at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(run, k, kind, round(v, 3), loads.get(k), int(k in loaded), now)
             for kind, got in seen.items() for k, v in got.items()],
        )  # fmt: skip
        if keep is not None:
            view = con.execute("SELECT kind, node FROM durations").fetchall()
            con.executemany(
                "INSERT INTO pruned(kind, node, run, at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(kind, node) DO UPDATE SET "
                "run=excluded.run, at=excluded.at",
                [(kind, k, run, now) for kind, k in view if not keep(k)],
            )
        con.execute(
            "DELETE FROM readings WHERE n IN (SELECT n FROM (SELECT n, ROW_NUMBER() OVER "
            "(PARTITION BY kind, node ORDER BY at DESC, n DESC) AS rk FROM readings) WHERE rk > ?)",
            (KEEP,),
        )


def update(key: str, change: Callable[[object], object], db: Path | None = None) -> None:
    """duration_kv's KEY read, CHANGEd and written in one transaction (no lost update): CHANGE is
    pure, handed the value (None for none) and returning the new one."""
    db, named = _where(db, False)
    with goh_db.transaction(db, named) as con:
        new = change(_value(con, key))
        if key == kv_rows.UNSAFE:
            kv_rows.put_unsafe(con, new)
        _keep(con, {key: new})


def _keep(con: sqlite3.Connection, values: Mapping[str, object]) -> None:
    con.executemany(
        "INSERT INTO duration_kv(key, value, at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, at=excluded.at",
        [(k, json.dumps(v), time.time()) for k, v in values.items()],
    )


def update_scalars(
    keys: Iterable[str],
    change: Callable[[dict[str, float]], Mapping[str, float]],
    db: Path | None = None,
) -> None:
    """The numbers under KEYS read, CHANGEd and written in one transaction."""
    db, named = _where(db, False)
    with goh_db.transaction(db, named) as con:
        was = {k: _value(con, k) for k in keys}
        _keep(con, change({k: float(v) for k, v in was.items() if isinstance(v, (int, float))}))


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "path"
    if cmd == "path":
        print(path())
    elif cmd == "dump" and (argv[1:] or ["dur"])[0] in KINDS:
        print(json.dumps(load((argv[1:] or ["dur"])[0]), sort_keys=True))
    else:
        print("usage: durations.py path | dump [dur|pole|pole_cpu]", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
