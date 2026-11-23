"""lib/land_records.py, the land records' door into goh.db (GOH.STATEDB S1): the import is a round
trip (the report from the table is the report from the files), a write is a row and a file, a
queue reads only its own rows, and a file with no row still reads (an old HEAD export wrote it)."""

from __future__ import annotations

import json
import shutil
import sqlite3

import _goh_db_fixture as gdb
import land_records
import land_reds
import landq_report as lr
import landq_state
from test_landq_report import fixture


def _report(q, born, capsys) -> tuple[str, str, dict]:
    rep = lr.build(lr.read_attempts(q, birth_of=born.get), "")
    lr.run(q, "", as_json=False)
    return lr.render(rep), capsys.readouterr().out, land_reds.report(q)


def test_the_report_from_the_imported_table_is_the_report_from_the_files(
    tmp_path, monkeypatch, capsys
):
    db = gdb.use(monkeypatch, tmp_path)
    q = tmp_path / "landq"
    born = fixture(q, new_only=False)
    red = {"cause": "other", "rule": "r", "tests": []}
    side = {p.name: red for p in (q / "done").glob("*-B.json")}
    (q / "red_causes.json").write_text(json.dumps(side))
    files = {p.stem: json.loads(p.read_text()) for p in (q / "done").glob("*.json")}
    before = _report(q, born, capsys)
    assert not db.exists(), "no goh.db: the files answered"
    assert land_records.import_done(q) == {"lands": 6, "red_causes": 1}
    assert land_records.import_done(q) == {"lands": 0, "red_causes": 0}, "a re-run adds nothing"
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM imports WHERE store='lands'").fetchone() == (1,)
    shutil.rmtree(q / "done")
    (q / "red_causes.json").unlink()
    assert dict(land_records.records(q)) == files, "every record JSON-equal, from the table alone"
    assert _report(q, born, capsys) == before


def test_a_write_is_a_row_and_a_file_and_get_reads_it(tmp_path, monkeypatch):
    db = gdb.use(monkeypatch, tmp_path)
    q = tmp_path / "landq"
    rec = {"sha": "a" * 40, "repo": "/x/r", "mode": "land", "state": "landed", "item": "X.Y",
           "attempt": 2, "at": "2026-11-23T10:00:00Z", "cov_risk": {"lines": 1}}  # fmt: skip
    path = gdb.put(q, "0001-X.Y", rec)
    assert landq_state.read(path) == rec
    with sqlite3.connect(db) as con:
        row = con.execute("SELECT item, state, attempt, at, body FROM lands").fetchone()
    assert row[:3] == ("X.Y", "landed", 2) and json.loads(row[4]) == rec
    assert row[3] == 1795428000.0
    path.unlink()
    assert land_records.get(q, "0001-X.Y") == rec, "the row answers with no file"
    assert land_records.get(q, "0002-X.Y") is None


def test_a_queue_reads_only_its_own_rows_and_a_file_without_a_row(tmp_path, monkeypatch):
    gdb.use(monkeypatch, tmp_path)
    a, b = tmp_path / "a", tmp_path / "b"
    gdb.put(a, "0001-A", {"state": "landed"})
    gdb.put(b, "0001-A", {"state": "red"})
    landq_state.write(a / "done" / "0002-OLD.json", {"state": "red"})  # an old HEAD's file
    assert land_records.records(a) == [
        ("0001-A", {"state": "landed"}),
        ("0002-OLD", {"state": "red"}),
    ]
    assert land_records.records(b, reverse=True) == [("0001-A", {"state": "red"})]


def test_no_goh_db_reads_the_files_and_creates_none(tmp_path, monkeypatch):
    db = gdb.use(monkeypatch, tmp_path)
    q = tmp_path / "landq"
    (q / "done").mkdir(parents=True)
    landq_state.write(q / "done" / "0001-A.json", {"state": "landed"})
    assert land_records.records(q) == [("0001-A", {"state": "landed"})]
    assert land_records.get(q, "0001-A") == {"state": "landed"}
    assert not db.exists()


def test_the_red_causes_are_rows_replaced_whole(tmp_path, monkeypatch):
    gdb.use(monkeypatch, tmp_path)
    q = tmp_path / "landq"
    land_records.set_causes(q, {"0001-A.json": {"cause": "x"}, "0002-B.json": {"cause": "y"}})
    land_records.set_causes(q, {"0002-B.json": {"cause": "z"}})
    assert land_records.causes(q) == {"0002-B.json": {"cause": "z"}}
    assert not (q / "red_causes.json").exists()
