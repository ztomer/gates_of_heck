"""GOH.KVBLOBS: goh.db's kv documents as rows (lib/kv_rows.py, schema file 0015), through the stores
that write and read them; an older file's documents filled into them, and 0021 that drops those."""

from __future__ import annotations

import json
import sqlite3

import db_bounds
import db_safe
import db_schema
import durations
import goh_db
import hook_ledgers
import kv_rows
import pytest
import small_stores
import state_columns
from _body_fixture import UNCUT
from _contract_fixture import contracts
from _kv_fixture import Held, duration_kv, kv, memory

PUSH = {"schema": 1, "repos": {
    "/p/b": {"at": "2026-11-23T04:55:42+00:00", "said": "not pushed", "status": "committed",
             "sweep": "/s/a.sweep.json", "where": "b"},
    "/p/a": {"at": "2026-11-23T04:55:43+00:00", "said": "red", "status": "refused",
             "sweep": "/s/a.sweep.json", "where": "a", "log": "/s/a.log"}}}  # fmt: skip
SWEEPS = {"schema": 1, "sweeps": [{"path": "/s/z", "repos": ["/p/a", "/p/b"]},
                                  {"path": "/s/a", "repos": ["/p/b"]}]}  # fmt: skip
PINS = {"schema": 2, "tag": "v1", "ruff": "0.17", "sweep": None,
        "repos": {"a": {"said": "pinned", "status": "committed"}}}  # fmt: skip
DOCS = {"pin_push": PUSH, "pin_sweeps": SWEEPS, "wf_pin.pins": PINS}
COUNT = {"pin_push": 2, "pin_sweeps": 3, "wf_pin.pins": 1}


@pytest.fixture
def held(monkeypatch) -> Held:
    return Held().lay(monkeypatch)


def rows(held: Held, name: str) -> list[tuple]:
    return held.goh.execute(f"SELECT * FROM {kv_rows.STORES[name].table} ORDER BY 1, 2").fetchall()


@pytest.mark.parametrize("name", DOCS)
def test_a_document_is_rows_read_back_as_it_was(held, name) -> None:
    p = f"/st/{small_stores.KV_FILES[name]}"
    small_stores.put(p, DOCS[name])
    assert len(rows(held, name)) == COUNT[name]
    assert small_stores.get(p) == DOCS[name]
    assert held.goh.execute("SELECT name FROM kv").fetchall() == []
    with pytest.raises(sqlite3.IntegrityError):
        kv(held.goh, name, "{}")  # an older copy's write


@pytest.mark.parametrize("odd", [
    {"schema": 1, "repos": {"/p/a": {**PUSH["repos"]["/p/a"], "new": 1}}},  # no column has it
    {"schema": 1, "repos": {"/p/a": {"at": "2026-11-23T04:55:43Z", "status": "x"}}},  # spelling
    {"schema": 1, "repos": {"/p/a": {"at": None, "status": "x"}}},
    {"schema": True, "repos": PUSH["repos"]},
    {"schema": 1, "repos": {"/p/a": "ok"}}, {"was": None}, [1], None,
])  # fmt: skip
def test_what_no_column_holds_is_refused_and_the_rows_stand(held, odd) -> None:
    small_stores.put("/st/pin_push.json", PUSH)
    was = rows(held, "pin_push")
    with pytest.raises(kv_rows.Unfit, match="pin_push"):
        small_stores.put("/st/pin_push.json", odd)
    assert small_stores.get("/st/pin_push.json") == PUSH and rows(held, "pin_push") == was


def test_the_sweeps_keep_their_order_and_a_repo_in_two_of_them(held) -> None:
    small_stores.put("/st/pin_sweeps.json", SWEEPS)
    got = held.goh.execute("SELECT sweep, ord, repo FROM pin_sweep_repos ORDER BY at, ord")
    assert got.fetchall() == [("/s/z", 0, "/p/a"), ("/s/z", 1, "/p/b"), ("/s/a", 0, "/p/b")]


def test_the_unsafe_ledger_joins_two_writers(held) -> None:
    durations.update("linux_unsafe", lambda was: {**(was or {}), "m1": "a"})
    durations.update("linux_unsafe", lambda was: {**was, "m2": "c"})
    want = {"m1": "a", "m2": "c"}
    assert durations.get("linux_unsafe") == want
    assert dict(held.goh.execute("SELECT module, why FROM linux_unsafe")) == want
    at = held.goh.execute("SELECT at FROM linux_unsafe WHERE module='m2'").fetchone()
    durations.update("linux_unsafe", lambda was: was)  # the same again is the same row
    assert held.goh.execute("SELECT at FROM linux_unsafe WHERE module='m2'").fetchone() == at


def test_the_first_time_an_item_was_seen_done_stands(held) -> None:
    hook_ledgers.record_watch({"ran_at": "1", "spend": {"first_seen_done": {"A.B": "t1"}}}, None)
    spend = {"first_seen_done": {"A.B": "t2", "C.D": "t2"}, "x": 1}
    hook_ledgers.record_watch({"ran_at": "2", "spend": spend}, None)
    spend["first_seen_done"]["A.B"] = "t1"
    assert hook_ledgers.watch() == {"ran_at": "2", "spend": spend}
    sql = "UPDATE spend_first_done SET first_done = 't9'"
    held.goh.execute(sql)  # an older copy saw both before a newer one did
    assert hook_ledgers.watch()["spend"]["first_seen_done"] == {"A.B": "t2", "C.D": "t2"}
    hook_ledgers.record_watch({"spend": {"first_seen_done": {"A.B": "t1", "C.D": "t2"}}}, None)
    hook_ledgers.record_watch({"ran_at": "3"}, None)  # a run with no SPEND section
    assert hook_ledgers.watch()["spend"] == {"first_seen_done": {"A.B": "t1", "C.D": "t2"}}


def test_a_document_and_its_rows_are_read_in_one_snapshot(held, monkeypatch) -> None:
    """A writer between the two reads would leave the mark of one and the rows of another."""
    seen = []
    was = kv_rows.first_done
    monkeypatch.setattr(kv_rows, "first_done", lambda con, *a: (
        seen.append(con.in_transaction), was(con, *a))[1])  # fmt: skip
    hook_ledgers.watch()
    assert seen == [True]


def _lay(con: sqlite3.Connection) -> None:
    for name, doc in DOCS.items():
        kv(con, name, json.dumps(doc), 5.0)
    kv(con, "model-choice-watch", json.dumps({"spend": {"first_seen_done": {"A.B": "t"}}}), 6.0)
    duration_kv(con, "linux_unsafe", json.dumps({"m": "why"}), 7.0)


def test_the_fill_is_every_entry_a_row_and_a_second_run_changes_nothing() -> None:
    con = memory(goh_db, UNCUT)
    _lay(con)
    assert len(kv_rows.left(con)) == 5
    n = {"pin_pushes": 2, "pin_sweep_repos": 3, "wf_pins": 1, "linux_unsafe": 1,
         "spend_first_done": 1}  # fmt: skip
    assert kv_rows.fill(con) == n and kv_rows.left(con) == []
    once = "\n".join(con.iterdump())
    assert kv_rows.fill(con) == n and "\n".join(con.iterdump()) == once
    assert con.execute("SELECT at FROM linux_unsafe").fetchone() == (7.0,)
    assert con.execute("SELECT at FROM wf_pins").fetchone() == (5.0,)
    assert con.execute("SELECT at FROM spend_first_done").fetchone() == (6.0,)
    assert con.execute("SELECT DISTINCT at FROM kv ORDER BY 1").fetchall() == [(5.0,), (6.0,)]


def test_a_fill_killed_at_each_statement_and_run_again_is_one_clean_run() -> None:
    clean = memory(goh_db, UNCUT)
    _lay(clean)
    kv_rows.fill(clean)
    want = "\n".join(clean.iterdump())

    class Killed(Exception):
        pass

    class Con:
        """A connection that dies before its Nth write."""

        def __init__(self, con: sqlite3.Connection, n: int) -> None:
            self.con, self.n = con, n

        def _one(self, sql: str) -> None:
            if not sql.startswith("SELECT"):
                self.n -= 1
                if self.n < 0:
                    raise Killed

        def execute(self, sql, *a):
            self._one(sql)
            return self.con.execute(sql, *a)

        def executemany(self, sql, *a):
            self._one(sql)
            return self.con.executemany(sql, *a)

    con, n = memory(goh_db, UNCUT), 0
    _lay(con)
    while True:
        con.execute("BEGIN")
        try:
            kv_rows.fill(Con(con, n))
        except Killed:
            con.execute("ROLLBACK")  # what a killed process's transaction is
            n += 1
            continue
        con.execute("COMMIT")
        break
    assert n > 12 and "\n".join(con.iterdump()) == want


def test_0021_refuses_a_document_its_rows_do_not_hold_and_drops_the_rest() -> None:
    con = memory(goh_db, UNCUT)
    _lay(con)
    kv(con, "estate_audit", "{}")
    with pytest.raises(sqlite3.IntegrityError):
        contracts(con, 21)
    assert con.execute("SELECT count(*) FROM kv").fetchone() == (5,)
    kv_rows.fill(con)
    want = {name: kv_rows.got(con, name) for name in DOCS}
    contracts(con, 21)
    assert [n for (n,) in con.execute("SELECT name FROM kv ORDER BY 1")] == [
        "estate_audit", "model-choice-watch"]  # fmt: skip
    assert con.execute("SELECT count(*) FROM duration_kv").fetchone() == (0,)
    assert {name: kv_rows.got(con, name) for name in DOCS} == want == DOCS
    with pytest.raises(sqlite3.IntegrityError, match="its rows hold it"):
        kv(con, "pin_push", "{}")
    with pytest.raises(sqlite3.IntegrityError, match="its rows hold it"):
        duration_kv(con, "linux_unsafe", "{}")


def test_goh_db_0015_is_safe_bounded_and_taken_by_a_file_that_has_0016() -> None:
    file = db_schema.migrations(goh_db.SEAM)[15].read_text(encoding="utf-8")
    assert db_schema.old_writers(file) == "safe" and db_safe.faults(goh_db.SEAM, 15) == []
    con = memory(goh_db, UNCUT)
    new = {"pin_pushes", "pin_sweep_repos", "wf_pins", "linux_unsafe", "spend_first_done",
           "fault_candidate_lists"}  # fmt: skip
    assert new - {"fault_candidate_lists"} <= set(db_bounds.bounds(goh_db.SEAM))
    assert db_bounds.unbounded(goh_db.SEAM) == []
    late = memory(goh_db, 14)  # 0016 first, then 0015
    files = db_schema.migrations(goh_db.SEAM)
    db_schema.apply(late, 16, files[16], "", True)
    have = {r[0] for r in late.execute("SELECT version FROM schema_version")}
    assert db_schema.on_open(files, have)[0] == 15
    db_schema.apply(late, 15, files[15], "", True)
    made = "SELECT type, name, sql FROM sqlite_master ORDER BY 1, 2"
    assert late.execute(made).fetchall() == con.execute(made).fetchall()
    for t in new:
        assert state_columns.columns(late, t) == state_columns.columns(con, t) != []
