"""lib/timing_derived.py -- the timing ledger's rows that are another table's facts (GOH.STEPTIMES).

    KINDS                  `ship` (a row of goh.db's ships), `land attempt` (a row of lands)
    has(con, row)          whether ROW's run is in its table: then it is kept there alone
    rows(kinds, since)     those kinds' rows since SINCE (epoch), made of the records

A ship's and a land attempt's wall time, outcome, item and loads are columns of `ships` and `lands`:
a ledger row beside them was a second copy, filled by a backfill. lib/timing_ledger.py `read` asks
here instead; a row whose record is gone stays a span (lib/span_store.py). Pinned by
tests/test_timing_ledger.py and test_span_store.py.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db_core
import goh_db  # a missing goh.db is unknown, never none

TABLES = {"ship": "ships", "land attempt": "lands"}
KINDS = tuple(TABLES)


def has(con: sqlite3.Connection, row: dict) -> bool:
    table = TABLES.get(str(row.get("kind")))
    return bool(table and row.get("ref") and con.execute(
        f"SELECT 1 FROM {table} WHERE run = ? LIMIT 1", (str(row["ref"]),)).fetchone())  # fmt: skip


def _attempts() -> list[dict]:
    import land_attempts  # reads the ledger itself: imported when asked
    import land_records

    reds: dict[str, int] = {}
    out = []
    for run, rec in land_records.records(land_attempts.queue_dir()):
        path = Path(f"{run}.json")
        item = land_attempts.item_of(path, rec)
        n = reds.get(item, 0)
        reds[item] = 0 if rec.get("state") == "landed" else n + 1
        row = land_attempts.row_of({"item": item, "attempt": n + 1, **rec}, path.stem)
        out += [row] if row else []
    return out


def _ships() -> list[dict]:
    import budget_ships
    import ship_records

    return [r for _, res in ship_records.every() for r in budget_ships.ship_rows(res)
            if r["kind"] == "ship"]  # fmt: skip


def rows(kinds: tuple[str, ...], since: float) -> list[dict]:
    found: list[dict] = []
    for kind, made in (("ship", _ships), ("land attempt", _attempts)):
        if kind in kinds:
            try:
                found += made()
            except (goh_db.Unknown, OSError, sqlite3.Error, ValueError) as e:
                db_core.warn(f"timing rows: the {TABLES[kind]} are unknown ({e})")
    return [r for r in found if r["epoch"] >= since]
