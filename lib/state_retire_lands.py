"""lib/state_retire_lands.py -- `goh state retire lands`: the land queue's done records and its side
files, the store that moved into goh.db 0002 (GOH.STATEDB S1), as lib/state_retire.py retires one.

The queue (GOH_LANDQ_DIR, else the state's `landq/`) held one `done/<run>.json` per finished land,
`red_causes.json` (land-reds' classes), and two ledgers, `guards.jsonl` (land --check's learned
refusals) and `selection_misses.jsonl`. Each is rows now (`lands`, `red_causes`, `land_guards`,
`selection_misses`), keyed by the queue's realpath (lib/land_records.py). The logs, the slots, the
pre-step run directories and lander.lock stay files (handover plan (e)); they are no record here.

A done record whose run is still a request in the queue (pending, running, early: its land is in
flight, an older lander may still rewrite it) is kept, named, until it ends. In the seam with
lib/state_retire.py (tests/_state_seam_scan.py): the one other module naming these files.
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
import land_records  # lib/, on the path from the line above

DONE = "done"
CAUSES = "red_causes.json"
LEDGERS = {"guards.jsonl": "guards", "selection_misses.jsonl": "misses"}  # file -> land_records'


def queue() -> Path:
    """The live land queue: GOH_LANDQ_DIR, else $XDG_STATE_HOME/goh/landq (~/.local/state)."""
    if env := os.environ.get("GOH_LANDQ_DIR"):
        return Path(env)
    import state_retire  # lib/: its state directory (it imports this module: a late import)

    return state_retire.state_dir() / "landq"


def files() -> list[Path]:
    q = queue()
    done = sorted(p for p in (q / DONE).glob("*.json") if not p.name.startswith("."))
    return done + [p for p in (q / CAUSES, *(q / n for n in LEDGERS)) if p.is_file()]


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
                if con.execute(sql, (_key(), p.stem)).fetchone():
                    return "its land is still in flight: retired once it ends"
    return None


def _cause(c: object) -> str:
    return str(c.get("cause") or "") if isinstance(c, dict) else ""


def load(con: sqlite3.Connection, paths: list[Path]) -> None:
    """Each file's records in (INSERT OR IGNORE: a row is newer than its file), each file's bytes in
    `imports`."""
    key, now = _key(), time.time()
    for p in paths:
        if p.parent.name == DONE:
            if isinstance(rec := _json(p), dict):
                con.execute(f"INSERT OR IGNORE {land_records.INSERT}",
                            land_records._row(key, p.stem, rec))  # fmt: skip
            n, table = 1, "lands"
        elif p.name == CAUSES:
            side = _json(p)
            side = side if isinstance(side, dict) else {}
            rows = [(key, name.removesuffix(".json"), _cause(c),
                     land_records.epoch({}, name.removesuffix(".json")), json.dumps(c))
                    for name, c in sorted(side.items())]  # fmt: skip
            con.executemany("INSERT OR IGNORE INTO red_causes (queue, run, cause, at, body) "
                            "VALUES (?, ?, ?, ?, ?)", rows)  # fmt: skip
            n, table = len(rows), "red_causes"
        else:
            name = LEDGERS[p.name]
            n = land_records.insert(con, key, name, _lines(p))
            table = land_records.LEDGERS[name][0]
        sha = hashlib.sha256(p.read_bytes()).hexdigest()
        con.execute("INSERT OR IGNORE INTO imports(store, source, sha256, rows, at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (table, str(p), sha, n, now))  # fmt: skip


def check(con: sqlite3.Connection, p: Path) -> str | None:
    """P's records read back from goh.db: a done record is its row's body; every cause is its row's
    (or a newer classification replaced it: the row was written after P); every ledger line is a
    row of the queue."""
    key = _key()
    if p.parent.name == DONE:
        row = con.execute("SELECT body FROM lands WHERE queue = ? AND run = ?",
                          (key, p.stem)).fetchone()  # fmt: skip
        if row is None:
            return "no lands row"
        return None if json.loads(row[0]) == _json(p) else "its lands row differs"
    if p.name == CAUSES:
        side = _json(p)
        have = {r: (json.loads(b), at) for r, b, at in con.execute(
            "SELECT run, body, at FROM red_causes WHERE queue = ?", (key,))}  # fmt: skip
        bad = [n for n, c in (side.items() if isinstance(side, dict) else ())
               if (got := have.get(n.removesuffix(".json"))) is None or got[0] != c]  # fmt: skip
        return f"{len(bad)} of its causes read back unequal or gone" if bad else None
    table = land_records.LEDGERS[LEDGERS[p.name]][0]
    have = Counter(b for (b,) in con.execute(f"SELECT body FROM {table} WHERE queue = ?", (key,)))
    miss = [r for r in _lines(p) if have[land_records._body(r)] == 0]
    return f"{len(miss)} of its lines have no {table} row" if miss else None
