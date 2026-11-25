"""lib/state_retire_lands.py -- `goh state retire lands`: the land queue's done records and its side
files, the store that moved into goh.db 0002 (GOH.STATEDB S1), as lib/state_retire.py retires one.

The queue (GOH_LANDQ_DIR, else the state's `landq/`) held one `done/<run>.json` per finished land,
`red_causes.json` (land-reds' classes), and two ledgers, `guards.jsonl` (land --check's learned
refusals) and `selection_misses.jsonl`. Each is rows now (`lands`, a class its land's own columns,
`selection_misses`; a guard is its red land's own row, so that file is only archived), keyed by the
queue's realpath (lib/land_records.py). The logs, the slots, the
pre-step run directories and lander.lock stay files (handover plan (e)); they are no record here.

A done record whose run is still a request in the queue (pending, running, early: its land is in
flight, an older lander may still rewrite it) is kept, named, until it ends. In the seam with
lib/state_retire.py (tests/_state_seam_scan.py): the one other module naming these files.

GOH.STATEDB S1: `goh state retire lands`, the land records' half of `state_retire.py`: each
`done/<run>.json` of the land queue (GOH_LANDQ_DIR, else the state's `landq/`) in as its `lands`
row, `red_causes.json` as its lands' classes and `selection_misses.jsonl` as
`selection_misses` rows (INSERT OR IGNORE, each file's bytes in `imports`); a record
whose run is still a request of the queue (its land in flight) is kept until it ends; `check`
passes a record equal to its row and a ledger line its queue holds. The logs, slots, pre-step
directories and lander.lock stay. In the seam beside `state_retire.py`.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import land_columns  # lib/, on the path from the line above
import land_records  # lib/, on the path from the line above
import state_paths
import state_rows

DONE = "done"
CAUSES = "red_causes.json"
MISSES, GUARDS = "selection_misses.jsonl", "guards.jsonl"  # a guard is its red land's own row


def queue() -> Path:
    """The live land queue: GOH_LANDQ_DIR, else $XDG_STATE_HOME/goh/landq (~/.local/state)."""
    if env := os.environ.get("GOH_LANDQ_DIR"):
        return Path(env)
    import state_retire  # lib/: its state directory (it imports this module: a late import)

    return state_retire.state_dir() / "landq"


def files() -> list[Path]:
    q = queue()
    done = sorted(p for p in (q / DONE).glob("*.json") if not p.name.startswith("."))
    return done + [p for p in (q / CAUSES, q / GUARDS, q / MISSES) if p.is_file()]


def _json(p: Path) -> object:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _lines(p: Path) -> list[dict]:
    """P's whole lines that are JSON objects (a torn last line and a bad line are no record)."""
    try:
        data = p.read_bytes()
    except OSError:
        return []
    out = []
    for line in data[: data.rfind(b"\n") + 1].decode("utf-8", errors="replace").splitlines():
        with contextlib.suppress(ValueError):
            if isinstance(row := json.loads(line), dict):
                out.append(row)
    return out


def _key() -> str:
    return land_records.queue_key(queue())


def keep(p: Path) -> str | None:
    """Why P stays for now: its run is still a request of the queue (its land is in flight)."""
    if p.parent.name != DONE:
        return None
    import goh_db

    with contextlib.suppress(OSError, sqlite3.Error), goh_db.ro() as con:
        for table in ("requests", "early"):
            sql = f"SELECT 1 FROM {table} WHERE queue = ? AND run = ?"
            with contextlib.suppress(sqlite3.OperationalError):  # a goh.db before 0007
                if con.execute(sql, (state_paths.encode(_key()), p.stem)).fetchone():
                    return "its land is still in flight: retired once it ends"
    return None


def load(con: sqlite3.Connection, paths: list[Path]) -> None:
    """Each file's records in (INSERT OR IGNORE: a row is newer than its file), each file's bytes in
    `imports`."""
    key, now = _key(), time.time()
    for p in paths:
        if p.parent.name == DONE:
            if isinstance(rec := _json(p), dict):
                at = land_records.epoch(rec, p.stem)
                land_columns.put(con, key, p.stem, rec, at, keep=True)
            n, table = 1, "lands"
        elif p.name == CAUSES:
            side = _json(p)
            side = side if isinstance(side, dict) else {}
            for name, c in side.items():  # a class is its land's own
                run = name.removesuffix(".json")
                land_columns.add_cause(con, key, run, c, lambda rec, run=run: (
                    land_records.epoch(rec, run)))  # fmt: skip
            n, table = len(side), "red_causes"
        elif p.name == GUARDS:
            n, table = 0, "land_guards"
        else:
            n, table = land_records.insert_misses(con, key, _lines(p)), "selection_misses"
        sha = hashlib.sha256(p.read_bytes()).hexdigest()
        con.execute("INSERT OR IGNORE INTO imports(store, source, sha256, rows, at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (table, state_paths.encode(str(p)), sha, n, now))  # fmt: skip


def check(con: sqlite3.Connection, p: Path) -> str | None:
    """P's records read back from goh.db: a done record is its row's; every cause is its row's
    (or a newer classification replaced it: the row was written after P); every ledger line is a
    row of the queue."""
    key = _key()
    if p.parent.name == DONE:
        have = land_columns.read(con, key, runs=[p.stem]).get(p.stem)
        if have is None:
            return "no lands row"
        want = _json(p)
        if isinstance(want, dict) and not want.get("at") and "at" in have:
            want = {**want, "at": have["at"]}  # a row written by name is never without one
        if isinstance(want, dict) and not want.get("red_cause"):
            have = {k: v for k, v in have.items() if k != "red_cause"}  # CAUSES gave it
        same = state_rows.said("lands", have) == state_rows.said("lands", want)
        return None if same else "its lands row differs"
    if p.name == CAUSES:
        side = _json(p)
        own = land_columns.read(con, key, ("red_cause",))
        bad = [n for n in (side if isinstance(side, dict) else ())
               if (r := own.get(n.removesuffix(".json"))) is not None
               and not r.get("red_cause")]  # fmt: skip  # one with a class keeps its own
        return f"{len(bad)} of its causes are in no land" if bad else None
    if p.name == GUARDS:
        return None  # each is its red land's own rows, counted there
    table = "selection_misses"
    have = state_rows.docs(con, table, *land_columns.ours(key))
    texts = Counter(state_rows.said(table, d) for d in have)
    miss = [r for r in _lines(p) if texts[state_rows.said(table, r)] == 0]
    return f"{len(miss)} of its lines have no {table} row" if miss else None
