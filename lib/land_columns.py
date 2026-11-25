"""lib/land_columns.py -- a land record as goh.db's typed columns and child rows
(GOH.LANDREADERS): `lands` and its eight child tables (schema file 0011), written and read by name.

    put(con, queue, run, rec, at)    the row, its child rows, and the document while it exists
    read(con, queue, keys, runs)     {run: record} from the columns, the named keys alone
    add_cause(con, queue, run, c)    a red land's class, when it has none of its own
    purge()                          old lands' coverage rows gone, once 0017 is applied
    Part                             a record of the named keys: any other key asked is an error

A WRITER NAMES EVERY COLUMN. `put` is an upsert whose `rest` is never NULL, so no trigger of 0011
fills the row: a scalar that has its column's type is its column, a list whose every element fits is
its child rows (the ones before it go first), and `rest` is what is left. A value of the wrong type
is in no column: it stays in `rest`, or from a cutover on in the dead-letter file
(lib/state_shape.py), said on one stderr line and by `goh state check`, for a land's record has no
other home and is never refused over a field (the columns' CHECKs refuse it, and `rest`'s CHECK
refuses a value that has a column).

A READER NAMES THE KEYS IT READS, and pays for their columns and child tables alone. A row that
`goh state backfill` has not filled yet (`rest` NULL) and a file from before 0011 are read from the
document, the one place their fields are.

TWO FORMS OF A PATH (lib/state_paths.py): a row of QUEUE, and its paths and repo, may be in either.
`put` stores the file's form and removes the run's row of the other; `read` takes both, gives every
path absolute, and of two rows of one run the last by queue, its own child rows with it.

SAID ANOTHER WAY: null, {} and [] are an absent key, and a record's `at` is its instant in UTC. One
[] says something: a judged coverage risk (it has a `head`) that carries no line judged every line
covered, and lib/diffcov_carry.py asks, so its `carry` is read back as the list it was, until
`purge` may have taken its rows (PURGE_DAYS, once 0017 is applied): an older land says nothing.

Pinned by tests/test_land_columns.py.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
import time
from collections.abc import Callable, Collection, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db_core  # lib/, on the path from the line above
import db_schema
import goh_db
import state_paths
import state_shape

HAVE = ("item", "sha", "enqueued", "repo", "mode", "state", "attempt", "requested", "started",
        "finished", "k")  # fmt: skip
KEY = ("queue", "run")
PURGE_DAYS = 14  # lib/state_sql/purge.sql's own
PURGE = Path(__file__).resolve().with_name("state_sql") / "purge.sql"


def _words(words: str, kind: str) -> list[tuple[str, str, str]]:
    """(its path in the record, its column, its type) per word `path[=column]`; a path with no
    column of its own is its words joined."""
    pairs = [w.partition("=") for w in words.split()]
    return [(p, c or p.replace(".", "_"), kind) for p, _, c in pairs]


SHARE = (_words("host outcome where=place", "text") + _words(
    "mac_est_s mac_s rerun_s saved_s t0 worker_s", "real") + _words("tests", "int"))  # fmt: skip
SCALARS = (
    _words("landed log why stage cause cause_detail k_why pre_affected.ran pre_affected.took "
           "pre_affected.by batch.id=batch pre_run.skipped red_cause.cause=red_class "
           "red_cause.rule=red_rule diffcov_item.id=diffcov_item diffcov_item.said=diffcov_said "
           "cov_risk.said=cov_said cov_risk.head=cov_head cov_risk.base=cov_base", "text")
    + _words("pre_s slot_s slot_wait_s load_start load_end pre_affected.lane_s pre_run.wait_s "
             "pre_run.run_s pre_run.limit_s pre_run.cpu_s", "real")
    + [(f"slot_wait_by_class.{c}", f"slot_wait_{c}_s", "real")
       for c in ("short", "land", "pre", "work")]
    + _words("batch.items=batch_items batch.gates=batch_gates pre_affected.tests pre_run.workers "
             "cov_risk.lines=cov_lines", "int")
    + [(f"pre_run.remote.{p}", f"remote_{p}", kind) for p, _, kind in [
        *SHARE, ("cause", "", "text"), ("fresh", "", "bool")]]
    + _words("early recorded", "bool")
)  # fmt: skip
LISTS = (
    ("guards", "guard"),
    ("red_cause.tests", "red_test"),
    ("red_logs", "red_log"),
)  # land_lists
OBJECTS = (
    ("flakes", "land_flakes", _words("test entry filed", "text")),
    ("remote_shares", "land_remote_shares", SHARE),
    ("cov_risk.carry", "land_cov_carry", _words("file text why", "text") + _words("line", "int")
     + _words("own measured", "bool")),
    ("cap_items", "land_cap_items", _words("id file said", "text") + _words("lines", "int")),
    ("waived", "land_waived", _words("node kind rule commit=commit_sha sha reason", "text")
     + _words("reading", "int")),
)  # fmt: skip
ITEMS = ("waived", "items", "land_waived_items")  # the one list inside an object
MAPS = (("cov_risk.files", "land_cov_files"),)  # {path: what it said}
CELLS = {
    "land_lists": ("kind", "ord", "value"),
    ITEMS[2]: ("ord", "i", "value"),
    **{t: ("ord", *(c for _, c, _ in words)) for _, t, words in OBJECTS},
    **{t: ("path", "said") for _, t in MAPS},
}  # each child's columns past its key, but `at`
KEYS = frozenset({p.partition(".")[0] for p, *_ in (*SCALARS, *LISTS, *OBJECTS, *MAPS)}
                 | {*HAVE, "guard", "at"})  # fmt: skip


def _fits(v: Any, kind: str) -> bool:
    if kind == "text":
        return isinstance(v, str)
    if isinstance(v, bool) or kind == "bool":
        return isinstance(v, bool) and kind == "bool"
    return isinstance(v, int) or (kind == "real" and isinstance(v, float))


def _get(doc: Any, path: str) -> Any:
    for k in path.split("."):
        doc = doc.get(k) if isinstance(doc, dict) else None
    return doc


def _drop(doc: dict, path: str) -> None:
    """PATH out of DOC, and each object it leaves with nothing in it."""
    head, _, tail = path.partition(".")
    if tail and isinstance(doc.get(head), dict):
        _drop(doc[head], tail)
    if not tail or doc.get(head) == {}:
        doc.pop(head, None)


def _set(doc: dict, path: str, v: Any) -> None:
    *parents, last = path.split(".")
    for k in parents:
        doc = doc.setdefault(k, {})
    doc[last] = v


def plain(rec: dict) -> dict:
    """REC as JSON says it and SQLite holds it: keys are text, a number that is none is null, a
    character no UTF-8 has is `?`."""
    text = json.dumps(rec, ensure_ascii=False).encode("utf-8", "replace").decode()
    return json.loads(text, parse_constant=lambda _: None)


def instant(at: float) -> str:
    return datetime.fromtimestamp(math.floor(at), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _same_instant(v: Any, at: float) -> bool:
    try:
        when = datetime.fromisoformat(v)  # no offset is UTC, as SQLite reads it
    except (TypeError, ValueError):
        return False
    when = when if when.tzinfo else when.replace(tzinfo=UTC)
    return math.floor(when.timestamp()) == math.floor(at)


def _object(x: Any, words: list, inner: str = "") -> tuple[list, list] | None:
    """(its cells, its inner list) when every key of X has a column and fits it; else None."""
    if not isinstance(x, dict) or set(x) - {p for p, *_ in words} - {inner}:
        return None
    if any(x.get(p) is not None and not _fits(x[p], kind) for p, _, kind in words):
        return None
    items = x.get(inner) or []
    if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
        return None
    cells = [x.get(p) for p, *_ in words]
    return [int(c) if isinstance(c, bool) else c for c in cells], items


def split(doc: dict, at: float) -> tuple[dict, list[tuple], dict]:
    """(the columns, the child rows as (table, *cells past the key), what is left) of DOC."""
    rest, cols, kids = json.loads(json.dumps(doc)), {}, []
    for c in HAVE:
        v = doc.get(c)
        cols[c] = v if v is None or isinstance(v, int | float | str) else json.dumps(v)
        rest.pop(c, None)
    for path, col, kind in SCALARS:
        v = _get(doc, path)
        fit = v is None or _fits(v, kind)
        cols[col] = (int(v) if isinstance(v, bool) else v) if fit else None
        if fit:
            _drop(rest, path)
    for path, name in LISTS:
        v = _get(doc, path)
        if isinstance(v, list) and all(isinstance(x, str) for x in v):
            kids += [("land_lists", name, i, x) for i, x in enumerate(v)]
            _drop(rest, path)
            if path == "guards" and doc.get("guard") == (v[0] if v else None):
                _drop(rest, "guard")
    for path, table, words in OBJECTS:
        v = _get(doc, path)
        rows = [_object(x, words, ITEMS[1] if path == ITEMS[0] else "") for x in v] if (
            isinstance(v, list)) else [None]  # fmt: skip
        if all(r is not None for r in rows):
            kids += [(table, i, *r[0]) for i, r in enumerate(rows)]
            kids += [(ITEMS[2], i, n, x) for i, r in enumerate(rows) for n, x in enumerate(r[1])]
            _drop(rest, path)
    for path, table in MAPS:
        v = _get(doc, path)
        if isinstance(v, dict) and all(isinstance(x, str) for x in v.values()):
            kids += [(table, p, said) for p, said in v.items()]
            _drop(rest, path)
    if _same_instant(doc.get("at"), at):
        rest.pop("at", None)
    for parent in ("pre_run.remote", *(k for k in KEYS if _get(rest, k) in ({}, []))):
        if _get(rest, parent) in ({}, []):
            _drop(rest, parent)
    return cols, kids, {k: v for k, v in rest.items() if v is not None}


def ours(queue: str) -> tuple[str, tuple[str, ...]]:
    """(clause, args) naming QUEUE's rows."""
    return "queue = ?", (state_paths.encode(queue),)


def put(
    con: sqlite3.Connection, queue: str, run: str, rec: dict, at: float, keep: bool = False
) -> None:
    """RUN's record in CON's transaction, replacing one before it (KEEP: leaving it)."""
    mine, args = ours(queue)
    queue = state_paths.encode(queue)
    sql = f"SELECT 1 FROM lands WHERE {mine} AND run = ?"
    if keep and con.execute(sql, (*args, run)).fetchone():
        return
    doc = plain(rec if rec.get("at") else {**rec, "at": instant(at)})
    doc = state_paths.put("lands", doc)
    cols, kids, rest = split(doc, at)
    rest, buried = state_shape.sift("lands", rest)
    if odd := sorted({k.partition(".")[0] for k in buried} | (KEYS & rest.keys())):
        kept = state_shape.DEAD if buried else "`rest`"
        db_core.warn(f"goh.db: lands {run}: {', '.join(odd)}: not of its column's type, kept in "
                     f"{kept} (goh state check)")  # fmt: skip
    if buried:
        state_shape.bury("lands", {"queue": queue, "run": run, **buried}, "a value of another type")
    row = {"queue": queue, "run": run, **cols, "at": at,
           "rest": json.dumps(rest, separators=(",", ":"))}  # fmt: skip
    sets = ", ".join(f"{c} = excluded.{c}" for c in row if c not in KEY)
    con.execute(f"INSERT INTO lands ({', '.join(row)}) VALUES ({', '.join('?' * len(row))}) "
                f"ON CONFLICT (queue, run) DO UPDATE SET {sets}", list(row.values()))  # fmt: skip
    for table in CELLS:
        con.execute(f"DELETE FROM {table} WHERE queue = ? AND run = ?", (queue, run))
    for table, *cells in kids:
        names = (*KEY, *CELLS[table], "at")
        marks = ", ".join("?" * len(names))
        sql = f"INSERT INTO {table} ({', '.join(names)}) VALUES ({marks})"
        con.execute(sql, (queue, run, *cells, at))


class Part(dict):
    """A record read for some of its keys: one that was not read is an error, never absent."""

    def __init__(self, rec: dict, keys: Collection[str]) -> None:
        super().__init__(rec)
        self.asked = frozenset(keys)

    def _has(self, key: str) -> None:
        if key not in self.asked:
            raise KeyError(f"{key}: this record was read for {', '.join(sorted(self.asked))} alone")

    def __contains__(self, key: object) -> bool:
        self._has(str(key))
        return super().__contains__(key)

    def __missing__(self, key: str) -> NoReturn:
        self._has(key)
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        self._has(key)
        return super().get(key, default)


def _merge(doc: dict, rest: dict) -> dict:
    """DOC with REST over it, object by object (SQLite's json_patch)."""
    for k, v in rest.items():
        mine = doc.get(k)
        doc[k] = _merge(mine, v) if isinstance(mine, dict) and isinstance(v, dict) else v
    return doc


def _value(v: Any, kind: str) -> Any:
    return bool(v) if kind == "bool" and v is not None else v


def _said(words: list, cells: Iterable) -> dict:
    return {p: _value(v, kind) for (p, _, kind), v in zip(words, cells, strict=True)
            if v is not None}  # fmt: skip


def read(
    con: sqlite3.Connection, queue: str, keys: Collection[str] | None = None,
    runs: Iterable[str] | None = None,
) -> dict[str, dict]:  # fmt: skip
    """{run: its record} of QUEUE (RUNS alone), of KEYS alone (each a top key; None: all)."""
    unknown = set(keys or ()) - KEYS
    if unknown:
        raise KeyError(f"no such key of a land record: {', '.join(sorted(unknown))}")

    def want(path: str) -> bool:
        return keys is None or path.partition(".")[0] in keys

    mine = [c for c in HAVE if want(c)]
    some = [w for w in SCALARS if want(w[0])]
    where, args = ours(queue)
    if runs is not None:
        where, args = f"{where} AND run IN (SELECT value FROM json_each(?))", (
            *args, json.dumps(list(runs)))  # fmt: skip
    cols = [*mine, *(c for _, c, _ in some)]
    sql = f"SELECT {', '.join(['queue', 'run', 'at', 'rest', *cols])} FROM lands WHERE {where}"
    out: dict[str, dict] = {}
    own: dict[tuple[str, str], dict] = {}  # (queue, run): its record
    kept = time.time() - PURGE_DAYS * 86400
    judged: set[tuple[str, str]] = set()  # whose coverage rows are still all there
    rests: dict[tuple[str, str], dict] = {}
    for at_queue, run, at, rest, *cells in con.execute(sql, args):
        rec = {c: v for c, v in zip(mine, cells, strict=False) if v is not None}
        for (path, _, kind), v in zip(some, cells[len(mine) :], strict=True):
            if v is not None:
                _set(rec, path, _value(v, kind))
        if want("at"):
            rec["at"] = instant(at)
        out[run] = own[at_queue, run] = rec
        judged |= {(at_queue, run)} if at >= kept else set()
        rests[at_queue, run] = json.loads(rest or "{}")
    if own:
        _children(con, want, own, where, args, judged)
    for key, rest in rests.items():
        _merge(own[key], {k: v for k, v in rest.items() if want(k)})
    out = {run: state_paths.got("lands", rec) for run, rec in out.items()}
    return out if keys is None else {run: Part(rec, keys) for run, rec in out.items()}


def _children(
    con: sqlite3.Connection, want: Callable[[str], bool], own: dict[tuple[str, str], dict],
    where: str, args: tuple, judged: Collection[tuple[str, str]],
) -> None:  # fmt: skip
    """Each record of OWN with the wanted lists, objects and maps of its child rows."""

    def rows(table: str, cols: str, order: str, more: str = "") -> Iterable[tuple]:
        sql = f"SELECT queue, run, {cols} FROM {table} WHERE {where}{more} "
        for queue, name, *cells in con.execute(f"{sql}ORDER BY queue, run, {order}", args):
            if (queue, name) in own:
                yield (queue, name), *cells

    def onto(key: tuple[str, str], path: str, v: Any) -> None:
        got = _get(own[key], path)
        if got is None:
            _set(own[key], path, got := [])
        got.append(v)

    paths = {name: p for p, name in LISTS if want(p)}
    if want("guard") and "guard" not in paths:
        paths["guard"] = ""
    if paths:
        kinds = f" AND kind IN ({', '.join(repr(k) for k in paths)})"
        for key, kind, i, value in rows("land_lists", "kind, ord, value", "kind, ord", kinds):
            if paths[kind]:
                onto(key, paths[kind], value)
            if kind == "guard" and not i and want("guard"):
                own[key]["guard"] = value
    for path, table, words in OBJECTS:
        if not want(path):
            continue
        inner: dict[tuple, list] = {}
        if path == ITEMS[0]:
            for key, i, value in rows(ITEMS[2], "ord, value", "ord, i"):
                inner.setdefault((key, i), []).append(value)
        for key, i, *cells in rows(table, ", ".join(CELLS[table]), "ord"):
            more = {ITEMS[1]: inner[key, i]} if (key, i) in inner else {}
            onto(key, path, {**_said(words, cells), **more})
    for path, table in MAPS:
        for key, name, said in rows(table, "path, said", "path") if want(path) else ():
            if _get(own[key], path) is None:
                _set(own[key], path, {})
            _get(own[key], path)[name] = said
    if want("cov_risk"):
        for key, rec in own.items():
            if (
                key in judged and _get(rec, "cov_risk.head") is not None
            ):  # judged: [] is every line covered
                rec["cov_risk"].setdefault("carry", [])


def purge() -> int:
    """lib/state_sql/purge.sql; rows gone. Nothing of a file this copy may not write, and none of
    one that is not there."""
    if not goh_db.path().is_file():
        return 0
    with goh_db.refusable(), goh_db.transaction() as con:
        return sum(con.execute(sql).rowcount for sql in db_schema.statements(
            PURGE.read_text(encoding="utf-8")))  # fmt: skip
    return 0


def landed(con: sqlite3.Connection, items: Iterable[str], keys: Collection[str]) -> list[dict]:
    """KEYS of every landed record of ITEMS, in any queue."""
    sql = ("SELECT queue, run FROM lands WHERE state = 'landed' AND item IN "
           "(SELECT value FROM json_each(?))")  # fmt: skip
    runs: dict[str, list[str]] = {}
    for queue, run in con.execute(sql, (json.dumps(list(items)),)):
        runs.setdefault(state_paths.decode(queue), []).append(run)
    return [rec for q, some in runs.items() for rec in read(con, q, keys, some).values()]


def add_cause(
    con: sqlite3.Connection, queue: str, run: str, cause: dict, when: Callable[[dict], float]
) -> bool:
    """RUN's red class is CAUSE, unless it has one, or no record; whether it was written."""
    rec = read(con, queue, runs=[run]).get(run)
    if rec is None or rec.get("red_cause"):
        return False
    put(con, queue, run, {**rec, "red_cause": cause}, when(rec))
    return True
