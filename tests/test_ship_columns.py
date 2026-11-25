"""lib/ship_columns.py: a ship's record and its pins written as columns and child rows, and read
from them (GOH.STEPTIMES, the plan's S4c). The tables are the migrations' own SQL in memory; what
0012's triggers make of a document is what the writer is held to, row for row.
"""

from __future__ import annotations

import copy
import json
import sqlite3
from typing import Any

import pytest
import ship_columns
import state_columns
from _columns_fixture import AT, PINS, RESULT, SHIP, STEP
from conftest import REPO_ROOT

SCHEMA = REPO_ROOT / "lib" / "goh_db_schema"
TABLES = ("ships", "ship_pins", *ship_columns.CHILDREN, "ship_pin_repos")
PLANNED = {
    "schema": 1, "repo": "/r/goh", "name": "goh", "item": None, "summary": "", "progress": False,
    "sha": "s1", "base_tag": None, "version": None, "bump": None, "install": None, "release": "r",
    "preflight": [], "timeout_s": 900, "result": RESULT, "status": "running", "steps": [],
    "started": "2026-11-18T11:00:00+00:00", "failed_step": None, "tail": [], "undo": [],
    "pid": None, "load1": 3.5, "allow_diffcov": None, "quiet_settings": [70.0, 600.0],
}  # fmt: skip


def memory() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:", isolation_level=None)
    for n in ("0003_ships", "0009_ships_v2", "0012_ships_columns"):
        con.executescript((SCHEMA / f"{n}.sql").read_text(encoding="utf-8"))
    return con


def put(con: sqlite3.Connection, doc: dict, at: float = AT) -> None:
    ship_columns.put(con, doc, RESULT, "goh", at)


def fill(con: sqlite3.Connection, doc: dict) -> None:
    """DOC as goh wrote one before the columns, for 0012's triggers to fill."""
    vals = [None if doc.get(c) is None else str(doc[c]) for c in ship_columns.HAVE]
    con.execute("INSERT OR REPLACE INTO ships(result, run, repo, item, sha, status, started, "
                "finished, version, body, at) VALUES (?, ?, 'goh', ?, ?, ?, ?, ?, ?, ?, ?)",
                (RESULT, "20261118T110640Z-c1", *vals, json.dumps(doc), AT))  # fmt: skip


def dump(con: sqlite3.Connection) -> dict[str, list[dict]]:
    out = {}
    for t in TABLES:
        cols = state_columns.columns(con, t)
        rows = [dict(zip(cols, r, strict=True)) for r in con.execute(f"SELECT * FROM {t}")]
        for r in rows:
            r |= {k: json.loads(r[k]) for k in ("rest", "body") if r.get(k)}
        out[t] = sorted(rows, key=lambda r: json.dumps(r, sort_keys=True))
    return out


def changed(path: str, v: Any) -> dict:
    doc = copy.deepcopy(SHIP)
    *heads, last = path.split(".")
    at = doc
    for k in heads:
        at = at[k]
    at[last] = v
    return doc


ODD = [("pid", "7"), ("land_wait_s", "soon"), ("land_wait_s", True), ("release_pushes", 1),
       ("surprise", {"a": 1}), ("repo", "/elsewhere/x"), ("quiet_settings", [1, 2]),
       ("preflight", ["a", 1]), ("steps", [{**STEP, "new": 1}]), ("steps", [STEP, "x"]),
       ("steps", [{**STEP, "profiles": []}]),
       ("steps", [{"name": "x", "seconds": 3, "code": None}]),
       ("bisect.runs", [{"sha": 1}]), ("bisect", {"odd": 1}), ("bisect", {}), ("bisect", "x"),
       ("grace_over.repos", {"r": ["a", 2]}), ("diffcov_red.faults", {"f": 1}), ("quiet", None),
       ("diffcov_red.lines", []), ("load1", 3), ("started", None), ("retried", None)]  # fmt: skip


@pytest.mark.parametrize("doc", [
    SHIP, PLANNED, {"status": "ok", "result": RESULT}, *(changed(p, v) for p, v in ODD)],
    ids=["whole", "planned", "bare", *(f"{p}={v}"[:30] for p, v in ODD)])  # fmt: skip
def test_the_writer_puts_what_the_triggers_put(doc: dict) -> None:
    old, new = memory(), memory()
    fill(old, doc)
    put(new, doc)
    assert dump(new) == dump(old)


@pytest.mark.parametrize("pins", [PINS, {"tag": "v1", "repos": {}}, {"tag": 1, "repos": {"r": 3}},
                                  {"repos": None, "odd": 1}])  # fmt: skip
def test_the_pins_writer_puts_what_the_triggers_put(pins: dict) -> None:
    old, new = memory(), memory()
    old.execute("INSERT INTO ship_pins(result, run, body, at) VALUES (?, ?, ?, ?)",
                (RESULT, "20261118T110640Z-c1", json.dumps(pins), AT))  # fmt: skip
    ship_columns.put_pins(new, pins, RESULT, AT)
    assert dump(new) == dump(old)
    assert ship_columns.read_pins(new) == ship_columns.read_pins(old)


def whole(con: sqlite3.Connection) -> dict:
    ((result, rec),) = ship_columns.read(con)
    assert result == RESULT
    return rec


def test_the_record_reads_back_whole_from_its_columns() -> None:
    con = memory()
    ship_columns.put_pins(con, PINS, RESULT, AT)
    put(con, SHIP)
    want = {k: v for k, v in SHIP.items() if k != "schema"}
    assert whole(con) == want
    con.execute("UPDATE ships SET body = '{}'")  # the document is gone: the plan's S7
    assert whole(con) == {**want, "repo": "goh"}  # its key, which state_paths gives a path
    assert dict(ship_columns.read_pins(con))[RESULT] == {k: v for k, v in PINS.items()
                                                         if k != "schema"}  # fmt: skip


def test_a_table_with_no_document_is_written_and_read() -> None:
    con = memory()
    con.execute("DROP VIEW ships_v2")
    con.execute("DROP VIEW IF EXISTS ships_v1")
    for t in ("fill", "refill", "bad"):
        con.execute(f"DROP TRIGGER ships_{t}")
        con.execute(f"DROP TRIGGER ship_pins_{t}")
    con.execute("ALTER TABLE ships DROP COLUMN body")
    con.execute("ALTER TABLE ship_pins DROP COLUMN body")
    ship_columns.put_pins(con, PINS, RESULT, AT)
    put(con, SHIP)
    assert whole(con)["steps"] == SHIP["steps"]
    assert dict(ship_columns.read_pins(con))[RESULT]["repos"] == PINS["repos"]


def test_a_planned_ship_has_every_key_it_was_planned_with() -> None:
    """A step's code indexes them: `res["steps"]` of a ship that has run none yet."""
    con = memory()
    put(con, PLANNED)
    assert whole(con) == {k: v for k, v in PLANNED.items() if k != "schema"}


def test_a_null_a_reader_indexes_is_there() -> None:
    con = memory()
    ran = {"name": "push", "status": "ok", "load1_end": 1.0, "idle_pct": None}
    skipped = {"name": "x", "status": "skipped"}
    put(con, {**PLANNED, "grace_over": {"why": "w", "count": None}, "steps": [ran, skipped]})
    assert whole(con)["grace_over"] == {"why": "w", "count": None}
    assert whole(con)["steps"] == [ran, skipped]


def test_a_second_write_leaves_nothing_of_the_first() -> None:
    con = memory()
    put(con, SHIP)
    put(con, {**PLANNED, "status": "ok", "steps": [{"name": "push", "status": "ok"}]}, AT + 1)
    got = dump(con)
    assert [r["name"] for r in got["ship_steps"]] == ["push"]
    assert got["ship_bisect"] == got["ship_grace"] == got["ship_lists"] == []
    assert got["ships"][0]["bisect_base"] is None and got["ships"][0]["at"] == AT + 1


def test_a_row_nobody_filled_is_read_from_its_document_and_filled_by_its_next_write() -> None:
    con = memory()
    con.execute("DROP TRIGGER ships_fill")
    fill(con, SHIP)
    assert whole(con) == SHIP
    put(con, whole(con))
    assert dump(con)["ships"][0]["rest"] == {}


def test_an_older_writer_s_row_replaces_this_one_s() -> None:
    con, clean = memory(), memory()
    put(con, SHIP)
    fill(con, PLANNED)
    fill(clean, PLANNED)
    assert dump(con) == dump(clean)


def test_where_names_the_rows() -> None:
    con = memory()
    put(con, SHIP)
    ship_columns.put(con, {**PLANNED, "result": "/s/other.json"}, "/s/other.json", "goh", AT)
    got = ship_columns.read(con, "x.status IN ('running', 'bisecting')")
    assert [r for r, _ in got] == ["/s/other.json"]
    assert [r for r, _ in ship_columns.read(con)] == sorted([RESULT, "/s/other.json"])
