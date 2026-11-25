"""lib/state_strip.py (GOH.LANDCOLS): a document goes only where its columns give it back, and never
before this goh ships the contract that drops the column."""

from __future__ import annotations

import json
from pathlib import Path

import goh_db
import hook_ledgers
import land_records
import pytest
import ship_records
import state_backfill
import state_columns
import state_strip
from _columns_fixture import LAND, PINS, RESULT, SHIP, copied, dump, killed, old_file

ON = "m.CONTRACT = 14"  # a number this goh ships stands for the contract it does not yet

KEYED = "lambda path: path.removeprefix('/r/')"  # a repo's key as S7's codec gives it


def key(path: str) -> str:
    return path.removeprefix("/r/")


@pytest.fixture
def db(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("GOH_DB", str(tmp_path / "state" / "goh.db"))
    p = old_file(tmp_path, 450)
    state_backfill.run()
    with goh_db.transaction() as con:  # the row the backfill leaves in `rest`: a key with no column
        con.execute("UPDATE agent_spawns SET rest = NULL, body = json_remove(body, '$.n')")
    return p


@pytest.fixture
def on(monkeypatch) -> None:
    monkeypatch.setattr(state_strip, "CONTRACT", 14)


def left(table: str) -> int:
    with goh_db.ro() as con:
        return con.execute(f"SELECT count(*) FROM {table} WHERE body <> '{{}}'").fetchone()[0]


def test_refused_while_this_goh_reads_the_documents(db, capsys) -> None:
    was = dump(db)
    assert state_strip.main() == 1
    assert "ships no contract 0017" in capsys.readouterr().err and dump(db) == was


def test_refused_while_a_ship_runs(db, on, capsys) -> None:
    ship_records.put({"result": "/s/results/goh/x.json", "status": "bisecting"})
    assert state_strip.main() == 1
    assert "ships still running: 1" in capsys.readouterr().err and left("lands") == 4


def test_every_document_the_columns_give_back_goes(db, on, tmp_path) -> None:
    ship_records.put_pins(RESULT, PINS)
    ship_records.put(SHIP)
    ship_records.put({"result": "/s/results/Fin_z/x.json", "status": "ok", "repo": "/r/Fin/z"})
    land_records.write(tmp_path, "01795000000000000000-A.B", LAND)
    lane = {"at": LAND["at"], "verdict": "room", "v": 1, "why": ["w"]}
    assert hook_ledgers.append(hook_ledgers.LANES, lane) is None
    with goh_db.ro() as con:
        docs = [(t, k, body) for t in state_columns.TABLES
                for _, k, body, _, _ in state_columns.rendered(con, t)]  # fmt: skip
    land_records.append(tmp_path, "misses", [{"kind": "miss", "test": t} for t in "ab"])
    assert len(docs) == 459 and state_strip.run(key) == (459, [])
    assert {t: left(t) for t in state_columns.TABLES if left(t)} == {"selection_misses": 2}
    assert hook_ledgers.lanes()[0]["why"] == ["w"]
    with goh_db.ro() as con:
        repos = con.execute("SELECT repo FROM ships ORDER BY repo").fetchall()
        assert repos == [("Fin/z",), ("goh",)]  # the keys: the first was Fin_z
        assert con.execute(
            "SELECT body FROM lands_v1 WHERE item = 'A.B' AND attempt = 2"
        ).fetchone()
        assert state_columns.check(con) == ['lands: /q 4-odd: `rest` holds {"slot_s":"soon"}']
    once = dump(db)
    assert state_strip.run(key) == (0, []) and dump(db) == once
    with goh_db.transaction() as con:  # the columns alone render every one of them again
        for t in (t for t in state_columns.TABLES if t not in state_strip.KEPT):
            con.execute(f"UPDATE {t} SET body = '{{ }}'")
        again = {(t, k): doc for t in state_columns.TABLES
                 for _, k, _, doc, _ in state_columns.rendered(con, t)}  # fmt: skip
    bad = [(t, k) for t, k, body in docs if state_columns.differs(t, body, again[t, k])]
    assert bad == []


def test_a_row_that_differs_or_was_never_filled_stays_whole_and_is_named(db, on) -> None:
    with goh_db.transaction() as con:
        con.execute("UPDATE lands SET pre_s = 9 WHERE run = '3-old'")
        con.execute("UPDATE agent_spawns SET rest = NULL WHERE n = 5")
        con.execute("UPDATE agent_spawns SET body = '{torn' WHERE n = 6")
        con.execute("UPDATE ship_pins SET tag = NULL")
    n, whole = state_strip.run(key)
    assert n == 451 and sorted(whole) == [
        "agent_spawns: 5: not filled",
        "agent_spawns: 6: its document is not JSON",
        "lands: /q 3-old: its columns do not give back pre_s",
        f"ship_pins: {RESULT}: its columns do not give back tag",
        f"ships: {RESULT}: its columns do not give back consumer_pins",
    ]
    assert (left("lands"), left("ships"), left("agent_spawns")) == (1, 1, 2)
    with goh_db.ro() as con:
        row = con.execute("SELECT body FROM lands WHERE run = '3-old'").fetchone()
    assert json.loads(row[0]) == {"at": LAND["at"], "item": "A.B", "pre_s": 1.5}


# each table takes one transaction that strips and one that finds no more; the spawns 200, 200, 50
@pytest.mark.parametrize("nth", [1, 3, 7, 8, 9])
def test_a_kill_then_a_run_is_one_clean_run(db, on, tmp_path, monkeypatch, nth) -> None:
    clean = copied(tmp_path / "clean" / "goh.db")
    killed("state_strip", nth, ON, KEYED)
    assert any(map(left, state_columns.TABLES)) and (dump(db) != dump(clean)) == (nth > 1)
    n, whole = state_strip.run(key)
    assert n > 0 and whole == [] and not any(map(left, state_columns.TABLES))
    monkeypatch.setenv("GOH_DB", str(clean))
    assert state_strip.run(key) == (456, []) and dump(db) == dump(clean)


def test_the_command(db, on, capsys) -> None:
    """The word alone has no codec for a ship's repo: the cutover hands it one."""
    was = dump(db)
    assert state_strip.main() == 1 and dump(db) == was
    assert "hands it the codec" in capsys.readouterr().err
    assert state_strip.main(key) == 0 and "456 documents dropped, 0 left" in capsys.readouterr().out


def test_what_changes_under_it_stays_whole(db, on, monkeypatch) -> None:
    """A ship that starts again and a red class set after the first check: seen under the lock.
    Stripped, the class could be folded in by nothing."""
    monkeypatch.setattr(state_strip, "_may", lambda: None)
    with goh_db.transaction() as con:
        con.execute("UPDATE ships SET status = 'running', body = '[]'")
        con.execute("INSERT INTO red_causes(queue, run, cause, at, body) VALUES "
                    "('/q', '3-old', 'conflict', 1, '{\"cause\": \"conflict\"}')")  # fmt: skip
    n, whole = state_strip.run(key)
    assert whole == ["lands: /q 3-old: its red cause is not folded in", f"ships: {RESULT}: it runs"]
    assert n == 454
    state_backfill.run()
    with goh_db.transaction() as con:
        con.execute("UPDATE ships SET status = 'ok'")
    assert state_strip.run(key)[0] >= 1 and left("lands") == 0
