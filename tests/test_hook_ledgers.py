"""The hook and cache ledgers in goh.db 0005 (GOH.STATEDBS4): each ledger's file, imported, reads
back equal from its table in line order (handover plan (b) step 3); a re-import adds nothing; a
line an old HEAD export appends later is imported once by the next write, before that write's row;
a file trimmed under its offset gives only the lines no row holds; a file not yet imported is read
under the table's rows; and a write goh.db does not take within its busy timeout lets the spawn
through, spilled and counted, drained in order by the next write."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import hook_ledgers as hl


def _spawn(i: int, desc: str = "GOH.STATEDBS4: the hook ledgers") -> dict:
    return {"event": "spawn", "time": f"2026-11-2{i % 4}T0{i % 9}:00:00+00:00", "description": desc,
            "tool_use_id": f"toolu_{i}", "session_id": "s", "model": "opus"}  # fmt: skip


def _outcome(i: int) -> dict:
    return {"event": "outcome", "time": f"2026-11-23T0{i % 9}:30:00+00:00", "agent_id": f"a{i}",
            "status": "landed", "tool_use_id": f"toolu_{i}"}  # fmt: skip


def _lane(i: int) -> dict:
    return {"at": f"2026-11-2{i % 4}T01:00:0{i % 9}+00:00", "verdict": "room", "reading": {"n": i}}


@pytest.fixture
def home(monkeypatch, tmp_path: Path) -> Path:
    for var, sub in (("XDG_CACHE_HOME", "cache"), ("XDG_STATE_HOME", "state")):
        (tmp_path / sub / "goh").mkdir(parents=True)
        monkeypatch.setenv(var, str(tmp_path / sub))
    monkeypatch.setenv("GOH_DB", str(tmp_path / "state" / "goh" / "goh.db"))
    monkeypatch.delenv("GOH_DB_BUSY_MS", raising=False)
    return tmp_path


def _write(p: Path, rows: list[dict], tail: str = "", compact: bool = False) -> None:
    sep = (",", ":") if compact else None
    p.write_text("".join(json.dumps(r, separators=sep) + "\n" for r in rows) + tail)


def _db(home: Path) -> sqlite3.Connection:
    return sqlite3.connect(home / "state" / "goh" / "goh.db")


def test_every_ledger_imported_reads_back_equal_in_line_order(home: Path) -> None:
    spawns = [_spawn(1), _outcome(1), _spawn(2, "fix it"), _outcome(2), _spawn(3)]
    _write(hl.spawns_file(), spawns, tail="not json\n" + '{"torn": ')  # a bad line, a torn write
    lanes = [_lane(i) for i in range(4)]
    _write(hl.lanes_file(), lanes, compact=True)
    cands = [{"id": "FAULTS.a", "faults": ["x"]}, {"id": "FAULTS.b", "faults": ["y"]}]
    _write(hl.candidates_file(), cands)
    hl.watch_file().write_text(json.dumps({"ran_at": "2026-11-23", "spend": {"current": {"a": 1}}}))
    assert hl.import_all() == {"agent_spawns": 5, "lane_starts": 4, "docs": 3}
    got = hl.export()
    assert got == {"agent-spawns.jsonl": spawns, "lane-starts.jsonl": lanes,
                   "fault-candidates.jsonl": cands,
                   "model-choice-watch.json": json.loads(hl.watch_file().read_text())}  # fmt: skip
    con = _db(home)
    assert [r[0] for r in con.execute("SELECT n FROM agent_spawns ORDER BY n")] == [1, 2, 3, 4, 5]
    assert con.execute("SELECT event, agent, item FROM agent_spawns ORDER BY n").fetchall() == [
        ("spawn", None, "GOH.STATEDBS4"), ("outcome", "a1", None), ("spawn", None, None),
        ("outcome", "a2", None), ("spawn", None, "GOH.STATEDBS4")]  # fmt: skip
    assert hl.import_all() == {"agent_spawns": 0, "lane_starts": 0, "docs": 0}  # re-run: harmless
    assert hl.spawns() == spawns


def test_a_line_appended_after_the_import_is_taken_once_before_the_next_row(home: Path) -> None:
    _write(hl.spawns_file(), [_spawn(1)])
    assert hl.append(hl.SPAWNS, _outcome(1)) is None  # the first write imports the file first
    with hl.spawns_file().open("a") as f:  # an old HEAD export still appending to the file
        f.write(json.dumps(_spawn(2)) + "\n")
    assert hl.spawns() == [_spawn(1), _outcome(1), _spawn(2)]  # pending, read under the rows
    assert hl.append(hl.SPAWNS, _outcome(2)) is None
    assert hl.append(hl.SPAWNS, _spawn(3)) is None
    assert hl.spawns() == [_spawn(1), _outcome(1), _spawn(2), _outcome(2), _spawn(3)]
    assert _db(home).execute("SELECT COUNT(*) FROM agent_spawns").fetchone() == (5,)


def test_a_file_trimmed_under_its_offset_gives_only_the_lines_no_row_holds(home: Path) -> None:
    _write(hl.lanes_file(), [_lane(i) for i in range(4)])
    assert hl.append(hl.LANES, _lane(4)) is None
    _write(hl.lanes_file(), [_lane(2), _lane(3), _lane(7)])  # lib/state_bounds.py's trim, then
    assert hl.append(hl.LANES, _lane(5)) is None  # a line no row holds
    assert hl.lanes() == [_lane(i) for i in (0, 1, 2, 3, 4, 7, 5)]


def test_a_file_not_yet_imported_is_read_with_no_database(home: Path) -> None:
    _write(hl.spawns_file(), [_spawn(1), _outcome(1)])
    assert not (home / "state" / "goh" / "goh.db").exists()
    assert hl.spawns() == [_spawn(1), _outcome(1)]
    assert not (home / "state" / "goh" / "goh.db").exists()  # a read never creates it


def test_a_busy_database_lets_the_spawn_through_spilled_and_counted(
    home: Path, monkeypatch
) -> None:
    monkeypatch.setattr(hl, "HOOK_BUSY_MS", 50)
    assert hl.append(hl.SPAWNS, _spawn(1)) is None
    holder = _db(home)
    holder.execute("BEGIN IMMEDIATE")  # another writer holds goh.db past the hook's wait
    warning = hl.append(hl.SPAWNS, _outcome(1))
    assert warning is not None and "spilled, counted" in warning, warning
    assert hl.spawns() == [_spawn(1), _outcome(1)]  # the reader sees the spilled row
    holder.execute("ROLLBACK")
    assert hl.append(hl.SPAWNS, _spawn(2)) is None  # the next write drains the spill first
    assert not hl.spill_file().exists()
    assert hl.spawns() == [_spawn(1), _outcome(1), _spawn(2)]
    assert hl.busy()["count"] == 1
    assert _db(home).execute("SELECT COUNT(*) FROM agent_spawns").fetchone() == (3,)


def test_the_hook_busy_timeout_is_restored_after_the_write(home: Path, monkeypatch) -> None:
    import os

    monkeypatch.setenv("GOH_DB_BUSY_MS", "30000")
    assert hl.append(hl.LANES, _lane(1)) is None
    assert os.environ["GOH_DB_BUSY_MS"] == "30000"
    monkeypatch.delenv("GOH_DB_BUSY_MS")
    assert hl.append(hl.LANES, _lane(2)) is None
    assert "GOH_DB_BUSY_MS" not in os.environ


def test_the_weekly_record_and_the_newest_runs_candidates(home: Path) -> None:
    _write(hl.candidates_file(), [{"id": "FAULTS.old"}])
    assert hl.candidates() == [{"id": "FAULTS.old"}]  # the file, before any run on goh.db
    hl.record_watch({"ran_at": "r1"}, [{"id": "FAULTS.a"}, {"id": "FAULTS.b"}])
    assert hl.watch() == {"ran_at": "r1"}
    assert hl.candidates() == [{"id": "FAULTS.a"}, {"id": "FAULTS.b"}]
    hl.record_watch({"ran_at": "r2"}, None)  # no SPEND section: the candidates stand
    assert hl.watch() == {"ran_at": "r2"}
    assert hl.candidates() == [{"id": "FAULTS.a"}, {"id": "FAULTS.b"}]


def test_the_cli_exports_each_store_as_the_file_it_was(home: Path, tmp_path: Path) -> None:
    _write(hl.spawns_file(), [_spawn(1), _outcome(1)])
    assert hl.main(["import"]) == 0
    out = tmp_path / "export"
    assert hl.main(["export", "--to", str(out)]) == 0
    got = [json.loads(ln) for ln in (out / "agent-spawns.jsonl").read_text().splitlines()]
    assert got == [_spawn(1), _outcome(1)]
    assert (out / "lane-starts.jsonl").read_text() == ""
