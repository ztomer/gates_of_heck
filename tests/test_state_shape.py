"""lib/state_shape.py and the writers over every shape of goh.db (GOH.ABSDATA): with documents,
under a cutover's fence, and after the contracts 0017 to 0020."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import _body_fixture as bf
import body_columns
import candidate_columns
import hook_ledgers
import land_columns
import land_records
import pytest
import ship_columns
import spawn_columns
import state_columns
import state_retire
import state_retire_verdicts
import state_rows
import state_shape
from _columns_fixture import AT, LAND, OUTCOME, PINS, RESULT, SHIP, SPAWN
from _contract_fixture import NUMBERS, RUN, contracts, fenced, read_all, write_all
from body_tables import AUDIT, LANES, TABLES

DOCUMENTS = ("lands", "ships", "ship_pins", "agent_spawns", "fault_candidates", "commit_gated",
             *(t.name for t in TABLES))  # fmt: skip
SHAPES = ["documents", "fenced", "contracts"]


def shaped(shape: str) -> sqlite3.Connection:
    con = bf.memory()
    if shape == "fenced":
        fenced(con)
    if shape == "contracts":
        contracts(con, *NUMBERS)
    return con


def bodies(con: sqlite3.Connection, table: str) -> set[str]:
    if "body" not in state_shape.columns(con, table):
        return set()
    return {b for (b,) in con.execute(f"SELECT body FROM {table}")}


@pytest.fixture
def letters(monkeypatch) -> list[tuple]:
    out: list[tuple] = []
    monkeypatch.setattr(state_shape, "bury", lambda *a: out.append(a))
    return out


@pytest.mark.parametrize("shape", SHAPES)
def test_every_writer_writes_and_its_reader_reads_every_shape(shape, letters) -> None:
    plain, con = shaped("documents"), shaped(shape)
    for c in (plain, con):
        write_all(c)
    want, got = read_all(plain), read_all(con)
    if shape != "documents":  # a ship's record names its repo by key once no document has its path
        for (_, a), (_, b) in zip(want["ships"], got["ships"], strict=True):
            assert (a.pop("repo"), b.pop("repo")) == ("/r/goh", "goh")
    assert got == want and all(want.values()) and letters == []
    left = {t for t in DOCUMENTS if bodies(con, t) - {"{}"}}
    assert left == (set(DOCUMENTS) if shape == "documents" else
                    {"selection_misses"} if shape == "fenced" else set())  # fmt: skip


def test_the_contracts_leave_one_body_and_no_table_of_red_causes_or_guards() -> None:
    con = shaped("contracts")
    tables = {t for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {t for t in tables if "body" in state_shape.columns(con, t)} == {"kv"}
    assert not {"red_causes", "land_guards"} & tables
    assert "item" not in state_shape.columns(con, "agent_spawns")
    triggers = con.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
    names = {n for (n,) in triggers}
    assert not {n for n in names if n.endswith(("_fill", "_refill", "_bad"))}
    for t in set(DOCUMENTS) - {"commit_gated"}:
        assert {f"{t}_rest_insert", f"{t}_rest_update"} <= names, t
        assert state_shape.promoted(t), t


def put(con: sqlite3.Connection, table: str, odd: dict) -> None:
    if table == "lands":
        land_columns.put(con, "/q", RUN, {**LAND, **odd}, AT)
    elif table == "ships":
        ship_columns.put(con, {**SHIP, **odd}, RESULT, "goh", AT)
    elif table == "ship_pins":
        ship_columns.put_pins(con, {**PINS, **odd}, RESULT, AT)
    elif table == "agent_spawns":
        spawn_columns.write(con, {**SPAWN, **odd})
    elif table == "fault_candidates":
        candidate_columns.write(con, {"item": "x", **odd}, AT)
    else:
        body_columns.write(con, LANES, {**bf.LANE, **odd}, bf.own(LANES, bf.LANE))


ODD = [("lands", {"pre_s": "soon"}, {"pre_s": "soon"}),
       ("lands", {"guards": "g", "guard": None}, {"guards": "g"}),
       ("lands", {"pre_run": {"wait_s": "x", "new": 1}}, {"pre_run.wait_s": "x"}),
       ("ships", {"pid": "x", "steps": "none"}, {"pid": "x", "steps": "none"}),
       ("ship_pins", {"tag": 5}, {"tag": 5}),
       ("agent_spawns", {"effort": 9}, None),
       ("fault_candidates", {"faults": "f"}, None),
       ("lane_starts", {"verdict": 3}, None),
       ("lane_starts", {"at": "soon"}, {"at": "soon"})]  # fmt: skip


@pytest.mark.parametrize("shape", SHAPES[1:])
@pytest.mark.parametrize(("table", "odd", "buried"), ODD)
def test_a_value_of_the_wrong_type_is_buried_or_refused(shape, table, odd, buried, letters) -> None:
    con = shaped(shape)
    if buried is None:
        with pytest.raises(spawn_columns.Refused):
            put(con, table, odd)
        assert con.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)
        return
    put(con, table, odd)
    ((name, row, _),) = letters
    assert name == table and {k: row[k] for k in buried} == buried
    ((rest,),) = con.execute(f"SELECT rest FROM {table}").fetchall()
    assert json.loads(rest) == ({"pre_run": {"new": 1}} if "pre_run" in odd else {})


def test_before_the_fence_a_wrong_value_stays_in_rest_and_an_unknown_key_always(letters) -> None:
    for shape in SHAPES:
        con = shaped(shape)
        put(con, "lands", {"later": [1]})
        assert con.execute("SELECT rest FROM lands").fetchone() == ('{"later":[1]}',)
    con = shaped("documents")
    put(con, "lands", {"pre_s": "soon"})
    assert con.execute("SELECT rest FROM lands").fetchone() == ('{"pre_s":"soon"}',)
    assert letters == []


@pytest.fixture(scope="module")
def sealed() -> sqlite3.Connection:
    con = shaped("contracts")
    write_all(con)
    return con


@pytest.mark.parametrize("table", sorted(set(DOCUMENTS) - {"commit_gated"}))
def test_a_contracts_rest_refuses_every_field_that_has_a_column(table, sealed) -> None:
    def rest(doc: object) -> None:
        sealed.execute(f"UPDATE {table} SET rest = ?", (json.dumps(doc),))

    for path in state_shape.promoted(table):
        doc: object = 1
        for k in reversed(path.split(".")):
            doc = {k: doc}
        with pytest.raises(sqlite3.IntegrityError, match="rest holds a field"):
            rest(doc)
    rest({"later": 1})


def test_the_check_and_the_backfill_know_the_contracts_file(letters, monkeypatch) -> None:
    con = shaped("contracts")
    write_all(con)
    monkeypatch.setattr(hook_ledgers, "dead", list)
    assert state_columns.check(con) == []
    con.execute("UPDATE lands SET rest = '{\"later\": 1}'")
    con.execute("UPDATE ships SET rest = NULL")
    con.execute("INSERT INTO land_lists VALUES ('/q', 'gone', 'guard', 0, 'g', 1)")
    got = state_columns.check(con)
    assert got == [f"lands: /q {RUN}: `rest` holds {{\"later\": 1}}",
                   "ships: 1 rows with no `rest`: nothing says what they held",
                   "land_lists: 1 of its rows have no lands row"]  # fmt: skip


CLEAR = [f"UPDATE {t} SET body = '{{}}'" for t in DOCUMENTS if t not in (
    "selection_misses", "commit_gated")]  # fmt: skip
GUARD = json.dumps({"sha": "gone", "item": "A.B"})


@pytest.mark.parametrize(
    ("n", "sql"),
    [
        *((n, None) for n in NUMBERS),  # every document still there
        (17, "UPDATE lands SET rest = NULL"),
        (17, "UPDATE lands SET rest = '{\"guards\": 3}'"),
        (17, "INSERT INTO red_causes SELECT queue, run, 'x', 1, '{}' FROM lands"),
        (17, f"INSERT INTO land_guards(queue, at, body) VALUES ('/q', 1, '{GUARD}')"),
        (18, "UPDATE ship_pins SET rest = '{\"repos\": {}}'"),
        (19, "UPDATE agent_spawns SET item = 'OLD.ITEM' WHERE n = 1"),
        (20, "UPDATE flakes SET rest = NULL WHERE n = 1"),
        (20, "UPDATE selection_misses SET rest = '{\"changed\": 1}' WHERE n = 1"),
    ],
)
def test_a_contract_refuses_what_a_column_does_not_hold(n, sql) -> None:
    con = shaped("documents")
    write_all(con)
    for clear in CLEAR if sql else ():
        con.execute(clear)
    contracts(con, *(k for k in NUMBERS if k < n and sql))
    if sql and "red_causes" in sql:
        con.execute("UPDATE lands SET red_class = NULL")
    if sql:
        con.execute(sql)
    was = con.execute("SELECT name, sql FROM sqlite_master").fetchall()
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed: n = 0"):
        contracts(con, n)
    assert con.execute("SELECT name, sql FROM sqlite_master").fetchall() == was


def test_an_update_under_the_fence_keeps_what_the_columns_hold(letters) -> None:
    """The wide refill of reuse_audit must not fill the row from the {} this writer leaves."""
    con = shaped("documents")
    body_columns.write(con, AUDIT, bf.OFF, bf.own(AUDIT, bf.OFF))
    fenced(con)
    assert body_columns.update(con, AUDIT, bf.ON, {"at": AT + 1}, "path = ?", ("carry",)) == 1
    assert [d for _, d in body_columns.read(con, AUDIT, order="path")] == [bf.ON]
    assert bodies(con, "reuse_audit") == {"{}"}


def test_the_contracts_file_has_no_ledger_of_guards_and_no_red_causes(monkeypatch) -> None:
    import contextlib

    con = shaped("contracts")
    put(con, "lands", {})
    monkeypatch.setattr(land_records, "_reader", lambda: contextlib.nullcontext(con))
    q = Path("/q")
    assert land_records.ledger(q, "guards") == []
    assert land_records.insert(con, "/q", "guards", [{"guard": "g", "at": "x"}]) == 0
    assert land_records.causes(q) == {f"{RUN}.json": LAND["red_cause"]}


def test_the_old_files_imports_name_no_document(tmp_path, letters) -> None:
    """`goh state retire`'s hook, small and reuse files, into the contracts' file, read back."""
    con = shaped("contracts")
    (shadow := tmp_path / "shadow.jsonl").write_text(
        json.dumps({**bf.SAMPLE, "outcome": "agreed"}) + "\n"
    )
    (audit := tmp_path / "carry.json").write_text(json.dumps(bf.OFF))
    (gated := tmp_path / "commit-gated.jsonl").write_text('{"tree": "t", "at": 5, "repo": "/r"}\n')
    (flakes := tmp_path / "register.jsonl").write_text(json.dumps(bf.FLAKE) + "\n")
    for _ in "ab":  # a second run adds nothing
        state_retire_verdicts._audit_load(con, [audit, shadow])
        state_retire._small_load(con, [gated, flakes])
    assert [state_retire_verdicts._audit_check(con, p) for p in (audit, shadow)] == [None, None]
    assert [state_retire._small_check(con, p) for p in (gated, flakes)] == [None, None]
    counts = [con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
              for t in ("reuse_audit", "reuse_shadow", "commit_gated", "flakes")]  # fmt: skip
    assert counts == [1, 1, 1, 1]
    shadow.write_text(shadow.read_text() + json.dumps({**bf.SAMPLE, "outcome": "other"}) + "\n")
    assert state_retire_verdicts._audit_check(con, shadow) == "1 lines have no reuse_shadow row"
    assert state_rows.missing(con, "agent_spawns", [SPAWN, OUTCOME]) == 2
