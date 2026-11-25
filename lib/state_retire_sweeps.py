"""lib/state_retire_sweeps.py -- `goh state retire sweeps`: the pin push sweeps' record files, the
store that moved into goh.db 0022 (GOH.PINSWEEP), as lib/state_retire.py retires a store.

A sweep was one JSON file beside its ship's result (`ship/<repo>/<run>.sweep.json`), or beside the
estate audit's state for a hand run (`wf_pin.sweep.json`), rewritten at each outcome. It is rows now
(lib/push_sweep_rows.py), named by that path. Its log, its pushes' logs and its timings' spool stay
files. A row already there is the sweep's own, newer than its file.

A FILE WHOSE SUPERVISOR IS GONE and says running, or says `failed` with a repo still `pushing` (one
of 62, 2026-11-20: a supervisor died and nothing ended its record), goes in settled as
lib/pin_sweep.py settles one: failed, the repos it never reached left unpushed with why. One whose
supervisor lives is kept. In the seam beside lib/state_retire.py (tests/_state_seam_scan.py).
Pinned by tests/test_state_retire_sweeps.py.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import estate_registry  # lib/, on the path from the line above
import goh_db
import pid_owner
import push_sweep_rows as rows
import ship_records
import state_paths

SUFFIX = ".sweep.json"
SETTLED = "not pushed: the sweep's supervisor ended before its push; the next sweep pushes it"


def files() -> list[Path]:
    hand = estate_registry.state_file().with_name(f"wf_pin{SUFFIX}")
    return sorted(ship_records.ship_dir().glob(f"*/*{SUFFIX}")) + [hand] * hand.is_file()


def _json(p: Path) -> dict | None:
    try:
        got = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return got if isinstance(got, dict) and isinstance(got.get("repos"), dict) else None


def _alive(doc: dict) -> bool:
    return doc.get("status") in rows.LIVE and pid_owner.alive_line(
        str(doc.get("pid_stamp") or doc.get("pid") or ""))  # fmt: skip


def keep(p: Path) -> str | None:
    """Why P stays for now: its supervisor rewrites it."""
    doc = _json(p)
    return "its supervisor still runs: retired once it ends" if doc and _alive(doc) else None


def _at(said: object) -> float | None:
    return datetime.fromisoformat(said).timestamp() if isinstance(said, str) else None


def record(doc: dict) -> dict:
    """DOC, a file's, as lib/push_sweep_rows.py puts and reads a sweep."""
    ended = doc.get("status") == "done"
    repos = {}
    for where, r in doc["repos"].items():
        row = {"repo": r["repo"], "status": r["status"], "said": r.get("said"),
               "log": r.get("log"), "at": _at(r.get("at"))}  # fmt: skip
        if row["status"] in rows.OPEN:
            row |= {"status": "committed", "said": SETTLED}
        repos[where] = {k: v for k, v in row.items() if v is not None}
    sweep = {
        "run": "file", "status": "done" if ended else "failed", "created": _at(doc.get("created")),
        "quiet": 0.0, "every": 0.0, "started": _at(doc.get("started")),
        "finished": _at(doc.get("finished")), "budget": float(doc.get("budget") or 0),
        "ship": doc.get("waits_on"),
        "ship_worker": doc.get("waits_on_pid_stamp"), "supervisor": doc.get("pid_stamp"),
        "exit": doc.get("load_exit"), "why": None if ended else SETTLED, "log": doc.get("log"),
    }  # fmt: skip
    return {**{k: v for k, v in sweep.items() if v is not None}, "repos": repos}


def _has(con: sqlite3.Connection, p: Path) -> bool:
    return con.execute("SELECT 1 FROM push_sweeps WHERE sweep = ?",
                       (rows.key(p),)).fetchone() is not None  # fmt: skip


def load(con: sqlite3.Connection, paths: list[Path]) -> None:
    for p in paths:
        doc, n = _json(p), 0
        if doc is not None and not _has(con, p):
            rec, n = record(doc), 1
            rows.insert(con, rows.key(p), rec, rec.get("finished") or p.stat().st_mtime)
        con.execute("INSERT OR IGNORE INTO imports(store, source, sha256, rows, at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    ("push_sweeps", state_paths.encode(str(p)),
                     hashlib.sha256(p.read_bytes()).hexdigest(), n, time.time()))  # fmt: skip


def check(con: sqlite3.Connection, p: Path) -> str | None:
    """P's record is its rows', or its rows are a later start's of the same name."""
    doc = _json(p)
    if doc is None:
        return "no sweep's record"
    got = rows.document(con, rows.key(p))
    if not got:
        return "no push_sweeps row"
    got.pop("path")
    if got["run"] != "file" or got == record(doc):
        return None
    return "its push_sweeps rows differ"


STORE = {"files": files, "load": load, "check": check, "keep": keep, "db": goh_db}
