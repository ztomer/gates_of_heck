"""`goh state retire lands` (lib/state_retire_lands.py, GOH.STATEDB S1 wave 3): the land queue's
done records, red_causes.json, guards.jsonl and selection_misses.jsonl are imported, read back
equal from goh.db, archived and deleted; a land still in flight keeps its record file; a row that
differs from its file keeps every file of the store."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import land_records
import landq_state
import state_paths
import state_retire as sr

DAY = "2026-11-24"
RED = {"state": "red", "item": "GOH.A", "repo": "/x/r", "sha": "a" * 40, "why": "exit 1",
       "at": "2026-11-20T10:00:00Z"}  # fmt: skip
LANDED = {**RED, "state": "landed", "why": "", "at": "2026-11-20T11:00:00Z"}
GUARD = {"state": "red", "guard": "tests/a.py::test_a", "repo": "/x/r",
         "at": "2026-11-20T10:00:00Z"}  # fmt: skip
MISS = {"kind": "judged", "day": "2026-11-19", "item": "GOH.A", "sha": "a", "test": "t"}


@pytest.fixture
def q(monkeypatch, tmp_path: Path) -> Path:
    for var, sub in (("XDG_CACHE_HOME", "cache"), ("XDG_STATE_HOME", "state")):
        (tmp_path / sub / "goh").mkdir(parents=True)
        monkeypatch.setenv(var, str(tmp_path / sub))
    monkeypatch.setenv("GOH_DB", str(tmp_path / "state" / "goh" / "goh.db"))
    monkeypatch.delenv("GOH_LANDQ_DIR", raising=False)
    q = tmp_path / "state" / "goh" / "landq"
    (q / "done").mkdir(parents=True)
    (q / "logs").mkdir()
    return q


def _files(q: Path) -> dict[str, Path]:
    """An old goh's queue: two records (one also a row already), the causes and both ledgers."""
    out = {}
    for run, rec in (("01795100000000000000-GOH.A", RED), ("01795100000000000001-GOH.A", LANDED)):
        out[run] = q / "done" / f"{run}.json"
        out[run].write_text(json.dumps(rec, indent=1))
    land_records.write(q, "01795100000000000001-GOH.A", LANDED)  # written by both, once
    causes = {"01795100000000000000-GOH.A.json": {"cause": "flake", "rule": "r", "tests": []}}
    out["causes"] = q / "red_causes.json"
    out["causes"].write_text(json.dumps(causes))
    out["guards"] = q / "guards.jsonl"
    out["guards"].write_text(json.dumps(GUARD) + "\n" + '{"torn')
    out["misses"] = q / "selection_misses.jsonl"
    out["misses"].write_text(json.dumps(MISS) + "\n")
    (q / "logs" / "01795100000000000000-GOH.A.log").write_text("a log stays\n")
    return out


def test_a_record_with_no_instant_is_imported(q: Path) -> None:
    path = q / "done" / "01795100000000000000-GOH.A.json"
    path.write_text(json.dumps({"state": "landed", "log": f"{q}/logs/a.log"}))
    said: list[str] = []
    assert sr.retire("lands", day=DAY, say=said.append) is True, said
    assert land_records.records(q) == [(path.stem, {
        "state": "landed", "log": f"{q}/logs/a.log", "at": "2026-11-19T14:53:20Z"})]  # fmt: skip


def test_the_land_files_are_imported_archived_and_deleted(q: Path) -> None:
    files = _files(q)
    before = {k: p.read_bytes() for k, p in files.items()}
    said: list[str] = []
    assert sr.retire("lands", day=DAY, say=said.append) is True, said
    assert not [p for p in files.values() if p.exists()], said
    assert (q / "logs" / "01795100000000000000-GOH.A.log").exists(), "a log is no record"
    to = q.parent / "retired" / DAY / "lands" / "state" / "landq"
    for k, p in files.items():
        assert (to / p.relative_to(q)).read_bytes() == before[k], k
    assert land_records.records(q) == [
        ("01795100000000000000-GOH.A", {**RED, "red_cause": {"cause": "flake", "rule": "r"}}),
        ("01795100000000000001-GOH.A", LANDED),
    ]
    assert {r: {**c, "tests": []} for r, c in land_records.causes(q).items()} == json.loads(
        before["causes"])  # fmt: skip
    assert land_records.misses(q) == [MISS]
    assert sr.stray() == []
    assert sr.retire("lands", day=DAY, say=said.append) is True
    assert said[-1] == "lands: no file left to retire"


def test_a_land_still_in_flight_keeps_its_record_file(q: Path) -> None:
    files = _files(q)
    run = "01795100000000000000-GOH.A"
    landq_state.put(q, run, {"item": "GOH.A", "repo": "/x/r", "sha": "a" * 40}, "running")
    said: list[str] = []
    assert sr.retire("lands", day=DAY, say=said.append) is True, said
    assert files[run].exists() and any("still in flight" in s for s in said), said
    assert not files["01795100000000000001-GOH.A"].exists()
    assert sr.stray() == [files[run]], "named until its land ends and a retire takes it"


def test_a_row_that_differs_from_its_file_keeps_every_land_file(q: Path) -> None:
    files = _files(q)
    land_records.write(q, "01795100000000000001-GOH.A", {**LANDED, "why": "rewritten"})
    said: list[str] = []
    assert sr.retire("lands", day=DAY, say=said.append) is False
    assert all(p.exists() for p in files.values()), said
    assert any("01795100000000000001-GOH.A.json: its lands row differs" in s for s in said), said


def test_the_import_reads_back_the_live_records_json_equal(q: Path) -> None:
    """The round trip: every record the import adds is its file's JSON, byte for byte parsed, and
    each file's bytes are ledgered in `imports` (a re-run adds nothing)."""
    files = _files(q)
    assert sr.retire("lands", dry=True, day=DAY, say=lambda s: None) is True
    assert all(p.exists() for p in files.values()), "a dry run moves nothing"
    con = sqlite3.connect(q.parent / "goh.db")
    try:
        n = con.execute("SELECT COUNT(*) FROM lands").fetchone()[0]
        sources = {r[0] for r in con.execute("SELECT source FROM imports")}
    finally:
        con.close()
    assert n == 2 and {state_paths.encode(str(p)) for p in files.values()} <= sources
    assert sr.retire("lands", dry=True, day=DAY, say=lambda s: None) is True
    con = sqlite3.connect(q.parent / "goh.db")
    try:
        assert con.execute("SELECT COUNT(*) FROM lands").fetchone()[0] == 2
    finally:
        con.close()
