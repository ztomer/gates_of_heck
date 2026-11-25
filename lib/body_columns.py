"""lib/body_columns.py -- a document of one of eight goh.db tables as typed columns and child rows
(GOH.BODYCOLS; goh.db 0016): the one writer and the one reader of the fields lib/body_tables.py
names, for the table's one store module.

    write(con, t, doc, own, ignore=False)   a new row: the table's OWN columns and DOC's fields;
                             False: IGNOREd, it is there
    update(con, t, doc, own, where, args)   the row WHERE names, rewritten as DOC; how many (0 or 1)
    read(con, t, where, args, order)        [(own columns, document)], from the columns
    Refused                                 a value no column takes

WHO CALLS. lane_starts: lib/hook_ledgers.py; flakes: lib/small_stores.py; selection_misses:
lib/land_records.py; reuse_shadow, reuse_audit: lib/reuse_audit_store.py; requests, early:
lib/landq_state.py. Each inside its own transaction.

ONE HOME. An unknown key stays in `rest`, which `goh state check` names; a known key of the wrong
type is Refused: no column takes it, and nothing would explain it in `rest`.
Pinned by tests/test_body_columns.py.
"""

from __future__ import annotations

import copy
import json
import math
import os
import sqlite3
import sys
from collections.abc import Sequence
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import state_shape  # lib/, beside this file
from body_tables import BOOL, INTEGER, REAL, TEXT, Fields, Kid, Table, get  # lib/, line above
from spawn_columns import Refused, _text

Own = dict[str, Any]


def _whole(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and -(1 << 63) <= v < 1 << 63


def _fits(kind: str, v: object) -> bool:
    if kind == TEXT:
        return isinstance(v, str)
    if kind == BOOL:
        return isinstance(v, bool)
    if kind == INTEGER:
        return _whole(v)
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _stored(kind: str, v: Any) -> Any:
    return None if v is None else float(v) if kind == REAL else int(v) if kind == BOOL else v


def _drop(rest: dict, path: str) -> None:
    *up, last = path.split(".")
    at = get(rest, ".".join(up)) if up else rest
    if isinstance(at, dict):
        at.pop(last, None)


def _put(doc: dict, path: str, v: object) -> None:
    *up, last = path.split(".")
    for k in up:
        doc = doc.setdefault(k, {})
    doc[last] = v


def _merge(doc: dict, rest: dict) -> dict:
    for k, v in rest.items():
        if isinstance(v, dict) and isinstance(doc.get(k), dict):
            _merge(doc[k], v)
        else:
            doc[k] = v
    return doc


# -- a document, taken apart -----------------------------------------------------------------------


def _flat(v: object, path: str = "") -> dict[str, Any]:
    """{dotted path: value} of V's leaves, an empty object one of them."""
    if not isinstance(v, dict) or not v:
        return {path: v}
    out: dict[str, Any] = {}
    for k, x in v.items():
        out |= _flat(x, f"{path}.{k}" if path else str(k))
    return out


def _cells(fields: Fields, v: object, also: dict[str, Any] | None = None) -> list | None:
    """Object V as FIELDS' cells, in order; None when a key or a type of it has no column (ALSO: the
    keys that only repeat the row's own)."""
    if not isinstance(v, dict):
        return None
    groups = {p.rsplit(".", 1)[0] for p in fields if "." in p}
    flat = {p: x for p, x in _flat(v).items()
            if x is not None and not (x == {} and p in groups)}  # fmt: skip
    if not v:
        return [None] * len(fields)
    for p, x in (also or {}).items():
        if flat.get(p) == x:
            flat.pop(p)
    if not all(p in fields and _fits(fields[p][1], x) for p, x in flat.items()):
        return None
    return [_stored(kind, flat.get(p)) for p, (_, kind) in fields.items()]


def _kid(kid: Kid, v: object, own: Own) -> list[tuple] | None:
    """V as the rows of KID, less the key, its kind and the stamp; None when it is kept in `rest`.
    Every key here is the document's own (`ord`, or a map's)."""
    if kid.shape == "one":
        cells = _cells(kid.fields, v, {"path": own.get("path")}) if v else None
        return None if cells is None else [tuple(cells)]
    if kid.shape == "map":
        ok = isinstance(v, dict) and all(isinstance(x, str) for x in v.values())
        return list(v.items()) if ok else None  # type: ignore[union-attr]
    if not isinstance(v, list):
        return None
    if kid.shape == "text":
        return list(enumerate(v)) if all(isinstance(x, str) for x in v) else None
    rows = [_cells(kid.fields, x) for x in v]
    return None if None in rows else [(i, *r) for i, r in enumerate(rows) if r is not None]


def _prefixes(t: Table) -> list[str]:
    paths = [*t.fields, *(k.path for k in t.kids)]
    up = {p.rsplit(".", n)[0] for p in paths for n in range(1, p.count(".") + 1)}
    return sorted(up, key=lambda p: (-p.count("."), p))


def _split(t: Table, doc: dict, own: Own) -> tuple[Own, dict[Kid, list[tuple]], dict]:
    """(columns, {kid: its rows}, what none of them takes), as 0016's fill triggers leave them."""
    rest = copy.deepcopy(doc)
    cols: Own = {}
    for path, (col, kind) in t.fields.items():
        v = get(doc, path)
        fits = v is not None and _fits(kind, v)
        cols[col] = _stored(kind, v) if fits else None
        if fits or v is None:
            _drop(rest, path)
    for path, names, kind in t.tuples:
        v = get(doc, path)
        fits = isinstance(v, list) and len(v) == len(names) and all(_fits(kind, x) for x in v)
        cols.update(zip(names, [_stored(kind, x) for x in v] if fits else [None] * len(names),
                        strict=True))  # fmt: skip
        if fits or v is None:
            _drop(rest, path)
    kids: dict[Kid, list[tuple]] = {}
    for kid in t.kids:
        v = get(doc, kid.path)
        rows = _kid(kid, v, own)
        kids[kid] = rows or []
        if rows is not None or v is None or (kid.shape == "one" and v == {}):
            _drop(rest, kid.path)
    for k in t.have:
        rest.pop(k, None)
    for path, is_same in t.same.items():
        if get(doc, path) is None or is_same(doc, own):
            _drop(rest, path)
    for path in _prefixes(t):
        if get(rest, path) in (None, {}, []):
            _drop(rest, path)
    return cols, kids, rest


# -- writing ---------------------------------------------------------------------------------------


def columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]


def _whole_row(con: sqlite3.Connection, t: Table, doc: dict, own: Own
               ) -> tuple[Own, dict[Kid, list[tuple]]]:  # fmt: skip
    doc = _text(doc)
    cols, kids, rest = _split(t, doc, own)
    known = [*t.fields, *(x[0] for x in t.tuples), *(k.path for k in t.kids)]
    odd = sorted(p for p in known if get(rest, p) is not None
                 and not any(q.startswith(f"{p}.") for q in known))  # fmt: skip
    if odd:
        raise Refused(f"{t.name}: {', '.join(odd)}: not a type its column takes")
    rest, buried = state_shape.sift(t.name, rest)
    if buried:  # a key the row says itself (its `at`), said another way
        state_shape.bury(t.name, {**own, **buried}, "a field its row's columns say otherwise")
    return {**own, **cols, "rest": json.dumps(rest, sort_keys=True)}, kids


def _rows(con: sqlite3.Connection, t: Table, key: Own, at: float,
          kids: dict[Kid, list[tuple]]) -> None:  # fmt: skip
    for kid, rows in kids.items():
        head = {**key, **({"kind": kid.kind} if kid.kind else {})}
        names = {"one": [], "map": ["path", "said"], "text": ["ord", "value"], "list": ["ord"]}
        cells = [c for c, _ in kid.fields.values()] if kid.shape in ("one", "list") else []
        cols = [*head, *names[kid.shape], *cells, "at"]
        con.executemany(
            f"INSERT INTO {kid.table}({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            [(*head.values(), *r, at) for r in rows],
        )


def write(con: sqlite3.Connection, t: Table, doc: dict, own: Own, ignore: bool = False) -> bool:
    row, kids = _whole_row(con, t, doc, own)
    how = "INSERT OR IGNORE" if ignore else "INSERT"
    cur = con.execute(f"{how} INTO {t.name}({', '.join(row)}) VALUES "
                      f"({', '.join('?' * len(row))})", list(row.values()))  # fmt: skip
    if cur.rowcount != 1:
        return False
    key = {k: own.get(k, cur.lastrowid if k == "n" else None) for k in t.key}
    _rows(con, t, key, own[t.stamp], kids)
    return True


def update(con: sqlite3.Connection, t: Table, doc: dict, own: Own, where: str,
           args: Sequence[Any]) -> int:  # fmt: skip
    """OWN: the table's own columns that change with it."""
    names = [*t.key, t.stamp]
    got = con.execute(f"SELECT {', '.join(names)} FROM {t.name} WHERE {where}", args).fetchone()
    if got is None:
        return 0
    key = dict(zip(t.key, got, strict=False))
    row, kids = _whole_row(con, t, doc, {**key, **own})
    ours = " AND ".join(f"{k} = ?" for k in t.key)
    sets = {c: v for c, v in row.items() if c not in t.key}
    con.execute(f"UPDATE {t.name} SET {', '.join(f'{c} = ?' for c in sets)} WHERE {ours}",
                [*sets.values(), *key.values()])  # fmt: skip
    for table in {kid.table for kid in kids}:
        con.execute(f"DELETE FROM {table} WHERE {ours}", list(key.values()))
    _rows(con, t, key, own.get(t.stamp, got[-1]), kids)
    return 1


# -- reading ---------------------------------------------------------------------------------------


def _value(kind: str, v: Any) -> Any:
    return bool(v) if kind == BOOL and v is not None else v


def _object(kid: Kid, cells: Sequence[Any]) -> dict:
    out: dict = {}
    for (path, (_, kind)), v in zip(kid.fields.items(), cells, strict=True):
        if v is not None or kid.full:
            _put(out, path, _value(kind, v))
    return out


def _kids(con: sqlite3.Connection, t: Table, where: str, args: Sequence[Any]
          ) -> dict[tuple, dict[str, Any]]:  # fmt: skip
    """{key: {path: value}} of every child row of the rows WHERE takes."""
    out: dict[tuple, dict[str, Any]] = {}
    n, names = len(t.key), ", ".join(t.key)
    for kid in t.kids:
        cells = [c for c, _ in kid.fields.values()] if kid.shape in ("one", "list") else []
        what = {"text": ["value"], "map": ["path", "said"]}.get(kid.shape, cells)
        order = {"one": "", "map": ", path"}.get(kid.shape, ", ord")
        sql = (f"SELECT {names}, {', '.join(what)} FROM {kid.table} WHERE ({names}) IN "
               f"(SELECT {names} FROM {t.name} WHERE {where})"
               + (f" AND kind = '{kid.kind}'" if kid.kind else "")
               + f" ORDER BY {names}{order}")  # fmt: skip
        for r in con.execute(sql, args):
            mine, got = out.setdefault(tuple(r[:n]), {}), r[n:]
            if kid.shape == "one":
                mine[kid.path] = {"path": r[0], **_object(kid, got)}
            elif kid.shape == "map":
                mine.setdefault(kid.path, {})[got[0]] = got[1]
            else:
                value = got[0] if kid.shape == "text" else _object(kid, got)
                mine.setdefault(kid.path, []).append(value)
    return out


def _document(t: Table, own: Own, cols: Own, kids: dict[str, Any], rest: dict) -> dict:
    doc: dict = {}
    for k in t.have:
        if own[k] is not None and not (k in t.blank and own[k] == ""):
            doc[k] = own[k]
    for path, (col, kind) in t.fields.items():
        if cols[col] is not None:
            _put(doc, path, _value(kind, cols[col]))
    for path, names, kind in t.tuples:
        if cols[names[0]] is not None:
            _put(doc, path, [_value(kind, cols[c]) for c in names])
    for path, v in kids.items():
        _put(doc, path, v)
    for path, v in t.derived(own, doc).items():
        _put(doc, path, v)
    _merge(doc, rest)
    for path, v, guard in t.always:
        if (not guard or get(doc, guard) is not None) and get(doc, path) is None:
            _put(doc, path, copy.copy(v))
    return doc


def read(con: sqlite3.Connection, t: Table, where: str = "1", args: Sequence[Any] = (),
         order: str = "n") -> list[tuple[Own, dict]]:  # fmt: skip
    """One snapshot: a row and its child rows, whoever writes meanwhile."""
    mine = not con.in_transaction
    if mine:
        con.execute("BEGIN")
    try:
        added = [c for c, _ in t.fields.values()] + [c for x in t.tuples for c in x[1]]
        base = [c for c in columns(con, t.name) if c not in (*added, "rest")]
        sql = (f"SELECT {', '.join([*base, 'rest', *added])} FROM {t.name} WHERE {where} "
               f"ORDER BY {order}")  # fmt: skip
        rows = con.execute(sql, args).fetchall()
        kids = _kids(con, t, where, args) if rows and t.kids else {}
    finally:
        if mine:
            con.execute("ROLLBACK")
    out = []
    for r in rows:
        own = dict(zip(base, r, strict=False))
        rest = json.loads(r[len(base)] or "{}")
        cols = dict(zip(added, r[len(base) + 1 :], strict=True))
        mine_kids = kids.get(tuple(own[k] for k in t.key), {})
        out.append((own, _document(t, own, cols, mine_kids, rest)))
    return out
