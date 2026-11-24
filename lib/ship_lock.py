"""lib/ship_lock.py -- the repo's `.ship.lock` names its holder, so a second goh ship refused by
it says WHO holds it and in what state, before anything starts (GOH.SHIPBISECTCOV).

2026-11-22: a ship's worker bisected for 50 min holding the lock while its record said `failed`,
and the next ship failed "at push" with only "another goh ship of gates_of_heck holds .ship.lock".
The holder now writes {"result", "pid"} into the lock file once it holds it (`claim`); `holder`
try-locks the file and, held, reads that record's state: its status and, bisecting, its step.

Pinned by tests/test_ship_red_cause.py.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import IO

import lock_order
import ship_records


def claim(held: IO[str], result: str) -> None:
    """HELD (the lock file, flocked by this process) names this worker and its record."""
    held.seek(0)
    held.truncate()
    held.write(json.dumps({"result": result, "pid": os.getpid()}))
    held.flush()


def take(held: IO[str], result: str) -> str:
    """Try-lock HELD (never waits) and claim it for RESULT: "", or the holder's line when held."""
    try:
        lock_order.flock(held, "ship", wait=False)
    except OSError:
        held.seek(0)
        return "another goh ship holds it: " + state(held.read())
    claim(held, result)
    return ""


def holder(lock: Path) -> str:
    """ "" when LOCK is free (or absent), else the holder's record, pid and state, one line."""
    if not lock.is_file():
        return ""
    with lock.open("a+") as fd:
        try:
            lock_order.flock(fd, "ship", wait=False)
        except OSError:
            fd.seek(0)
            return state(fd.read())
        lock_order.unlock(fd)
    return ""


def state(said: str) -> str:
    try:
        who = json.loads(said)
    except ValueError:
        return "a holder that names no record (an older goh ship)"
    result = str(who.get("result", ""))
    rec: dict = ship_records.get(result) or {} if result else {}
    status = rec.get("status", "no record")
    line = f"{result} (pid {who.get('pid')}, {status}"
    if probe := rec.get("bisect_step"):
        sha = str(probe.get("sha", ""))[:8]
        line += f", bisect step {probe.get('n')} of {probe.get('lands')} lands on {sha}"
    elif done := [s.get("name") for s in rec.get("steps", [])]:
        line += f", after {done[-1]}"
    return line + ")"
