"""lib/durations.py, the suite's timing tables in goh.db (GOH.DURATIONSDB, goh.db 0006): two runs
ending together keep both runs' readings, the JSON files import once and read the same, a prune is
a row a later reading lifts, and each (kind, node) keeps its newest KEEP readings."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import db_core
import durations
import goh_db
import pytest
from conftest import REPO_ROOT
from timing_bounds import patience

WRITER = textwrap.dedent("""
    import sys, time
    from pathlib import Path
    sys.path[:0] = [sys.argv[1] + "/lib"]
    import durations
    db, me, gate = Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4])
    (gate / me).touch()
    while len(list(gate.iterdir())) < 2:
        time.sleep(0.005)
    durations.record({"dur": {f"t.py::{me}": 1.0}}, keep=lambda k: True, db=db)
""")


@pytest.mark.load_sensitive
def test_two_runs_ending_together_keep_both_readings(tmp_path: Path) -> None:
    """The lost update the JSON merge had (read, merge, replace with no lock: one of two runs
    ending together kept only its own node): on goh.db each run INSERTs, and both rows survive."""
    db, gate = tmp_path / "goh.db", tmp_path / "gate"
    gate.mkdir()
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", WRITER, str(REPO_ROOT), str(db), me, str(gate)],
            stdin=subprocess.DEVNULL,
        )
        for me in ("a", "b")
    ]
    assert [p.wait(timeout=patience(60)) for p in procs] == [0, 0]
    assert durations.load(db=db) == {"t.py::a": 1.0, "t.py::b": 1.0}


def test_the_json_files_import_once_and_read_the_same(tmp_path: Path) -> None:
    """Before the first write a reader reads each JSON file; the write imports it (by sha256, once)
    under its own readings, and the view equals the file's entries plus the run's."""
    db = tmp_path / "goh.db"
    table = {"t.py::test_a": 3.0, "t.py::test_b": 0.25, "bad": "x"}
    (tmp_path / "test-durations.json").write_text(json.dumps(table))
    (tmp_path / "test-durations.poles.json").write_text(json.dumps({"t.py::test_a": 2.0}))
    (tmp_path / "linux-unsafe.json").write_text(json.dumps({"test_m": "why"}))
    assert durations.load(db=db) == {"t.py::test_a": 3.0, "t.py::test_b": 0.25}
    assert durations.get("linux_unsafe", db=db) == {"test_m": "why"}
    durations.record({"dur": {"t.py::test_c": 1.5}}, db=db)
    durations.record({"dur": {"t.py::test_c": 1.25}}, db=db)  # a second write imports nothing
    with goh_db.ro(db) as con:
        assert con.execute(
            "SELECT store, source, rows FROM imports ORDER BY source"
        ).fetchall() == [
            ("durations", "linux-unsafe.json", 1),
            ("durations", "test-durations.json", 2),
            ("durations", "test-durations.poles.json", 1),
        ]
        assert con.execute("PRAGMA user_version").fetchone()[0] >= 1
    assert durations.load(db=db) == {
        "t.py::test_a": 3.0,
        "t.py::test_b": 0.25,
        "t.py::test_c": 1.25,
    }
    assert durations.load("pole", db=db) == {"t.py::test_a": 2.0}
    assert durations.get("linux_unsafe", db=db) == {"test_m": "why"}


def test_a_prune_is_a_row_a_later_reading_lifts(tmp_path: Path) -> None:
    db = tmp_path / "goh.db"
    durations.record({"dur": {"t.py::a": 1.0, "t.py::b": 2.0}, "pole": {"t.py::b": 2.0}}, db=db)
    durations.record({"dur": {"t.py::a": 1.1}}, keep=lambda k: k != "t.py::b", db=db)
    assert durations.load(db=db) == {"t.py::a": 1.1}
    assert durations.load("pole", db=db) == {}
    durations.record({"dur": {"t.py::b": 3.0}}, db=db)
    assert durations.load(db=db) == {"t.py::a": 1.1, "t.py::b": 3.0}


def test_each_node_keeps_its_newest_readings(tmp_path: Path) -> None:
    db = tmp_path / "goh.db"
    for i in range(durations.KEEP + 3):
        durations.record({"dur": {"t.py::a": float(i)}, "pole_cpu": {"t.py::a": 0.5}}, db=db)
    with goh_db.ro(db) as con:
        got = con.execute(
            "SELECT kind, COUNT(*), MAX(seconds) FROM readings GROUP BY kind ORDER BY kind"
        ).fetchall()
    assert got == [("dur", durations.KEEP, float(durations.KEEP + 2)), ("pole_cpu", 5, 0.5)]
    assert durations.load(db=db) == {"t.py::a": float(durations.KEEP + 2)}


def test_a_kv_update_is_one_transaction(tmp_path: Path) -> None:
    db = tmp_path / "goh.db"
    durations.update("linux_unsafe", lambda v: {**(v or {}), "m1": "a"}, db=db)
    durations.update("linux_unsafe", lambda v: {**(v or {}), "m2": "b"}, db=db)
    assert durations.get("linux_unsafe", db=db) == {"m1": "a", "m2": "b"}


def test_no_database_and_no_file_is_no_table(tmp_path: Path) -> None:
    assert durations.load(db=tmp_path / "goh.db") == {}
    assert durations.get("remote_rates", db=tmp_path / "goh.db") is None
    assert not (tmp_path / "goh.db").exists()  # a read never creates it


def test_the_path_is_the_named_database_else_goh_db(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GOH_DB", str(tmp_path / "x.db"))
    assert durations.path({}) == tmp_path / "x.db"
    assert durations.path({"GOH_TEST_DURATIONS": "/n/goh.db"}) == Path("/n/goh.db")
    assert os.environ.get("GOH_TEST_DURATIONS") is None  # this suite's run popped it


def test_only_the_runners_named_table_opens_in_the_real_home(tmp_path: Path, monkeypatch) -> None:
    """Under a suite the real home's goh.db is refused (lib/db_core.py `guard`); the one exception
    is the table tools/pytest.sh named for its top-level run (`named`), which tests/_schedule.py
    took before any test ran."""
    real = tmp_path / "real"
    db = real / ".local" / "state" / "goh" / "goh.db"
    monkeypatch.setenv("GATES_OF_HECK_SUITE_REAL_HOME", str(real))
    monkeypatch.delenv("GATES_OF_HECK_SUITE_REAL_XDG_STATE_HOME", raising=False)
    with pytest.raises(db_core.RealHomeOpen):
        durations.record({"dur": {"t.py::a": 1.0}}, db=db)
    durations.record({"dur": {"t.py::a": 1.0}}, db=db, named=True)
    assert durations.load(db=db, named=True) == {"t.py::a": 1.0}
