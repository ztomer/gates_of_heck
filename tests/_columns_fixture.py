"""Documents for the tests of goh.db's typed columns (GOH.LANDCOLS): one of each kind with EVERY key
goh's code writes, and a file from before the columns holding rows of them.

    LAND SHIP PINS SPAWN OUTCOME   a whole document each (a key with no column shows in `rest`)
    ten(tmp_path)                  the seam at schema 10: the copy of goh that knows no columns
    old_file(tmp_path, spawns)     goh.db as that copy wrote it, N spawn rows in it
    copied(to)                     goh.db, migrated by this goh, copied whole to TO
    dump(path)                     every row of every table, for a killed run against a clean one
    killed(code, *args)            CODE run in a child that must die of SIGKILL
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path

import db_core
import goh_db
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

AT = 1_795_000_000.0  # 2026-11-18T11:06:40Z
RESULT = "/s/results/goh/20261118T110640Z-c1.json"
SHARE = {"host": "h", "outcome": "ran", "where": "w", "mac_est_s": 1.5, "mac_s": 2.0,
         "rerun_s": 0.5, "saved_s": 1.0, "t0": 9.5, "worker_s": 3.0, "tests": 4}  # fmt: skip
LAND = {
    "item": "A.B", "sha": "s1", "enqueued": "s0", "repo": "/r/x", "mode": "land", "state": "red",
    "attempt": 2, "requested": "2026-11-18T11:00:00+00:00", "started": "2026-11-18T11:01:00+00:00",
    "finished": "2026-11-18T11:06:40+00:00", "k": 3, "k_why": "why", "at": "2026-11-18T11:06:40Z",
    "landed": "s2", "log": "/l/x.log", "why": "exit 1", "stage": "gate", "cause": "gate-test",
    "cause_detail": "d", "pre_s": 12.5, "slot_s": 1.0, "slot_wait_s": 2.5, "load_start": 3.1,
    "load_end": 4.2, "early": True, "recorded": False, "guard": "g1", "guards": ["g1", "g2"],
    "red_logs": ["/l/1", "/l/2"],
    "pre_affected": {"ran": "all", "took": "9 s", "by": "map", "lane_s": 4.5, "tests": 12},
    "batch": {"id": "b1", "items": 2, "gates": 1},
    "pre_run": {"skipped": "no", "wait_s": 1.0, "run_s": 2.0, "limit_s": 3.0, "cpu_s": 4.0,
                "workers": 5, "remote": {**SHARE, "cause": "c", "fresh": True}},
    "red_cause": {"cause": "flake", "rule": "r", "tests": ["t::a", "t::b"]},
    "diffcov_item": {"id": "GOH.X", "said": "filed"},
    "cov_risk": {"said": "s", "head": "h", "base": "b", "lines": 7, "files": {"a.py": "3 lines"},
                 "carry": [{"file": "a.py", "text": "x", "why": "w", "line": 3, "own": True,
                            "measured": False}]},
    "slot_wait_by_class": {"short": 0.5, "land": 1.0, "pre": 0.0, "work": 1.0},
    "flakes": [{"test": "t::a", "entry": "E1", "filed": "yes"}],
    "remote_shares": [SHARE, {**SHARE, "where": "v"}],
    "cap_items": [{"id": "GOH.Y", "file": "a.py", "lines": 480, "said": "filed"}, {"said": "no"}],
    "waived": [
        {"node": "t::a", "kind": "main-red", "reading": 7, "sha": "s", "reason": "r"},
        {"node": "t::b", "kind": "rule-in-flight", "rule": "t::b", "commit": "c s",
         "items": ["A.B", "C.D"], "reason": "r"}],
}  # fmt: skip
STEP = {"name": "verify", "log": "/l/v", "status": "ok", "why": "w", "timings": "/l/t",
        "seconds": 1.5, "idle_pct": 90.0, "load1_end": 2.0, "code": 0,
        "profiles": [{"audit": "a", "bin": "b", "fresh": 1, "reused": 2}]}  # fmt: skip
PINS = {"schema": 2, "tag": "v1", "ruff": "0.1", "sweep": "/s/sweep.json",
        "repos": {"r1": {"status": "committed", "said": "ok"},
                  "r2": {"status": "skipped", "said": "dirty"}}}  # fmt: skip
SHIP = {
    "schema": 1, "result": RESULT, "item": "A.B", "sha": "s1", "status": "failed", "version": "1.2",
    "started": "2026-11-18T11:00:00+00:00", "finished": "2026-11-18T11:06:40+00:00",
    "repo": "/r/goh", "name": "goh", "summary": "s", "remote": "origin", "branch": "main",
    "handoff_sha": "h", "base_tag": "v1", "release": "r", "install": "i", "loopctl": "/x/l.py",
    "failed_step": "suite", "bump": "patch", "verdicts": "v", "allow_diffcov": "no", "pid": 7,
    "pid_stamp": "p", "land_wait_s": 1.5, "own_s": 2.5, "load1": 3.5, "timeout_s": 900,
    "release_pushes": True, "progress": False, "preflight": ["a"], "undo": ["u"], "tail": ["t"],
    "before_clock": {"name": "n", "status": "s", "log": "l", "seconds": 1.0},
    "grace_over": {"over_from": "a", "canary_sha": "c", "why": "w", "count": 1,
                   "repos": {"r1": ["x", "y"]}},
    "bisect": {"base": "b", "culprit": "c", "since": "s", "subject": "j", "fault": "f", "lands": 4,
               "candidate": "d", "flake": True, "tests": ["t::a"],
               "runs": [{"sha": "a", "verdict": "red"}],
               "confirm": [{"sha": "a", "verdict": "red"}, {"sha": "b", "verdict": "green"}]},
    "bisect_step": {"n": 1, "lands": 4, "sha": "a", "recheck": True},
    "diffcov_red": {"base": "b", "said": "s", "faults": {"f1": "filed"},
                    "lines": [{"file": "a", "text": "x", "commit": "c", "land": "l",
                               "subject": "j", "line": 3}]},
    "quiet": {"quiet": True, "min_idle_pct": 70.0, "max_s": 600.0, "idle_pct_first": 50.0,
              "idle_pct": 80.0, "waited_s": 30.0, "load1": 2.0, "readings": 4, "off": "o",
              "unmeasured": "u"},
    "quiet_settings": [70.0, 600.0], "steps": [STEP, {"name": "x", "status": "skipped"}],
    "retried": [STEP],
    "consumer_pins": {"tag": "v1", "ruff": "0.1", "sweep": "/s/sweep.json", "committed": ["r1"],
                      "not_committed": {"r2": "dirty"}},
}  # fmt: skip
SPAWN = {
    "event": "spawn", "time": "2026-11-18T11:06:40+00:00", "session_id": "S", "tool_use_id": "T",
    "model": "opus", "model_id": "opus", "effort": "high", "tags": ["t"], "subagent_type": "x",
    "exempt": False, "description": "A.B: do", "declared": "opus@high", "explored": True,
    "route": "r", "pool": {"said": "tried", "why": "w"}, "agent_type_escape": "e",
    "prompt_sha": "p", "first_line": "f", "apps": ["a", "b"], "profile": "ultra",
    "profile_why": "w", "lane": "ok", "items": ["A.B", "C.D"], "no_item_why": "n",
    "meter": {"read_at": "r", "5h": 1.5, "7d": 2.5, "age_h": 0.5, "ok": True},
}  # fmt: skip
OUTCOME = {
    "event": "outcome", "time": "2026-11-18T11:06:40+00:00", "session_id": "S", "agent_id": "ag",
    "tool_use_id": "T", "agent_type": "x", "status": "landed", "report": "r", "faults": ["f"],
    "tokens": {"cache_creation": 1, "cache_read": 2, "input": 3, "output": 4, "total": 10},
    "tokens_settled": True, "scratch_loose": ["/p"], "ci_direct": ["gh"], "cores": 12,
    "load": [1.5, 2.5, 3.5],
    "apps": {"before": ["a"], "missing": ["b"], "failed": ["c"], "relaunched": ["d"],
             "exits": {"b": {"gone_by": "g", "last_seen": "l"}}},
}  # fmt: skip


def ten(tmp_path: Path) -> db_core.Seam:
    d = tmp_path / "ten"
    d.mkdir()
    for p in goh_db.SEAM.schema.glob("00[01]?_*.sql"):
        if int(p.name[:4]) <= 10:
            shutil.copy(p, d)
    return dataclasses.replace(goh_db.SEAM, schema=d)


def old_file(tmp_path: Path, spawns: int = 3) -> Path:
    """Rows of every kind as code of schema 10 writes them, with what the backfill must repair: a
    land that died in its enqueue, a red with its class in red_causes; and two with a value no
    column takes (a land's `slot_s`, every spawn's `n`)."""
    land = ("INSERT INTO lands(queue, run, item, sha, state, at, body) "
            "VALUES ('/q', ?, ?, ?, ?, ?, ?)")  # fmt: skip
    red = "INSERT INTO red_causes(queue, run, cause, at, body) VALUES ('/q', ?, 'flake', ?, ?)"
    ship = (
        "INSERT INTO ships(result, run, repo, status, body, at) VALUES (?, 'r', 'goh', 'ok', ?, ?)"
    )
    died = {"item": "D.E", "state": "red", "at": AT, "recorded": "2026-11-18T06:06:40-0500"}
    lands = {"1-died": died, "2-red": {"item": "A.B", "state": "red", "sha": "s"},
             "3-old": {"item": "A.B", "pre_s": 1.5}, "4-odd": {"slot_s": "soon"}}  # fmt: skip
    doc = {"repo": "/r/goh", "status": "ok", "steps": [STEP], "result": RESULT,
           "consumer_pins": SHIP["consumer_pins"]}  # fmt: skip
    with db_core.transaction(ten(tmp_path)) as con:
        for run, said in lands.items():
            rec = {"at": LAND["at"], **said}
            row = (run, rec.get("item"), rec.get("sha"), rec.get("state"), AT, json.dumps(rec))
            con.execute(land, row)
        for run in ("1-died", "2-red", "9-gone"):  # 9-gone: its land was archived
            con.execute(red, (run, AT, json.dumps(LAND["red_cause"])))
        con.execute(ship, (RESULT, json.dumps(doc), AT))
        con.execute("INSERT INTO ship_pins(result, run, body, at) VALUES (?, 'r', ?, ?)",
                    (RESULT, json.dumps(PINS), AT))  # fmt: skip
        con.executemany(
            "INSERT INTO agent_spawns(at, event, body) VALUES (?, 'spawn', ?)",
            [
                (
                    AT,
                    json.dumps(
                        {"event": "spawn", "time": SPAWN["time"], "n": i, "tags": ["a", "b"]}
                    ),
                )
                for i in range(spawns)
            ],
        )
    return goh_db.path()


def copied(to: Path) -> Path:
    to.parent.mkdir(parents=True)
    with goh_db.connect() as con, contextlib.closing(sqlite3.connect(to)) as out:
        con.backup(out)
    return to


def dump(p: Path) -> dict[str, list]:
    con = sqlite3.connect(p)
    try:
        names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        skip = {"meta", "schema_version", "sqlite_sequence", "sqlite_stat1"}
        return {t: sorted(con.execute(f"SELECT * FROM {t}"), key=repr)
                for t in names if t not in skip}  # fmt: skip
    finally:
        con.close()


# the Nth write of the run is SIGKILLed with its statements done and its COMMIT not
KILL = """
import contextlib, os, signal, sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
import goh_db, {module} as m
real, left = goh_db.transaction, [int(sys.argv[3])]
@contextlib.contextmanager
def transaction(*a):
    with real(*a) as con:
        yield con
        left[0] -= 1
        if not left[0]: os.kill(os.getpid(), signal.SIGKILL)
goh_db.transaction = transaction
{more}
m.run({call})
"""


def killed(module: str, nth: int, more: str = "", call: str = "") -> None:
    code = KILL.format(module=module, more=more, call=call)
    got = subprocess.run([sys.executable, "-c", code, str(REPO_ROOT / "lib"), str(REPO_ROOT),
                          str(nth)], capture_output=True, text=True, check=False,
                         env=hermetic_env(GOH_DB=os.environ["GOH_DB"]),
                         stdin=subprocess.DEVNULL, timeout=patience(60))  # fmt: skip
    assert got.returncode == -signal.SIGKILL, got.stderr
