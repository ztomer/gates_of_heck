"""lib/reuse_audit_store.py -- where lib/reuse_audit.py's records live (GOH.VERDICTDB V1, 0008).

WHY goh.db, not goh-cache.db. A path's record is the audit's one recorded state: OFF means a reuse
disagreed with its fresh run (lib/reuse_audit.py). Deleting it would turn a wrong cache back on,
so it is durable state (handover plan (0) 4), one row of goh.db's `reuse_audit` per registered
path; every compared sample is a row of `reuse_shadow` (the review's evidence).

    db()           NAMED, else <GOH_REUSE_DIR>/goh.db when a caller names the reuse root (every
                   test does, so each test has its own audit, as it had its own files), else
                   goh.db's own path (GOH_DB, else $XDG_STATE_HOME/goh/goh.db)
    get(name)      the record of path NAME, or None
    put(rec)       write REC whole as its path's record
    shadow_add(row)  one compared sample
    shadow()       every sample, oldest first

THE FILES BEFORE THE SWITCH are read by nothing (wave 3's delete commit): `goh state retire
reuse-audit` imports the record files and shadow.jsonl (INSERT OR IGNORE, the `imports` ledger),
reads every one back and archives them (lib/state_retire.py). The audit's flock is reuse/audit.lock,
beside the directory that went (handover plan (e): locks stay files).

A WRITE THIS COPY MAY NOT MAKE (a newer goh's file, a lane's copy before main applies 0008) is
skipped with one stderr line (goh_db.refusable): the audit's count is lost, never a gate's verdict.
NAMED: a land suite's controller counts its reuses into the caller's goh.db, which the runner
names (tests/_land_verdicts.py, GOH_LAND_VERDICTS_AUDIT_DB), the one real-home goh.db it may open.
Pinned by tests/test_reuse_audit_store.py.

GOH.VERDICTDB V1: where `reuse_audit.py`'s records live, durable state in goh.db (migration 0008),
never the disposable cache: one `reuse_audit` row per path (`get`/`put`) and one `reuse_shadow` row
per compared sample (`shadow_add`/`shadow`). `db()`: `NAMED` (a land suite's controller: the
caller's goh.db, `GOH_LAND_VERDICTS_AUDIT_DB`), else beside a named `GOH_REUSE_DIR`, else goh.db's
own. Files from before the switch are read by nothing (`state_retire_verdicts.py` imports, reads
back and archives them); `lock_path()` is `reuse/audit.lock`.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import sys
import time
from collections.abc import Iterator
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import goh_db  # lib/, beside this file: the one door to goh.db
import reuse_store  # lib/: the reuse root (GOH_REUSE_DIR)

NAMED: Path | None = None  # the caller's goh.db a land suite's controller counts into


def db() -> Path:
    if NAMED is not None:
        return NAMED
    if reuse_store.root_named():
        return reuse_store.root() / goh_db.SEAM.file
    return goh_db.path()


def lock_path() -> Path:
    """lib/reuse_audit.py's flock, a file under the reuse root (handover plan (e): locks stay)."""
    return reuse_store.root() / "audit.lock"


def _named() -> bool:
    return NAMED is not None


def _dump(row: object) -> str:
    return json.dumps(row, sort_keys=True)


@contextlib.contextmanager
def _read() -> Iterator[sqlite3.Connection | None]:
    """The database read-only, or None when there is none (or it predates 0008)."""
    with contextlib.ExitStack() as stack:
        try:
            con = stack.enter_context(goh_db.ro(db(), _named()))
            con.execute("SELECT 1 FROM reuse_audit LIMIT 1")
        except (OSError, sqlite3.Error):
            con = None
        yield con


def get(name: str) -> dict | None:
    """Path NAME's record, or None."""
    with _read() as con:
        row = None if con is None else con.execute(
            "SELECT body FROM reuse_audit WHERE path=?", (name,)).fetchone()  # fmt: skip
    rec = json.loads(row[0]) if row else None
    return rec if isinstance(rec, dict) else None


def put(rec: dict) -> None:
    """REC written whole as the record of its path (`rec["path"]`)."""
    with goh_db.refusable(), goh_db.transaction(db(), _named()) as con:
        con.execute("INSERT INTO reuse_audit(path, body, at) VALUES (?, ?, ?) ON CONFLICT(path) "
                    "DO UPDATE SET body=excluded.body, at=excluded.at",
                    (str(rec["path"]), _dump(rec), time.time()))  # fmt: skip


def shadow_add(row: dict) -> None:
    """One compared sample (`row["path"]`, `row["epoch"]`)."""
    with goh_db.refusable(), goh_db.transaction(db(), _named()) as con:
        con.execute("INSERT INTO reuse_shadow(epoch, path, body) VALUES (?, ?, ?)",
                    (float(row.get("epoch") or time.time()), str(row.get("path") or ""),
                     _dump(row)))  # fmt: skip


def shadow() -> list[dict]:
    """Every compared sample, in the order written."""
    with _read() as con:
        rows = [] if con is None else [
            b for (b,) in con.execute("SELECT body FROM reuse_shadow ORDER BY n")]  # fmt: skip
    return [json.loads(b) for b in rows]
