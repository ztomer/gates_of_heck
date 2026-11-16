"""`goh ship --status RESULT`: the marks a ship result holds; and the result's writing and a step
log's tail (split from tools/ship.py at its 500-line cap)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import canary_grace  # tools/ship.py, the one importer, puts lib/ on sys.path
import consumer_pins
import pin_sweep
import ship_profile

from tui.lib import err, info, ok, step

TAIL = 40  # a failed step's last log lines, carried in the result
profile = ship_profile.profile  # each step's GOH_TIMINGS file (tools/ship.py's one name for it)


def save(res: dict) -> None:
    """Write RES to its result path whole (a temp file renamed over it)."""
    path = Path(res["result"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def carry(res: dict) -> None:
    """What a step left for the result: the release canary's grace-end finding (GOH.CANARYGRACE)
    and, once goh's install step committed them, each consumer's pins and the record of the sweep
    that pushes them after the ship (GOH.PINPROPAGATE, GOH.PINPUSHGATE)."""
    canary_grace.carry(res)
    consumer_pins.carry(res)


def tail(log: Path) -> list[str]:
    try:
        return log.read_text(encoding="utf-8", errors="replace").splitlines()[-TAIL:]
    except OSError:
        return []


def status(result: str) -> int:
    """Print the marks a result holds. 0 shipped, 1 failed, 2 unreadable, 3 running, 4 the worker
    is gone (killed, or the machine slept through a reboot) while the result still says running."""
    try:
        res = json.loads(Path(result).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        err(f"goh ship: cannot read {result}: {e}")
        return 2
    if pre := res.get("before_clock"):  # the land's loaded run, apart (GOH.SHIPLOADEDWAIT)
        (ok if pre["status"] == "ok" else err)(
            f"before the clock: {pre['name']}: {pre['status']} ({pre['seconds']} s waited for the "
            f"land's run, no step's time; {pre['log']})")  # fmt: skip
    for s in res["steps"]:
        mark = {"ok": ok, "skipped": step}.get(s["status"], err)
        mark(f"{s['name']}: {s['status']}" + (f" ({s['why']})" if s.get("why") else ""))
    for said in (canary_grace.line(res), consumer_pins.line(res)):
        if said:
            step(said)
    pin_sweep.say(res)  # the consumers' pushes, after the ship and apart from its install
    if prof := ship_profile.table(res):  # where the time went (GOH.SHIPPROFILE)
        step(prof[0])
        print("\n".join(prof[1:]))
    if res["status"] == "running":
        pid = res.get("pid")
        try:
            if pid:
                os.kill(pid, 0)
            info(f"running: {result}")
            return 3
        except OSError:
            err(f"the worker (pid {pid}) is gone and the result says running: {result}")
            return 4
    return 0 if res["status"] == "ok" else 1
