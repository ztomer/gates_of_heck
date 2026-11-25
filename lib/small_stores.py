"""lib/small_stores.py -- goh's small state stores in goh.db (GOH.STATEDBS3, goh.db 0004).

    small_stores.py export [STATE_DIR]

WHY. Seven stores were files rewritten whole or appended per line: five whole-file JSON
documents (the estate audit's state, the pin push ledger and sweep registry, the cache evictor's
ledger, wf-pin's consumer pin record), the commit-gated ledger and the flake register. They move
into goh.db's `kv`, `commit_gated` and `flakes` tables (lib/goh_db_schema/0004_small_stores.sql),
the pin ledger, the sweep registry and the pin record in rows too (lib/kv_rows.py, GOH.KVBLOBS);
the CPU idle window moves into goh-cache.db's `kv` (lib/cpu_window.py), being disposable.

WHERE. Each store keeps the path its file had (its env override: GOH_ESTATE_STATE,
GOH_CACHE_EVICT_STATE, GOH_FLAKE_DIR, ...), and its rows live in the goh.db BESIDE that path, so a
caller (a test) that names its own file has its own database, as it had its own file:

    db(p)     GOH_DB when set, else p's directory's goh.db (the default paths: goh_db.path())

THE FILES THEY WERE. goh.db alone holds them since the database plan's wave 3: `goh state retire
small-stores` (lib/state_retire.py) imported each file, read it back equal and archived it; a
store's path is its identity (the database beside it, and its kv name), never a file read. No
database yet reads as no value: each store starts empty, as its file once did.
Pinned by tests/test_small_stores.py.

GOH.STATEDBS3: goh's small state stores in goh.db (0004): `kv` (one JSON document per name:
`estate_audit`, `pin_push`, `pin_sweeps`, `cache-evict`, `wf_pin.pins`; `get`/`update`/`put`, a
read-change-write in one transaction), `commit_gated` (`gated_add`/`gated`: every tree a commit
gate passed) and `flakes` (`flake_add`/`flakes`: the flake register's records and filings, in the
order written; their fields columns, `body_columns.py`). A store keeps its file's path (its env
override) and its rows live in the goh.db
beside it (`db(p)`: `GOH_DB` when set), so a caller naming its own file has its own database. The
path is identity only: no store reads or writes its file (retired by `state_retire.py`); no
database reads as no value. `small_stores.py export [STATE_DIR] [--to DIR]`.
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

import calendar
import contextlib
import json
import os
import sqlite3
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import body_columns  # the lib directory, on the path from the line above
import body_tables
import goh_db
import kv_rows
import state_paths

# kv names: each was the file `<name>.json` (kv_name), beside the database (the estate audit's,
# the pin push ledger's and the sweep registry's dir is GOH_ESTATE_STATE's)
KV_NAMES = ("estate_audit", "pin_push", "pin_sweeps", "cache-evict", "wf_pin.pins")
KV_FILES = {name: f"{name}.json" for name in KV_NAMES}


def named(beside: Path | str, name: str) -> Path:
    """The file kv NAME was, beside BESIDE: the path its readers and writers name it by."""
    return Path(beside).with_name(KV_FILES[name])


GATED_FILE = "commit-gated.jsonl"
FLAKE_DIR = "flakes"


def db(beside: Path | str) -> Path:
    """The goh.db a store whose file is BESIDE lives in: GOH_DB when set, else beside it."""
    if os.environ.get("GOH_DB"):
        return goh_db.path()
    return Path(beside).expanduser().with_name("goh.db")


def kv_name(p: Path | str) -> str:
    """A whole-file store's kv name: its file name less `.json` (estate_audit.json: estate_audit)"""
    return Path(p).name.removesuffix(".json")


def _dump(row: object) -> str:
    return json.dumps(row, sort_keys=True)


@contextlib.contextmanager
def _read(p: Path) -> Iterator[sqlite3.Connection | None]:
    """The database beside P read-only, or None when there is none (or it predates 0004)."""
    with contextlib.ExitStack() as stack:
        try:
            con = stack.enter_context(goh_db.ro(db(p)))
            con.execute("SELECT 1 FROM kv LIMIT 1")
        except (OSError, sqlite3.Error):
            con = None
        yield con


@contextlib.contextmanager
def _write(p: Path) -> Iterator[sqlite3.Connection]:
    with goh_db.transaction(db(p)) as con:
        yield con


# -- kv: the whole-file JSON stores --------------------------------------------------------------


def _kept(con: sqlite3.Connection, name: str, body: str) -> object:
    """NAME's value: its rows' when its document says they hold it (lib/kv_rows.py)."""
    return state_paths.got(name, kv_rows.got(con, name, json.loads(body)))


def get(p: Path | str) -> object:
    """The store whose path is P: its kv row, else None."""
    p = Path(p)
    with _read(p) as con:
        if con is not None:
            con.execute("BEGIN")  # a document and its rows are one snapshot
            row = con.execute("SELECT body FROM kv WHERE name=?", (kv_name(p),)).fetchone()
            if row is not None:
                return _kept(con, kv_name(p), row[0])
    return None


def update(p: Path | str, change: Callable[[object], object]) -> object:
    """The store whose file is P read, CHANGEd and written in one transaction: CHANGE is handed the
    value (None for none) and returns the new one, returned here. Raises sqlite3.Error, OSError."""
    p = Path(p)
    try:
        with _write(p) as con:
            row = con.execute("SELECT body FROM kv WHERE name=?", (kv_name(p),)).fetchone()
            name = kv_name(p)
            new = change(_kept(con, name, row[0]) if row else None)
            kept = kv_rows.put(con, name, state_paths.put(con, name, new))
            con.execute("INSERT INTO kv(name, body, at) VALUES (?, ?, ?) "
                        "ON CONFLICT(name) DO UPDATE SET "
                        "body=excluded.body, at=excluded.at",
                        (name, _dump(kept), time.time()))  # fmt: skip
    except goh_db.db_core.NewerFile as e:  # this copy may not write it: the value, unkept
        goh_db.refused(e)
        return change(get(p))
    return new


def put(p: Path | str, value: object) -> None:
    update(p, lambda _old: value)


# -- commit_gated: every tree a commit gate passed ------------------------------------------------


def _epoch(at: object) -> float:
    """An ISO stamp (`2026-11-22T21:27:12Z`) or a number as epoch seconds; 0 when neither."""
    if isinstance(at, (int, float)):
        return float(at)
    try:
        return float(calendar.timegm(time.strptime(str(at)[:19], "%Y-%m-%dT%H:%M:%S")))
    except ValueError:
        return 0.0


def _stamp(at: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at))


def gated_add(p: Path | str, row: dict) -> None:
    """One commit-gated row ({tree, repo, at}) for the ledger whose file is P."""
    p = Path(p)
    with goh_db.refusable(), _write(p) as con:
        con.execute("INSERT INTO commit_gated(at, tree, body) VALUES (?, ?, ?)",
                    (_epoch(row.get("at")), str(row["tree"]),
                     _dump(state_paths.put(con, "commit_gated", row))))  # fmt: skip


def gated(p: Path | str) -> list[dict]:
    """The commit-gated rows of the ledger whose path is P, oldest first: {tree, at}, their columns
    (the document's `repo`, a worktree's path, is read by nothing: GOH.BODYCOLS)."""
    with _read(Path(p)) as con:
        if con is None:
            return []
        return [
            {"tree": tree, "at": _stamp(at)}
            for tree, at in con.execute("SELECT tree, at FROM commit_gated ORDER BY n")
        ]


# -- flakes: the flake register --------------------------------------------------------------


def flake_add(d: Path | str, test: str, row: dict) -> None:
    """One row of TEST (a record, or a filing) in the register whose directory is D."""
    d = Path(d)
    own = {"epoch": float(row.get("epoch") or time.time()), "test": test}
    with goh_db.refusable(), goh_db.transaction(db(d)) as con:
        body_columns.write(con, body_tables.FLAKES, state_paths.put(con, "flakes", row), own)


def flakes(d: Path | str, test: str | None = None) -> list[dict]:
    """The register's rows (TEST's only, when named) in the order written; records carry `id`,
    filings `filed`."""
    with contextlib.ExitStack() as stack:
        try:
            con = stack.enter_context(goh_db.ro(db(Path(d))))
            rows = body_columns.read(con, body_tables.FLAKES)
        except (OSError, sqlite3.Error):
            rows = []
    return [state_paths.got("flakes", doc) for own, doc in rows
            if test is None or own["test"] == test]  # fmt: skip


# -- the export ----------------------------------------------------------------------------------


def export(state: Path | str) -> dict[str, object]:
    """Every store as its readers see it: {kv name: value, commit_gated: [row], flakes: [row]}."""
    state = Path(state)
    out: dict[str, object] = {name: get(state / f) for name, f in KV_FILES.items()}
    out["commit_gated"] = gated(state / GATED_FILE)
    out["flakes"] = flakes(state / FLAKE_DIR)
    return out


def main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="small_stores.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=("export",))
    ap.add_argument("state", nargs="?", default=str(goh_db.path().parent))
    ap.add_argument("--to", type=Path, help="export: the directory each store is written to")
    a = ap.parse_args(argv)
    got = export(a.state)
    if a.to is None:
        print(json.dumps(got, sort_keys=True))
        return 0
    a.to.mkdir(parents=True, exist_ok=True)
    for name, value in got.items():
        (a.to / f"{name}.json").write_text(_dump(value) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
