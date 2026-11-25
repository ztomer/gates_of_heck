"""goh.db in memory on each side of its contracts (GOH.ABSDATA): every writer's whole document in,
the rows read back, and the contracts 0017 to 0021 as `goh state contract` runs them.

    fenced(con)          a cutover's fence put up
    contracts(con, *n)   those numbers applied, each in one transaction
    write_all(con)       one whole document through each table's one writer, into a cut file
    old_all(con)         the same documents as an older goh inserts them, into an uncut one
    read_all(con, root)  {table: what its one reader gives}
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import _body_fixture as bf
import body_columns
import candidate_columns
import db_schema
import goh_db
import land_columns
import ship_columns
import small_stores
import spawn_columns
import state_paths
import state_shape
from _columns_fixture import AT, LAND, OUTCOME, PINS, RESULT, SHIP, SPAWN
from body_tables import TABLES

STORES = {"early": "requests", "selection_misses": "misses"}  # its name in lib/state_paths.py
NUMBERS = (17, 18, 19, 20, 21)
SHIP_RUN = Path(RESULT).stem
RUN = "01795000000000000000-A.B"
CANDIDATE = {"id": "FAULTS.x", "item": "triage", "phase": "FX", "repo": "?", "status": "todo",
             "done_state": "d", "faults": ["f", "g"], "depends": ["A.B"]}  # fmt: skip


def fenced(con: sqlite3.Connection) -> None:
    con.execute("INSERT INTO meta(key, value) VALUES (?, '1 x')", (state_shape.FENCE,))


def statements(n: int) -> list[str]:
    return db_schema.statements(db_schema.migrations(goh_db.SEAM)[n].read_text(encoding="utf-8"))


def contracts(con: sqlite3.Connection, *numbers: int) -> None:
    for n in numbers:
        con.execute("BEGIN")
        try:
            for sql in statements(n):
                con.execute(sql)
            con.execute("INSERT INTO schema_version VALUES (?, 'x', 0, 't')", (n,))
        except sqlite3.Error:
            con.execute("ROLLBACK")
            raise
        con.execute("COMMIT")


def _rooted(root: str, doc: Any) -> Any:
    return json.loads(json.dumps(doc).replace('"/', f'"{root}/')) if root else doc


def _rows(root: str, runs: bool) -> Iterator[tuple[Any, int, dict]]:
    """(table, n, document) of each in lib/body_tables.py; no RUNS, no request and no early run."""
    for t in TABLES:
        if runs or t.name not in ("requests", "early"):
            first = 10 if t.name == "early" else 0  # a run is in one of the two
            for n, whole in enumerate(bf.DOCS[t.name], first):
                yield t, n, bf.named(t, _rooted(root, whole), n)


def write_all(con: sqlite3.Connection, root: str = "", runs: bool = True) -> None:
    """Every table's one writer; ROOT is put before every path of the documents."""
    result = state_paths.encode(root + RESULT)
    queue = state_paths.encode(root + "/q")
    ship_columns.put_pins(con, state_paths.put("ship_pins", _rooted(root, PINS)), result, AT)
    ship = _rooted(root, SHIP)
    ship_columns.put(con, state_paths.put("ships", ship), result,
                     state_paths.repo_key(ship["repo"]), AT)  # fmt: skip
    land_columns.put(con, queue, RUN, state_paths.put("lands", _rooted(root, LAND)), AT)
    for row in (SPAWN, OUTCOME):
        spawn_columns.write(con, state_paths.put("agent_spawns", _rooted(root, row)))
    candidate_columns.write(con, CANDIDATE, AT)
    for t, n, whole in _rows(root, runs):
        doc = state_paths.put(STORES.get(t.name, t.name), whole)
        own = bf.own(t, doc, n)
        own |= {"queue": state_paths.encode(root + own["queue"])} if "queue" in own else {}
        body_columns.write(con, t, doc, own)
    small_stores.gated_row(con, {"tree": "t0", "repo": f"{root}/r", "at": LAND["at"]})


def insert(con: sqlite3.Connection, table: str, **row: Any) -> None:
    row = {k: json.dumps(v, sort_keys=True) if isinstance(v, dict) else v for k, v in row.items()}
    con.execute(f"INSERT INTO {table}({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                list(row.values()))  # fmt: skip


def old_all(con: sqlite3.Connection, root: str = "", runs: bool = True) -> None:
    """The same rows as an older goh writes them into an uncut file: whole documents, its paths
    absolute, each row's columns the triggers'."""
    ship, result = _rooted(root, SHIP), root + RESULT
    insert(con, "ship_pins", result=result, run=SHIP_RUN, at=AT, body=_rooted(root, PINS))
    plain = {k: ship[k] for k in ("item", "sha", "status", "started", "finished", "version")}
    insert(con, "ships", result=result, run=SHIP_RUN, repo="goh", at=AT, body=ship, **plain)
    old_land(con, root + "/q", RUN, _rooted(root, LAND))
    for row in (SPAWN, OUTCOME):
        insert(con, "agent_spawns", at=AT, event=row["event"], agent=row.get("agent_id"),
               item=(row.get("items") or [None])[0], body=_rooted(root, row))  # fmt: skip
    insert(con, "fault_candidates", at=AT, body=CANDIDATE)
    for t, n, doc in _rows(root, runs):
        own = bf.own(t, doc, n)
        bf.old(con, t, doc, {**own, "queue": root + own["queue"]} if "queue" in own else own)
    insert(con, "commit_gated", tree="t0", at=AT, body={"tree": "t0", "repo": f"{root}/r"})


def old_land(con: sqlite3.Connection, queue: str, run: str, land: dict) -> None:
    plain = ("item", "sha", "enqueued", "repo", "mode", "state", "attempt", "requested", "started",
             "finished", "k")  # fmt: skip
    insert(con, "lands", queue=queue, run=run, at=AT, body=land,
           **{k: land[k] for k in plain if k in land})  # fmt: skip


def read_all(con: sqlite3.Connection, root: str = "") -> dict[str, list]:
    queue = root + "/q"
    out: dict[str, list] = {
        "lands": list(land_columns.read(con, queue).values()),
        "ships": ship_columns.read(con),
        "ship_pins": ship_columns.read_pins(con),
        "agent_spawns": spawn_columns.read(con),
        "fault_candidates": candidate_columns.read(con),
        "commit_gated": con.execute("SELECT tree, at FROM commit_gated").fetchall(),
    }
    for t in TABLES:
        order = "n" if t.key == ("n",) else ", ".join(t.key)
        out[t.name] = [d for _, d in body_columns.read(con, t, order=order)]
    return out
