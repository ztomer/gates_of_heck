"""GOH.KVBLOBS: a fault candidate's fields as columns and child rows (lib/candidate_columns.py),
written as 0015's triggers write an older copy's, read back the same on every shape of the file."""

from __future__ import annotations

import json
import sqlite3

import candidate_columns as cc
import goh_db
import pytest
import state_backfill
import state_columns
from _kv_fixture import memory

FULL = {"id": "FAULTS.x", "item": "triage", "phase": "FX", "repo": "?", "status": "todo",
        "done_state": "each", "depends": ["A.B"], "faults": ["one", "two"]}  # fmt: skip
ODD = [
    {"id": 7, "item": {"a": 1}, "faults": {"a": 1}, "depends": ["x", 1], "new": True},
    {"id": None, "faults": [], "depends": None, "status": ""},
    {"faults": ["a"], "depends": []},
    {},
]
DOCS = [FULL, *ODD]


def old(con: sqlite3.Connection, doc: dict, at: float = 2.0) -> int:
    """As code from before 0015: the document alone."""
    sql = "INSERT INTO fault_candidates(at, body) VALUES (?, ?)"
    return con.execute(sql, (at, json.dumps(doc, sort_keys=True))).lastrowid


def held(con: sqlite3.Connection, n: int) -> tuple:
    cols = f"at, rest, body, {', '.join(cc.TEXT)}"
    row = con.execute(f"SELECT {cols} FROM fault_candidates WHERE n = ?", (n,)).fetchone()
    kids = con.execute("SELECT kind, ord, value, at FROM fault_candidate_lists WHERE n = ? "
                       "ORDER BY 1, 2", (n,)).fetchall()  # fmt: skip
    return (row[0], json.loads(row[1]), *row[2:], kids)


def bare(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if v not in (None, [], {})}


@pytest.mark.parametrize("doc", DOCS)
def test_the_writer_and_the_trigger_leave_the_same_row(doc) -> None:
    con = memory(goh_db)
    a, b = cc.write(con, doc, 2.0), old(con, doc)
    ours, theirs = held(con, a), held(con, b)
    assert (ours[0], bare(theirs[1])) == (theirs[0], ours[1]) and ours[2:] == theirs[2:]
    assert cc.read(con) == [bare(doc)] * 2


def test_every_shape_of_the_file_reads_the_same() -> None:
    before = memory(goh_db, 14)
    for d in DOCS:
        cc.write(before, d, 2.0)
    want = [bare(d) for d in DOCS]
    assert cc.read(before) == want and cc.read(before, "n = ?", (1,)) == want[:1]
    con = memory(goh_db)
    con.execute("DROP TRIGGER fault_candidates_fill")
    for d in DOCS:
        old(con, d)
    assert cc.read(con) == want  # not filled
    con = memory(goh_db)
    for d in DOCS:
        cc.write(con, d, 2.0)
    con.execute("UPDATE fault_candidates SET body = '{}'")  # stripped
    assert cc.read(con) == want
    for t in ("fill", "refill", "bad"):
        con.execute(f"DROP TRIGGER fault_candidates_{t}")
    con.execute("ALTER TABLE fault_candidates DROP COLUMN body")
    n = cc.write(con, FULL, 3.0)
    assert cc.read(con) == [*want, FULL] and cc.read(con, "n = ?", (n,)) == [FULL]


def test_a_shrinking_candidate_leaves_no_old_row_and_a_gone_one_none() -> None:
    con = memory(goh_db)
    n = old(con, FULL)
    con.execute("INSERT OR REPLACE INTO fault_candidates(n, at, body) VALUES (?, 2.0, ?)",
                (n, json.dumps({**FULL, "faults": ["one"], "depends": []})))  # fmt: skip
    kids = "SELECT value FROM fault_candidate_lists"
    assert con.execute(kids).fetchall() == [("one",)]
    con.execute("DELETE FROM fault_candidates")
    assert con.execute(kids).fetchall() == []


def test_the_backfill_fills_and_the_document_is_rendered_again() -> None:
    con = memory(goh_db)
    con.execute("DROP TRIGGER fault_candidates_fill")
    sql = "SELECT sql FROM sqlite_master WHERE name='fault_candidates_fill'"
    made = memory(goh_db).execute(sql).fetchone()[0]
    for d in DOCS:
        old(con, d)
    con.execute(made)
    sql = dict(state_backfill.steps())["fault_candidates"]
    assert con.execute(sql).rowcount == len(DOCS) and con.execute(sql).rowcount == 0
    got = list(state_columns.rendered(con, "fault_candidates"))
    assert len(got) == len(DOCS) - 1  # {} is no document
    assert all(state_columns.same("fault_candidates", d, again) for _, _, d, again, _ in got)


def test_text_no_column_holds_is_written_as_one_that_does() -> None:
    con = memory(goh_db)
    cc.write(con, {"id": "a\udc80", "faults": ["b\udc80"]}, 1.0)
    assert cc.read(con) == [{"id": "a?", "faults": ["b?"]}]
