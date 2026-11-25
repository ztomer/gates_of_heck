"""lib/state_rows.py -- a table's rows as documents, for the imports that read their old files back
(lib/state_retire*.py): no import names a stored document, which a contract drops (GOH.ABSDATA).

    said(table, doc)     DOC as one text: equal texts say the same (lib/state_columns.py's equality:
                         null, {} and [] are an absent key, an instant in any spelling, a path's
                         two forms)
    docs(con, table, where, args)   every row WHERE names as its document, from its columns
    add(con, table, doc, own)       DOC as a new row of TABLE, its OWN columns beside
    missing(con, table, rows)       how many of ROWS no row of TABLE says, as often as ROWS does
Pinned by tests/test_state_retire.py, tests/test_state_retire_verdicts.py and their siblings.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections import Counter
from collections.abc import Iterable
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import body_columns  # lib/, on the path from the line above
import body_tables
import candidate_columns
import land_columns
import ship_columns
import spawn_columns
import state_columns
import state_paths

BODIES = {t.name: t for t in body_tables.TABLES}


def said(table: str, doc: Any) -> str:
    if table == "agent_spawns" and isinstance(doc, dict):
        doc = spawn_columns.normal(doc)
    doc = state_columns.spelled(table, json.dumps(state_paths.got(table, doc)))
    if table == "flakes" and "id" in doc and "epoch" in doc:
        doc.setdefault("at", int(doc["epoch"]))  # its row reads back with one
    return json.dumps(doc, sort_keys=True)


def docs(con: sqlite3.Connection, table: str, where: str = "1", args: tuple = ()) -> list[dict]:
    if table in BODIES:
        order = "n" if BODIES[table].key == ("n",) else ", ".join(BODIES[table].key)
        return [d for _, d in body_columns.read(con, BODIES[table], where, args, order)]
    if table == "ships":
        return [d for _, d in ship_columns.read(con, where, args)]
    if table == "ship_pins":
        return [d for _, d in ship_columns.read_pins(con, where, args)]
    if table == "fault_candidates":
        return candidate_columns.read(con, where, args)
    if table == "agent_spawns":
        return spawn_columns.read(con)
    raise ValueError(table)


def land(con: sqlite3.Connection, queue: str, run: str) -> dict | None:
    return land_columns.read(con, queue, runs=[run]).get(run)


def add(con: sqlite3.Connection, table: str, doc: dict, own: dict, ignore: bool = False) -> bool:
    doc = state_paths.put(con, table, doc)
    if table == "fault_candidates":
        return bool(candidate_columns.write(con, doc, own["at"]))
    return body_columns.write(con, BODIES[table], doc, own, ignore)


def missing(con: sqlite3.Connection, table: str, rows: Iterable[dict]) -> int:
    have = Counter(said(table, d) for d in docs(con, table))
    return sum((Counter(said(table, r) for r in rows) - have).values())
