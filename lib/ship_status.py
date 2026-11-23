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
import pid_owner
import pin_sweep
import ship_profile
import ship_quiet

from tui.lib import err, info, ok, step

TAIL = 40  # a failed step's last log lines, carried in the result
profile = ship_profile.profile  # each step's GOH_TIMINGS file (tools/ship.py's one name for it)
ticks = ship_quiet.ticks  # the tick counters a step's `idle_pct` is read between (GOH.SHIPQUIET)


def quiet_settings(cfg: dict[str, str]) -> list[float]:
    """[minimum idle %, maximum wait s] from the ship's keys; a bad one is a Refused, named."""
    from ship_version import Refused

    try:
        return list(ship_quiet.settings(cfg))
    except ValueError as e:
        raise Refused(str(e)) from None


def quiet_wait(res: dict, resume: bool = False) -> None:
    """Before the land lock, the bounded wait for a quiet host, recorded in `quiet` (GOH.SHIPQUIET);
    a resumed ship (it measures nothing more) and a result planned before the keys wait for none."""
    if resume:
        return
    low, most = res.get("quiet_settings") or (ship_quiet.DEFAULT_IDLE_PCT, 0.0)
    res["quiet"] = ship_quiet.wait(low, most, poll_s=ship_quiet.poll_s(), say=info)
    save(res)


def step_load(start: tuple[int, int] | None) -> dict:
    """A step entry's load: the CPU idle over its run since START's ticks, and load1 at its end."""
    return {"idle_pct": ship_quiet.idle_since(start), "load1_end": round(os.getloadavg()[0], 2)}


def record_ship_timing(res: dict) -> None:
    """The finished ship's rows in the timing ledger (`goh budget`: `<repo> ship` and `<repo>
    release`, GOH.GATEBUDGET); a fault is said, never raised: the instrument never fails a ship."""
    try:
        import budget  # lib/, on the path through tools/ship.py, the one importer

        budget.record_ship(res)
    except (OSError, ValueError, KeyError, TypeError) as e:
        info(f"goh ship: the timing ledger was not written: {e}")


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


ABANDONED = "abandoned"  # the failed_step of a result whose worker died saying running


def _gone(res: dict) -> bool:
    """True for a result that says running (or bisecting) whose worker is gone, by its start time
    (GOH.STRAYSIGNAL2: a worker's pid reused since reads as gone)."""
    if not isinstance(res, dict) or res.get("status") not in ("running", "bisecting"):
        return False
    pid = res.get("pid")
    return bool(pid) and not pid_owner.alive_line(str(res.get("pid_stamp") or pid))


def settle(path: Path) -> bool:
    """Write the terminal record a killed worker could not (GOH.SHIPEARLY): a result that says
    running while its worker is gone becomes `failed` at ABANDONED, naming the pid, so no record
    stays status-less or running forever (two Finance_zinc ships of 2026-11-17 said running six
    days after their workers died). True when it rewrote PATH."""
    try:
        res = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not _gone(res):
        return False
    done = [s["name"] for s in res.get("steps", []) if isinstance(s, dict) and "name" in s]
    said = (f"the worker (pid {res['pid']}) is gone and the result said running; "
            f"steps done: {', '.join(done) or 'none'}")  # fmt: skip
    res.update(status="failed", failed_step=ABANDONED, tail=[said])
    res["result"] = str(path)
    save(res)
    return True


def settle_all(directory: Path, say=None) -> list[Path]:
    """`settle` over every ship result in DIRECTORY (a repo's ship records): the ones rewritten,
    each said through SAY when given."""
    done = [p for p in sorted(directory.glob("*.json")) if settle(p)]
    for p in done if say else ():
        say(f"goh ship: an earlier ship's worker is gone; recorded it as failed: {p}")
    return done


def status(result: str) -> int:
    """Print the marks a result holds. 0 shipped, 1 failed, 2 unreadable, 3 running, 4 the worker
    is gone (killed, or the machine slept through a reboot) while the result still says running."""
    try:
        res = json.loads(Path(result).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        err(f"goh ship: cannot read {result}: {e}")
        return 2
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
    if res["status"] in ("running", "bisecting"):  # a bisecting worker runs on (GOH.SHIPBISECTCOV)
        pid = res.get("pid")
        # By its start time (GOH.STRAYSIGNAL2): a worker's pid reused since reads as gone.
        if not pid or pid_owner.alive_line(str(res.get("pid_stamp") or pid)):
            info(f"running: {result}")
            return 3
        err(f"the worker (pid {pid}) is gone and the result says running: {result}")
        if settle(Path(result)):
            info(f"recorded it as failed at {ABANDONED}: {result}")
        return 4
    return 0 if res["status"] == "ok" else 1
