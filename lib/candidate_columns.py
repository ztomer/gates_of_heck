"""lib/candidate_columns.py -- a fault candidate of `fault_candidates` as typed columns and child
rows (GOH.KVBLOBS; goh.db 0015), the one writer and the one reader of its fields.

    write(con, doc, at)   DOC's columns, its fault_candidate_lists rows, and its document while the
                          table has a `body`; its n. Inside the caller's transaction
    read(con, where, args)   the candidates WHERE names as documents, in the order written

What no column takes (an unknown key, a value of the wrong type) stays in `rest`, which `goh state
check` names; from a cutover on (lib/state_shape.py) such a row is Refused, for its caller to bury.
`rest` IS WRITTEN, never NULL, so 0015's fill trigger (older code's) does not run on
these rows; a row it has not filled (`rest` NULL: `goh state backfill`), and a file from before
0015, are read from the document. A null, an empty list and an absent key read the same: absent.
Pinned by tests/test_candidate_columns.py.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import state_shape  # lib/, on the path from the line above
from spawn_columns import (
    Refused,
    _text as utf8,  # a lone surrogate is no text a column holds
)

TEXT = ("id", "item", "phase", "repo", "status", "done_state")
LISTS = {"faults": "fault", "depends": "depend"}
EMPTY: tuple[Any, ...] = (None, {}, [])


def _columns(con: sqlite3.Connection) -> set[str]:
    return {r[1] for r in con.execute("PRAGMA table_info(fault_candidates)")}


def write(con: sqlite3.Connection, doc: dict, at: float) -> int:
    doc = utf8(doc)
    rest = {k: v for k, v in doc.items() if v not in EMPTY}
    cols: dict[str, Any] = {"at": at}
    for k in TEXT:
        cols[k] = rest.pop(k) if isinstance(rest.get(k), str) else None
    lists = []
    for k, kind in LISTS.items():
        v = rest.get(k)
        if isinstance(v, list) and all(isinstance(x, str) for x in v):
            lists += [(kind, i, x) for i, x in enumerate(rest.pop(k))]
    cols["rest"] = json.dumps(rest, sort_keys=True)
    have = _columns(con)
    if (odd := sorted({*TEXT, *LISTS} & rest.keys())) and (
            state_shape.closing(con, "fault_candidates")):  # fmt: skip
        raise Refused(f"{', '.join(odd)}: not a type its column takes")
    cols |= state_shape.document(con, "fault_candidates", json.dumps(doc, sort_keys=True))
    names = [c for c in cols if c in have]
    sql = f"INSERT INTO fault_candidates({', '.join(names)}) VALUES ({', '.join('?' * len(names))})"
    n = int(con.execute(sql, [cols[c] for c in names]).lastrowid or 0)
    if "rest" in have:
        con.executemany("INSERT INTO fault_candidate_lists(n, kind, ord, value, at) "
                        "VALUES (?, ?, ?, ?, ?)", [(n, *r, at) for r in lists])  # fmt: skip
    return n


def read(con: sqlite3.Connection, where: str = "1", args: tuple = ()) -> list[dict]:
    """One snapshot is the caller's: two reads of one transaction."""
    have = _columns(con)
    if "rest" not in have:  # a file no writer of this goh has opened yet
        docs = con.execute(f"SELECT body FROM fault_candidates WHERE {where} ORDER BY n", args)
        return [_bare(json.loads(b)) for (b,) in docs]
    lists: dict[int, dict[str, list[str]]] = {}
    for n, kind, v in con.execute(
        f"SELECT n, kind, value FROM fault_candidate_lists WHERE n IN "
        f"(SELECT n FROM fault_candidates WHERE {where}) ORDER BY n, kind, ord", args):  # fmt: skip
        lists.setdefault(n, {}).setdefault(kind, []).append(v)
    body = "body" if "body" in have else "NULL"
    out = []
    for n, rest, unfilled, *values in con.execute(
        f"SELECT n, rest, CASE WHEN rest IS NULL THEN {body} END, {', '.join(TEXT)} "
        f"FROM fault_candidates WHERE {where} ORDER BY n", args):  # fmt: skip
        if rest is None:
            out.append(_bare(json.loads(unfilled or "{}")))
            continue
        doc = dict(zip(TEXT, values, strict=True)) | json.loads(rest)
        doc |= {k: lists[n][kind] for k, kind in LISTS.items() if kind in lists.get(n, {})}
        out.append(_bare(doc))
    return out


def _bare(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if v not in EMPTY}
