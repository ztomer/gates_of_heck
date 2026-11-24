"""`goh state retire` (lib/state_retire.py, GOH.STATEDB wave 3): a moved store's files are imported,
read back equal from goh.db, copied into the dated archive and only then deleted; one record that
does not read back equal keeps every file of its store; a dry run moves nothing; `stray` names a
retired store's file still on disk."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import hook_ledgers as hl
import state_retire as sr

DAY = "2026-11-24"


@pytest.fixture
def home(monkeypatch, tmp_path: Path) -> Path:
    for var, sub in (("XDG_CACHE_HOME", "cache"), ("XDG_STATE_HOME", "state")):
        (tmp_path / sub / "goh").mkdir(parents=True)
        monkeypatch.setenv(var, str(tmp_path / sub))
    monkeypatch.setenv("GOH_DB", str(tmp_path / "state" / "goh" / "goh.db"))
    return tmp_path


def _spawn(i: int) -> dict:
    return {"event": "spawn", "time": f"2026-11-2{i % 4}T0{i % 9}:00:00+00:00",
            "description": f"GOH.X{i}: a spawn", "tool_use_id": f"toolu_{i}"}  # fmt: skip


def _jsonl(p: Path, rows: list[dict], tail: str = "") -> Path:
    p.write_text("".join(json.dumps(r) + "\n" for r in rows) + tail)
    return p


def _hook_files(home: Path) -> dict[str, Path]:
    cache, state = home / "cache" / "goh", home / "state" / "goh"
    return {
        "spawns": _jsonl(cache / "agent-spawns.jsonl", [_spawn(1), _spawn(2), _spawn(1)], "{torn"),
        "lanes": _jsonl(state / "lane-starts.jsonl", [{"at": "2026-11-23T01:00:00Z", "n": 1}]),
        "cands": _jsonl(cache / "fault-candidates.jsonl", [{"id": "FAULTS.a"}]),
        "watch": _jsonl(cache / "model-choice-watch.json", [{"ran_at": "r0"}]),
    }


def test_the_hook_ledgers_read_back_equal_are_archived_then_deleted(home: Path) -> None:
    files = _hook_files(home)
    assert hl.append(hl.SPAWNS, _spawn(2)) is None  # a row since the switch: the file's 2nd line
    said: list[str] = []
    assert sr.retire("hook-ledgers", day=DAY, say=said.append) is True, said
    to = home / "state" / "goh" / "retired" / DAY / "hook-ledgers"
    for p in files.values():
        assert not p.exists()
        where = "cache" if p.parent == home / "cache" / "goh" else "state"
        assert (to / where / p.name).is_file()
    assert (to / "cache" / "agent-spawns.jsonl").read_text().endswith("{torn")  # byte for byte
    # every line in once, the duplicate twice; the row already there not again
    assert sorted(r["tool_use_id"] for r in hl.spawns()) == ["toolu_1", "toolu_1", "toolu_2"]
    assert hl.lanes() == [{"at": "2026-11-23T01:00:00Z", "n": 1}]
    assert hl.candidates() == [{"id": "FAULTS.a"}]
    assert hl.watch() == {"ran_at": "r0"}
    assert sr.stray() == []
    assert sr.retire("hook-ledgers", day=DAY, say=said.append) is True  # again: nothing left
    assert said[-1] == "hook-ledgers: no file left to retire"


def test_one_record_that_does_not_read_back_keeps_every_file(home: Path) -> None:
    files = _hook_files(home)
    db = home / "state" / "goh" / "goh.db"
    hl.record_watch({"ran_at": "other"}, None)  # a watch row that is not the file's...
    con = sqlite3.connect(db)
    with con:  # ...and older than it: the file holds a record goh.db does not
        con.execute("UPDATE kv SET at=0 WHERE name='model-choice-watch'")
    con.close()
    said: list[str] = []
    assert sr.retire("hook-ledgers", day=DAY, say=said.append) is False
    assert all(p.exists() for p in files.values())
    assert not (home / "state" / "goh" / "retired").exists()
    assert any("NOT retired" in s and "model-choice-watch.json" in s for s in said), said
    assert sorted(sr.stray()) == sorted(files.values())
    assert sr.main(["stray"]) == 1


def test_a_dry_run_moves_nothing(home: Path) -> None:
    files = _hook_files(home)
    assert sr.main(["retire", "hook-ledgers", "--dry-run"]) == 0
    assert all(p.exists() for p in files.values())


def test_an_unknown_store_is_refused(home: Path) -> None:
    with pytest.raises(SystemExit) as e:
        sr.main(["retire", "no-such-store"])
    assert e.value.code == 2
