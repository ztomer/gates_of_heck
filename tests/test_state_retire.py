"""`goh state retire` (lib/state_retire.py, GOH.STATEDB wave 3): a moved store's files are imported,
read back equal from goh.db, copied into the dated archive and only then deleted; one record that
does not read back equal keeps every file of its store; a dry run moves nothing; `stray` names a
retired store's file still on disk."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import hook_ledgers as hl
import pid_owner
import ship_records
import ship_status
import state_retire as sr

DAY = "2026-11-24"


@pytest.fixture
def home(monkeypatch, tmp_path: Path) -> Path:
    for var, sub in (("XDG_CACHE_HOME", "cache"), ("XDG_STATE_HOME", "state")):
        (tmp_path / sub / "goh").mkdir(parents=True)
        monkeypatch.setenv(var, str(tmp_path / sub))
    monkeypatch.setenv("GOH_DB", str(tmp_path / "state" / "goh" / "goh.db"))
    monkeypatch.setenv("GOH_SHIP_DIR", str(tmp_path / "state" / "goh" / "ship"))
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
    assert hl.lanes() == [{"at": "2026-11-23T01:00:00+00:00", "n": 1}]
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


# -- ships (GOH.STATEDB S2) -----------------------------------------------------------------------

DEAD = "1 Thu Jan  1 00:00:00 1970"  # pid 1 with another start time: a worker long gone


def _ship(p: Path, **kw) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {"schema": 1, "status": "ok", "item": "X.ONE", "sha": "a" * 40, "result": str(p),
            "steps": [{"name": "suite", "status": "ok"}], **kw}  # fmt: skip
    p.write_text(json.dumps(body, indent=1) + "\n")
    return p


def _ship_files(home: Path) -> dict[str, Path]:
    """A ship directory as two repos left it on 2026-11-17: one finished ship with its pins, two
    results saying running whose workers died, one whose worker still runs, and side files."""
    base = home / "state" / "goh" / "ship"
    done = _ship(base / "alpha" / "20261117T000000Z-aaaaaaaa.json")
    pins = done.with_suffix(".pins.json")
    pins.write_text(json.dumps({"schema": 2, "tag": "v1", "repos": {}}))
    return {
        "done": done,
        "pins": pins,
        "dead1": _ship(
            base / "consumer" / "20261117T222215Z-11111111.json",
            status="running",
            pid=14851,
            pid_stamp=f"14851 {DEAD[2:]}",
        ),
        "dead2": _ship(
            base / "consumer" / "20261117T224712Z-22222222.json",
            status="running",
            pid=76982,
            pid_stamp=f"76982 {DEAD[2:]}",
        ),
        "live": _ship(
            base / "alpha" / "20261124T000000Z-cccccccc.json",
            status="running",
            pid=os.getpid(),
            pid_stamp=pid_owner.stamp_line(os.getpid()),
        ),
        "side": _jsonl(base / "alpha" / "20261117T000000Z-aaaaaaaa.sweep.json", [{}]),
        "cov": _jsonl(base / "alpha" / "covsrc.json", [{}]),
        "log": _jsonl(base / "alpha" / "20261117T000000Z-aaaaaaaa.log", [{}]),
    }


def test_the_ship_files_are_settled_imported_archived_and_deleted(home: Path) -> None:
    f = _ship_files(home)
    said: list[str] = []
    assert sr.retire("ships", day=DAY, say=said.append) is True, said
    to = home / "state" / "goh" / "retired" / DAY / "ships" / "state" / "ship"
    for k in ("done", "pins", "dead1", "dead2"):
        assert not f[k].exists(), k
        assert (to / f[k].parent.name / f[k].name).is_file(), k
    for k in ("live", "side", "cov", "log"):  # a running ship's file and the files that stay
        assert f[k].exists(), k
    assert any("kept" in s and f["live"].name in s for s in said), said
    assert ship_records.get(f["done"])["status"] == "ok"
    assert ship_records.get_pins(f["done"])["tag"] == "v1"
    for k in ("dead1", "dead2"):  # settled as goh ship settles one; the archive keeps the file
        rec = ship_records.get(f[k])
        assert rec["status"] == "failed" and rec["failed_step"] == ship_status.ABANDONED, rec
        assert json.loads((to / "consumer" / f[k].name).read_text())["status"] == "running"
    assert ship_records.get(f["live"]) is None  # left to its worker, whose save writes the row
    assert sr.stray() == [f["live"]]
    assert sr.retire("ships", day=DAY, say=said.append) is True  # again: only the live one


def test_a_ship_file_its_row_does_not_match_keeps_every_ship_file(home: Path) -> None:
    f = _ship_files(home)
    ship_records.put({**json.loads(f["done"].read_text()), "status": "failed"})
    con = sqlite3.connect(home / "state" / "goh" / "goh.db")
    with con:  # the row is OLDER than its file and says otherwise: the file holds a record
        con.execute("UPDATE ships SET at=0")
    con.close()
    said: list[str] = []
    assert sr.retire("ships", day=DAY, say=said.append) is False
    assert any("NOT retired" in s and f["done"].name in s for s in said), said
    assert all(p.exists() for p in f.values())
