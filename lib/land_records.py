"""lib/land_records.py -- the land records' one door (GOH.STATEDB S1): goh.db's `lands` and
`red_causes` tables (lib/goh_db_schema/0002_lands.sql), with the `<landq>/done/*.json` files from
before the switch still read.

    write(q, run, rec)     the record's row, then its done/ file (kept until wave 3's delete commit:
                           .loop's readers and old HEAD exports still read the files)
    runs(q)                every run with a record, by name only
    records(q)             [(run, rec)] oldest first: every row of this queue, and every done/ file
                           whose run has no row (written by an old HEAD export, or not imported)
    get(q, run)            one record, or None (`goh land --wait` polls it)
    causes(q) / set_causes(q, {run: cause})     the red classes (was `<q>/red_causes.json`)
    import_done(q)         every done/ file and red_causes.json into the tables, INSERT OR IGNORE
                           (a re-run is harmless), one `imports` row per run that added rows

A ROW IS KEYED BY (queue, run): `queue` is the land queue's realpath, so a test's queue under
tmp_path never reads the lander's rows, nor another test's. No goh.db yet, or one an older goh
wrote without the tables, reads as no rows: the files answer.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import goh_db  # lib/, on the path from the line above
import landq_state

COLS = ("item", "sha", "enqueued", "repo", "mode", "state", "attempt", "requested", "started",
        "finished", "k")  # fmt: skip


def queue_key(q: Path) -> str:
    return os.path.realpath(q)


def file(q: Path, run: str = "") -> Path:
    """Queue Q's done/ directory, or RUN's file in it: the files from before the switch, read and
    still written until wave 3's delete commit (the one place this module names them)."""
    d = q / "done"
    return d / f"{run}.json" if run else d


def epoch(rec: dict, run: str = "") -> float:
    """The record's epoch seconds: its `at`, else `finished`, else the run's enqueue stamp."""
    for key in ("at", "finished"):
        raw = rec.get(key)
        if isinstance(raw, str) and raw:
            with contextlib.suppress(ValueError):
                return datetime.fromisoformat(raw).timestamp()
    ns = run.partition("-")[0]
    return int(ns) / 1e9 if ns.isdigit() else 0.0


def _row(queue: str, run: str, rec: dict) -> tuple:
    vals = [rec.get(c) for c in COLS]
    vals = [v if v is None or isinstance(v, (int, float, str)) else json.dumps(v) for v in vals]
    return (queue, run, *vals, epoch(rec, run), json.dumps(rec))


INSERT = (f"INTO lands (queue, run, {', '.join(COLS)}, at, body) VALUES "
          f"({', '.join('?' * (len(COLS) + 4))})")  # fmt: skip


def write(q: Path, run: str, rec: dict) -> None:
    """RUN's record: its row (replacing one before it), then `done/<run>.json`. A row that could
    not be written is said, and the file still holds the record for every reader."""
    try:
        with goh_db.transaction() as con:
            con.execute(f"INSERT OR REPLACE {INSERT}", _row(queue_key(q), run, rec))
    except (OSError, sqlite3.Error) as e:
        sys.stderr.write(f"land_records: {run}: no goh.db row ({e}); done/ holds it\n")
    file(q).mkdir(parents=True, exist_ok=True)
    landq_state.write(file(q, run), rec)


@contextlib.contextmanager
def _reader():
    """goh.db read-only, or None when there is no file or no lands table yet."""
    try:
        with goh_db.ro() as con:
            has = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='lands'")
            yield con if has.fetchone() else None
    except FileNotFoundError:
        yield None


def _rows(q: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    with _reader() as con:
        if con is None:
            return out
        for run, body in con.execute("SELECT run, body FROM lands WHERE queue = ? ORDER BY run",
                                     (queue_key(q),)):  # fmt: skip
            with contextlib.suppress(ValueError):
                rec = json.loads(body)
                if isinstance(rec, dict):
                    out[run] = rec
    return out


def _files(q: Path) -> list[str]:
    try:
        names = os.listdir(file(q))
    except OSError:
        return []
    return [n[:-5] for n in names if n.endswith(".json") and not n.startswith(".")]


def records(q: Path, reverse: bool = False) -> list[tuple[str, dict]]:
    """`(run, record)` for every land of queue Q, oldest first (newest first with REVERSE)."""
    out = _rows(q)
    for run in _files(q):
        if run not in out and (rec := landq_state.read(file(q, run))) is not None:
            out[run] = rec
    return sorted(out.items(), reverse=reverse)


def runs(q: Path) -> list[str]:
    """Every run queue Q has a record of, by name only (no record is parsed), oldest first."""
    have: set[str] = set(_files(q))
    with _reader() as con:
        if con is not None:
            have.update(r[0] for r in con.execute("SELECT run FROM lands WHERE queue = ?",
                                                  (queue_key(q),)))  # fmt: skip
    return sorted(have)


def get(q: Path, run: str) -> dict | None:
    """RUN's record, or None while it has none (a torn file reads as none: read again)."""
    with _reader() as con:
        if con is not None:
            got = con.execute("SELECT body FROM lands WHERE queue = ? AND run = ?",
                              (queue_key(q), run)).fetchone()  # fmt: skip
            if got:
                with contextlib.suppress(ValueError):
                    rec = json.loads(got[0])
                    if isinstance(rec, dict):
                        return rec
    return landq_state.read(file(q, run))


SIDE = "red_causes.json"


def causes(q: Path) -> dict[str, dict]:
    """{`<run>.json`: cause} for queue Q: the rows, then the side file's names they lack."""
    out: dict[str, dict] = {}
    with contextlib.suppress(OSError, ValueError):
        side = json.loads((q / SIDE).read_text())
        if isinstance(side, dict):
            out.update(side)
    with _reader() as con:
        if con is not None:
            for run, body in con.execute("SELECT run, body FROM red_causes WHERE queue = ?",
                                         (queue_key(q),)):  # fmt: skip
                with contextlib.suppress(ValueError):
                    out[f"{run}.json"] = json.loads(body)
    return out


def set_causes(q: Path, side: dict[str, dict]) -> None:
    """Queue Q's red classes are SIDE (`<run>.json` -> cause), replacing the ones before."""
    key = queue_key(q)
    with goh_db.transaction() as con:
        con.execute("DELETE FROM red_causes WHERE queue = ?", (key,))
        con.executemany(
            "INSERT INTO red_causes (queue, run, cause, at, body) VALUES (?, ?, ?, ?, ?)",
            [(key, name.removesuffix(".json"), str(c.get("cause") or ""),
              epoch({}, name.removesuffix(".json")), json.dumps(c))
             for name, c in sorted(side.items())],
        )  # fmt: skip


def import_done(q: Path) -> dict[str, int]:
    """Every done/ file and the red_causes.json side file of queue Q into goh.db, INSERT OR IGNORE
    on (queue, run); the rows each added. The files stay (wave 3 deletes them)."""
    key, digest, rows = queue_key(q), hashlib.sha256(), []
    for run in sorted(_files(q)):
        p = file(q, run)
        with contextlib.suppress(OSError):
            raw = p.read_bytes()
            with contextlib.suppress(ValueError):
                rec = json.loads(raw)
                if isinstance(rec, dict):
                    rows.append(_row(key, run, rec))
                    digest.update(run.encode() + b"\0" + raw)
    side: dict = {}
    with contextlib.suppress(OSError, ValueError):
        side = json.loads((q / SIDE).read_text())
    side = side if isinstance(side, dict) else {}
    with goh_db.transaction() as con:
        before = con.total_changes
        con.executemany(f"INSERT OR IGNORE {INSERT}", rows)
        lands = con.total_changes - before
        before = con.total_changes
        con.executemany(
            "INSERT OR IGNORE INTO red_causes (queue, run, cause, at, body) VALUES (?, ?, ?, ?, ?)",
            [(key, n.removesuffix(".json"), str(c.get("cause") or "") if isinstance(c, dict)
              else "", epoch({}, n.removesuffix(".json")), json.dumps(c))
             for n, c in sorted(side.items())],
        )  # fmt: skip
        reds = con.total_changes - before
        if lands or reds:
            con.execute("INSERT OR IGNORE INTO imports VALUES (?, ?, ?, ?, ?)",
                        ("lands", str(file(q)), digest.hexdigest(), lands + reds,
                         time.time()))  # fmt: skip
    return {"lands": lands, "red_causes": reds}
