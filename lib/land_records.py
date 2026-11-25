"""lib/land_records.py -- the land records' one door (GOH.STATEDB S1): goh.db's `lands` and
`red_causes` tables (lib/goh_db_schema/0002_lands.sql) and the `land_guards` and
`selection_misses` ledgers. The `<landq>/done/*.json` files they were are read by nothing since the
database plan's wave 3 (`goh state retire lands`, lib/state_retire_lands.py, archives them).

    write(q, run, rec)     the record's row (the request it finishes leaves the queue with it)
    runs(q)                every run with a record, by name only
    records(q)             [(run, rec)] oldest first
    get(q, run)            one record, or None (`goh land --wait` polls it)
    ref(q, run)            a record's reference: a path no file is at (lib/landq_look.py's)
    causes(q) / set_causes(q, {run: cause})     the red classes
    ledger(q, name)        a ledger's rows oldest first: "guards" (land_guards: the pre-step
                           refusals land --check learns) or "misses" (selection_misses)
    append(q, name, rows)  rows onto a ledger, each body once per queue; how many were new

A RED ROW NAMES ITS CAUSE (GOH.LANDEREXIT1): `write` gives a red record that has none its cause
class from its log (lib/land_cause.py `explain`) and refuses one whose `cause` is no class
(Unexplained): no red row says only `exit 1`.

A ROW IS KEYED BY (queue, run), a ledger row by (queue, body): `queue` is the land queue's
realpath, so a test's queue under tmp_path never reads the lander's rows, nor another test's. No
goh.db yet, or one an older goh wrote without the tables, reads as no rows; `known()` says
whether there is a goh.db at all (a report says "unknown" without one, never zero).

GOH.STATEDB S1: the land records' one door, goh.db's `lands` and `red_causes` keyed by (queue
realpath, run); since wave 3 the rows are the one record and no `done/` file is read or written.
`write` (the row, the request it finishes gone in the same transaction; a row that cannot be
written is tried once more, then said and raised by `goh_db.must`), `records` (every row of the
queue), `get` (`goh land --wait`), `runs` (names only), `ref` (a record's reference: a name, no
file), `known` (whether there is a goh.db: `goh land --report` says unknown without one),
`causes`/`set_causes`, `ledger`/`append`/`insert` (`land_guards`, `selection_misses`; a body once
per queue). No goh.db yet reads as no rows. The old files are archived by `state_retire_lands.py`.
Invoked by `landq.py` `record`, `land_reds.py`, `land_check.py` `learn`, `selection_miss.py`, every
land-record reader. Pinned by `test_land_records.py`, `test_record_must.py`
(`tests/_goh_db_fixture.py`).
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import goh_db  # lib/, on the path from the line above
import land_cause
import landq_state

COLS = ("item", "sha", "enqueued", "repo", "mode", "state", "attempt", "requested", "started",
        "finished", "k")  # fmt: skip


def queue_key(q: Path) -> str:
    return os.path.realpath(q)


REF = "lands"  # the table a record's reference names


def ref(q: Path, run: str) -> Path:
    """RUN's record's reference beside queue Q's requests: `<q>/lands/<run>.json`, a path no file
    is at (lib/landq_look.py tells a record from a request by its parent's name)."""
    return q / REF / f"{run}.json"


def known() -> bool:
    """Whether goh.db exists: without it the lands are unknown, never none (handover plan (d))."""
    return goh_db.path().exists()


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


class Unexplained(ValueError):
    """A red record whose `cause` is no class of lib/land_cause.py's NAMED."""


def explained(rec: dict) -> dict:
    """REC with its cause named when it is red and names none; Unexplained when its cause is no
    class (module doc, GOH.LANDEREXIT1)."""
    if rec.get("state") != "red":
        return rec
    rec = rec if "cause" in rec else {**rec, **land_cause.explain(rec)}
    if rec.get("cause") not in land_cause.NAMED:
        raise Unexplained(f"a red land record with no cause class: {rec.get('cause')!r} "
                          f"(why: {str(rec.get('why'))[:120]})")  # fmt: skip
    return rec


def write(q: Path, run: str, rec: dict, request: Path | None = None) -> None:
    """RUN's record: its row, replacing one before it (its one home since wave 3). REQUEST, the
    request it finishes (lib/landq_state.py), leaves the queue in the same transaction: a kill
    leaves it queued or done, never both (GOH.LANDQDB). A row that cannot be written is tried once
    more, then said and raised (goh_db.must): the request stays queued for the next lander."""
    rec = explained(rec)

    def put() -> None:
        with goh_db.transaction() as con:
            con.execute(f"INSERT OR REPLACE {INSERT}", _row(queue_key(q), run, rec))
            if request is not None:
                landq_state.finish(con, request)

    goh_db.must(put, f"the land record {run}")  # raised after one retry: never a land unrecorded


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


def records(q: Path, reverse: bool = False) -> list[tuple[str, dict]]:
    """`(run, record)` for every land of queue Q, oldest first (newest first with REVERSE)."""
    return sorted(_rows(q).items(), reverse=reverse)


def runs(q: Path) -> list[str]:
    """Every run queue Q has a record of, by name only (no record is parsed), oldest first."""
    have: set[str] = set()
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
    return None


def causes(q: Path) -> dict[str, dict]:
    """{`<run>.json`: cause} for queue Q."""
    out: dict[str, dict] = {}
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
    with goh_db.refusable(), goh_db.transaction() as con:
        con.execute("DELETE FROM red_causes WHERE queue = ?", (key,))
        con.executemany(
            "INSERT INTO red_causes (queue, run, cause, at, body) VALUES (?, ?, ?, ?, ?)",
            [(key, name.removesuffix(".json"), str(c.get("cause") or ""),
              epoch({}, name.removesuffix(".json")), json.dumps(c))
             for name, c in sorted(side.items())],
        )  # fmt: skip


# a ledger: its table and the column a row's key fills
LEDGERS = {"guards": ("land_guards", "test"), "misses": ("selection_misses", "day")}


def _body(row: dict) -> str:
    return json.dumps(row, sort_keys=True)


def _ledger_row(key: str, name: str, row: dict) -> tuple:
    col = LEDGERS[name][1]
    val = row.get("guard") if col == "test" else row.get("day")
    at = epoch({"at": f"{val}T00:00:00Z"}) if col == "day" and val else epoch(row)
    return (key, at, None if val is None else str(val), _body(row))


def ledger(q: Path, name: str) -> list[dict]:
    """Ledger NAME of queue Q, in the order its rows were appended."""
    table, bodies = LEDGERS[name][0], []
    with _reader() as con:
        if con is not None:
            sql = f"SELECT body FROM {table} WHERE queue = ? ORDER BY n"
            bodies = [b for (b,) in con.execute(sql, (queue_key(q),))]
    out = []
    for body in bodies:
        with contextlib.suppress(ValueError):
            out.append(json.loads(body))
    return out


def insert(con: sqlite3.Connection, key: str, name: str, rows: list[dict]) -> int:
    """ROWS onto ledger NAME of queue KEY in CON's transaction, each body once; how many new."""
    table, col = LEDGERS[name]
    before = con.total_changes
    sql = f"INSERT OR IGNORE INTO {table} (queue, at, {col}, body) VALUES (?, ?, ?, ?)"
    con.executemany(sql, [_ledger_row(key, name, r) for r in rows])
    return con.total_changes - before


def append(q: Path, name: str, rows: list[dict]) -> int:
    """ROWS onto ledger NAME of queue Q, each body once; how many were new (0 when this copy may
    not write goh.db, said)."""
    with goh_db.refusable(), goh_db.transaction() as con:
        return insert(con, queue_key(q), name, rows)
    return 0
