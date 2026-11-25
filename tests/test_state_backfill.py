"""lib/state_backfill.py (GOH.LANDCOLS): the rows from before the columns, filled; killed anywhere
and run again it is the same file as one clean run."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import db_core
import goh_db
import pytest
import state_backfill
import state_columns
from _columns_fixture import copied, dump, killed, old_file


@pytest.fixture
def db(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("GOH_DB", str(tmp_path / "state" / "goh.db"))
    return old_file(tmp_path, 1200)


def found() -> list[str]:
    with goh_db.ro() as con:
        return state_columns.check(con)


def test_every_row_is_filled_and_a_second_run_changes_none(db) -> None:
    done = {"repaired": 1, "folded": 2, "lands": 2, "ship_pins": 1, "ships": 1,
            "agent_spawns": 1200}  # fmt: skip
    assert state_backfill.run() == done
    once = dump(db)
    assert state_backfill.run() == dict.fromkeys(done, 0) and dump(db) == once
    with goh_db.ro() as con:
        rows = {r: (c, k) for r, c, k in con.execute("SELECT run, red_class, rest FROM lands")}
        tests = con.execute("SELECT run, value FROM land_lists WHERE kind = 'red_test'").fetchall()
        assert con.execute("SELECT count(*) FROM spawn_lists").fetchone() == (2400,)
    odd = '{"slot_s":"soon"}'
    assert rows == {"1-died": ("flake", "{}"), "2-red": ("flake", "{}"), "3-old": (None, "{}"),
                    "4-odd": (None, odd)}  # fmt: skip
    assert sorted(tests) == [(r, t) for r in ("1-died", "2-red") for t in ("t::a", "t::b")]
    # the spawns' own `n` key has no column: named, like the odd land
    assert [x for x in found() if not x.startswith("agent_spawns: ")] == [
        f"lands: /q 4-odd: `rest` holds {odd}"]  # fmt: skip


# 1 the repair, 2 the fold, 3 to 5 the small tables, 6 to 8 the spawns' 500, 500 and 200
@pytest.mark.parametrize("nth", [1, 2, 3, 5, 6, 8])
def test_a_kill_then_a_run_is_one_clean_run(db, tmp_path, monkeypatch, nth) -> None:
    clean = copied(tmp_path / "clean" / "goh.db")  # migrated: both runs start at the same rows
    was = dump(clean)
    killed("state_backfill", nth)
    assert found() and (dump(db) != was) == (nth > 1)
    state_backfill.run()
    monkeypatch.setenv("GOH_DB", str(clean))
    state_backfill.run()
    assert dump(db) == dump(clean) != was


def test_a_class_set_after_the_fold_is_folded_by_the_next_run(db, capsys) -> None:
    """land_records.set_causes writes red_causes until the table is gone: the check names a class
    not folded, the backfill folds it. A folded land keeps the class it has."""
    state_backfill.run()
    with goh_db.transaction() as con:
        con.execute("UPDATE red_causes SET body = json_set(body, '$.cause', 'other')")
        con.execute("INSERT INTO red_causes(queue, run, cause, at, body) VALUES "
                    "('/q', '3-old', 'conflict', 1, '{\"cause\": \"conflict\"}')")  # fmt: skip
    assert "lands: 1 red causes not folded in: goh state backfill" in found()
    assert state_backfill.main() == 0 and "1 folded" in capsys.readouterr().out
    with goh_db.ro() as con:
        got = con.execute("SELECT red_class FROM lands ORDER BY run").fetchall()
    assert got == [("flake",), ("flake",), ("conflict",), (None,)]


def test_the_repair_touches_only_the_key_that_is_wrong(db) -> None:
    at = '"at":"2026-11-18T11:06:40Z"'
    docs = {"a": '{"at":1795000000,"recorded":false}', "b": f'{{{at},"recorded":"x"}}',
            "c": '{"at":1795000000.5}'}  # fmt: skip
    with goh_db.transaction() as con:
        for run, doc in docs.items():
            con.execute("INSERT INTO lands(queue, run, at, body) VALUES ('/p', ?, 1795000000, ?)",
                        (run, doc))  # fmt: skip
        con.execute("INSERT INTO red_causes(queue, run, cause, at, body) VALUES "
                    "('/p', 'a', 'x', 1, '{torn')")  # fmt: skip
    assert state_backfill.run()["repaired"] == 4
    with goh_db.ro() as con:
        got = dict(con.execute("SELECT run, body FROM lands WHERE queue = '/p'"))
        assert not any(" /p " in x for x in state_columns.check(con))
    assert got == {"a": f'{{{at},"recorded":false}}', "b": f'{{{at},"recorded":true}}',
                   "c": f"{{{at}}}"}  # fmt: skip


def test_a_document_that_is_no_json_is_left_and_said(db, capsys) -> None:
    with goh_db.transaction() as con:
        con.execute("UPDATE agent_spawns SET body = '{torn' WHERE n = 7")
    assert state_backfill.main() == 1
    assert "agent_spawns: 7: its document is not JSON" in capsys.readouterr().err
    with goh_db.ro() as con:
        assert state_columns.unfilled(con, "agent_spawns") == (0, ["7"])


def test_rows_that_stay_unfilled_stop_it(db, monkeypatch, capsys) -> None:
    real = state_backfill.steps()
    none = [(n, s if n != "ships" else "UPDATE ships SET rest = NULL") for n, s in real]
    monkeypatch.setattr(state_backfill, "steps", lambda: none)
    assert state_backfill.main() == 1
    assert "ships: 1 rows stay unfilled" in capsys.readouterr().err


def test_only_the_code_of_record_moves_the_shared_files_rows(db, monkeypatch) -> None:
    monkeypatch.setattr(db_core, "shared", lambda *_: True)
    monkeypatch.setattr(db_core, "of_record", lambda *_: False)
    was = dump(db)
    with pytest.raises(db_core.NotOfRecord):
        state_backfill.run()
    assert dump(db) == was


def test_no_file_is_not_made(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("GOH_DB", str(tmp_path / "none.db"))
    assert state_backfill.main() == 1 and "no goh.db" in capsys.readouterr().err
    assert not (tmp_path / "none.db").exists()


def test_an_older_schema_is_refused(tmp_path, db, monkeypatch) -> None:
    import dataclasses

    old = dataclasses.replace(goh_db.SEAM, schema=tmp_path / "ten")
    monkeypatch.setattr(goh_db, "SEAM", old)
    with pytest.raises(sqlite3.OperationalError, match="no schema file 0011"):
        state_backfill.run()
