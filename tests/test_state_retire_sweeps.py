"""`goh state retire sweeps` (lib/state_retire_sweeps.py, GOH.PINSWEEP): the push sweeps' files as
goh.db rows, in memory, and then no file."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pid_owner
import push_sweep_rows as rows
import pytest
import state_retire as sr
from _kv_fixture import Held

DAY = "2026-11-25"
DOC = {
    "schema": 1, "status": "done", "created": "2026-11-20T12:00:00+00:00", "budget": 2700,
    "waits_on": "/s/a.json", "waits_on_pid": 5, "waits_on_pid_stamp": "5 t", "log": "/s/a.log",
    "pid": 7, "pid_stamp": "7 t", "started": "2026-11-20T12:01:00+00:00",
    "finished": "2026-11-20T12:40:00+00:00", "load_exit": 1, "repos": {
        "x": {"repo": "/p/x", "status": "pushed", "said": "pushed", "at":
              "2026-11-20T12:02:00+00:00"},
        "y": {"repo": "/p/y", "status": "refused", "said": "NOT pushed", "log": "/s/y.log",
              "at": "2026-11-20T12:03:00+00:00"},
        "z": {"repo": "/p/z", "status": "committed", "said": "its owner's"}}}  # fmt: skip
CUT = {**DOC, "status": "failed", "repos": {**DOC["repos"], "z": {"repo": "/p/z", "status":
                                                                  "pushing"}}}  # fmt: skip


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    Held().lay(monkeypatch)
    for var, d in (("GOH_SHIP_DIR", "ship"), ("XDG_STATE_HOME", "state")):
        monkeypatch.setenv(var, str(tmp_path / d))
    monkeypatch.setenv("GOH_ESTATE_STATE", str(tmp_path / "estate.json"))
    return tmp_path


def put(home: Path, name: str, doc: object) -> Path:
    p = home / "ship" / "goh" / f"{name}.sweep.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc))
    return p


def test_each_file_is_its_rows_then_archived_and_gone(home) -> None:
    done, cut = put(home, "a", DOC), put(home, "b", CUT)
    hand = home / "wf_pin.sweep.json"
    hand.write_text(json.dumps(DOC))
    assert sr.stray() == [done, cut, hand]
    said: list[str] = []
    assert sr.retire("sweeps", day=DAY, say=said.append), said
    assert sr.stray() == [] and list((sr.archive_dir(DAY) / "sweeps").rglob("a.sweep.json"))
    rec = rows.read(done)
    assert (rec["status"], rec["exit"], rec["ship"], rec["supervisor"]) == (
        "done", 1, "/s/a.json", "7 t")  # fmt: skip
    assert rec["finished"] - rec["started"] == 39 * 60
    assert rec["repos"]["y"] == {"repo": "/p/y", "status": "refused", "said": "NOT pushed",
                                 "log": "/s/y.log", "at": rec["started"] + 120}  # fmt: skip
    rec = rows.read(cut)  # its supervisor died: no repo says pushing for ever
    assert rec["status"] == "failed" and rec["repos"]["z"]["status"] == "committed"
    assert "supervisor ended" in rec["repos"]["z"]["said"]
    assert rows.read(hand)["repos"].keys() == DOC["repos"].keys()
    assert rows.live() == []


def test_a_live_supervisors_file_and_one_that_is_no_record_stay(home) -> None:
    live = put(home, "a", {**DOC, "status": "running",
                           "pid_stamp": pid_owner.stamp_line(os.getpid())})  # fmt: skip
    said: list[str] = []
    assert sr.retire("sweeps", day=DAY, say=said.append) and "kept" in said[0]
    assert live.exists() and rows.read(live) == {}
    odd = put(home, "b", [])
    assert not sr.retire("sweeps", day=DAY, say=said.append)
    assert odd.exists() and any("no sweep's record" in s for s in said)


def test_rows_already_there_are_the_sweeps_own(home) -> None:
    p = put(home, "a", DOC)
    rows.put(p, {"run": "later", "status": "done", "created": 1.0, "quiet": 0.0, "every": 0.0,
                 "budget": 1.0, "repos": {}})  # fmt: skip
    assert sr.retire("sweeps", day=DAY, say=lambda _s: None)
    assert rows.read(p)["run"] == "later" and not p.exists()
