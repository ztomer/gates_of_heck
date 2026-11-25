"""lib/candidate_columns.py -- a fault candidate of `fault_candidates` as typed columns and child
rows (GOH.KVBLOBS; goh.db 0015), the one writer and the one reader of its fields.

    write(con, doc, at)   DOC's columns and its fault_candidate_lists rows; its n. Inside the
                          caller's transaction
    read(con, where, args)   the candidates WHERE names as documents, in the order written

An unknown key stays in `rest`, which `goh state check` names; a row with a known key of the wrong
type is Refused, for its caller to bury. A null, an empty list and an absent key read the same:
absent. Pinned by tests/test_candidate_columns.py.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from spawn_columns import (  # lib/, on the path from the line above
    Refused,
    _text as utf8,  # a lone surrogate is no text a column holds
)

TEXT = ("id", "item", "phase", "repo", "status", "done_state")
LISTS = {"faults": "fault", "depends": "depend"}
EMPTY: tuple[Any, ...] = (None, {}, [])


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
    if odd := sorted({*TEXT, *LISTS} & rest.keys()):
        raise Refused(f"{', '.join(odd)}: not a type its column takes")
    sql = f"INSERT INTO fault_candidates({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
    n = int(con.execute(sql, list(cols.values())).lastrowid or 0)
    con.executemany("INSERT INTO fault_candidate_lists(n, kind, ord, value, at) "
                    "VALUES (?, ?, ?, ?, ?)", [(n, *r, at) for r in lists])  # fmt: skip
    return n


def read(con: sqlite3.Connection, where: str = "1", args: tuple = ()) -> list[dict]:
    """One snapshot is the caller's: two reads of one transaction."""
    lists: dict[int, dict[str, list[str]]] = {}
    for n, kind, v in con.execute(
        f"SELECT n, kind, value FROM fault_candidate_lists WHERE n IN "
        f"(SELECT n FROM fault_candidates WHERE {where}) ORDER BY n, kind, ord", args):  # fmt: skip
        lists.setdefault(n, {}).setdefault(kind, []).append(v)
    out = []
    for n, rest, *values in con.execute(
        f"SELECT n, rest, {', '.join(TEXT)} FROM fault_candidates WHERE {where} ORDER BY n", args
    ):
        doc = dict(zip(TEXT, values, strict=True)) | json.loads(rest or "{}")
        doc |= {k: lists[n][kind] for k, kind in LISTS.items() if kind in lists.get(n, {})}
        out.append(_bare(doc))
    return out


def _bare(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if v not in EMPTY}
