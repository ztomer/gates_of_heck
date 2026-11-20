#!/usr/bin/env python3
"""The consumers' pin PUSHES, out of goh ship: a detached, bounded, recorded sweep
(GOH.PINPUSHGATE).

    lib/pin_sweep.py supervise SWEEP.json   # what start() detaches: wait for the ship, then run
    lib/pin_sweep.py run SWEEP.json         # the pushes, each gate under load.py (not by hand)

WHY. GOH.PINPROPAGATE made goh ship's install push every consumer's pin commit through that
consumer's FULL push gate, inside the ship: at v0.124.0 the install took 2037 s, 2010 s of it those
gates (divoom-control 756, monitor 469, CadGoose 215, token-pool 184), where the commit-only install
of v0.121.0 took 86 s. Owner 2026-11-15: a ship takes 5 minutes, 10 at most. Ruling (option a): the
install still COMMITS each pin; the pushes leave the ship. They are not narrowed: a pin bump changes
the goh the consumer's gate runs, so the consumer's full gate is the release's test against it.

HOW. `scripts/wf_pin.py --bump` commits, then start() writes the sweep's record beside the ship
result (`<result>.sweep.json`) and detaches `supervise` in a session of its own. It waits for the
ship that started it to return (its result leaves `running`, or its worker is gone; bounded by
SHIP_WAIT), then runs the pushes, bounded (BOUND, after which their session is killed), each
consumer's gate through tools/load.py -- the one way load is made: one gate slot per consumer gate,
given back when it ends (GOH.SWEEPSLOTS), so a land interleaves after at most one consumer gate.
Each consumer is pushed ONE at a time (GOH.LOADSOURCE, below), its gate's output kept
(`<result>.sweep.pushes/NAME.log`), and its outcome written to the record as it ends: pushed,
refused (with that log), superseded (left to a newer sweep), or committed and not pushed with why
(an owner's commit ahead, behind its upstream, the budget spent). What the sweep never reached --
killed at its bound, the slot refused -- the supervisor names the same way.

ONE GATE, ONE SWEEP (GOH.LOADSOURCE, 2026-11-19). Sampled each second for 10 min at load 30-70 on
16 cores, a release's sweep was 5000-10300 CPU-s of consumer push gates (54823 s across the day's
nine), run as many at once as the machine had gate slots: two full consumer gates (divoom-control's
eight browser workers among them) beside the land and the lanes, background work holding every
long slot. Now the pushes run one at a time, so a sweep holds at most one slot whatever
GOH_GATE_SLOTS says; its span grows, and a newer release no longer stacks a second sweep on it.
Every sweep is registered (registry_path(), beside the ledger) from start() until it is done: an
older sweep leaves each repo a newer one lists to it (`superseded`, naming the newer record: that
push carries every older pin commit), and the newer one starts its gates only once every older
sweep has ended (bounded by BOUND). The release rate sets how often a consumer's gate runs, never
how many run at once.

WHO READS IT. `goh ship --status RESULT` prints the sweep's rows apart from the install's (lines()),
and its profile rows (`<result>.sweep.timings.jsonl`) in the table as step `sweep`. Every outcome
but pushed also goes to the estate's ledger (ledger_path(), beside the estate audit's state), which
the next `goh wf-pin` listing reads to name each repo still holding an unpushed pin commit with its
log, and the next ship's sweep pushes it: no pin commit is left unpushed in silence.
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
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import consumer_pins
import estate_registry
import killtree
import step_timings

from tui.lib import counted, err, info, ok, step

GOH = Path(__file__).resolve().parent.parent
SHIP_WAIT = 3600  # how long the sweep waits for its ship to return (goh ship's step bound)
BOUND = consumer_pins.BUDGET + 300  # the sweep's deadline: the pushes' budget, then their grace
POLL = 1.0
START_GRACE = 60  # seconds a registered sweep may go without its supervisor's pid (_active)
_lock = threading.Lock()


def now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def sweep_path(result: Path | str) -> Path:
    """The sweep's record beside a goh ship result (or beside wf-pin's own record)."""
    return Path(result).with_suffix(".sweep.json")


def timings_path(sweep: Path | str) -> Path:
    return Path(sweep).with_suffix(".timings.jsonl")


def log_dir(sweep: Path | str) -> Path:
    return Path(sweep).with_suffix(".pushes")


def ledger_path() -> Path:
    """Every repo whose pin commit a sweep left unpushed, with why and its log; beside the estate
    audit's state, so a test's GOH_ESTATE_STATE is its own ledger too."""
    return estate_registry.state_file().with_name("pin_push.json")


def read(path: Path | str) -> dict:
    try:
        body = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return body if isinstance(body, dict) else {}


def _write(path: Path, body: dict) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def update(path: Path, change: Callable[[dict], object]) -> dict:
    """Rewrite the sweep record through CHANGE(body), whole; one writer at a time in this process
    (the supervisor never writes while its run does: it waits on the run)."""
    with _lock:
        body = read(path)
        change(body)
        _write(path, body)
        return body


def registry_path() -> Path:
    """Every sweep started and not yet done, in the order started (GOH.LOADSOURCE)."""
    return estate_registry.state_file().with_name("pin_sweeps.json")


def _active(rec: dict) -> bool:
    """A sweep record still to run or running, with its supervisor alive -- or not yet named, for
    START_GRACE seconds after it was made: start() registers before its supervisor starts, and a
    supervisor that never started must not hold every newer sweep back for its whole bound."""
    if rec.get("status") not in ("pending", "running"):
        return False
    try:
        pid = int(rec.get("pid") or 0)
        if pid <= 0:
            made = dt.datetime.fromisoformat(str(rec.get("created")))
            return (dt.datetime.now(dt.UTC) - made).total_seconds() < START_GRACE
        os.kill(pid, 0)
    except (OSError, ValueError, TypeError):
        return False
    return True


def register(path: Path, repos: list[str]) -> None:
    """Add the sweep at PATH (pushing REPOS) to the registry; sweeps that are done leave it."""

    def change(old: dict) -> dict:
        rows = [r for r in (old.get("sweeps") or [])
                if isinstance(r, dict) and r.get("path") not in (None, str(path))
                and _active(read(r["path"]))]  # fmt: skip
        rows.append({"path": str(path), "repos": sorted(repos)})
        return {"schema": 1, "sweeps": rows}

    estate_registry.update_state(registry_path(), change)


def _around(path: Path) -> tuple[list[dict], list[dict]]:
    """(the registered sweeps before PATH, those after it); both empty when it is not registered."""
    rows = [r for r in (read(registry_path()).get("sweeps") or []) if isinstance(r, dict)]
    keys = [r.get("path") for r in rows]
    if str(path) not in keys:
        return [], []
    i = keys.index(str(path))
    return rows[:i], rows[i + 1 :]


def newer(path: Path, repo: str) -> str | None:
    """The newest sweep started after PATH that lists REPO and has not ended: its push carries this
    sweep's pin commit for REPO, so this sweep leaves REPO to it."""
    return next((r["path"] for r in reversed(_around(path)[1])
                 if repo in (r.get("repos") or []) and _active(read(r["path"]))), None)  # fmt: skip


def older(path: Path) -> str | None:
    """A sweep started before PATH that has not ended (one sweep at a time)."""
    return next((r["path"] for r in _around(path)[0] if _active(read(r["path"]))), None)


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
    return (read(ledger_path()).get("repos") or {}).get(str(Path(repo).resolve()))


def start(result: Path | str, repos: dict[str, Path], budget: float, wait: bool = False) -> Path:
    """Record a sweep of REPOS (where -> path) beside RESULT and start it: detached in its own
    session (the ship's way), or WAIT: in the foreground, for a hand run and the tests."""
    path = sweep_path(result)
    path.parent.mkdir(parents=True, exist_ok=True)
    starter = read(result)
    running = starter.get("status") == "running"
    _write(path, {
        "schema": 1, "status": "pending", "created": now(), "budget": budget,
        "waits_on": str(result) if running else None,
        "waits_on_pid": starter.get("pid") if running else None,
        "log": str(path.with_suffix(".log")),
        "repos": {w: {"repo": str(Path(r).resolve()), "status": "pending"}
                  for w, r in sorted(repos.items())},
    })  # fmt: skip
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("GOH_TIMINGS", "GOH_SHIP_", "GOH_GATE_VERDICTS"))}  # fmt: skip
    # Registered before its supervisor starts, so the supervisor always finds itself there.
    register(path, [str(Path(r).resolve()) for r in repos.values()])
    argv = [sys.executable, str(Path(__file__).resolve()), "supervise", str(path)]
    with path.with_suffix(".log").open("ab") as out:
        child = subprocess.Popen(argv, env=env, stdout=out, stderr=out, stdin=subprocess.DEVNULL,
                                 start_new_session=True, close_fds=True)  # fmt: skip
    update(path, lambda b: b.update(pid=child.pid))
    if wait:
        child.wait()
    return path


def _ship_running(rec: dict) -> bool:
    if not rec.get("waits_on") or read(rec["waits_on"]).get("status") != "running":
        return False
    try:
        pid = int(rec.get("waits_on_pid") or 0)
        if pid <= 0:
            return True  # a running ship whose worker has not said its pid yet
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def supervise(path: Path) -> int:
    """Wait for the ship, run the pushes (each gate under load.py), then name what they never
    reached."""
    rec = read(path)
    deadline = time.monotonic() + SHIP_WAIT
    if _ship_running(rec):
        info(f"pin sweep: waiting for the ship to return ({rec['waits_on']})")
    while _ship_running(rec) and time.monotonic() < deadline:
        time.sleep(POLL)
    # ONE SWEEP AT A TIME (GOH.LOADSOURCE): an older sweep still running leaves this one's repos to
    # it as it reaches them; this one waits for it to end, bounded, before starting any gate.
    late = time.monotonic() + BOUND
    if (first := older(path)) is not None:
        info(f"pin sweep: waiting for the older sweep {first} to end (one sweep at a time)")
    while older(path) is not None and time.monotonic() < late:
        time.sleep(POLL)
    update(path, lambda b: b.update(status="running", started=now()))
    # The sweep's own process holds NO gate slot (GOH.SWEEPSLOTS): each consumer gate takes one
    # through tools/load.py and gives it back when it ends (consumer_pins.push). Under one slot of
    # its own, `width` gates ran nested in it -- three heavy gates while admission counted two.
    # Bounded here as load.py bounded it: at BOUND the whole session is killed.
    argv = [sys.executable, str(Path(__file__).resolve()), "run", str(path)]
    env = {k: v for k, v in os.environ.items() if k != "GOH_GATE_SLOT"}
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, env=env, start_new_session=True)
    try:
        code = proc.wait(timeout=BOUND)
    except subprocess.TimeoutExpired:
        killtree.stop_group(proc.pid, proc=proc)  # TERM first: a killed git leaves its lock
        code = proc.wait()
    why = (f"not pushed: the sweep ended before its push (exit {code}; bound {BOUND} s; "
           f"log {rec.get('log')}); the next ship's sweep pushes it")  # fmt: skip
    left: list[tuple[str, str]] = []

    def finish(b: dict) -> None:
        for where, r in (b.get("repos") or {}).items():
            if r.get("status") in ("pending", "pushing"):
                r.update(status="committed", said=why)
                left.append((where, r["repo"]))
        b.update(status="done", finished=now(), load_exit=code)

    update(path, finish)
    for where, repo in left:
        ledger(repo, where, {"status": "committed", "said": why, "log": rec.get("log"),
                             "at": now(), "sweep": str(path)})  # fmt: skip
    return 0 if code == 0 and not left else 1


def run(path: Path) -> int:
    """The pushes: each repo of the record, one at a time (GOH.LOADSOURCE), each outcome written as
    it ends; a repo a newer sweep lists is left to it."""
    rec = read(path)
    os.environ[step_timings.ENV] = str(timings_path(path))
    os.environ.pop(step_timings.PARENT_ENV, None)
    consumer_pins.budget(float(rec.get("budget", consumer_pins.BUDGET)))
    logs = log_dir(path)
    repos = {w: r["repo"] for w, r in (rec.get("repos") or {}).items()
             if r.get("status") == "pending"}  # fmt: skip

    def log(where: str) -> Path:
        return logs / f"{Path(repos[where]).name}.log"

    def job(where: str) -> tuple[str, str]:
        later = newer(path, repos[where])
        if later is not None:  # its push carries this pin commit: no gate here (GOH.LOADSOURCE)
            return "superseded", f"left to the newer sweep {later}, whose push carries this pin"
        update(path, lambda b: b["repos"][where].update(status="pushing", at=now()))
        return consumer_pins.push(Path(repos[where]), "", log(where))

    def said(where: str, status: str, why: str) -> None:
        entry = {"status": status, "said": why, "at": now()}
        if status == "refused":
            entry["log"] = str(log(where))
        update(path, lambda b: b["repos"][where].update(entry))
        if status == "superseded":  # the newer sweep's outcome is the ledger's, not this one's
            info(f"{where}: {why}")
            return
        ledger(repos[where], where, None if status == "pushed" else {**entry, "sweep": str(path)})
        (ok if status == "pushed" else err)(f"{where}: {why}")

    jobs = [(w, partial(job, w)) for w in sorted(repos)]
    done = consumer_pins.sweep(jobs, said, row="push", ok=("pushed", "superseded"), at_once=1)
    n = sum(v["status"] == "pushed" for v in done.values())
    left = sum(v["status"] == "superseded" for v in done.values())
    info(f"pin sweep: {n} of {counted(len(done), 'consumer')} pushed"
         + (f", {left} left to a newer sweep" if left else ""))  # fmt: skip
    return 0 if n + left == len(done) else 1


def lines(res: dict) -> list[tuple[str, str]]:
    """(mark, text) rows `goh ship --status` prints for the result's sweep, apart from the install:
    one head (pending, running, done) and one row per consumer."""
    where = (res.get("consumer_pins") or {}).get("sweep")
    if not where:
        return []
    rec = read(where)
    if not rec:
        return [("err", f"push sweep: no record at {where}")]
    repos = rec.get("repos") or {}
    n = sum(r.get("status") == "pushed" for r in repos.values())
    state = rec.get("status", "?")
    if state == "done":
        state += f" ({rec.get('started', '?')} to {rec.get('finished', '?')})"
    else:
        state += f" (pid {rec.get('pid')}; log {rec.get('log')})"
    out = [("step", f"push sweep: {state}: {n} of {counted(len(repos), 'consumer')} pushed")]
    for w, r in sorted(repos.items()):
        mark = {"pushed": "ok", "pending": "step", "pushing": "step",
                "superseded": "step"}.get(r.get("status"), "err")  # fmt: skip
        said = r.get("said") or ""
        out.append((mark, f"  push {w}: {r.get('status')}" + (f" ({said})" if said else "")))
    return out


def say(res: dict) -> None:
    for mark, text in lines(res):
        {"ok": ok, "err": err}.get(mark, step)(text)


def main(argv: list[str] | None = None) -> int:
    a = sys.argv[1:] if argv is None else argv
    if len(a) != 2 or a[0] not in ("supervise", "run"):
        err("usage: lib/pin_sweep.py supervise|run SWEEP.json")
        return 2
    return (supervise if a[0] == "supervise" else run)(Path(a[1]))


if __name__ == "__main__":
    sys.exit(main())
