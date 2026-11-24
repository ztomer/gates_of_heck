"""The land records' fixture side (GOH.STATEDB S1): a test's own goh.db, and a done record put
the way the lander puts it (lib/land_records.py: its goh.db row).

    use(monkeypatch, tmp_path)   GOH_DB at tmp_path/goh.db (never the suite's, never the real one)
    made(monkeypatch, tmp_path)  use(), and the file made: a goh.db holding no record (a missing one
                                 is unknown, never empty: goh_db.Unknown)
    land(q, run, rec)            RUN's record in queue Q, its row as the lander writes it; its
                                 reference (land_records.ref: a name, no file is there)
    landed(q, run)               RUN's record in queue Q (None when it has none)
    unland(q, run)               RUN's record in queue Q gone (a test's "without that record")
    ledger(db, table)            a hook ledger's rows in a test's goh.db (GOH.STATEDBS4)
    ship(result, rec)            a ship record's row at RESULT, as goh ship's save writes it (S2)
    shipped(result)              RESULT's ship row (None when it has none)
    pins(result, body)           a ship's pin record row (lib/consumer_pins.py's install step)
    pinned(result)               RESULT's pin record row (None when it has none)

A ship record and its pins are rows only (GOH.STATEDB S2, wave 3): a test plants them here, never
as files under a ship directory, which nothing reads.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import goh_db
import land_records  # lib/, on the path from the line above
import ship_records


def use(monkeypatch, tmp_path: Path) -> Path:
    db = tmp_path / "goh.db"
    monkeypatch.setenv("GOH_DB", str(db))
    return db


def made(monkeypatch, tmp_path: Path) -> Path:
    db = use(monkeypatch, tmp_path)
    with goh_db.transaction():
        pass
    return db


def land(q: Path, run: str, rec: dict) -> Path:
    land_records.write(q, run, rec)
    return land_records.ref(q, run)


def landed(q: Path, run: str) -> dict | None:
    return land_records.get(q, run)


def unland(q: Path, run: str) -> None:
    with goh_db.transaction() as con:
        con.execute(
            "DELETE FROM lands WHERE queue = ? AND run = ?", (land_records.queue_key(q), run)
        )


def ledger(db: Path, table: str = "agent_spawns") -> list[dict]:
    """TABLE's rows in a test's own goh.db DB, in the order written (lib/hook_ledgers.py,
    GOH.STATEDBS4: agent_spawns, lane_starts); [] when there is no file."""
    if not db.exists():
        return []
    con = sqlite3.connect(db)
    try:
        return [json.loads(b) for (b,) in con.execute(f"SELECT body FROM {table} ORDER BY n")]
    finally:
        con.close()


def ship(result: Path | str, rec: dict) -> Path:
    """REC as RESULT's ship row (its `result` set to RESULT, as lib/ship_status.py save puts it);
    RESULT's directory is made, since its step logs live there."""
    Path(result).parent.mkdir(parents=True, exist_ok=True)
    ship_records.put({**rec, "result": str(result)})
    return Path(result)


def shipped(result: Path | str) -> dict | None:
    return ship_records.get(result)


def pins(result: Path | str, body: dict) -> None:
    ship_records.put_pins(result, body)


def pinned(result: Path | str) -> dict | None:
    return ship_records.get_pins(result)
