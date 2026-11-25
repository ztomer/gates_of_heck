"""goh.db's typed columns (GOH.LANDCOLS, schema files 0011 to 0014, goh-cache 0004): what a writer
that knew only `body` left in an uncut file, and what `goh state check` names of either
(lib/state_columns.py)."""

from __future__ import annotations

import contextlib
import json
import re
import sqlite3
from collections.abc import Iterator

import _body_fixture as bf
import db_bounds
import enqueue_witness
import goh_cache_db
import goh_db
import hook_ledgers
import land_columns
import landq_inflight
import pytest
import ship_columns
import state_columns
from _columns_fixture import AT, LAND, OUTCOME, PINS, RESULT, SHIP, SPAWN

RUN = "01795000000000000000-A.B"


class File:
    """The one connection behind this test's `goh_db` doors, an uncut file in memory."""

    con: sqlite3.Connection


@contextlib.contextmanager
def held(*_a, **_k):
    yield File.con


@pytest.fixture
def db(monkeypatch) -> Iterator[sqlite3.Connection]:
    File.con = bf.memory(bf.UNCUT)
    monkeypatch.setattr(hook_ledgers, "dead", list)
    yield File.con
    File.con.close()


def q(sql: str, *args: object) -> list[tuple]:
    return File.con.execute(sql, args).fetchall()


def spawn(row: dict) -> int:
    """As code of schema 10 writes it: the document alone, its columns the trigger's."""
    with held() as con:
        con.execute("INSERT INTO agent_spawns(at, event, agent, body) VALUES (?, ?, ?, ?)",
                    (AT, row.get("event") or "", row.get("agent_id"), json.dumps(row)))  # fmt: skip
        return con.execute("SELECT max(n) FROM agent_spawns").fetchone()[0]


def put(table: str, doc: dict) -> None:
    """DOC as a writer of TABLE that knew its old columns and `body` alone."""
    if table == "lands":
        land = {"at": LAND["at"], **doc}
        mine = {k: doc[k] for k in land_columns.HAVE if doc.get(k) is not None}
        replace("lands", queue="/q", run=RUN, at=AT, body=land, **mine)
    elif table == "ships":
        mine = {k: doc[k] for k in ship_columns.HAVE if doc.get(k) is not None}
        key = doc.get("repo", "").rpartition("/")[2]
        replace("ships", result=doc["result"], run="r", repo=key, at=AT, body=doc, **mine)
    elif table == "ship_pins":
        replace("ship_pins", result=RESULT, run="r", at=AT, body=doc)
    else:
        spawn(doc)


def replace(table: str, **row: object) -> None:
    row = {k: json.dumps(v) if isinstance(v, dict) else v for k, v in row.items()}
    File.con.execute(f"INSERT OR REPLACE INTO {table}({', '.join(row)}) VALUES "
                     f"({', '.join('?' * len(row))})", list(row.values()))  # fmt: skip


WHOLE = [("lands", LAND), ("ship_pins", PINS), ("ships", SHIP), ("agent_spawns", SPAWN),
         ("agent_spawns", OUTCOME)]  # fmt: skip


def found() -> list[str]:
    with held() as con:
        return state_columns.check(con)


@pytest.mark.parametrize(("table", "doc"), WHOLE, ids=["land", "pins", "ship", "spawn", "outcome"])
def test_every_key_of_a_document_has_a_column(db, table, doc) -> None:
    if table == "ships":
        put("ship_pins", PINS)  # consumer_pins is read back from them
    put(table, doc)
    assert q(f"SELECT rest FROM {table}") == [("{}",)]
    assert [x for x in found() if "ship_pin_repos" not in x and "ship_pins: 1" not in x
            and not x.startswith("ship_pin")] == []  # fmt: skip
    with held() as con:
        ((_, _, body, again, _),) = state_columns.rendered(con, table)
        for key in json.loads(body):  # each one changes the rendering when it goes
            less = {k: v for k, v in json.loads(body).items() if k != key}
            empty = key == "schema" or json.loads(body)[key] in (None, [], {})
            assert empty or state_columns.differs(table, json.dumps(less), again) == [key]


def test_a_second_write_leaves_nothing_of_the_first(db) -> None:
    """REPLACE fires no DELETE trigger: the fill empties the child tables itself."""
    put("ship_pins", PINS)
    put("ships", SHIP)
    put("lands", LAND)
    put("lands", {"state": "landed", "item": "A.B", "guards": ["g9"], "why": "w"})
    put("ships", {"result": RESULT, "status": "ok", "repo": "/r/goh", "tail": ["z"]})
    put("ship_pins", {"schema": 2, "repos": {"r9": {"status": "pushed", "said": "s"}}})
    assert q("SELECT kind, value FROM land_lists") == [("guard", "g9")]
    assert q("SELECT why, pre_s, red_class, rest FROM lands") == [("w", None, None, "{}")]
    assert q("SELECT kind, value FROM ship_lists") == [("tail", "z")]
    assert q("SELECT repo FROM ship_pin_repos") == [("r9",)]
    for t in (
        "land_flakes",
        "land_waived",
        "land_waived_items",
        "land_cov_carry",
        "ship_steps",
        "ship_bisect",
        "ship_grace",
        "ship_diffcov_lines",
        "ship_diffcov_faults",
    ):
        assert q(f"SELECT count(*) FROM {t}") == [(0,)], t


@pytest.mark.parametrize("table", ["lands", "ships", "agent_spawns"])
def test_an_update_of_the_body_fills(db, table) -> None:
    """A row nothing filled yet, and one whose writer empties `rest` (the fold's form): both are
    filled by the UPDATE. A bare UPDATE of a filled row's document, which no writer makes, is
    named: its columns no longer give the document back."""
    put("ship_pins", PINS)  # consumer_pins is read back from them
    put(table, {"lands": LAND, "ships": SHIP, "agent_spawns": SPAWN}[table])
    key = {"lands": "why", "ships": "summary", "agent_spawns": "report"}[table]
    for sets in ("rest = NULL, ", ""):
        with held() as con:
            con.execute(f"UPDATE {table} SET {sets}body = json_set(body, '$.{key}', 'new')")
            con.execute(f"UPDATE {table} SET rest = NULL")  # a row from before the columns
            con.execute(f"UPDATE {table} SET body = json_set(body, '$.{key}', 'newer')")
        assert q(f"SELECT {key}, rest FROM {table}") == [("newer", "{}")]
    with held() as con:
        con.execute(f"UPDATE {table} SET body = json_set(body, '$.{key}', 'newest')")
    assert any(f"{table}: " in x and f"do not give back {key}" in x for x in found()), found()


ODD = [  # a document's value, the column or child it must not reach, what `rest` keeps
    ("lands", {"slot_s": "soon"}, "SELECT slot_s FROM lands", {"slot_s": "soon"}),
    ("lands", {"batch": {"id": 7, "new": 1}}, "SELECT batch FROM lands",
     {"batch": {"id": 7, "new": 1}}),
    ("lands", {"guard": "g", "guards": ["g", 2]}, "SELECT value FROM land_lists",
     {"guard": "g", "guards": ["g", 2]}),
    ("lands", {"flakes": [{"test": "t", "later": 1}]}, "SELECT test FROM land_flakes",
     {"flakes": [{"test": "t", "later": 1}]}),
    ("lands", {"waived": [{"node": "n", "items": [1]}]}, "SELECT node FROM land_waived",
     {"waived": [{"node": "n", "items": [1]}]}),
    ("lands", {"cov_risk": {"files": {"-": 3}}}, "SELECT said FROM land_cov_files",
     {"cov_risk": {"files": {"-": 3}}}),
    ("lands", {"recorded": "2026"}, "SELECT recorded FROM lands", {"recorded": "2026"}),
    ("lands", {"nobody": {"a": [1]}}, "", {"nobody": {"a": [1]}}),
    ("ships", {"retried": 1}, "SELECT kind FROM ship_steps", {"retried": 1}),
    ("ships", {"pid": "7"}, "SELECT pid FROM ships", {"pid": "7"}),
    ("agent_spawns", {"load": [1.0, 2.0]}, "SELECT load1 FROM agent_spawns", {"load": [1.0, 2.0]}),
    ("agent_spawns", {"apps": {"exits": {"a": {"gone_by": 9}}}}, "SELECT app FROM spawn_app_exits",
     {"apps": {"exits": {"a": {"gone_by": 9}}}}),
    ("agent_spawns", {"effort": 9}, "SELECT effort FROM agent_spawns", {"effort": 9}),
]  # fmt: skip


@pytest.mark.parametrize(("table", "odd", "where", "rest"), ODD,
                         ids=[f"{t}-{next(iter(o))}" for t, o, _, _ in ODD])  # fmt: skip
def test_a_value_of_the_wrong_type_stays_in_rest(db, table, odd, where, rest) -> None:
    """No write is refused and nothing is dropped: the document is given back whole, and the row is
    named until somebody looks at it."""
    base = {
        "lands": {"at": LAND["at"]},
        "ships": {"result": RESULT, "status": "ok", "repo": "goh"},
        "agent_spawns": {"event": "spawn", "time": SPAWN["time"]},
    }[table]
    put(table, {**base, **odd})
    assert json.loads(q(f"SELECT rest FROM {table}")[0][0]) == rest
    assert {v for (v,) in q(where)} <= {None} if where else True
    (line,) = found()
    assert line.startswith(f"{table}: ") and "`rest` holds" in line


def test_a_null_an_empty_list_and_an_offset_leave_nothing(db) -> None:
    put("lands", {"at": "2026-11-18T06:06:40-05:00", "flakes": [], "batch": None,
                            "pre_run": {}, "cov_risk": {"carry": []}, "why": None,
                            "waived": [{"node": "n", "items": None}]})  # fmt: skip
    assert q("SELECT rest, at FROM lands") == [("{}", AT)] and found() == []
    with held() as con:
        ((_, _, body, again, _),) = state_columns.rendered(con, "lands")
    other = body.replace("06:06:40", "06:06:41")  # another instant is no spelling
    assert state_columns.differs("lands", other, again) == ["at"]


def test_a_document_that_is_no_json_is_kept_and_named(db) -> None:
    put("agent_spawns", SPAWN)
    for body in ("{torn", '{"a": NaN}'):
        with held() as con:
            con.execute("UPDATE agent_spawns SET body = ?", (body,))
            con.execute("INSERT INTO agent_spawns(at, event, body) VALUES (1, 'x', ?)", (body,))
    assert q("SELECT count(*) FROM agent_spawns WHERE rest IS NULL") == [(3,)]
    assert found() == [f"agent_spawns: {n}: its document is not JSON" for n in (1, 2, 3)]


@pytest.mark.parametrize(("parent", "n"), [("lands", 8), ("ships", 6), ("ship_pins", 1),
                                           ("agent_spawns", 2)])  # fmt: skip
def test_a_child_row_never_outlives_its_parent(db, parent, n) -> None:
    for t, doc in WHOLE:
        put(t, doc)
    with held() as con:
        kids = {t for t in db_bounds.tables(con) if t.startswith(f"{parent.rstrip('s')}_".replace(
            "agent_spawn_", "spawn_")) and t not in ("ship_pins", "land_guards")}  # fmt: skip
        kids = {t for t in kids if con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]}
        kids -= {"ship_pin_repos"} if parent == "ships" else set()
        assert len(kids) >= n, kids
        con.execute(f"DELETE FROM {parent}")
        assert [t for t in kids if con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]] == []


def test_an_orphan_is_counted(db) -> None:
    put("lands", {"at": LAND["at"], "guard": "g", "guards": ["g"]})
    with held() as con:
        con.execute("INSERT INTO land_lists VALUES ('/gone', 'r', 'guard', 0, 'g', 1)")
    assert found() == ["land_lists: 1 of its rows have no lands row"]


def test_a_ship_of_no_status_is_compared(db) -> None:
    put("ships", {"result": RESULT, "odd": 1})
    with held() as con:
        con.execute("UPDATE ships SET sha = 'another'")
    assert [x.split(": ")[2] for x in found()] == [
        '`rest` holds {"odd":1}', "its columns do not give back repo, sha"]  # fmt: skip


def test_a_running_ship_is_not_compared(db) -> None:
    put("ship_pins", PINS)  # the install step wrote them; the record carries them later
    put("ships", {"result": RESULT, "status": "running", "repo": "/r/goh"})
    assert found() == []
    put("ships", {"result": RESULT, "status": "ok", "repo": "/r/goh"})
    assert found() == [f"ships: {RESULT}: its columns do not give back consumer_pins"]


def test_a_stripped_row_reads_whole_through_the_views(db) -> None:
    """lands_v1 and ships_v2 render the document of a row that has none (K2), and hand back the
    stored text while there is one."""
    put("ship_pins", PINS)
    put("lands", LAND)
    put("ships", SHIP)
    for table, view in (("lands", "lands_v1"), ("ships", "ships_v2")):
        ((body,),) = q(f"SELECT body FROM {table}")
        assert q(f"SELECT body FROM {view}") == [(body,)]
        with held() as con:
            con.execute(f"UPDATE {table} SET body = '{{}}'")
        ((again,),) = q(f"SELECT body FROM {view}")
        assert again != body and state_columns.same(table, body, again)


def test_a_true_is_no_one() -> None:
    assert not state_columns.same("lands", '{"early": true}', '{"early": 1}')
    assert state_columns.same("lands", '{"k": 1}', '{"k": 1.0, "why": null, "f": [], "b": {}}')
    assert state_columns.differs("ships", '{"repo": "/r/a/b"}', '{"repo": "a_b"}') == []
    for other in ("c", "", "xa_b"):
        again = json.dumps({"repo": other})
        assert state_columns.differs("ships", '{"repo": "/r/a/b"}', again) == ["repo"]
    utc = ('{"at": "2026-11-18T11:06:40"}', '{"at": "2026-11-18T11:06:40Z"}')
    assert state_columns.same("lands", *utc)  # no offset is UTC, as SQLite reads it
    assert state_columns.differs("lands", "[1]", "[2]") == ["the whole document"]


def test_the_check_of_no_file_and_of_one_with_no_columns(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("GOH_DB", str(tmp_path / "goh.db"))
    assert state_columns.main() == 2 and "unknown, not none" in capsys.readouterr().err
    monkeypatch.setattr(goh_db, "ro", held)
    monkeypatch.setattr(goh_db, "known", held)
    File.con = bf.memory(10)
    (line,) = state_columns.check(File.con)
    assert "no columns" in line
    File.con = bf.memory(bf.UNCUT)
    File.con.execute("DROP TRIGGER lands_fill")
    replace("lands", queue="/q", run="1", at=AT, body=LAND)
    replace("red_causes", queue="/q", run="1", at=AT, cause="x", body={"cause": "x"})
    assert state_columns.main() == 1
    assert "lands: 1 rows not filled" in capsys.readouterr().err


def test_goh_cache_0004_is_safe_and_every_table_of_it_bounded(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GOH_CACHE_DB", str(tmp_path / "goh-cache.db"))
    with goh_cache_db.connect() as con:
        new = {"steps", "remote_secs", "evict_objs", "canary_memo", "verified_shas"}
        assert new <= set(db_bounds.tables(con))
    assert new <= set(db_bounds.bounds(goh_cache_db.SEAM))
    assert db_bounds.unbounded(goh_cache_db.SEAM) == []


def test_an_instant_is_written_with_a_colon(tmp_path, monkeypatch) -> None:
    """R5: `Z` or `+HH:MM`. The two writers that wrote `-0500`, which SQLite reads as no time."""
    monkeypatch.setenv("GOH_DB", str(tmp_path / "goh.db"))
    monkeypatch.setenv("TZ", "EST5")
    import time

    time.tzset()
    try:
        landq_inflight.died(tmp_path, {"item": "A.B", "pid": 999999, "repo": "/r"})
        said: list[str] = []
        monkeypatch.setattr(enqueue_witness, "_say", said.append)
        monkeypatch.setattr(enqueue_witness, "table", str)
        monkeypatch.setattr(enqueue_witness.os, "kill", lambda *_: None)
        monkeypatch.setattr(enqueue_witness.signal, "signal", lambda *_: None)
        enqueue_witness._witness(15, None)
    finally:
        monkeypatch.undo()
        time.tzset()
    with contextlib.closing(sqlite3.connect(tmp_path / "goh.db")) as con:
        ((rest, why, stamp),) = con.execute("SELECT rest, why, at FROM lands")
        at = re.search(r"\d{4}-\S+-05:00", why)
        assert at and rest == "{}"
        assert con.execute("SELECT unixepoch(?)", (at[0],)).fetchone() == (int(stamp),)
    assert "-05:00 (GOH.ENQUEUEVANISH)" in said[0]
