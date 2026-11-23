"""lib/land_records.py, the land records' door into goh.db (GOH.STATEDB S1): the import is a round
trip (the report from the table is the report from the files), a write is a row and a file, a
queue reads only its own rows, and a file with no row still reads (an old HEAD export wrote it)."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3

import _goh_db_fixture as gdb
import land_records
import land_reds
import landq_report as lr
import landq_state
import state_bounds
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
    assert land_records.import_done(q) == {
        "lands": 6,
        "red_causes": 1,
        "land_guards": 0,
        "selection_misses": 0,
    }
    assert land_records.import_done(q) == dict.fromkeys(
        ("lands", "red_causes", "land_guards", "selection_misses"), 0
    )
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


def test_the_sweep_imports_a_done_file_before_its_bound_prunes_it(tmp_path, monkeypatch):
    """The window's file half: a record only an old HEAD export wrote, past its 14 days, is a row
    before state_bounds prunes the file (the record is never in neither)."""
    root = tmp_path / "state"
    monkeypatch.setenv("GOH_DB", str(root / "goh.db"))
    q = root / "landq"
    (q / "done").mkdir(parents=True)
    old = q / "done" / "01000000000000000000-OLD.json"
    landq_state.write(old, {"state": "landed", "at": "2026-10-01T00:00:00Z"})
    then = 1_790_000_000.0  # a fixed clock: the sweep is told its now
    os.utime(old, (then, then))
    state_bounds.sweep(root, now=then + 30 * 86400)
    assert not old.exists(), "pruned past its bound"
    assert land_records.get(q, old.stem) == {"state": "landed", "at": "2026-10-01T00:00:00Z"}


def test_every_reader_reads_a_record_that_is_only_a_row(tmp_path, monkeypatch):
    """The class: each reader the door replaced sees a record goh.db holds and done/ does not (the
    state wave 3 leaves), so none of them still globs the directory."""
    import admit_replay
    import land_attempts
    import land_check
    import land_e2e
    import land_profile
    import landq_alone
    import landq_look
    import landq_size
    import landq_window

    gdb.use(monkeypatch, tmp_path)
    q = tmp_path / "landq"
    sha = "c" * 40
    red = {"state": "red", "mode": "land", "repo": "/x/r", "sha": sha, "item": "GOH.A",
           "why": "exit 1", "guards": ["tests/t.py::test_x"], "pre_s": 42.0, "slot_wait_s": 3.0,
           "at": "2026-11-23T10:00:00Z"}  # fmt: skip
    landed = {**red, "state": "landed", "why": "", "guards": [], "at": "2026-11-23T11:00:00Z",
              "started": "2026-11-23T10:30:00+00:00",
              "finished": "2026-11-23T11:00:00+00:00"}  # fmt: skip
    gdb.put(q, "01795400000000000000-GOH.A", red)
    gdb.put(q, "01795400000000000001-GOH.A", landed)
    gdb.put(q, "01795400000000000002-GOH.B", {**red, "item": "GOH.B"})
    shutil.rmtree(land_records.file(q))
    assert land_attempts.attempt_of(q, "GOH.B") == 2
    assert admit_replay.landq_slot_waits(q) == {sha[:12]: 3.0}
    assert [item for item, _ in land_profile.chains(q)] == ["GOH.A"]
    monkeypatch.setattr(land_e2e, "instrumented", lambda *_: False)  # no logs: a plain land
    assert [r["item"] for r in land_e2e.rows(q, [])] == ["GOH.A"]
    assert landq_size.history(q, "/x/r") == [True, False, True]  # red, landed, red: newest first
    assert landq_window.expected(q, "/x/r") == 42.0
    assert landq_alone.red_last(q, "GOH.B") == "exit 1"
    assert landq_look.find_request(q, "GOH.B") == land_records.file(q, "01795400000000000002-GOH.B")
    assert [r["item"] for r in land_check.records(q)] == ["GOH.B", "GOH.A"]


def test_the_ledgers_import_once_and_learn_writes_a_row_not_the_file(tmp_path, monkeypatch):
    import land_check
    import selection_miss

    db = gdb.use(monkeypatch, tmp_path)
    q = tmp_path / "landq"
    q.mkdir()
    g1 = {"state": "red", "guard": "tests/a.py::test_a", "guards": ["tests/a.py::test_a"],
          "repo": "/x/r", "at": "2026-11-20T10:00:00Z"}  # fmt: skip
    g2 = {**g1, "at": "2026-11-21T10:00:00Z"}
    miss = {"kind": "judged", "day": "2026-11-19", "item": "GOH.A", "sha": "a", "test": "t"}
    (q / "guards.jsonl").write_text(f"{json.dumps(g1)}\n{json.dumps(g2)}\n")
    (q / "selection_misses.jsonl").write_text(json.dumps(miss) + "\n")
    before = (land_records.ledger(q, "guards"), selection_miss.read(q))
    assert before == ([g1, g2], [miss]), "no goh.db: the files answer"
    got = land_records.import_done(q)
    assert (got["land_guards"], got["selection_misses"]) == (2, 1)
    again = land_records.import_done(q)
    assert (again["land_guards"], again["selection_misses"]) == (0, 0)
    (q / "guards.jsonl").unlink()
    (q / "selection_misses.jsonl").unlink()
    assert (land_records.ledger(q, "guards"), selection_miss.read(q)) == before
    assert selection_miss.append(q, [miss]) == 0, "each row once"
    land_check.learn(q, {"repo": "/x/r", "item": "GOH.B", "sha": "b"},
                     "refused (failing: tests/b.py::test_b)")  # fmt: skip
    assert not (q / "guards.jsonl").exists(), "the ledger is goh.db's"
    assert [r["guard"] for r in land_check.records(q)][:1] == ["tests/b.py::test_b"]
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT test FROM land_guards ORDER BY n DESC").fetchone() == (
            "tests/b.py::test_b",
        )
