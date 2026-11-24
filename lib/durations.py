"""lib/durations.py -- the suite's timing tables in goh.db (GOH.DURATIONSDB, goh.db 0006).

    durations.py path | import | dump [dur|pole|pole_cpu]

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
    kv        duration_kv: remote_rates and linux_unsafe (lib/remote_lane.py), one JSON value each

THE JSON FILES BEFORE THE SWITCH (handover plan (b)). Each is imported once, by its sha256 (the
`imports` ledger), in the first write's transaction; until then a reader reads it under the
database's rows. A file stays on disk until wave 3's delete commit: an old HEAD export keeps
writing it, and a changed file is a new import.
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
import hashlib
import json
import os
import sqlite3
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import goh_db  # the lib directory, on the path from the line above

ENV = "GOH_TEST_DURATIONS"
KINDS = ("dur", "pole", "pole_cpu")
KEEP = 5
STORE = "durations"
LEGACY = {  # the files the tables were, beside the database (plan (b) step 2)
    "dur": "test-durations.json",
    "pole": "test-durations.poles.json",
    "pole_cpu": "test-durations.poles-cpu.json",
    "remote_rates": "remote-rates.json",
    "linux_unsafe": "linux-unsafe.json",
}


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


def legacy(db: Path, what: str) -> Path:
    return db.with_name(LEGACY[what])


def _sha(p: Path) -> str | None:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return None


def _imported(con: sqlite3.Connection, db: Path, what: str) -> bool:
    """True when `what`'s JSON file is gone or its bytes are in the imports ledger."""
    sha = _sha(legacy(db, what))
    return (
        sha is None
        or con.execute(
            "SELECT 1 FROM imports WHERE store=? AND source=? AND sha256=?",
            (STORE, LEGACY[what], sha),
        ).fetchone()
        is not None
    )


def _json(p: Path) -> object:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _numbers(data: object) -> dict[str, float]:
    if not isinstance(data, dict):
        return {}
    return {k: float(v) for k, v in data.items() if isinstance(v, (int, float))}


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
    """{node id: seconds} of KIND: the view, over the JSON file not yet imported; {} for none."""
    db, named = _where(db, named)
    with _read(db, named) as con:
        if con is None:
            return _numbers(_json(legacy(db, kind)))
        base = {} if _imported(con, db, kind) else _numbers(_json(legacy(db, kind)))
        if base:
            gone = {r[0] for r in con.execute("SELECT node FROM pruned WHERE kind=?", (kind,))}
            base = {k: v for k, v in base.items() if k not in gone}
        rows = con.execute(OWN_WALL if kind == "pole" else
                           "SELECT node, seconds FROM durations WHERE kind=?", (kind,))  # fmt: skip
        return {**base, **{k: float(v) for k, v in rows}}


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
    """duration_kv's KEY (remote_rates | linux_unsafe), else its JSON file's, else None."""
    db, named = _where(db, False)
    with _read(db, named) as con:
        if con is not None:
            row = con.execute("SELECT value FROM duration_kv WHERE key=?", (key,)).fetchone()
            if row is not None:
                return json.loads(row[0])
    return _json(legacy(db, key))


def _import(con: sqlite3.Connection, db: Path, now: float) -> int:
    """Every JSON file beside DB not yet in the imports ledger, inside the caller's transaction:
    its entries as readings stamped with the file's mtime, its kv value unless the key has one."""
    rows = 0
    for what, name in LEGACY.items():
        p = legacy(db, what)
        sha = _sha(p)
        if sha is None or _imported(con, db, what):
            continue
        data, at = _json(p), p.stat().st_mtime
        if what in KINDS:
            got = _numbers(data)
            con.executemany(
                "INSERT INTO readings(run, node, kind, seconds, loaded, at) "
                "VALUES (?, ?, ?, ?, 0, ?)",
                [(f"import:{sha[:12]}", k, what, v, at) for k, v in got.items()],
            )
            n = len(got)
        else:
            put = "INSERT OR IGNORE INTO duration_kv VALUES (?, ?, ?)"
            n = 0 if data is None else con.execute(put, (what, json.dumps(data), at)).rowcount
        con.execute("INSERT OR IGNORE INTO imports VALUES (?, ?, ?, ?, ?)",
                    (STORE, name, sha, n, now))  # fmt: skip
        rows += n
    return rows


def import_files(db: Path | None = None) -> int:
    """The JSON files' rows into DB (`durations.py import`); how many were read in."""
    db, named = _where(db, False)
    with goh_db.transaction(db, named) as con:
        return _import(con, db, time.time())


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
        _import(con, db, now)
        con.executemany(
            "INSERT INTO readings(run, node, kind, seconds, load, loaded, at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(run, k, kind, round(v, 3), loads.get(k), int(k in loaded), now)
             for kind, got in seen.items() for k, v in got.items()],
        )  # fmt: skip
        if keep is not None:
            view = con.execute("SELECT kind, node FROM durations").fetchall()
            con.executemany(
                "INSERT INTO pruned VALUES (?, ?, ?, ?) ON CONFLICT(kind, node) DO UPDATE SET "
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
        _import(con, db, time.time())
        row = con.execute("SELECT value FROM duration_kv WHERE key=?", (key,)).fetchone()
        new = change(json.loads(row[0]) if row else None)
        con.execute(
            "INSERT INTO duration_kv VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
            "value=excluded.value, at=excluded.at",
            (key, json.dumps(new), time.time()),
        )


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "path"
    if cmd == "path":
        print(path())
    elif cmd == "import":
        print(import_files())
    elif cmd == "dump" and (argv[1:] or ["dur"])[0] in KINDS:
        print(json.dumps(load((argv[1:] or ["dur"])[0]), sort_keys=True))
    else:
        print("usage: durations.py path | import | dump [dur|pole|pole_cpu]", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
