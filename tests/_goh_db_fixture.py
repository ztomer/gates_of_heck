"""The land records' fixture side (GOH.STATEDB S1): a test's own goh.db, and a done record put
the way the lander puts it (lib/land_records.py: the row, then the done/ file).

    use(monkeypatch, tmp_path)   GOH_DB at tmp_path/goh.db (never the suite's, never the real one)
    put(q, run, rec)             RUN's record in queue Q, row and file
    ledger(db, table)            a hook ledger's rows in a test's goh.db (GOH.STATEDBS4)
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import land_records  # lib/, on the path from the line above


def use(monkeypatch, tmp_path: Path) -> Path:
    db = tmp_path / "goh.db"
    monkeypatch.setenv("GOH_DB", str(db))
    return db


def put(q: Path, run: str, rec: dict) -> Path:
    land_records.write(q, run, rec)
    return land_records.file(q, run)


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
