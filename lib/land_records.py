"""lib/land_records.py -- the land records' one door (GOH.STATEDB S1): goh.db's `lands` and
`red_causes` tables (lib/goh_db_schema/0002_lands.sql) and the `land_guards` and
`selection_misses` ledgers. The `<landq>/done/*.json` files they were are read by nothing since the
database plan's wave 3 (`goh state retire lands`, lib/state_retire_lands.py, archives them).

    write(q, run, rec)     the record's row (the request it finishes leaves the queue with it)
    runs(q) / stamps(q)    every run with a record, by name only; {run: its epoch seconds}
    records(q, keys=, runs=)   [(run, rec)] oldest first; KEYS: those keys of each alone
    get(q, run)            one record, or None (`goh land --wait` polls it)
    ref(q, run)            a record's reference: a path no file is at (lib/landq_look.py's)
    causes(q) / set_causes(q, {run: cause})     the red classes; a class goes to a red that has none
    ledger(q, name)        a ledger's rows oldest first: "guards" (land_guards: the pre-step
                           refusals land --check learns) or "misses" (selection_misses)
    append(q, name, rows)  rows onto a ledger, each body once per queue; how many were new

A RECORD IS ITS COLUMNS AND CHILD ROWS (GOH.LANDREADERS, lib/land_columns.py): `write` upserts them
and the document beside them, every reader here reads them. A reader of a few keys names them, and a
key it did not name is an error (land_columns.Part).

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
`stamps`, `causes`/`set_causes` (a red's class is its land's own columns; one `red_causes` still
holds is read until `goh state backfill` folds it in), `ledger`/`append`/`insert` (`land_guards`,
`selection_misses`; a body once per queue). No goh.db yet reads as no rows. The old files are
archived by `state_retire_lands.py`.
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
import state_shape


def queue_key(q: Path) -> str:
    return os.path.realpath(q)


def _ours(q: Path) -> tuple[str, tuple[str, ...]]:
    """(clause, args) naming queue Q's rows, under either form of its path."""
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
        sql = f"SELECT run, at FROM lands WHERE {ours} ORDER BY queue"
        return {} if con is None else dict(con.execute(sql, args))


def get(q: Path, run: str) -> dict | None:
    """RUN's record, or None while it has none."""
    return dict(records(q, runs=[run])).get(run)


def causes(q: Path) -> dict[str, dict]:
    """{`<run>.json`: cause} for queue Q: each land's own, else the one `red_causes` holds for it
    (an older goh wrote it there, and `goh state backfill` has not folded it in yet; contract 0017
    drops the table once none is left)."""
    out: dict[str, dict] = {}
    with _reader() as con:
        if con is not None:
            ours, args = _ours(q)
            sql = f"SELECT run, body FROM red_causes WHERE {ours} ORDER BY queue"
            for run, body in con.execute(sql, args) if state_shape.has(con, "red_causes") else ():
                with contextlib.suppress(ValueError):
                    out[f"{run}.json"] = json.loads(body)
            own = land_columns.read(con, queue_key(q), ("red_cause",)).items()
            out.update({f"{run}.json": c for run, r in own if (c := r.get("red_cause"))})
    return out


def set_causes(q: Path, side: dict[str, dict]) -> None:
    """Each red of SIDE (`<run>.json` -> cause) with no class of its own takes that one, in its
    land's columns; one with a class, and a run of no record, is left."""
    key = queue_key(q)
    with goh_db.refusable(), goh_db.transaction() as con:
        for name, cause in sorted(side.items()):
            run = name.removesuffix(".json")
            land_columns.add_cause(con, key, run, cause, lambda rec, run=run: epoch(rec, run))


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
    out: list[dict] = []
    with _reader() as con:
        if con is not None:
            ours, args = _ours(q)
            if name == "misses":
                out = [doc for _, doc in body_columns.read(con, body_tables.MISSES, ours, args)]
            elif state_shape.has(con, table):
                sql = f"SELECT body FROM {table} WHERE {ours} ORDER BY n"
                bodies = [b for (b,) in con.execute(sql, args)]
    for body in bodies:
        with contextlib.suppress(ValueError):
            out.append(json.loads(body))
    return state_paths.got(name, out)


def insert(con: sqlite3.Connection, key: str, name: str, rows: list[dict]) -> int:
    """ROWS onto ledger NAME of queue KEY in CON's transaction, each body once; how many new."""
    table, col = LEDGERS[name]
    key = state_paths.column(con, key)
    if name == "misses":
        own = [(r, _ledger_row(key, name, r)) for r in rows]
        return sum(body_columns.write(con, body_tables.MISSES, r,
                                      {"queue": q, "at": at, "day": day}, ignore=True)
                   for r, (q, at, day, _) in own)  # fmt: skip
    if not state_shape.has(con, table):  # contract 0017: each guard is its red land's own row
        return 0
    sql = f"INSERT OR IGNORE INTO {table} (queue, at, {col}, body) VALUES (?, ?, ?, ?)"
    kept = [_ledger_row(key, name, state_paths.put(con, name, r)) for r in rows]
    return con.executemany(sql, kept).rowcount  # total_changes counts a trigger's rows too


def append(q: Path, name: str, rows: list[dict]) -> int:
    """ROWS onto ledger NAME of queue Q, each body once; how many were new (0 when this copy may
    not write goh.db, said)."""
    with goh_db.refusable(), goh_db.transaction() as con:
        return insert(con, queue_key(q), name, rows)
    return 0
