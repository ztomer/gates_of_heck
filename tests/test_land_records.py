"""lib/land_records.py, the land records' door into goh.db (GOH.STATEDB S1, wave 3): a write is a
row and no file, a queue reads only its own rows, a done/ file is read by nothing (`goh state
retire lands` archives the old ones: tests/test_state_retire_lands.py), and with no goh.db the
lands are unknown, never none."""

from __future__ import annotations

import json
import sqlite3

import _goh_db_fixture as gdb
import land_records
import land_reds
import landq_report as lr
import landq_state


def _report(q, born, capsys) -> tuple[str, str, dict]:
    rep = lr.build(lr.read_attempts(q, birth_of=born.get), "")
    lr.run(q, "", as_json=False)
    return lr.render(rep), capsys.readouterr().out, land_reds.report(q)


def test_a_write_is_a_row_and_no_file_and_get_reads_it(tmp_path, monkeypatch):
    db = gdb.use(monkeypatch, tmp_path)
    q = tmp_path / "landq"
    rec = {"sha": "a" * 40, "repo": "/x/r", "mode": "land", "state": "landed", "item": "X.Y",
           "attempt": 2, "at": "2026-11-23T10:00:00Z", "cov_risk": {"lines": 1}}  # fmt: skip
    path = gdb.land(q, "0001-X.Y", rec)
    assert path == land_records.ref(q, "0001-X.Y") and not path.exists(), "a reference, no file"
    assert not (q / "done").exists(), "no done/ file is written"
    with sqlite3.connect(db) as con:
        row = con.execute("SELECT item, state, attempt, at FROM lands").fetchone()
    assert row == ("X.Y", "landed", 2, 1795428000.0)
    assert land_records.get(q, "0001-X.Y") == rec, "the row answers"
    assert land_records.get(q, "0002-X.Y") is None


def test_a_queue_reads_only_its_own_rows_and_never_a_done_file(tmp_path, monkeypatch):
    gdb.use(monkeypatch, tmp_path)
    a, b = tmp_path / "a", tmp_path / "b"
    at = {"at": "2026-11-18T11:06:40Z"}
    gdb.land(a, "0001-A", {"state": "landed", **at})
    gdb.land(b, "0001-A", {"state": "red", **at})
    (a / "done").mkdir(parents=True)
    landq_state.write(a / "done" / "0002-OLD.json", {"state": "red"})  # an old goh's file
    assert land_records.records(a) == [("0001-A", {"state": "landed", **at})]
    assert land_records.runs(a) == ["0001-A"]
    assert land_records.get(a, "0002-OLD") is None, "a done/ file is read by nothing"
    named = {"state": "red", "cause": "lander", "cause_detail": "", **at}  # at write: LANDEREXIT1
    assert land_records.records(b, reverse=True) == [("0001-A", named)]


def test_no_goh_db_is_unknown_and_creates_none(tmp_path, monkeypatch, capsys):
    db = gdb.use(monkeypatch, tmp_path)
    q = tmp_path / "landq"
    (q / "done").mkdir(parents=True)
    landq_state.write(q / "done" / "0001-A.json", {"state": "landed"})
    assert not land_records.known()
    assert land_records.records(q) == [] and land_records.get(q, "0001-A") is None
    assert lr.run(q, "", as_json=False) == 2, "the report says unknown, never zero lands"
    assert "unknown" in capsys.readouterr().err
    assert not db.exists()


def test_a_class_set_later_is_the_lands_own_and_no_file(tmp_path, monkeypatch):
    gdb.use(monkeypatch, tmp_path)
    q = tmp_path / "landq"
    gdb.land(q, "0002-B", {"state": "red"})
    land_records.set_causes(q, {"0001-A.json": {"cause": "x"}, "0002-B.json": {"cause": "y"}})
    land_records.set_causes(q, {"0002-B.json": {"cause": "z"}})  # it has one: the first stays
    assert land_records.causes(q) == {"0002-B.json": {"cause": "y"}}
    assert land_records.get(q, "0002-B")["red_cause"] == {"cause": "y"}
    assert not (q / "red_causes.json").exists()


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
    gdb.land(q, "01795400000000000000-GOH.A", red)
    gdb.land(q, "01795400000000000001-GOH.A", landed)
    gdb.land(q, "01795400000000000002-GOH.B", {**red, "item": "GOH.B"})
    assert not (q / "done").exists()
    assert land_attempts.attempt_of(q, "GOH.B") == 2
    assert admit_replay.landq_slot_waits(q) == {sha[:12]: 3.0}
    assert [item for item, _ in land_profile.chains(q)] == ["GOH.A"]
    monkeypatch.setattr(land_e2e, "instrumented", lambda *_: False)  # no logs: a plain land
    assert [r["item"] for r in land_e2e.rows(q, [])] == ["GOH.A"]
    assert landq_size.history(q, "/x/r") == [True, False, True]  # red, landed, red: newest first
    assert landq_window.expected(q, "/x/r") == 42.0
    assert landq_alone.red_last(q, "GOH.B") == "exit 1: lander"  # named at write: LANDEREXIT1
    assert landq_look.find_request(q, "GOH.B") == land_records.ref(q, "01795400000000000002-GOH.B")
    assert [r["item"] for r in land_check.records(q)] == ["GOH.B", "GOH.A"]


def test_the_misses_are_rows_and_their_file_is_read_by_nothing(tmp_path, monkeypatch):
    import selection_miss

    gdb.use(monkeypatch, tmp_path)
    q = tmp_path / "landq"
    q.mkdir()
    miss = {"kind": "judged", "day": "2026-11-19", "item": "GOH.A", "sha": "a", "test": "t"}
    (q / "selection_misses.jsonl").write_text(json.dumps(miss) + "\n")  # an old goh's ledger file
    assert selection_miss.read(q) == []
    assert selection_miss.append(q, [miss]) == 1
    assert selection_miss.append(q, [miss]) == 0, "each row once"
    assert selection_miss.read(q) == [miss]


def test_a_red_row_carries_its_cause_class_or_is_refused(tmp_path, monkeypatch):
    """GOH.LANDEREXIT1: the class is 'a red the record cannot explain', closed at this door. A red
    written with no cause is named here, from its log (lib/land_cause.py `explain`); one written
    with a cause that is no class (`unclassified`, `other`, none) is refused and no row is left."""
    import land_cause
    import pytest

    db = gdb.use(monkeypatch, tmp_path)
    q, log = tmp_path / "landq", tmp_path / "land.log"
    log.write_text("✓ pre-land green in 9 s\n29:FAILED tests/test_a.py::test_b - AssertionError\n")
    gdb.land(q, "0001-A", {"state": "red", "why": "exit 1", "log": str(log)})
    got = land_records.get(q, "0001-A")
    assert (got["cause"], got["why"]) == ("gate-test", "exit 1: gate-test, tests/test_a.py::test_b")
    for bad in ("unclassified", "other", ""):
        with pytest.raises(land_records.Unexplained, match="no cause class"):
            land_records.write(q, f"0002-{bad}", {"state": "red", "why": "exit 1", "cause": bad})
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT run FROM lands").fetchall() == [("0001-A",)]
    assert land_cause.explain({"state": "landed"}) == {}
