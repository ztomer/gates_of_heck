"""GOH.BODYCOLS: the documents of goh.db's seven small body tables as columns and child rows
(lib/body_columns.py, lib/body_tables.py, schema file 0016): written as 0016's triggers write an
older copy's, read back as the document was, on every shape of the file."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator

import body_columns
import db_bounds_with
import goh_db
import pytest
import reuse
import state_columns
from _body_fixture import (
    AT,
    DOCS,
    FLAKE,
    HOLDER,
    JUDGED,
    LAND,
    LANE,
    MARK,
    MISS,
    OFF,
    ON,
    PUSH,
    READING,
    SAMPLE,
    STARVED,
    contract,
    held,
    memory,
    named,
    old,
    own,  # fmt: skip
)
from body_columns import Refused, read, update, write
from body_tables import AUDIT, EARLY, FLAKES, LANES, MISSES, REQUESTS, SHADOW, TABLES, Table
from conftest import REPO_ROOT

NAMES = {t.name: t for t in TABLES}


@pytest.fixture(scope="module")
def whole() -> Iterator[sqlite3.Connection]:
    con = memory()
    yield con
    con.close()


@pytest.fixture
def con(whole) -> Iterator[sqlite3.Connection]:
    whole.execute("BEGIN")
    yield whole
    whole.execute("ROLLBACK")


def docs(con: sqlite3.Connection, t: Table) -> list[dict]:
    return [doc for _, doc in read(con, t, order="rowid")]


def rendered(con: sqlite3.Connection, t: Table) -> list[list[str]]:
    """What `goh state check` finds wrong with each row."""
    return [state_columns.differs(t.name, doc, again)
            for _, _, doc, again, _ in state_columns.rendered(con, t.name)]  # fmt: skip


R = READING
ODD: dict[str, list[dict]] = {
    "lane_starts": [
        {**LANE, "at": "2026-11-18T06:06:40-05:00"}, {**LANE, "at": "2026-11-18T12:00:00Z"},
        {**LANE, "at": 5.0}, {**LANE, "at": "soon"},
        {**LANE, "v": 2}, {**LANE, "v": None, "why": None}, {**LANE, "why": ["a", 2]},
        {**LANE, "mode": 3}, {**LANE, "later": {"a": [1]}}, {**LANE, "reading": "none"},
        {**LANE, "reading": None}, {**LANE, "reading": []},
        {**LANE, "reading": {**R, "v": True, "room": {**R["room"], "why": ["other"]}}},
        {**LANE, "reading": {**R, "cpu_weight": {"in_flight": 0, "per_core": 0.0},
                                  "load": None, "cpu": {"source": "unmeasured: x",
                                                           "idle_pct": None, "window_s": None}}},
        {**LANE, "reading": {**R, "load": {"avg": [1, 2.0], "cores": 1.5, "per_core": 1}}},
        {**LANE, "reading": {k: v for k, v in R.items() if k != "slots"}},
        {**LANE, "reading": {**R, "slots": {**R["slots"], "holders": [{**HOLDER, "by": 1}]}}},
        {**LANE, "reading": {**R, "slots": {**R["slots"], "holders": [{"repo": {}}, {}]}}},
        {**LANE, "reading": {**R, "slots": {**R["slots"], "holders": [7]}}},
        {**LANE, "reading": {**R, "presteps": {**R["presteps"], "live": [], "holders": []}}},
    ],
    "flakes": [
        {**FLAKE, "load": None}, {**FLAKE, "load": "high"}, {**FLAKE, "load": True},
        {**FLAKE, "at": "x"},
        {**FLAKE, "test": "t::other"}, {"test": "t::a", "error": "e", "epoch": AT},
        {**FLAKE, "log": None},
        {**FLAKE, "log": {"first": "/l", "third": 1}}, {"filed": 3, "epoch": AT},
        {**FLAKE, "site": "café →"},
    ],
    "selection_misses": [
        {**JUDGED, "changed": []}, {**JUDGED, "changed": ["a", 1]}, {**MISS, "missed": True},
        {**JUDGED, "missed": 1.5}, {"kind": "miss"}, {**MISS, "pre_ran": 1, "new": None},
    ],
    "reuse_shadow": [
        {**SAMPLE, "fields": {"node": "n", "new": "x"}}, {**SAMPLE, "fields": {}},
        {**SAMPLE, "repeated": "yes", "again": None}, {"path": "p", "epoch": 4, "fresh": 1},
        {**SAMPLE, "fields": "x"},
    ],
    "reuse_audit": [
        {**OFF, "evidence": None}, {**OFF, "evidence": {**SAMPLE, "new": 1}},
        {**OFF, "evidence": {**SAMPLE, "fields": {"new": "x"}}}, {**OFF, "evidence": "x"},
        {**OFF, "evidence": {**SAMPLE, "path": "other"}}, {**OFF, "closed": [{"uses": "x"}]},
        {**OFF, "closed": [{}]}, {**ON, "enabled": {"epoch": 1}}, {"path": "carry", "state": 1},
        {**OFF, "window": {}, "closed": None}, {**OFF, "evidence": {**SAMPLE, "again": {}}},
    ],
    "requests": [
        {**LAND, "slot": None}, {**LAND, "pre": None, "main_red": None},
        {**LAND, "main_red": {"sha": "m", "nodes": []}}, {**LAND, "pre_s": "soon"},
        {**LAND, "cov_risk": {"said": "not read"}}, {**LAND, "new": {"a": 1}},
        {**LAND, "cov_risk": {**LAND["cov_risk"], "files": {}, "carry": []}},
        {**LAND, "cov_risk": {**LAND["cov_risk"], "files": {"a": 1}, "carry": [{"x": 1}]}},
        {**LAND, "pre_run": {"wait_s": 1, "workers": 2, "remote": {"fresh": 1}}},
        {**MARK, "pre": {"number": 1, "born": [1, 2, 3]}}, {**MARK, "pre": {"born": [1.5, 2]}},
        {**MARK, "slot": {"token": 4}}, {**LAND, "pre_verdict": "x"}, {"item": "A.B"},
    ],
    "early": [{"item": "A.B", "pid": True, "until": 5, "mode": "land"}, {"sha": 1}],
}  # fmt: skip
SHAPES = [(t.name, d) for t in TABLES for d in [*DOCS[t.name], *ODD[t.name]]]


@pytest.mark.parametrize(("name", "doc"), SHAPES, ids=[f"{n}{i}" for i, (n, _) in
                                                       enumerate(SHAPES)])  # fmt: skip
def test_the_writer_and_an_older_copys_trigger_leave_the_same_row(con, name, doc) -> None:
    """Columns, child rows, `rest` and document; and both are read back as the document was."""
    t = NAMES[name]
    one, two = named(t, doc, 1), named(t, doc, 2)
    old(con, t, one, own(t, one, 1))
    assert write(con, t, two, own(t, two, 2))
    a, b = held(con, t)
    assert (json.loads(a.pop("body")), json.loads(b.pop("body"))) == (one, two)
    assert json.loads(json.dumps(a, default=str).replace('"p1"', '"p2"')) == json.loads(
        json.dumps(b, default=str)
    )
    assert rendered(con, t) == [[], []]
    for said, got in zip((one, two), docs(con, t), strict=True):
        assert state_columns.differs(name, json.dumps(said), json.dumps(got)) == []


@pytest.mark.parametrize("name", NAMES)
def test_every_key_of_a_document_has_a_column_and_is_read_back_as_it_was(con, name) -> None:
    """B of the data plan: `rest` empty, every column of the table filled by one or another."""
    t = NAMES[name]
    want = [named(t, doc, n) for n, doc in enumerate(DOCS[name], 1)]
    for n, doc in enumerate(want, 1):
        write(con, t, doc, own(t, doc, n))
    rows = held(con, t)
    assert [r["rest"] for r in rows] == [{}] * len(rows)
    filled = {k for r in rows for k, v in r.items() if v not in (None, [])}
    assert set(rows[0]) - filled == ({"run"} if name == "selection_misses" else set())
    got = docs(con, t)
    assert json.dumps(got, sort_keys=True) == json.dumps(want, sort_keys=True)  # byte for byte


def test_the_names_here_are_the_files_columns(con) -> None:
    """A column of 0016 no field has, or a field with no column, is a value nothing gives back."""
    before, kin = memory(15), db_bounds_with.withs(goh_db.SEAM)
    for t in TABLES:
        assert sorted(kin.get(t.name, [])) == sorted({k.table for k in t.kids}), t.name
        was = set(body_columns.columns(before, t.name))
        mine = [c for c, _ in t.fields.values()] + [c for x in t.tuples for c in x[1]]
        assert set(body_columns.columns(con, t.name)) - was == {*mine, "rest"}, t.name
        assert len(mine) == len(set(mine)), t.name
        for kid in t.kids:
            cells = {c for c, _ in kid.fields.values()} | {"at", *t.key}
            cells |= {"kind"} if kid.kind else set()
            cells |= {"text": {"ord", "value"}, "list": {"ord"}, "map": {"path", "said"},
                      "one": set()}[kid.shape]  # fmt: skip
            assert cells == set(body_columns.columns(con, kid.table)), kid.table
    before.close()


def test_every_field_a_reuse_path_names_and_every_key_a_request_gets_has_a_column() -> None:
    fields = {f"fields.{f}" for p in reuse.PATHS.values() for f in p.fields}
    assert fields <= set(SHADOW.fields)
    assert fields <= set(next(k for k in AUDIT.kids if k.kind == "now").fields)
    top = {p.split(".")[0] for p in [*REQUESTS.fields, *REQUESTS.have]}
    top |= {p.split(".")[0] for p in (k.path for k in REQUESTS.kids)}
    put = re.compile(r"\b(?:req|marker|m)\[\"(\w+)\"\]\s*=[^=]")
    found = set()
    for p in [*(REPO_ROOT / "lib").glob("land*.py"), REPO_ROOT / "lib" / "main_red.py",
              REPO_ROOT / "tools" / "landq.py"]:  # fmt: skip
        found |= set(put.findall(p.read_text(encoding="utf-8")))
    assert {"pre_run", "main_red", "cov_risk", "superseded"} <= found <= top


@pytest.mark.parametrize("new", [False, True], ids=["an older copy", "this copy"])
def test_a_request_written_again_leaves_nothing_of_the_first(con, new) -> None:
    for t in (REQUESTS, EARLY):
        for doc in (LAND, {**PUSH, "main_red": {"sha": "n", "nodes": ["t::c"]}, "x": 1}):
            if doc is LAND:
                write(con, t, doc, own(t, doc))
            elif new:
                mine = {k: v for k, v in own(t, doc).items() if k in ("item", "repo", "sha")}
                mine = mine if t is REQUESTS else {}
                assert update(con, t, doc, mine, "run LIKE ?", ("1795%",)) == 1
                assert update(con, t, doc, mine, "run = ?", ("none",)) == 0
            else:
                con.execute(f"UPDATE {t.name} SET body = ?", (json.dumps(doc),))
                if t is REQUESTS:
                    con.execute("UPDATE requests SET item = '', repo = '/r/goh', sha = 's1', "
                                "mode = 'ship'")  # fmt: skip
        (row,) = held(con, t)
        assert row["rest"] == {"x": 1} and row["slot"] is None and row["pre_s"] is None
        assert row["summary"] == "say" and row["main_red_sha"] == "n"
        assert row["request_nodes"] == [(0, "t::c", AT)] and row["request_cov_carry"] == []
        assert rendered(con, t) in ([[]], [["mode"]])
        con.execute(f"DELETE FROM {t.name}")
        assert con.execute("SELECT count(*) FROM request_nodes").fetchone() == (0,)


@pytest.mark.parametrize("new", [False, True], ids=["an older copy", "this copy"])
def test_a_reuse_record_written_again_leaves_nothing_of_the_first(con, new) -> None:
    def put(doc: dict) -> None:
        mine = {"path": "carry", "at": AT}
        if new and not update(con, AUDIT, doc, mine, "path = ?", ("carry",)):
            write(con, AUDIT, doc, mine)
        elif not new:
            con.execute("INSERT INTO reuse_audit(path, body, at) VALUES ('carry', ?, ?) ON CONFLICT"
                        "(path) DO UPDATE SET body=excluded.body, at=excluded.at",
                        (json.dumps(doc), AT))  # fmt: skip

    put(OFF)
    (row,) = held(con, AUDIT)
    assert len(row["reuse_windows"]) == 2 and [k[0] for k in row["reuse_evidence"]] == ["now"]
    put({**ON, "later": 1})
    (row,) = held(con, AUDIT)
    assert row["reuse_windows"] == [] and [k[0] for k in row["reuse_evidence"]] == ["was"]
    assert (row["state"], row["cause"], row["was_cause"]) == ("on", None, "disagree")
    assert row["rest"] == {"later": 1} and rendered(con, AUDIT) == [[]]
    put(STARVED)
    (row,) = held(con, AUDIT)
    assert row["reuse_evidence"] == [] and row["rest"] == {} and docs(con, AUDIT) == [STARVED]


def test_evidence_outlives_its_shadow(con) -> None:
    write(con, SHADOW, {**SAMPLE, "outcome": "disagreed"}, own(SHADOW, SAMPLE))
    write(con, AUDIT, OFF, own(AUDIT, OFF))
    con.execute("DELETE FROM reuse_shadow")
    assert docs(con, AUDIT) == [OFF] and docs(con, SHADOW) == []


@pytest.mark.parametrize("name", NAMES)
def test_the_contracts_file_is_written_and_read_whole(con, name) -> None:
    t, want = NAMES[name], DOCS[name]
    for n, doc in enumerate(want, 1):
        write(con, t, named(t, doc, n), own(t, named(t, doc, n), n))
    before = docs(con, t)
    contract(con, t)
    assert docs(con, t) == before
    late = [named(t, doc, n) for n, doc in enumerate(want, 11)]
    for n, doc in enumerate(late, 11):
        write(con, t, doc, own(t, doc, n))
    assert docs(con, t)[len(want) :] == late
    if t in (REQUESTS, EARLY):
        assert update(con, t, MARK, {}, "run LIKE ?", ("%-11",)) == 1
        assert con.execute("SELECT count(*) FROM request_nodes WHERE run LIKE '%-11'"
                           ).fetchone() == (0,)  # fmt: skip
    with pytest.raises(Refused, match="not a type its column takes"):
        wrong = {"lane_starts": "mode", "flakes": "site", "selection_misses": "kind",
                 "reuse_shadow": "key"}.get(name, "state" if t is AUDIT else "lane")  # fmt: skip
        write(con, t, named(t, {**want[0], wrong: 9}, 99), own(t, named(t, want[0], 99), 99))
    last = named(t, {**want[0], "unknown": 9}, 100)
    assert write(con, t, last, own(t, last, 100)) and docs(con, t)[-1]["unknown"] == 9


def test_a_row_not_filled_yet_and_a_file_from_before_the_columns_read_their_documents(con) -> None:
    before = memory(15)
    for t in TABLES:
        want = [named(t, doc, n) for n, doc in enumerate(DOCS[t.name], 1)]
        for n, doc in enumerate(want, 1):
            old(before, t, doc, own(t, doc, n))
            old(con, t, doc, own(t, doc, n))
        con.execute(f"UPDATE {t.name} SET rest = NULL")
        assert docs(before, t) == docs(con, t) == want
        doc = DOCS[t.name][0]
        assert write(before, t, doc, own(t, doc, 9)) and docs(before, t)[-1] == doc  # no 0016 yet
        con.execute(f"DELETE FROM {t.name}")
        assert read(con, t, order="rowid") == []
    con.execute("INSERT INTO flakes(epoch, test, body) VALUES (1, 't', 'torn'), (1, 't', '[]')")
    assert docs(con, FLAKES) == [] and not before.in_transaction
    before.close()


def test_a_read_is_one_snapshot_of_its_own_and_takes_only_what_is_asked(con) -> None:
    for n, doc in enumerate((LAND, MARK), 1):
        write(con, REQUESTS, doc, own(REQUESTS, doc, n))
    ((mine, doc),) = read(con, REQUESTS, "run LIKE ? AND state = ?", ("%-1", "pending"), "run")
    assert (mine["state"], mine["filed_at"], doc) == ("pending", AT, LAND)
    assert read(con, REQUESTS, "state = 'running'", order="run") == []


def test_a_miss_is_written_once_and_the_second_adds_no_child_row(con) -> None:
    mine = own(MISSES, JUDGED)
    assert [write(con, MISSES, JUDGED, mine, ignore=True) for _ in range(2)] == [True, False]
    assert con.execute("SELECT n, value FROM miss_files").fetchall() == [(1, "a.py"), (1, "b.py")]
    con.execute("DELETE FROM selection_misses")
    assert con.execute("SELECT count(*) FROM miss_files").fetchone() == (0,)


def test_a_request_childs_parent_is_either_of_its_two_tables(con) -> None:
    write(con, EARLY, LAND, own(EARLY, LAND, 1))
    write(con, REQUESTS, LAND, own(REQUESTS, LAND, 2))
    assert state_columns.orphans(con) == []
    con.execute("INSERT INTO request_nodes VALUES ('/q', 'gone', 0, 't', 1.0)")
    assert state_columns.orphans(con) == [
        "request_nodes: 1 of its rows have no requests or early row"
    ]


def test_text_no_column_holds_is_replaced(con) -> None:
    write(con, FLAKES, {**FLAKE, "site": "cut \ud83d"}, own(FLAKES, FLAKE))
    assert docs(con, FLAKES)[0]["site"] == "cut ?" and rendered(con, FLAKES) == [[]]


def test_a_lane_start_is_read_whole_without_its_constants_kept_twice(con) -> None:
    write(con, LANES, LANE, own(LANES, LANE))
    assert con.execute("SELECT kind, value FROM lane_why").fetchall() == [("why", "full")]
    (got,) = docs(con, LANES)
    assert got == LANE and got["reading"]["room"]["why"] is not got["why"]
