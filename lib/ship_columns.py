"""lib/ship_columns.py -- a ship's record and its pins as goh.db's typed columns and child rows
(GOH.STEPTIMES, the plan's S4c): `ships`, `ship_pins` and their child tables (schema file 0012).

    put(con, res, key, repo, at)     RES as KEY's row of ships, its child rows, and its document
                                     while the table has a `body`
    put_pins(con, body, key, at)     a ship's pins, the same
    read(con, where, args)           (result, record) per row of ships WHERE takes
    read_pins(con, where, args)      (result, pins)

A WRITER NAMES EVERY COLUMN. A scalar of its column's type is its column, a list or an object whose
every element fits is its child rows (the ones before it go first), and `rest` is what is left: an
unknown key, or a value no column takes, which `goh state check` names. `rest` is never NULL, so
0012's triggers (older code's) fill nothing: what they would have put there is what this puts there,
row for row (tests/test_ship_columns.py).

A READER READS THE COLUMNS, by the SQL that renders them (lib/state_sql/doc_ships.sql), so every
reader of every language gives one document. A row with `rest` NULL (`goh state backfill` has not
filled it) is read from its document. A record's `repo` is its path: the document's while there is
one, the column's key after. Null, {} and [] are an absent key, but for what a reader indexes: what
a record is planned with (PLANNED, PLANNED_LISTS), a grace's `count`, a run step's `idle_pct`.

TWO FORMS OF A PATH (lib/state_paths.py): the caller gives RES in the file's form and reads it back.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import state_columns  # lib/, beside this file: where a document's rendering is

HAVE = ("item", "sha", "status", "started", "finished", "version")  # text since 0003
TEXT, REAL, INT, BOOL = "text", "real", "int", "bool"


def _words(words: str, kind: str) -> list[tuple[str, str, str]]:
    """(its path in the document, its column, its type) per word `path[=column]`."""
    pairs = [w.partition("=") for w in words.split()]
    return [(p, c or p.replace(".", "_"), kind) for p, _, c in pairs]


SCALARS = (
    _words("before_clock.name before_clock.status before_clock.log name summary remote branch "
           "handoff_sha base_tag release install loopctl failed_step bump verdicts allow_diffcov "
           "pid_stamp grace_over.over_from=grace_over_from grace_over.canary_sha=grace_canary_sha "
           "grace_over.why=grace_why bisect.base bisect.culprit bisect.since bisect.subject "
           "bisect.fault bisect.candidate bisect_step.sha quiet.off quiet.unmeasured "
           "diffcov_red.base=diffcov_base diffcov_red.said=diffcov_said", TEXT)
    + _words("before_clock.seconds land_wait_s own_s load1 quiet.min_idle_pct quiet.max_s "
             "quiet.idle_pct_first quiet.idle_pct quiet.waited_s quiet.load1", REAL)
    + _words("timeout_s pid quiet.readings grace_over.count=grace_count bisect.lands "
             "bisect_step.n bisect_step.lands", INT)
    + _words("release_pushes progress quiet.quiet=quiet_ok bisect.flake bisect_step.recheck", BOOL)
)  # fmt: skip
LISTS = (("preflight", "preflight"), ("undo", "undo"), ("tail", "tail"),
         ("bisect.tests", "bisect_test"))  # fmt: skip
STEP = (_words("name log status why timings", TEXT) + _words("seconds idle_pct load1_end", REAL)
        + _words("code", INT))  # fmt: skip
PROFILE = ("audit", "bin", "fresh", "reused")
VERDICT = _words("sha verdict", TEXT)
OBJECTS = (
    ("steps", "ship_steps", "step", STEP),
    ("retried", "ship_steps", "retry", STEP),
    ("bisect.runs", "ship_bisect", "run", VERDICT),
    ("bisect.confirm", "ship_bisect", "confirm", VERDICT),
    ("diffcov_red.lines", "ship_diffcov_lines", None,
     _words("file text commit=commit_sha land subject", TEXT) + _words("line", INT)),
)  # fmt: skip
CHILDREN = ("ship_bisect", "ship_diffcov_faults", "ship_diffcov_lines", "ship_grace", "ship_lists",
            "ship_steps")  # fmt: skip
PARENTS = ("before_clock", "bisect", "bisect_step", "diffcov_red", "grace_over", "quiet")
PINS = _words("tag ruff sweep", TEXT)
PIN = _words("status said", TEXT)
# what every record has from its plan on (tools/ship.py), though null or empty
PLANNED = dict.fromkeys(("item", "version", "failed_step", "pid", "base_tag", "bump", "release",
                         "install", "allow_diffcov"))  # fmt: skip
PLANNED_LISTS = ("preflight", "tail", "undo")  # `steps` is in every record


def _fits(v: Any, kind: str) -> bool:
    if kind == TEXT:
        return isinstance(v, str)
    if isinstance(v, bool) or kind == BOOL:
        return isinstance(v, bool) and kind == BOOL
    if isinstance(v, int):
        return kind == REAL or -(1 << 63) <= v < 1 << 63
    return kind == REAL and isinstance(v, float) and math.isfinite(v)


def _stored(v: Any, kind: str) -> Any:
    return v if v is None else int(v) if kind == BOOL else float(v) if kind == REAL else v


def _get(doc: Any, path: str) -> Any:
    for k in path.split("."):
        doc = doc.get(k) if isinstance(doc, dict) else None
    return doc


def _drop(rest: dict, path: str) -> None:
    head, _, key = path.partition(".")
    if not key:
        rest.pop(head, None)
    elif isinstance(rest.get(head), dict):
        rest[head] = {k: v for k, v in rest[head].items() if k != key}


def _cells(v: Any, words: list[tuple[str, str, str]]) -> dict[str, Any] | None:
    """The object V as its columns; None when one of its keys has none, or is of another type."""
    if not isinstance(v, dict):
        return None
    out: dict[str, Any] = {}
    by = {p: (c, kind) for p, c, kind in words}
    for k, x in v.items():
        if x is None:
            continue
        if k == "profiles" and words is STEP:
            if not (isinstance(x, list) and len(x) == 1 and isinstance(x[0], dict)):
                return None
            out |= {f"profile_{p}": x[0].get(p) for p in PROFILE}
            continue
        if k not in by or not _fits(x, by[k][1]):
            return None
        out[by[k][0]] = _stored(x, by[k][1])
    return out


def _strings(v: Any) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


def _scalars(doc: dict, rest: dict, words: list[tuple[str, str, str]]) -> dict[str, Any]:
    cols: dict[str, Any] = {}
    for path, col, kind in words:
        v = _get(doc, path)
        cols[col] = _stored(v, kind) if v is not None and _fits(v, kind) else None
        if v is None or cols[col] is not None:
            _drop(rest, path)
    return cols


def _split(doc: dict, repo: str) -> tuple[dict[str, Any], list[tuple[str, dict]], dict]:
    """(columns, child rows as (table, cells), what none of them takes)."""
    rest = dict(doc)
    cols = _scalars(doc, rest, SCALARS)
    kids: list[tuple[str, dict]] = []
    for path, kind in LISTS:
        if _strings(v := _get(doc, path)):
            kids += [("ship_lists", {"kind": kind, "ord": i, "value": x}) for i, x in enumerate(v)]
            _drop(rest, path)
    for path, table, kind, words in OBJECTS:
        v = _get(doc, path)
        got = [_cells(x, words) for x in v] if isinstance(v, list) else [None]
        if all(c is not None for c in got):
            named = {"kind": kind} if kind else {}
            kids += [(table, {**named, "ord": i, **(c or {})}) for i, c in enumerate(got)]
            _drop(rest, path)
    faults = _get(doc, "diffcov_red.faults")
    if isinstance(faults, dict) and all(isinstance(x, str) for x in faults.values()):
        kids += [("ship_diffcov_faults", {"fault": k, "said": x}) for k, x in faults.items()]
        _drop(rest, "diffcov_red.faults")
    over = _get(doc, "grace_over.repos")
    if isinstance(over, dict) and all(_strings(x) for x in over.values()):
        kids += [("ship_grace", {"repo": r, "ord": i, "value": x})
                 for r, said in over.items() for i, x in enumerate(said)]  # fmt: skip
        _drop(rest, "grace_over.repos")
    nothing = [p for p, *_ in (*LISTS, *OBJECTS)] + ["diffcov_red.faults", "grace_over.repos"]
    for path in (p for p in nothing if _get(doc, p) is None):
        _drop(rest, path)
    quiet = [_get(doc, "quiet.min_idle_pct"), _get(doc, "quiet.max_s")]
    if json.dumps(quiet) == json.dumps(doc.get("quiet_settings")):
        _drop(rest, "quiet_settings")
    where = doc.get("repo")
    if where is None or (isinstance(where, str) and repo
                         and where.replace("/", "_").endswith(repo.replace("/", "_"))):  # fmt: skip
        _drop(rest, "repo")
    for k in ("result", *HAVE, "schema", "consumer_pins", "quiet_settings", "repo"):
        if k not in ("quiet_settings", "repo") or rest.get(k) is None:
            rest.pop(k, None)
    for k in (k for k in PARENTS if rest.get(k) in (None, {}, [])):
        rest.pop(k, None)
    return cols, kids, rest


def _json(rest: dict) -> str:
    return json.dumps(rest, ensure_ascii=False, separators=(",", ":"))  # as SQLite writes it


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    return set(state_columns.columns(con, table))


def _upsert(con: sqlite3.Connection, table: str, cols: dict[str, Any]) -> None:
    have = _columns(con, table)
    cols = {c: v for c, v in cols.items() if c in have}  # `body` leaves with a contract
    sets = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "result")
    con.execute(f"INSERT INTO {table}({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
                f"ON CONFLICT(result) DO UPDATE SET {sets}", list(cols.values()))  # fmt: skip


def _children(
    con: sqlite3.Connection, key: str, tables: tuple[str, ...], kids: list[tuple[str, dict]],
    at: float,
) -> None:  # fmt: skip
    for table in tables:
        con.execute(f"DELETE FROM {table} WHERE result = ?", (key,))
    for table, cells in kids:
        row = {"result": key, **cells, "at": at}
        con.execute(f"INSERT INTO {table}({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                    list(row.values()))  # fmt: skip


def put(con: sqlite3.Connection, res: dict, key: str, repo: str, at: float) -> None:
    cols, kids, rest = _split(res, repo)
    cols |= {"result": key, "run": Path(key).stem, "repo": repo, "at": at, "rest": _json(rest),
             "body": json.dumps(res),
             **{c: None if res.get(c) is None else str(res[c]) for c in HAVE}}  # fmt: skip
    _upsert(con, "ships", cols)
    _children(con, key, CHILDREN, kids, at)


def put_pins(con: sqlite3.Connection, body: dict, key: str, at: float) -> None:
    rest = {k: v for k, v in body.items() if k != "schema"}
    cols = _scalars(body, rest, PINS)
    repos = body.get("repos")
    got = {r: _cells(v, PIN) for r, v in repos.items()} if isinstance(repos, dict) else {}
    kids: list[tuple[str, dict]] = []
    if all(c is not None for c in got.values()) and (repos is None or isinstance(repos, dict)):
        kids = [("ship_pin_repos", {"repo": r, **(c or {})}) for r, c in got.items()]
        rest.pop("repos", None)
    _upsert(con, "ship_pins", {**cols, "result": key, "run": Path(key).stem, "at": at,
                               "rest": _json(rest), "body": json.dumps(body)})  # fmt: skip
    _children(con, key, ("ship_pin_repos",), kids, at)


def _rendered(
    con: sqlite3.Connection, table: str, where: str, args: tuple | list
) -> Iterator[tuple[str, str | None, dict | None, dict]]:
    """(result, rest, the document, the one the columns render) per row of TABLE WHERE takes."""
    have = _columns(con, table)
    body = "x.body" if "body" in have else "'{}'"
    if "rest" not in have:  # a file no writer of this goh has opened yet
        docs = con.execute(f"SELECT x.result, {body} FROM {table} x WHERE {where}", args)
        for result, doc in docs.fetchall():
            yield result, None, json.loads(doc), {}
        return
    sql = (state_columns.SQL / f"doc_{table}.sql").read_text(encoding="utf-8")
    _, _, tail = sql.partition("x.body,")
    sql = f"SELECT x.result, x.rest, {body},{tail} WHERE {where} ORDER BY x.result"
    for result, rest, doc, again in con.execute(sql, args):
        kept = json.loads(doc) if doc and (rest is None or doc != "{}") else None
        yield result, rest, kept, json.loads(again)


def read(con: sqlite3.Connection, where: str = "1", args: tuple | list = ()) -> list:
    out = []
    for result, rest, doc, again in _rendered(con, "ships", where, args):
        if rest is None:
            out.append((result, doc or {}))
            continue
        if doc and doc.get("repo") is not None:  # the column holds its name until it is the key
            again["repo"] = doc["repo"]
        if "grace_over" in again:  # not judged is a count of None, said beside its why
            again["grace_over"].setdefault("count", None)
        for step in again.get("steps", ()):
            if "idle_pct" not in step and "load1_end" in step:  # an idle nobody could read
                step["idle_pct"] = None
        lists = {k: [] for k in PLANNED_LISTS}
        planned = "timeout_s" in again  # a ship's; no other record has one
        out.append((result, {"steps": [], **(PLANNED | lists if planned else {}), **again}))
    return out


def read_pins(con: sqlite3.Connection, where: str = "1", args: tuple | list = ()) -> list:
    return [(result, (doc or {}) if rest is None else {"repos": {}, **again})
            for result, rest, doc, again in _rendered(con, "ship_pins", where, args)]  # fmt: skip
