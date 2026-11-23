"""A ship whose worker died saying running gets its terminal record (GOH.SHIPEARLY).

The 2026-11-21 recurrence pass counted ship records with no final status. Read again on 2026-11-23:
every goh result record was terminal (the status-less files were the sidecars, `*.pins.json` and
`*.suite.profiles.json`), but two Finance_zinc results of 2026-11-17 still said `running`, six days
after their workers died: a killed worker writes nothing, and only `goh ship --status` noticed, by
exit 4, without writing. `ship_status.settle` writes what the worker could not -- `failed` at
`abandoned`, naming the pid -- and every ship start settles its directory's records.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from _shipped_kit import shipped_clone
from conftest import REPO_ROOT
from test_ship import _result, _ship

sys.path.insert(0, str(REPO_ROOT / "lib"))
import pid_owner  # lib/, put on the path above
import ship_status


def _record(path: Path, pid: int, stamp: str | None, status: str = "running") -> None:
    res = {"schema": 1, "status": status, "pid": pid, "pid_stamp": stamp, "result": str(path),
           "steps": [{"name": "suite", "status": "ok"}], "failed_step": None,
           "tail": []}  # fmt: skip
    path.write_text(json.dumps(res))


def test_a_running_record_whose_worker_is_gone_is_settled_failed(tmp_path: Path) -> None:
    rec = tmp_path / "20261117T224712Z-7c11ad93.json"
    _record(rec, 1, "1 Thu Jan  1 00:00:00 1970")  # pid 1, another start time: a reused pid
    assert ship_status.settle(rec)
    res = json.loads(rec.read_text())
    assert res["status"] == "failed" and res["failed_step"] == ship_status.ABANDONED, res
    assert "pid 1" in res["tail"][0] and "suite" in res["tail"][0], res["tail"]


def test_a_live_worker_and_a_terminal_record_and_a_sidecar_are_left_alone(tmp_path: Path) -> None:
    live = tmp_path / "live.json"
    _record(live, os.getpid(), pid_owner.stamp_line(os.getpid()))
    done = tmp_path / "done.json"
    _record(done, 1, "1 Thu Jan  1 00:00:00 1970", status="ok")
    pins = tmp_path / "x.pins.json"
    pins.write_text(json.dumps({"consumers": {}}))
    before = {p: p.read_text() for p in (live, done, pins)}
    assert ship_status.settle_all(tmp_path) == []
    assert {p: p.read_text() for p in before} == before


def test_a_ship_start_settles_its_directorys_dead_records(tmp_path: Path, tmp_path_factory) -> None:
    shipped = shipped_clone(tmp_path, tmp_path_factory)
    dead = tmp_path / "20261117T222215Z-7c11ad93.json"
    _record(dead, 1, "1 Thu Jan  1 00:00:00 1970")
    r = _ship(shipped, tmp_path, "--wait")
    assert "recorded it as failed" in r.stdout + r.stderr, r.stdout + r.stderr
    assert json.loads(dead.read_text())["failed_step"] == ship_status.ABANDONED
    assert _result(tmp_path)["status"] in ("ok", "failed")
