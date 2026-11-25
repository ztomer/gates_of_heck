#!/usr/bin/env python3
"""The consumers' pin PUSHES, out of goh ship: a detached, bounded, recorded sweep, one a day
(GOH.PINPUSHGATE, GOH.PINSWEEP).

    lib/pin_sweep.py supervise SWEEP RUN   # what start() detaches: wait until due, then run
    lib/pin_push.py SWEEP RUN              # the pushes, each gate under load.py (not by hand)

WHY. GOH.PINPROPAGATE made goh ship's install push every consumer's pin commit through that
consumer's FULL push gate, inside the ship: at v0.124.0 the install took 2037 s, 2010 s of it those
gates (divoom-control 756, monitor 469, CadGoose 215, token-pool 184), where the commit-only install
of v0.121.0 took 86 s. Owner 2026-11-15: a ship takes 5 minutes, 10 at most. Ruling (option a): the
install still COMMITS each pin; the pushes leave the ship. They are not narrowed: a pin bump changes
the goh the consumer's gate runs, so the consumer's full gate is the release's test against it.

ONE A DAY (GOH.PINSWEEP, 2026-11-25). A sweep per release held one long gate slot 28.9 h over 62
sweeps in eight days, and 56 of 61 were replaced within 6 h. `scripts/wf_pin.py --bump` still
commits at every release and start() still records a sweep, but its pushes wait until it is due
(lib/pin_schedule.py), and one a newer release replaced meanwhile leaves its repos to it: that push
carries every older pin commit. A repo whose gate would say what it last said is held
(lib/push_hold.py), the repo whose pin has waited longest goes first (the budget starved the
alphabet's end: 42 gates killed at it, 10 of them the last repo's), and a refusal's reason is the
step its log names.

HOW. start() writes the sweep's record (lib/push_sweep_rows.py) and detaches `supervise` in a
session of its own. It waits for the ship that started it to return (bounded by SHIP_WAIT), then
until the sweep is due, then runs the pushes, bounded (BOUND, after which their session is killed),
each consumer's gate through tools/load.py -- the one way load is made: one gate slot per consumer
gate, given back when it ends (GOH.SWEEPSLOTS), so a land interleaves after at most one consumer
gate. ONE sweep runs at a time, and it pushes ONE consumer at a time (GOH.LOADSOURCE, 2026-11-19: a
release's sweep was 5000-10300 CPU-s of consumer gates, run as many at once as the machine had
slots), its gate's output kept (`<result>.sweep.pushes/NAME.log`). Each outcome is written as it
ends: pushed, refused (with that log), held, superseded, or committed and not pushed with why (an
owner's commit ahead, behind its upstream, the budget spent). What the sweep never reached -- killed
at its bound, the slot refused -- the supervisor names the same way; what a supervisor that died
left, settle() does, at the next sweep, ship or listing.

WHO READS IT. `goh ship --status RESULT` prints the sweep's rows apart from the install's (lines()),
and its profile rows (`<result>.sweep.timings.jsonl`) in the table as step `sweep`. Every outcome
but pushed also goes to the estate's ledger (ledger_path(), beside the estate audit's state), which
the next `goh wf-pin` listing reads to name each repo still holding an unpushed pin commit with its
log, and the next sweep pushes it: no pin commit is left unpushed in silence.
Pinned by tests/test_pin_sweep.py and tests/test_pin_sweep_daily.py.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # outside any checkout: nothing to re-run
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import datetime as dt
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.append(str(Path(__file__).resolve().parent.parent / "gates"))
import _head_cache
import consumer_pins
import estate_registry
import goh_db
import killtree
import pid_owner
import pin_schedule
import proc_deadline
import push_sweep_rows as rows
import ship_records
import small_stores

from tui.lib import counted, err, info, ok, step

GOH = Path(__file__).resolve().parent.parent
SHIP_WAIT = 3600  # how long the sweep waits for its ship to return (goh ship's step bound)
BOUND = consumer_pins.BUDGET + 300  # the sweep's deadline: the pushes' budget, then their grace
POLL = 1.0
DUE_POLL = 30.0  # between two looks at a sweep not due yet
START_GRACE = 60  # seconds a registered sweep may go without its supervisor's pid (active)
read = rows.read


def now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def _when(at: float | None) -> str:
    return dt.datetime.fromtimestamp(at, dt.UTC).isoformat(timespec="seconds") if at else "?"


def sweep_path(result: Path | str) -> Path:
    """The name of the sweep's record beside a goh ship result (or wf-pin's own): never a file."""
    return Path(result).with_suffix(".sweep.json")


def timings_path(sweep: Path | str) -> Path:
    return Path(sweep).with_suffix(".timings.jsonl")


def log_dir(sweep: Path | str) -> Path:
    return Path(sweep).with_suffix(".pushes")


def ledger_path() -> Path:
    """Every repo whose pin commit a sweep left unpushed, with why and its log; beside the estate
    audit's state, so a test's GOH_ESTATE_STATE is its own ledger too."""
    return small_stores.named(estate_registry.state_file(), "pin_push")


def active(rec: dict, at: float | None = None) -> bool:
    """A sweep still to run or running, its supervisor or its run alive -- or no supervisor named
    yet, for START_GRACE seconds after it was made: start() writes the record first, and one whose
    supervisor never started must not hold every newer sweep back."""
    if rec.get("status") not in rows.LIVE:
        return False
    named = [p for p in (rec.get("supervisor"), rec.get("runner")) if p]
    if not named:
        return (time.time() if at is None else at) - rec["created"] < START_GRACE
    return any(map(pid_owner.alive_line, named))


def settle(at: float | None = None) -> list[str]:
    """Each sweep whose processes are gone (a reboot, a kill) ended as failed, what it never reached
    left to the next one and named in the ledger: no record says pending or running for good."""
    done = []
    for rec in rows.live():
        if active(rec, at):
            continue
        why = (f"not pushed: the sweep's supervisor ended before its push (log {rec.get('log')}); "
               "the next sweep pushes it")  # fmt: skip
        with goh_db.refusable():  # a copy that may not write it leaves it to one that may
            left = rows.fail(rec["path"], rec["run"], why, time.time())
            _name_left(rec["path"], left, why, rec.get("log"))
            done.append(rec["path"])
    return done


def _name_left(path: Path | str, left: list[tuple[str, str]], why: str, log: str | None) -> None:
    for where, repo in left:
        ledger(repo, where, {"status": "committed", "said": why, "log": log, "at": now(),
                             "sweep": str(path)})  # fmt: skip


def ledger(repo: str, where: str, entry: dict | None) -> None:
    """REPO's line in the ledger: ENTRY (an unpushed pin commit), or none (pushed)."""

    def change(old: dict) -> dict:
        repos = dict(old.get("repos") or {})
        if entry is None:
            repos.pop(repo, None)
        else:
            repos[repo] = {"where": where, **entry}
        return {"schema": 1, "repos": repos}

    estate_registry.update_state(ledger_path(), change)


def unpushed(repo: Path) -> dict | None:
    """The ledger's line for REPO, or None."""
    return (estate_registry.read_state(ledger_path()).get("repos") or {}).get(
        str(Path(repo).resolve())
    )


def place(result: Path | str) -> Path:
    """The name a sweep beside RESULT starts under: one a live sweep holds (a hand run's, which
    has no result of its own) is numbered, never taken from it."""
    path = sweep_path(result)
    if not active(read(path)):
        return path
    return sweep_path(Path(result).with_suffix(f".{time.time_ns()}.json"))


def start(path: Path, result: Path | str, repos: dict[str, Path], budget: float,
          schedule: tuple[float, float], wait: bool = False) -> Path:  # fmt: skip
    """Record a sweep of REPOS (where -> path) as PATH (place(RESULT)) and start it on SCHEDULE
    (lib/pin_schedule.py settings): detached in its own session (the ship's way), or WAIT: due now
    and in the foreground, for a hand run and the tests."""
    settle()
    path.parent.mkdir(parents=True, exist_ok=True)
    starter = ship_records.get(result) or {}
    running = starter.get("status") == "running"
    made = time.time()
    run_id = f"{made:.6f}-{os.getpid()}"
    log = path.with_suffix(".log")
    rows.put(path, {
        "run": run_id, "status": "pending", "created": made, "budget": budget,
        "quiet": 0.0 if wait else schedule[0], "every": schedule[1], "log": str(log),
        "ship": str(result) if running else None,
        "ship_worker": str(starter.get("pid_stamp") or starter.get("pid") or "") or None
        if running else None,
        "repos": {w: {"repo": str(Path(r).resolve()), "status": "pending"}
                  for w, r in sorted(repos.items())},
    })  # fmt: skip
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("GOH_TIMINGS", "GOH_SHIP_", "GOH_GATE_VERDICTS"))}  # fmt: skip
    argv = [sys.executable, _me(), "supervise", str(path), run_id]
    with log.open("ab") as out:
        child = subprocess.Popen(argv, env=env, stdout=out, stderr=out, stdin=subprocess.DEVNULL,
                                 start_new_session=True, close_fds=True)  # fmt: skip
    # With its start time (GOH.STRAYSIGNAL2): `active` asks whether THIS supervisor runs, and a
    # bare pid reused once it ended read a stranger as the sweep still going.
    rows.update(path, supervisor=pid_owner.stamp_line(child.pid) or str(child.pid))
    if wait:
        child.wait()
    return path


def _ship_running(rec: dict) -> bool:
    if not rec.get("ship") or (ship_records.get(rec["ship"]) or {}).get("status") != "running":
        return False
    # a running ship whose worker has not said its pid yet runs
    return not rec.get("ship_worker") or pid_owner.alive_line(rec["ship_worker"])


def _me(name: str = Path(__file__).name) -> str:
    """This file, or the one NAMEd beside it, in the checkout: run from there it is HEAD's copy,
    exported when it is gone."""
    here = Path(os.environ.get("GOH_LIVE_ROOT") or GOH) / "lib" / name
    return str(here if here.is_file() else Path(__file__).resolve().parent / name)


def _nap(seconds: float) -> None:
    """A supervisor's sleep. Its code is an export pruned an hour after its last use, the schema
    files with it, and its wait is hours: each nap is a use (gates/_head_cache.py)."""
    time.sleep(seconds)
    _head_cache.use(str(GOH))


def wait_due(path: Path, run_id: str, clock: Callable[[], float] = time.time,
             sleep: Callable[[float], None] = _nap) -> bool:  # fmt: skip
    """Wait until PATH's sweep is due; False as soon as it is not to run: its record is another
    start's, or a newer sweep took every repo of it."""
    told = 0.0
    while True:
        rec = read(path)
        if rec.get("run") != run_id or rec.get("status") != "pending":
            return False
        if not rows.untaken(path):
            return True  # begin() writes what took them
        at = pin_schedule.due(rec["created"], rec["quiet"], rec["every"], rows.anchor(path))
        if at <= clock():
            return True
        if at != told:
            told = at
            rows.update(path, due=at)
            info(f"pin sweep: due {_when(at)} (no release since for {rec['quiet']:.0f} s, or "
                 f"{rec['every']:.0f} s since the last sweep)")  # fmt: skip
        sleep(min(DUE_POLL, max(0.05, at - clock())))


def _begin(path: Path, run_id: str, sleep: Callable[[float], None] = _nap) -> bool:
    """PATH's sweep taken to running once no other runs (bounded); False when it is not to run."""
    late, told = time.monotonic() + BOUND, ""
    while True:
        got, other = rows.begin(path, run_id, time.time())
        if got == "superseded":
            info(f"pin sweep: left to the newer sweep {other}")
        if got != "busy":
            return got == "go"
        settle()  # an older sweep whose processes are gone is no reason to wait
        if other != told:
            told = other
            info(f"pin sweep: waiting for the running sweep {other} to end (one sweep at a time)")
        if time.monotonic() >= late:
            why = f"not pushed: the sweep {other} ran past {BOUND} s; the next sweep pushes it"
            _name_left(path, rows.fail(path, run_id, why, time.time()), why, read(path).get("log"))
            return False
        sleep(POLL)


def supervise(path: Path, run_id: str) -> int:
    """Wait for the ship and until the sweep is due, run the pushes (each gate under load.py), then
    name what they never reached."""
    rec = read(path)
    deadline = time.monotonic() + SHIP_WAIT
    if _ship_running(rec):
        info(f"pin sweep: waiting for the ship to return ({rec['ship']})")
    while _ship_running(rec) and time.monotonic() < deadline:
        _nap(POLL)
    if not wait_due(path, run_id) or not _begin(path, run_id):
        return 0
    # The sweep's own process holds NO gate slot (GOH.SWEEPSLOTS): each consumer gate takes one
    # through tools/load.py and gives it back when it ends (consumer_pins.push). Under one slot of
    # its own, `width` gates ran nested in it -- three heavy gates while admission counted two.
    # Bounded here as load.py bounded it: at BOUND the whole session is killed.
    argv = [sys.executable, _me("pin_push.py"), str(path), run_id]
    env = {k: v for k, v in os.environ.items() if k != "GOH_GATE_SLOT"}
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, env=env, start_new_session=True)
    rows.update(path, runner=pid_owner.stamp_line(proc.pid) or str(proc.pid))
    code = proc_deadline.wait(proc, BOUND)
    if code is None:
        killtree.stop_group(proc.pid, proc=proc)  # TERM first: a killed git leaves its lock
        code = proc.wait()
    why = (f"not pushed: the sweep ended before its push (exit {code}; bound {BOUND} s; "
           f"log {rec.get('log')}); the next sweep pushes it")  # fmt: skip
    left = rows.fail(path, run_id, why, time.time(), "done", code)
    _name_left(path, left, why, rec.get("log"))
    return 0 if code == 0 and not left else 1


def _state(rec: dict) -> str:
    status = rec["status"]
    if status in ("done", "failed"):
        return f"{status} ({_when(rec.get('started'))} to {_when(rec.get('finished'))})"
    due = f"due {_when(rec['due'])}; " if status == "pending" and rec.get("due") else ""
    return f"{status} ({due}pid {(rec.get('supervisor') or '?').split()[0]}; log {rec.get('log')})"


def lines(res: dict) -> list[tuple[str, str]]:
    """(mark, text) rows `goh ship --status` prints for the result's sweep, apart from the install:
    one head (pending, running, done, failed) and one row per consumer."""
    where = (res.get("consumer_pins") or {}).get("sweep")
    if not where:
        return []
    rec = read(where)
    if not rec:
        return [("err", f"push sweep: no record of {where}")]
    repos = rec["repos"]
    n = sum(r["status"] == "pushed" for r in repos.values())
    head = f"push sweep: {_state(rec)}: {n} of {counted(len(repos), 'consumer')} pushed"
    out = [("step", head)]
    for w, r in sorted(repos.items()):
        mark = {"pushed": "ok", "pending": "step", "pushing": "step",
                "superseded": "step"}.get(r["status"], "err")  # fmt: skip
        waited = f" [{r['slot_wait_s']:.0f} s for a gate slot]" if r.get("slot_wait_s") else ""
        said = r.get("said") or ""
        out.append((mark, f"  push {w}: {r['status']}" + (f" ({said})" if said else "") + waited))
    return out


def say(res: dict) -> None:
    settle()
    for mark, text in lines(res):
        {"ok": ok, "err": err}.get(mark, step)(text)


def main(argv: list[str] | None = None) -> int:
    a = sys.argv[1:] if argv is None else argv
    if len(a) != 3 or a[0] != "supervise":
        err("usage: lib/pin_sweep.py supervise SWEEP RUN")
        return 2
    return supervise(Path(a[1]), a[2])


if __name__ == "__main__":
    sys.exit(main())
