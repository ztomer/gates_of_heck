"""The load runs that are burning cores NOW, each named with its why and its owner's directory.

GOH.LOADWHY (2026-11-19). A machine at load 110 on 16 cores, and nobody could say which of the
sessions had made it: twelve spinners sat beside a test run that needed none, and the only way to
find them was `pgrep -f release-` and a registry read by hand. `tools/load.py` now records, in the
registry it holds while its spinners run, WHY it made the load (`--why`, required for N>0) and the
directory its owner ran in; this module reads those records back for the two places a session
looks: `goh agent-stalls` and the SessionStart advisories.

A run is LIVE when its registry names spinners and its owner is not gone (a dead owner's registry is
the backstop's to reap, lib/load_reap.py; naming it here would call residue an experiment). A
registry written before the why existed says so rather than hiding the run.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import gates_in_flight
import load_registry as reg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tui.lib import counted

MIN_WHY_WORDS = 3  # a sentence naming the defect, not "test" or "perf"


def why_refusal(count: int, why: str | None) -> str | None:
    """Why spinners are refused for want of a reason, or None (GOH.LOADWHY). N=0 makes no load."""
    if count == 0 or len((why or "").split()) >= MIN_WHY_WORDS:
        return None
    return (
        f"{counted(count, 'spinner')} need `--why <the timing defect this load reproduces>` "
        f"(at least {MIN_WHY_WORDS} words; it is recorded and named while the run is live). To "
        "run a test, a gate or a build you want admitted without making load, pass N=0: "
        "`tools/load.py --wait 0 -- <command>` makes no spinner and needs no why."
    )


def describe(data: dict) -> str:
    pids = data.get("pids")
    count = len(pids) if isinstance(pids, list) else 0
    command = data.get("command")
    shown = " ".join(str(part) for part in command) if isinstance(command, list) else ""
    owner = f"owner pid {data.get('owner_pid')} (started {data.get('owner_start')})"
    why = f", why: {data['why']}" if data.get("why") else ""
    return f"{owner}, {counted(count, 'spinner')}{why}, command: {shown}"


def status(directory: Path) -> int:
    """`load.py --status`: the gate slots held, then who holds the experiment slot."""
    path = reg.registry_path(directory)
    for gate in gates_in_flight.slot_holders(exclude=set()):
        print(f"load: a gate holds a slot: {gate.describe()}")
    if not path.exists():
        print(f"load: no experiment holds {path}")
        return 0
    data = reg.read(path)
    if data is None:
        print(f"load: {path} is unreadable; remove it by hand if no experiment is running")
        return 1
    print(f"load: {path} holds an experiment: {describe(data)}")
    return 0


def _minutes(seconds: float) -> str:
    return f"{max(0, int(seconds // 60))}m"


def lines(directory: Path | None = None, now: float | None = None) -> list[str]:
    """One line per live spinner run in the load directory: how many, how long, who, why."""
    clock = time.time() if now is None else now
    out: list[str] = []
    for path in reg.registry_files(directory):
        data = reg.read(path)
        if not data or reg.owner_gone(data):
            continue
        pids = data.get("pids")
        count = len(pids) if isinstance(pids, list) else 0
        if not count:
            continue
        try:
            age = clock - float(data.get("started_at", clock))
        except (TypeError, ValueError):
            age = 0.0
        why = str(data.get("why") or "").strip() or "no why recorded"
        where = data.get("cwd") or "unknown directory"
        command = data.get("command")
        shown = " ".join(str(part) for part in command) if isinstance(command, list) else ""
        out.append(
            f"⚠ load: {counted(count, 'spinner')} for {_minutes(age)}, owner pid "
            f"{data.get('owner_pid')} in {where} -- why: {why} -- running: {shown[:100]}"
        )
    return out
