"""lib/state_retire_ships.py -- `goh state retire ships`: goh ship's result and pin files, the
store that moved into goh.db 0003 (GOH.STATEDB S2), as lib/state_retire.py retires a store.

A ship directory (lib/ship_records.py ship_dir(): GOH_SHIP_DIR, else the state's `ship/`) held one
result JSON per run, rewritten at each step, and a `<result>.pins.json` sidecar beside it. Both are
rows now (`ships`, `ship_pins`), keyed by the result path. The sweep's record is
lib/state_retire_sweeps.py's; the step logs, the suite's profiles and the coverage summary stay
files (handover plan (e)); they are no record here.

A result that says running while its worker is gone (in ANY repo's directory: goh ship settled
only the shipped repo's) is settled first, as lib/ship_status.py settles one: its row becomes
`failed` at `abandoned`, written now, so it reads back as newer than its file and the archived file
stays the record as it was. A result whose worker still runs is kept, named, until it ends. In the
seam with lib/state_retire.py (tests/_state_seam_scan.py): the one other module naming the files.

GOH.STATEDB S2: `goh state retire ships`, the ship records' half of `state_retire.py`: each result
and `.pins.json` file under `ship_records.ship_dir()` in as its `ships`/`ship_pins` row (INSERT OR
IGNORE), a result saying running whose worker is gone (any repo's) settled in its row as
`ship_status.abandoned` writes it, a live ship's file kept until it ends; `check` passes a row
equal to its file or written after it. The step logs, profiles and covsrc.json stay (sweeps:
`state_retire_sweeps.py`). In the seam beside `state_retire.py`. Pinned by `test_state_retire.py`.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ship_columns  # lib/, on the path from the line above
import ship_records
import ship_status
import state_columns
import state_paths
import state_rows

# a ship directory's files that are no record: the sweep's record, the suite's profiles, the
# coverage summary (the step logs are .log and .jsonl and stay, handover plan (e))
SIDE = (".sweep.json", ".profiles.json")
LIVE = ("running", "bisecting")
PINS = ship_records.PINS


def base() -> Path:
    return ship_records.ship_dir()


def files() -> list[Path]:
    return [p for p in sorted(base().glob("*/*.json"))
            if not p.name.endswith(SIDE) and p.name != "covsrc.json"
            and not p.name.startswith(".")]  # fmt: skip


def _json(p: Path) -> object:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def key(p: Path) -> tuple[str, str]:
    """(table, result) of a ship directory's record file: a `.pins.json` is its result's pins."""
    if p.name.endswith(PINS):
        return "ship_pins", str(p)[: -len(PINS)] + ".json"
    return "ships", str(p)


def keep(p: Path) -> str | None:
    """Why P stays for now: its ship still runs (its worker rewrites it)."""
    rec = _json(p) if key(p)[0] == "ships" else None
    if isinstance(rec, dict) and rec.get("status") in LIVE and not ship_status._gone(rec):
        return "its ship still runs (its worker rewrites it): retired once it ends"
    return None


def _put(con: sqlite3.Connection, rec: dict, result: str, at: float) -> None:
    """REC as ship_records.put writes it, at AT."""
    ship_columns.put(con, state_paths.put("ships", rec), state_paths.encode(result),
                     ship_records.repo_of({**rec, "result": result}), at)  # fmt: skip


def load(con: sqlite3.Connection, paths: list[Path]) -> None:
    """Each file's record in (unless its result has a row: that is newer than its file), a
    dead worker's running result settled in its row, each file's bytes in `imports`."""
    now = time.time()
    for p in paths:
        table, result = key(p)
        rec, raw = _json(p), p.read_bytes()
        if not isinstance(rec, dict):
            continue
        if table == "ships" and ship_status._gone(rec):
            have = ship_columns.read(con, "x.result = ?", (state_paths.encode(result),))
            live = have[0][1] if have else rec
            if ship_status._gone(live):  # its row says running too (or there is none): settle
                _put(con, ship_status.abandoned(live, result), result, now)
        ours = (state_paths.encode(result),)
        if con.execute(f"SELECT 1 FROM {table} WHERE result = ?", ours).fetchone():
            pass  # a row is newer than its file
        elif table == "ships":
            _put(con, rec, result, p.stat().st_mtime)
        else:
            ship_columns.put_pins(con, state_paths.put(table, rec), *ours, p.stat().st_mtime)
        con.execute("INSERT OR IGNORE INTO imports(store, source, sha256, rows, at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (table, state_paths.encode(str(p)), hashlib.sha256(raw).hexdigest(), 1,
                     now))  # fmt: skip


def check(con: sqlite3.Connection, p: Path) -> str | None:
    """P's record is its row's, or its row was written after P (a later step, a settle)."""
    table, result = key(p)
    result = state_paths.encode(result)
    row = con.execute(f"SELECT result, at FROM {table} WHERE result = ?", (result,)).fetchone()
    if row is None:
        return f"no {table} row"
    (doc,) = state_rows.docs(con, table, "x.result = ?", (result,))
    want = _json(p)
    if isinstance(want, dict):
        named = {"result": result, "repo": p.parent.name} if table == "ships" else {}
        want = {**named, **state_paths.put(table, want)}  # the row says both
    differ = state_columns.differs(table, json.dumps(want), json.dumps(doc))
    if not {*differ} - {"consumer_pins"} or float(row[1] or 0) > p.stat().st_mtime:
        return None
    return f"its {table} row differs and is older than the file"
