"""`goh state retire landq` (lib/state_retire_landq.py, GOH.LANDQDB): an older goh's land queue
request files (`<q>/{enqueuing,pending,running}/<run>.json`) and early reds (`<q>/early/<run>.json`)
go in as their goh.db rows under lander.lock, are read back, archived and deleted, and their
directories go once empty; an in-flight row is never rewritten; a land already recorded is never
queued again; a lander holding lander.lock keeps every file."""

from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import land_records
import landq_state
import state_retire as sr

DAY = "2026-11-24"
REQ = {"item": "GOH.A", "repo": "/x/r", "sha": "a" * 40, "mode": "land"}
STRAY = {
    "pending/01795100000000000001-GOH.A.json": REQ,
    "running/01795100000000000000-GOH.B.json": {**REQ, "item": "GOH.B"},
    "enqueuing/4242-GOH.C.json": {"pid": 4242, "item": "GOH.C", "until": 1.5e9},
    "early/01795100000000000002-GOH.D.json": {**REQ, "item": "GOH.D", "state": "red"},
}


@pytest.fixture
def q(monkeypatch, tmp_path: Path) -> Path:
    for var, sub in (("XDG_CACHE_HOME", "cache"), ("XDG_STATE_HOME", "state")):
        (tmp_path / sub / "goh").mkdir(parents=True)
        monkeypatch.setenv(var, str(tmp_path / sub))
    monkeypatch.setenv("GOH_DB", str(tmp_path / "state" / "goh" / "goh.db"))
    monkeypatch.delenv("GOH_LANDQ_DIR", raising=False)
    q = tmp_path / "state" / "goh" / "landq"
    (q / "logs").mkdir(parents=True)
    return q


def _plant(q: Path, files: dict[str, dict] = STRAY) -> dict[str, Path]:
    out = {}
    for rel, rec in files.items():
        p = q / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rec, indent=1) + "\n")
        out[rel] = p
    return out


def _rows(q: Path) -> dict[str, tuple[str, dict]]:
    con = sqlite3.connect(q.parent / "goh.db")
    try:
        got = {
            run: (state, json.loads(b))
            for run, state, b in con.execute(
                "SELECT run, state, body FROM requests WHERE queue = ?", (os.path.realpath(q),)
            )
        }
        got |= {
            run: ("early", json.loads(b))
            for run, b in con.execute(
                "SELECT run, body FROM early WHERE queue = ?", (os.path.realpath(q),)
            )
        }
    finally:
        con.close()
    return got


def test_the_stray_request_files_go_in_as_rows_and_are_archived_and_deleted(q: Path) -> None:
    files = _plant(q)
    before = {k: p.read_bytes() for k, p in files.items()}
    assert sorted(sr.stray()) == sorted(files.values()), "each stray file is named"
    said: list[str] = []
    assert sr.retire("landq", day=DAY, say=said.append) is True, said
    assert not [p for p in files.values() if p.exists()], said
    for d in ("enqueuing", "pending", "running", "early"):
        assert not (q / d).exists(), f"{d}/ is removed once empty"
    assert (q / "logs").is_dir(), "the logs stay"
    to = q.parent / "retired" / DAY / "landq" / "state" / "landq"
    for k in files:
        assert (to / k).read_bytes() == before[k], k
    assert _rows(q) == {Path(k).stem: (Path(k).parent.name, v) for k, v in STRAY.items()}
    assert sr.stray() == []
    assert sr.retire("landq", day=DAY, say=said.append) is True
    assert said[-1] == "landq: no file left to retire"


def test_an_in_flight_row_is_left_as_it_is(q: Path) -> None:
    run = "01795100000000000001-GOH.A"
    landq_state.put(q, run, {**REQ, "slot": 3}, "running")
    files = _plant(q, {f"pending/{run}.json": REQ})
    said: list[str] = []
    assert sr.retire("landq", day=DAY, say=said.append) is True, said
    assert _rows(q) == {run: ("running", {**REQ, "slot": 3})}, "never moved, never rewritten"
    assert not files[f"pending/{run}.json"].exists(), "its row holds the request"


def test_a_stray_request_whose_land_is_recorded_is_not_queued_again(q: Path) -> None:
    run = "01795100000000000001-GOH.A"
    land_records.write(q, run, {**REQ, "state": "landed"})
    files = _plant(q, {f"pending/{run}.json": REQ, f"early/{run}.json": REQ})
    said: list[str] = []
    assert sr.retire("landq", day=DAY, say=said.append) is True, said
    assert _rows(q) == {}, "a landed run is not a request again"
    assert not [p for p in files.values() if p.exists()], said


def test_a_lander_holding_its_lock_keeps_every_file(q: Path) -> None:
    files = _plant(q)
    fd = os.open(q / "lander.lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        said: list[str] = []
        assert sr.retire("landq", day=DAY, say=said.append) is True, said
        assert all(p.exists() for p in files.values()), said
        assert sum("lander.lock" in s for s in said) == len(files), said
        assert not (q.parent / "goh.db").exists(), "nothing was imported"
    finally:
        os.close(fd)
    assert sr.retire("landq", day=DAY, say=lambda s: None) is True
    assert not [p for p in files.values() if p.exists()]


def test_a_file_that_is_not_whole_keeps_every_file_of_the_store(q: Path) -> None:
    files = _plant(q)
    torn = q / "pending" / "01795100000000000009-GOH.T.json"
    torn.write_text('{"item": "GOH.T"')
    said: list[str] = []
    assert sr.retire("landq", day=DAY, say=said.append) is False
    assert torn.exists() and all(p.exists() for p in files.values()), said
    assert any("01795100000000000009-GOH.T.json: " in s for s in said), said
