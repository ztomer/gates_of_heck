"""lib/ship_columns.py: a ship's record and its pins written as columns and child rows, and read
from them (GOH.STEPTIMES, the plan's S4c). The file is in memory; what 0012's triggers made of an
older copy's document is what the writer is held to, row for row.
"""

from __future__ import annotations

import copy
import json
import sqlite3
from typing import Any

import _body_fixture as bf
import pytest
import ship_columns
import state_columns
import state_shape
from _columns_fixture import AT, PINS, RESULT, SHIP, STEP
from _contract_fixture import insert

REPO = "/r/goh"
TABLES = ("ships", "ship_pins", *ship_columns.CHILDREN, "ship_pin_repos")
PLANNED = {
    "schema": 1, "repo": "/r/goh", "name": "goh", "item": None, "summary": "", "progress": False,
    "sha": "s1", "base_tag": None, "version": None, "bump": None, "install": None, "release": "r",
    "preflight": [], "timeout_s": 900, "result": RESULT, "status": "running", "steps": [],
    "started": "2026-11-18T11:00:00+00:00", "failed_step": None, "tail": [], "undo": [],
    "pid": None, "load1": 3.5, "allow_diffcov": None, "quiet_settings": [70.0, 600.0],
}  # fmt: skip


def put(con: sqlite3.Connection, doc: dict, at: float = AT) -> None:
    ship_columns.put(con, doc, RESULT, REPO, at)


def fill(con: sqlite3.Connection, doc: dict) -> None:
    """DOC as goh wrote one before the columns, for 0012's triggers to fill."""
    plain = {c: str(doc[c]) for c in ship_columns.HAVE if doc.get(c) is not None}
    run = "20261118T110640Z-c1"
    insert(con, "ships", result=RESULT, run=run, repo=REPO, at=AT, body=doc, **plain)


def dump(con: sqlite3.Connection) -> dict[str, list[dict]]:
    out = {}
    for t in TABLES:
        cols = state_columns.columns(con, t)
        rows = [dict(zip(cols, r, strict=True)) for r in con.execute(f"SELECT * FROM {t}")]
        for r in rows:
            r |= {k: json.loads(r[k]) for k in ("rest",) if r.get(k)}
            r.pop("body", None)
        out[t] = sorted(rows, key=lambda r: json.dumps(r, sort_keys=True))
    return out


def sifted(rows: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """As the cutover leaves them: what fits no column out of `rest`."""
    for t in ("ships", "ship_pins"):
        for r in rows[t]:
            r["rest"] = state_shape.sift(t, r["rest"])[0]
    return rows


@pytest.fixture(autouse=True)
def letters(monkeypatch) -> list[tuple]:
    out: list[tuple] = []
    monkeypatch.setattr(state_shape, "bury", lambda *a: out.append(a))
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
    old, new = bf.memory(bf.UNCUT), bf.memory()
    fill(old, doc)
    put(new, doc)
    want, got = sifted(dump(old)), dump(new)
    if doc.get("quiet") is None and doc.get(
        "quiet_settings"
    ):  # the triggers had no column for it: 0018 refuses it in `rest`
        want["ships"][0] |= {"quiet_min_idle_pct": 70.0, "quiet_max_s": 600.0}
    assert got == want


@pytest.mark.parametrize("pins", [PINS, {"tag": "v1", "repos": {}}, {"tag": 1, "repos": {"r": 3}},
                                  {"repos": None, "odd": 1}])  # fmt: skip
def test_the_pins_writer_puts_what_the_triggers_put(pins: dict) -> None:
    old, new = bf.memory(bf.UNCUT), bf.memory()
    old.execute("INSERT INTO ship_pins(result, run, body, at) VALUES (?, ?, ?, ?)",
                (RESULT, "20261118T110640Z-c1", json.dumps(pins), AT))  # fmt: skip
    ship_columns.put_pins(new, pins, RESULT, AT)
    assert dump(new) == sifted(dump(old))
    ((_, got),) = ship_columns.read_pins(new)
    assert got.get("tag") == (pins.get("tag") if isinstance(pins.get("tag"), str) else None)


def whole(con: sqlite3.Connection) -> dict:
    ((result, rec),) = ship_columns.read(con)
    assert result == RESULT
    return rec


def test_the_record_reads_back_whole_from_its_columns() -> None:
    con = bf.memory()
    ship_columns.put_pins(con, PINS, RESULT, AT)
    put(con, SHIP)
    want = {k: v for k, v in SHIP.items() if k != "schema"} | {"repo": REPO}
    assert whole(con) == want
    assert dict(ship_columns.read_pins(con))[RESULT] == {k: v for k, v in PINS.items()
                                                         if k != "schema"}  # fmt: skip


def test_a_planned_ship_has_every_key_it_was_planned_with() -> None:
    """A step's code indexes them: `res["steps"]` of a ship that has run none yet."""
    con = bf.memory()
    put(con, PLANNED)
    assert whole(con) == {k: v for k, v in PLANNED.items() if k != "schema"}


def test_a_planned_ship_keeps_the_quiet_wait_it_was_planned_with(letters) -> None:
    """Its worker reads the wait from the row it was planned in (lib/ship_status.py quiet_wait)."""
    con = bf.memory()
    put(con, PLANNED)
    assert whole(con)["quiet_settings"] == [70.0, 600.0] and "quiet" not in whole(con)
    waited = {"quiet": True, "min_idle_pct": 70.0, "max_s": 600.0}
    put(con, {**PLANNED, "quiet": waited})
    assert whole(con)["quiet"] == waited and whole(con)["quiet_settings"] == [70.0, 600.0]
    assert letters == []


def test_a_null_a_reader_indexes_is_there() -> None:
    con = bf.memory()
    ran = {"name": "push", "status": "ok", "load1_end": 1.0, "idle_pct": None}
    skipped = {"name": "x", "status": "skipped"}
    put(con, {**PLANNED, "grace_over": {"why": "w", "count": None}, "steps": [ran, skipped]})
    assert whole(con)["grace_over"] == {"why": "w", "count": None}
    assert whole(con)["steps"] == [ran, skipped]


def test_a_second_write_leaves_nothing_of_the_first() -> None:
    con = bf.memory()
    put(con, SHIP)
    put(con, {**PLANNED, "status": "ok", "steps": [{"name": "push", "status": "ok"}]}, AT + 1)
    got = dump(con)
    assert [r["name"] for r in got["ship_steps"]] == ["push"]
    assert got["ship_bisect"] == got["ship_grace"] == got["ship_lists"] == []
    assert got["ships"][0]["bisect_base"] is None and got["ships"][0]["at"] == AT + 1


def test_where_names_the_rows() -> None:
    con = bf.memory()
    put(con, SHIP)
    ship_columns.put(con, {**PLANNED, "result": "/s/other.json"}, "/s/other.json", "goh", AT)
    got = ship_columns.read(con, "x.status IN ('running', 'bisecting')")
    assert [r for r, _ in got] == ["/s/other.json"]
    assert [r for r, _ in ship_columns.read(con)] == sorted([RESULT, "/s/other.json"])
