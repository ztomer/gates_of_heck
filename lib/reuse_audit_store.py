"""lib/reuse_audit_store.py -- where lib/reuse_audit.py's records live (GOH.VERDICTDB V1, 0008).

WHY goh.db, not goh-cache.db. A path's record is the audit's one recorded state: OFF means a reuse
disagreed with its fresh run (lib/reuse_audit.py). Deleting it would turn a wrong cache back on,
so it is durable state (handover plan (0) 4), one row of goh.db's `reuse_audit` per registered
path; every compared sample is a row of `reuse_shadow` (the review's evidence).

    db()           NAMED, else <GOH_REUSE_DIR>/goh.db when a caller names the reuse root (every
                   test does, so each test has its own audit, as it had its own files), else
                   goh.db's own path (GOH_DB, else $XDG_STATE_HOME/goh/goh.db)
    get(name)      the record of path NAME, or None
    put(rec)       write REC whole as its path's record (its fields are columns and child rows,
                   lib/body_columns.py)
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
import os
import sqlite3
import sys
import time
from collections.abc import Iterator
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import body_columns  # lib/, beside this file: a record and a sample as columns
import body_tables
import goh_db  # lib/, beside this file: the one door to goh.db
import reuse_store  # lib/: the reuse root (GOH_REUSE_DIR)
import state_paths

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
        got = [] if con is None else body_columns.read(
            con, body_tables.AUDIT, "path = ?", (name,), "path")  # fmt: skip
    return state_paths.got("reuse_audit", got[0][1]) if got else None


def put(rec: dict) -> None:
    """REC written whole as the record of its path (`rec["path"]`)."""
    with goh_db.refusable(), goh_db.transaction(db(), _named()) as con:
        doc, own = state_paths.put(con, "reuse_audit", rec), {"path": str(rec["path"])}
        own["at"], key = time.time(), (own["path"],)
        if not body_columns.update(con, body_tables.AUDIT, doc, own, "path = ?", key):
            body_columns.write(con, body_tables.AUDIT, doc, own)


def shadow_add(row: dict) -> None:
    """One compared sample (`row["path"]`, `row["epoch"]`)."""
    with goh_db.refusable(), goh_db.transaction(db(), _named()) as con:
        own = {"epoch": float(row.get("epoch") or time.time()), "path": str(row.get("path") or "")}
        body_columns.write(con, body_tables.SHADOW, state_paths.put(con, "reuse_shadow", row), own)


def shadow() -> list[dict]:
    """Every compared sample, in the order written."""
    with _read() as con:
        rows = [] if con is None else body_columns.read(con, body_tables.SHADOW)
    return [state_paths.got("reuse_shadow", doc) for _, doc in rows]
