"""lib/kv_rows.py -- goh.db's kv documents as rows (GOH.KVBLOBS; goh.db 0015): the one writer and
the one reader of pin_pushes, pin_sweep_repos, wf_pins, linux_unsafe and spend_first_done.

    put(con, name, doc)      DOC as NAME's rows; Unfit, and the rows as they were, when they do not
                             give it back
    got(con, name)           NAME's document from its rows, None with none
    unsafe(con), put_unsafe(con, value)           linux_unsafe: {module: why}
    first_done(con, doc), put_first_done(con, doc)   a spend report's first_seen_done
    fill(con)                a document an older goh kept, put into its rows: {table: rows}
    left(con)                one line per such document no row holds

WHO CALLS. lib/small_stores.py (`update`, `get`) for the three pin documents, lib/durations.py for
linux_unsafe, lib/hook_ledgers.py for the watch's report; each inside its own transaction.

NO DOCUMENT IS KEPT (GOH.OLDFORMS): contract 0021 drops the ones an older goh left and refuses a new
one. Such a document's MARK says its rows held it, whole; `fill` (the cutover's, and `goh state
backfill`) makes rows of one with none. linux_unsafe and first_seen_done only grow.

pin_sweep_repos is an older goh's sweep registry: push_sweeps (0022, GOH.PINSWEEP) says which sweep
is newer, and nothing writes it. THE ORDER of its sweeps is their `at`, apart by a millisecond.
Paths and repos are in the document's own spelling (lib/state_paths.py).
Pinned by tests/test_kv_rows.py.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from spawn_columns import instant  # lib/, on the path from the line above

MARK = "rows"  # in a document an older goh kept: the schema file whose rows give it back
NUMBER = 15
REPORT, REPORT_KEY = "model-choice-watch", "first_seen_done"  # kv, and in its `spend`
UNSAFE = "linux_unsafe"  # duration_kv
SHAPE = (KeyError, TypeError, AttributeError, ValueError, OverflowError, sqlite3.Error)


class Unfit(ValueError):
    """A document its rows do not give back: a new field needs its column first."""


def _said(**fields: Any) -> dict:
    return {k: v for k, v in fields.items() if v is not None}


def _push_rows(doc: dict, _now: float) -> list[tuple]:
    return [(repo, e["status"], e.get("said"), e.get("sweep"), e.get("where"), e.get("log"),
             instant(e["at"])) for repo, e in doc["repos"].items()]  # fmt: skip


def _push_doc(rows: list[tuple]) -> dict:
    return {"schema": 1, "repos": {
        repo: _said(status=status, said=said, sweep=sweep, where=place, log=log,
                    at=datetime.fromtimestamp(at, UTC).isoformat(timespec="seconds"))
        for repo, status, said, sweep, place, log, at in rows}}  # fmt: skip


def _sweep_rows(doc: dict, now: float) -> list[tuple]:
    return [(s["path"], i, repo, now + n / 1000) for n, s in enumerate(doc["sweeps"])
            for i, repo in enumerate(s["repos"])]  # fmt: skip


def _sweep_doc(rows: list[tuple]) -> dict:
    repos: dict[str, list[str]] = {}
    for sweep, _, repo, _ in rows:
        repos.setdefault(sweep, []).append(repo)
    return {"schema": 1, "sweeps": [{"path": p, "repos": r} for p, r in repos.items()]}


def _pin_rows(doc: dict, now: float) -> list[tuple]:
    return [(repo, e["status"], e.get("said"), doc["tag"], doc["ruff"], doc["sweep"], now)
            for repo, e in doc["repos"].items()]  # fmt: skip


def _pin_doc(rows: list[tuple]) -> dict:
    tag, ruff, sweep = rows[0][3:6]
    return {"schema": 2, "tag": tag, "ruff": ruff, "sweep": sweep,
            "repos": {r[0]: _said(status=r[1], said=r[2]) for r in rows}}  # fmt: skip


@dataclass(frozen=True)
class Store:
    table: str
    columns: str
    order: str
    rows: Callable[[dict, float], list[tuple]]
    doc: Callable[[list[tuple]], dict]  # of one row or more


STORES = {
    "pin_push": Store("pin_pushes", "repo, status, said, sweep, place, log, at", "repo",
                      _push_rows, _push_doc),
    "pin_sweeps": Store("pin_sweep_repos", "sweep, ord, repo, at", "at, sweep, ord",
                        _sweep_rows, _sweep_doc),
    "wf_pin.pins": Store("wf_pins", "repo, status, said, tag, ruff, sweep, at", "repo",
                         _pin_rows, _pin_doc),
}  # fmt: skip


def _read(con: sqlite3.Connection, s: Store) -> dict | None:
    rows = con.execute(f"SELECT {s.columns} FROM {s.table} ORDER BY {s.order}").fetchall()
    return s.doc(rows) if rows else None


def put(con: sqlite3.Connection, name: str, doc: Any, now: float | None = None) -> None:
    s = STORES[name]
    con.execute("SAVEPOINT kv_rows")
    try:
        con.execute(f"DELETE FROM {s.table}")
        rows = s.rows(doc, time.time() if now is None else now)
        con.executemany(f"INSERT INTO {s.table}({s.columns}) "
                        f"VALUES ({', '.join('?' * len(s.columns.split(',')))})", rows)  # fmt: skip
        why = "" if not rows or _same(_read(con, s), doc) else "its rows do not give it back"
    except SHAPE as e:
        why = f"{type(e).__name__}: {e}"
    if why:
        con.execute("ROLLBACK TO kv_rows")
    con.execute("RELEASE kv_rows")
    if why:
        raise Unfit(f"{name}: {why}")


def _same(a: Any, b: Any) -> bool:
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)  # a true is no 1


def got(con: sqlite3.Connection, name: str) -> dict | None:
    return _read(con, STORES[name])


# -- the two that only grow ------------------------------------------------------------------------


def _pairs(con: sqlite3.Connection, table: str) -> dict[str, str]:
    return dict(con.execute(f"SELECT {COLS[table]} FROM {table} ORDER BY 1"))


COLS = {"linux_unsafe": "module, why", "spend_first_done": "item, first_done"}


def texts(value: Any) -> dict[str, str]:
    return {k: v for k, v in value.items() if isinstance(v, str)} if isinstance(value, dict) else {}


def unsafe(con: sqlite3.Connection) -> dict[str, str] | None:
    return _pairs(con, "linux_unsafe") or None


def put_unsafe(con: sqlite3.Connection, value: Any, at: float | None = None) -> None:
    con.executemany(
        "INSERT INTO linux_unsafe(module, why, at) VALUES (?, ?, ?) ON CONFLICT(module) DO "
        "UPDATE SET why = excluded.why, at = excluded.at WHERE why <> excluded.why",
        [(m, w, time.time() if at is None else at) for m, w in texts(value).items()])  # fmt: skip


def _part(doc: Any) -> Any:
    spend = doc.get("spend") if isinstance(doc, dict) else None
    return spend.get(REPORT_KEY) if isinstance(spend, dict) else None


def first_done(con: sqlite3.Connection, doc: Any) -> Any:
    """The watch's report DOC, its first_seen_done the rows' and what only DOC has: the first
    instant an item was seen done stands."""
    rows = _pairs(con, "spend_first_done")
    if not rows or not isinstance(doc, dict):
        return doc
    spend = doc["spend"] if isinstance(doc.get("spend"), dict) else {}
    said = texts(_part(doc))
    first = {i: min(v, said.get(i, v)) for i, v in rows.items()}
    return {**doc, "spend": {**spend, REPORT_KEY: {**said, **first}}}


def put_first_done(con: sqlite3.Connection, doc: Any, at: float | None = None) -> None:
    con.executemany(
        "INSERT INTO spend_first_done(item, first_done, at) VALUES (?, ?, ?) ON CONFLICT(item) "
        "DO UPDATE SET first_done = excluded.first_done, at = excluded.at "
        "WHERE excluded.first_done < first_done",
        [(i, v, time.time() if at is None else at) for i, v in texts(_part(doc)).items()]
    )  # fmt: skip


# -- the documents an older goh kept ---------------------------------------------------------------


def _docs(con: sqlite3.Connection) -> dict[str, tuple[Any, float]]:
    out = {n: (json.loads(b), at) for n, b, at in con.execute(
        f"SELECT name, body, at FROM kv WHERE name IN ({', '.join('?' * (len(STORES) + 1))})",
        (*STORES, REPORT))}  # fmt: skip
    for value, at in con.execute("SELECT value, at FROM duration_kv WHERE key = ?", (UNSAFE,)):
        out[UNSAFE] = (json.loads(value), at)
    return out


def fill(con: sqlite3.Connection) -> dict[str, int]:
    """Inside the caller's transaction; a second run changes nothing."""
    docs = _docs(con)
    for name in STORES:
        doc, at = docs.get(name, (None, 0.0))
        if isinstance(doc, dict) and doc.get(MARK) != NUMBER:
            try:
                put(con, name, doc, at)
            except Unfit:
                continue  # `left` names it, and contract 0021 refuses it
            con.execute("UPDATE kv SET body = ? WHERE name = ?",
                        (json.dumps({**doc, MARK: NUMBER}, sort_keys=True), name))  # fmt: skip
    if REPORT in docs:
        put_first_done(con, *docs[REPORT])
    if UNSAFE in docs:
        value, at = docs[UNSAFE]
        put_unsafe(con, {**texts(value), **_pairs(con, "linux_unsafe")}, at)
    tables = [s.table for s in STORES.values()] + list(COLS)
    return {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}


def left(con: sqlite3.Connection) -> list[str]:
    out, docs = [], _docs(con)
    for name, s in STORES.items():
        doc = docs.get(name, ({MARK: NUMBER}, 0.0))[0]
        if not isinstance(doc, dict) or doc.get(MARK) != NUMBER:
            out.append(f"kv {name}: not in {s.table}: goh state backfill")
    for table, part in (("linux_unsafe", docs.get(UNSAFE, (None,))[0]),
                        ("spend_first_done", _part(docs.get(REPORT, (None,))[0]))):  # fmt: skip
        if n := len(texts(part).keys() - _pairs(con, table).keys()):
            out.append(f"{table}: {n} not in it: goh state backfill")
    return out
