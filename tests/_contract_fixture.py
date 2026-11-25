"""goh.db in memory on each side of its contracts (GOH.ABSDATA): every writer's whole document in,
the rows read back, and the contracts 0017 to 0020 as `goh state contract` runs them.

    fenced(con)          a cutover's fence put up
    contracts(con, *n)   those numbers applied, each in one transaction
    write_all(con)       one whole document through each table's one writer
    read_all(con)        {table: what its one reader gives}
"""

from __future__ import annotations

import sqlite3

import _body_fixture as bf
import body_columns
import candidate_columns
import db_schema
import goh_db
import land_columns
import ship_columns
import small_stores
import spawn_columns
import state_shape
from _columns_fixture import AT, LAND, OUTCOME, PINS, RESULT, SHIP, SPAWN
from body_tables import TABLES

NUMBERS = (17, 18, 19, 20)
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
        except sqlite3.Error:
            con.execute("ROLLBACK")
            raise
        con.execute("COMMIT")


def write_all(con: sqlite3.Connection) -> None:
    ship_columns.put_pins(con, PINS, RESULT, AT)
    ship_columns.put(con, SHIP, RESULT, "goh", AT)
    land_columns.put(con, "/q", RUN, LAND, AT)
    for row in (SPAWN, OUTCOME):
        spawn_columns.write(con, row)
    candidate_columns.write(con, CANDIDATE, AT)
    for t in TABLES:
        first = 10 if t.name == "early" else 0  # a run is in requests or in early, never in both
        for n, doc in enumerate(bf.DOCS[t.name], first):
            body_columns.write(con, t, bf.named(t, doc, n), bf.own(t, bf.named(t, doc, n), n))
    small_stores.gated_row(con, {"tree": "t0", "repo": "/r", "at": LAND["at"]})


def read_all(con: sqlite3.Connection) -> dict[str, list]:
    out: dict[str, list] = {
        "lands": list(land_columns.read(con, "/q").values()),
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
