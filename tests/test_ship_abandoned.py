"""A ship whose worker died saying running gets its terminal record (GOH.SHIPEARLY).

The 2026-11-21 recurrence pass counted ship records with no final status. Read again on 2026-11-23:
every goh result record was terminal (the status-less files were the sidecars, `*.pins.json` and
`*.suite.profiles.json`), but two Finance_zinc results of 2026-11-17 still said `running`, six days
after their workers died: a killed worker writes nothing, and only `goh ship --status` noticed, by
exit 4, without writing. `ship_status.settle` writes what the worker could not -- `failed` at
`abandoned`, naming the pid -- and every ship start settles EVERY repo's records (it settled only
the shipped repo's, so the two of another repo were never settled). The records are goh.db rows
(GOH.STATEDB S2); `goh state retire ships` settles a file's the same way
(tests/test_state_retire.py).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import _goh_db_fixture as gdb
from _shipped_kit import shipped_clone
from conftest import REPO_ROOT
from test_ship import _result, _ship

sys.path.insert(0, str(REPO_ROOT / "lib"))
import pid_owner  # lib/, put on the path above
import ship_status


def _record(path: Path, pid: int, stamp: str | None, status: str = "running") -> None:
    gdb.ship(path, {"schema": 1, "status": status, "pid": pid, "pid_stamp": stamp,
                    "steps": [{"name": "suite", "status": "ok"}], "failed_step": None,
                    "tail": []})  # fmt: skip


DEAD = "1 Thu Jan  1 00:00:00 1970"  # pid 1, another start time: a reused pid


def test_a_running_record_whose_worker_is_gone_is_settled_failed(monkeypatch, tmp_path) -> None:
    gdb.made(monkeypatch, tmp_path)
    rec = tmp_path / "20261117T224712Z-7c11ad93.json"
    _record(rec, 1, DEAD)
    assert ship_status.settle(rec)
    res = gdb.shipped(rec)
    assert res["status"] == "failed" and res["failed_step"] == ship_status.ABANDONED, res
    assert "pid 1" in res["tail"][0] and "suite" in res["tail"][0], res["tail"]
    assert not rec.exists()  # a row, never a file


def test_a_live_worker_and_a_terminal_record_and_a_pin_row_are_left_alone(
    monkeypatch, tmp_path
) -> None:
    gdb.made(monkeypatch, tmp_path)
    live = tmp_path / "a" / "live.json"
    _record(live, os.getpid(), pid_owner.stamp_line(os.getpid()))
    done = tmp_path / "b" / "done.json"
    _record(done, 1, DEAD, status="ok")
    gdb.pins(done, {"consumers": {}})
    before = (gdb.shipped(live), gdb.shipped(done), gdb.pinned(done))
    assert ship_status.settle_all() == []
    assert (gdb.shipped(live), gdb.shipped(done), gdb.pinned(done)) == before


def test_settling_reaches_every_repos_dead_records(monkeypatch, tmp_path) -> None:
    gdb.made(monkeypatch, tmp_path)
    dead = [tmp_path / repo / "20261117T222215Z-7c11ad93.json" for repo in ("one", "two")]
    for p in dead:
        _record(p, 1, DEAD)
    assert ship_status.settle_all() == dead
    assert {gdb.shipped(p)["failed_step"] for p in dead} == {ship_status.ABANDONED}


def test_no_goh_db_settles_nothing_and_says_the_ships_are_unknown(monkeypatch, tmp_path) -> None:
    gdb.use(monkeypatch, tmp_path)  # no file: unknown, never "no ship running"
    assert ship_status.settle_all() == []
    import ship_records

    try:
        ship_records.live()
    except ship_status.goh_db.Unknown:
        pass
    else:
        raise AssertionError("a missing goh.db read as no ship")
    assert not (tmp_path / "goh.db").exists()


def test_a_ship_start_settles_another_repos_dead_records(
    monkeypatch, tmp_path: Path, tmp_path_factory
) -> None:
    shipped = shipped_clone(tmp_path, tmp_path_factory)
    db = gdb.made(monkeypatch, tmp_path)  # its own: another test's ship start settles the suite's
    dead = tmp_path / "another_repo" / "20261117T222215Z-7c11ad93.json"  # not the shipped repo's
    _record(dead, 1, DEAD)
    r = _ship(shipped, tmp_path, "--wait", GOH_DB=str(db))
    assert "recorded it as failed" in r.stdout + r.stderr, r.stdout + r.stderr
    assert gdb.shipped(dead)["failed_step"] == ship_status.ABANDONED
    assert _result(tmp_path)["status"] in ("ok", "failed")
