"""lib/land_records.py -- the land records' one door (GOH.STATEDB S1): goh.db's `lands`
(lib/goh_db_schema/0002_lands.sql) and its `selection_misses`. The `<landq>/done/*.json` files they
were are read by nothing since the database plan's wave 3 (`goh state retire lands`,
lib/state_retire_lands.py, archives them).

    write(q, run, rec)     the record's row (the request it finishes leaves the queue with it)
    runs(q) / stamps(q)    every run with a record, by name only; {run: its epoch seconds}
    records(q, keys=, runs=)   [(run, rec)] oldest first; KEYS: those keys of each alone
    get(q, run)            one record, or None (`goh land --wait` polls it)
    ref(q, run)            a record's reference: a path no file is at (lib/landq_look.py's)
    causes(q) / set_causes(q, {run: cause})     the red classes; a class goes to a red that has none
    misses(q)              the selection misses, oldest first (lib/selection_miss.py)
    append_misses(q, rows)     rows onto them, each once per queue; how many were new

A RECORD IS ITS COLUMNS AND CHILD ROWS (GOH.LANDREADERS, lib/land_columns.py): `write` upserts them,
every reader here reads them. A reader of a few keys names them, and a
key it did not name is an error (land_columns.Part).

A RED ROW NAMES ITS CAUSE (GOH.LANDEREXIT1): `write` gives a red record that has none its cause
class from its log (lib/land_cause.py `explain`) and refuses one whose `cause` is no class
(Unexplained): no red row says only `exit 1`.

A ROW IS KEYED BY (queue, run), a miss by all it says: `queue` is the land queue's
realpath, so a test's queue under tmp_path never reads the lander's rows, nor another test's. No
goh.db yet, or one an older goh wrote without the tables, reads as no rows; `known()` says
whether there is a goh.db at all (a report says "unknown" without one, never zero).

GOH.STATEDB S1: the land records' one door, goh.db's `lands` keyed by (queue realpath, run); since
wave 3 the rows are the one record and no `done/` file is read or written. `write` (the row, the
request it finishes gone in the same transaction; a row that cannot be written is tried once more,
then said and raised by `goh_db.must`), `records` (every row of the queue), `get` (`goh land
--wait`), `runs` (names only), `ref` (a record's reference: a name, no file), `known` (whether there
is a goh.db: `goh land --report` says unknown without one), `stamps`, `causes`/`set_causes` (a red's
class is its land's own columns), `misses`/`append_misses`/`insert_misses` (`selection_misses`; each
once per queue). No goh.db yet reads as no rows. The old files are archived by
`state_retire_lands.py`. Invoked by `landq.py` `record`, `land_reds.py`, `selection_miss.py`, every
land-record reader. Pinned by `test_land_records.py`, `test_record_must.py`
(`tests/_goh_db_fixture.py`).
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import sys
from collections.abc import Collection, Iterable
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import body_columns  # lib/, on the path from the line above
import body_tables
import goh_db  # lib/, on the path from the line above
import land_cause
import land_columns
import landq_state
import state_paths


def queue_key(q: Path) -> str:
    return os.path.realpath(q)


def _ours(q: Path) -> tuple[str, tuple[str, ...]]:
    """(clause, args) naming queue Q's rows."""
    return land_columns.ours(queue_key(q))


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
            land_columns.put(con, queue_key(q), run, rec, epoch(rec, run))
            if request is not None:
                landq_state.finish(con, request)

    goh_db.must(put, f"the land record {run}")  # raised after one retry: never a land unrecorded


@contextlib.contextmanager
def _reader():
    """goh.db read-only, or None when there is no file or no lands table yet."""
    try:
        with goh_db.ro() as con:
            con.execute("BEGIN")  # one snapshot: a record's columns and child rows are one write's
            has = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='lands'")
            yield con if has.fetchone() else None
    except FileNotFoundError:
        yield None


def records(
    q: Path, reverse: bool = False, keys: Collection[str] | None = None,
    runs: Iterable[str] | None = None,
) -> list[tuple[str, dict]]:  # fmt: skip
    """`(run, record)` for every land of queue Q (of RUNS alone), oldest first (newest first with
    REVERSE); with KEYS, those keys of each record alone, read from their columns."""
    with _reader() as con:
        got = {} if con is None else land_columns.read(con, queue_key(q), keys, runs)
    return sorted(got.items(), key=lambda kv: kv[0], reverse=reverse)


def runs(q: Path) -> list[str]:
    """Every run queue Q has a record of, by name only (no record is read), oldest first."""
    return sorted(stamps(q))


def stamps(q: Path) -> dict[str, float]:
    """{run: its record's epoch seconds} for queue Q, no record read."""
    with _reader() as con:
        ours, args = _ours(q)
        sql = f"SELECT run, at FROM lands WHERE {ours}"
        return {} if con is None else dict(con.execute(sql, args))


def get(q: Path, run: str) -> dict | None:
    """RUN's record, or None while it has none."""
    return dict(records(q, runs=[run])).get(run)


def causes(q: Path) -> dict[str, dict]:
    """{`<run>.json`: cause} for queue Q's lands that have one."""
    with _reader() as con:
        own = {} if con is None else land_columns.read(con, queue_key(q), ("red_cause",))
    return {f"{run}.json": c for run, r in own.items() if (c := r.get("red_cause"))}


def set_causes(q: Path, side: dict[str, dict]) -> None:
    """Each red of SIDE (`<run>.json` -> cause) with no class of its own takes that one, in its
    land's columns; one with a class, and a run of no record, is left."""
    key = queue_key(q)
    with goh_db.refusable(), goh_db.transaction() as con:
        for name, cause in sorted(side.items()):
            run = name.removesuffix(".json")
            land_columns.add_cause(con, key, run, cause, lambda rec, run=run: epoch(rec, run))


def misses(q: Path) -> list[dict]:
    """Queue Q's selection misses, in the order they were appended."""
    with _reader() as con:
        rows = [] if con is None else body_columns.read(con, body_tables.MISSES, *_ours(q))
    return [doc for _, doc in rows]


def insert_misses(con: sqlite3.Connection, key: str, rows: list[dict]) -> int:
    """ROWS onto queue KEY's in CON's transaction, each once; how many new."""
    n, queue = 0, state_paths.encode(key)
    for r in rows:
        day = r.get("day")
        own = {"queue": queue, "day": None if day is None else str(day),
               "at": epoch({"at": f"{day}T00:00:00Z"}) if day else epoch(r)}  # fmt: skip
        n += body_columns.write(con, body_tables.MISSES, r, own, ignore=True)
    return n


def append_misses(q: Path, rows: list[dict]) -> int:
    """ROWS onto queue Q's, each once; how many were new (0 when this copy may not write goh.db,
    said)."""
    with goh_db.refusable(), goh_db.transaction() as con:
        return insert_misses(con, queue_key(q), rows)
    return 0
