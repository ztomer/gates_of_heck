"""The goh gates in flight on this machine: who is running a commit or push gate right now.

THE CLASS (GOH.LOADX, 2026-11-01). tools/load.py refused a second LOAD experiment and nothing
else: a divoom repro ran 15 spinners twice (load average 137 on 16 cores) while zinc's pre-push
gate -- which fails past 30 minutes -- was running beside it. Deliberate load is a cost charged to
every other session's gate, so the thing that makes it has to be able to ask who is paying.

THE REGISTRY is not a new one. Every goh gate already JOINS the bench lock (lib/bench_lock.sh,
`bench_lock_join`): the hook entry points gates/commit_gate.sh ("pre-commit") and
gates/push_gate.sh ("pre-push"), local_ci.sh, and every runner through goh_init -- one file per
gate, named by its pid, in $GOH_BENCH_LOCK_DIR/gates, holding its stage and its repo, removed by
the gate's own cleanup. A nested gate does not join again (GOH_BENCH_JOINED), so one commit is one
entry. This module is the ONE reader of it for anything that is not the bench lock itself.

AN ENTRY IS A GATE IN FLIGHT only while:
  * its pid runs (not dead, not a zombie) -- a hook that was SIGKILLed leaves its file behind, and
    a dead entry is PRUNED here, as the bench lock's own drain prunes it;
  * the process holding that pid started no later than the entry was written -- a pid is not an
    identity, and a number the kernel handed to a later process is not the gate that registered;
  * it is not an ANCESTOR of the reader -- a gate that runs load.py inside its own suite chose
    that load (and this repo's suite, run by its own pre-push, would otherwise refuse itself).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tui.lib import report  # tui.lib, whatever the caller's path

DEFAULT_DIR = "/tmp/gates-of-heck-bench.lock"  # lib/bench_lock.sh's BENCH_LOCK_DIR default
START_SLACK_S = 2.0  # `ps` reports a start to the second; the entry's mtime is finer


@dataclass(frozen=True)
class Gate:
    pid: int
    stage: str
    repo: str
    since: float  # when it registered (the entry's mtime)

    def elapsed(self, now: float | None = None) -> float:
        return max(0.0, (time.time() if now is None else now) - self.since)

    def describe(self, now: float | None = None) -> str:
        repo = self.repo or "an unnamed repo"
        return f"{self.stage} in {repo} (pid {self.pid}, running {_duration(self.elapsed(now))})"


def _duration(seconds: float) -> str:
    whole = int(seconds)
    if whole < 60:
        return f"{whole}s"
    if whole < 3600:
        return f"{whole // 60}m{whole % 60:02d}s"
    return f"{whole // 3600}h{whole % 3600 // 60:02d}m"


def registry_dir() -> Path:
    """Where the gates register: the bench lock's `gates/`, moved by GOH_BENCH_LOCK_DIR."""
    return Path(os.environ.get("GOH_BENCH_LOCK_DIR") or DEFAULT_DIR) / "gates"


def _ps(pid: int, field: str) -> str:
    try:
        out = subprocess.run(
            ["ps", "-o", f"{field}=", "-p", str(pid)],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=10,
            check=False,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return " ".join(out.split())


def _started(pid: int) -> float | None:
    """The process's start, in epoch seconds; None when it cannot be read."""
    text = _ps(pid, "lstart")
    try:
        return time.mktime(time.strptime(text, "%a %b %d %H:%M:%S %Y"))
    except ValueError:
        return None


def _running(pid: int) -> bool:
    """The process runs: `kill -0` alone answers yes for a zombie (as lib/bench_lock.sh says)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    state = _ps(pid, "stat")
    return bool(state) and not state.startswith("Z")


def ancestors(pid: int | None = None) -> set[int]:
    """This process and every ancestor of it, up to (not including) launchd."""
    found: set[int] = set()
    current = os.getpid() if pid is None else pid
    while current > 1 and current not in found:
        found.add(current)
        parent = _ps(current, "ppid")
        if not parent.isdigit():
            break
        current = int(parent)
    return found


def _read(entry: Path) -> tuple[str, str] | None:
    try:
        lines = entry.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    stage = lines[0].strip() if lines else ""
    repo = lines[1].strip() if len(lines) > 1 else ""
    return stage or "a gate", repo


def in_flight(directory: Path | None = None, exclude: set[int] | None = None) -> list[Gate]:
    """The live gates registered in `directory`, oldest first; dead entries are pruned.

    `exclude` defaults to the caller's own ancestry (`ancestors()`); pass an empty set to see
    every live gate.
    """
    root = registry_dir() if directory is None else directory
    skip = ancestors() if exclude is None else exclude
    try:
        entries = [path for path in root.iterdir() if path.name.isdigit()]
    except OSError:
        return []
    gates: list[Gate] = []
    for entry in entries:
        pid = int(entry.name)
        try:
            since = entry.stat().st_mtime
        except OSError:
            continue
        if not _running(pid):
            entry.unlink(missing_ok=True)
            continue
        started = _started(pid)
        if started is not None and started > since + START_SLACK_S:
            entry.unlink(missing_ok=True)  # a recycled pid: the gate that wrote this is gone
            continue
        if pid in skip:
            continue
        read = _read(entry)
        if read is None:
            continue
        gates.append(Gate(pid, read[0], read[1], since))
    return sorted(gates, key=lambda gate: gate.since)


POLL_S = 1.0  # how often a waiting caller looks again
DEFAULT_WAIT_S = 900.0  # `--wait` without `--wait-max`: 15 minutes, never the experiment's --max


@dataclass(frozen=True)
class Hold:
    """What a load would starve, and what the wait for it saw.

    `ahead` are the gates the load waits for: the ones in flight when it asked. `joined` are the
    gates that started WHILE it waited -- named, never waited on (GOH.LOADWAIT, 2026-11-08: a wait
    for a CLEAR machine is a wait behind every gate that will ever start, and on a machine of a
    dozen sessions the registry is rarely empty -- ZN.APPCHECK's `--wait` held 25-50 min and once
    spent its whole budget queueing). A gate that starts after the wait began is no worse off than
    one that starts a second after the experiment does, which nothing could have spared either.
    """

    ahead: list[Gate]
    joined: list[Gate]
    waited_s: float = 0.0

    def __bool__(self) -> bool:
        return bool(self.ahead)


def _key(gate: Gate) -> tuple[int, float]:
    return gate.pid, gate.since  # a pid is not an identity; the entry's mtime with it is


def hold_off(wait: bool, wait_s: float = DEFAULT_WAIT_S, poll_s: float = POLL_S) -> Hold:
    """The gates a load would starve: none when the machine is clear. With `wait`, look again
    every `poll_s` until the gates in flight NOW are done, for at most `wait_s` (its own bound,
    not the experiment's), and say what it waits for."""
    busy = in_flight()
    if not busy or not wait:
        return Hold(busy, [])
    report(
        f"load: waiting (at most {_duration(wait_s)}, --wait-max) for {len(busy)} gate(s) in "
        "flight; gates that start meanwhile are named, not waited on:",
        *(gate.describe() for gate in busy),
    )
    wanted = {_key(gate) for gate in busy}
    began = time.monotonic()
    give_up = began + wait_s
    ahead, joined = busy, []
    while ahead and time.monotonic() < give_up:
        time.sleep(min(poll_s, max(0.0, give_up - time.monotonic())))
        live = in_flight()
        ahead = [gate for gate in live if _key(gate) in wanted]
        joined = [gate for gate in live if _key(gate) not in wanted]
    return Hold(ahead, joined, time.monotonic() - began)


def load_reading(ncpu: int) -> str:
    """The machine's load as the kernel reports it, beside the cores it is shared by."""
    try:
        one, five, fifteen = os.getloadavg()
    except OSError:
        return f"load average unreadable on {ncpu} core(s)"
    return f"load average {one:.1f} / {five:.1f} / {fifteen:.1f} (1/5/15 min) on {ncpu} core(s)"


def names(gates: list[Gate]) -> str:
    return "; ".join(gate.describe() for gate in gates)


def refusal(hold: Hold, count: int, waited: bool, wait_s: float, ncpu: int) -> str:
    """Why a load of `count` spinners does not start beside the gates ahead, and the ways on.
    One message, for the Kare lib's `err`: the head names what is in flight, the rest why."""
    if waited:
        head = (
            f"load: --wait-max of {_duration(wait_s)} expired after {_duration(hold.waited_s)}; "
            f"still waiting for: {names(hold.ahead)}"
        )
    else:
        head = f"load: another session's gate is in flight on this machine: {names(hold.ahead)}"
    lines = [head]
    if hold.joined:
        lines.append(f"started during the wait, not waited on: {names(hold.joined)}")
    lines += [
        f"the machine: {load_reading(ncpu)}",
        (
            f"{count} spinner(s) would starve it, and a starved gate fails on its own clock "
            "(zinc's pre-push, 2026-11-01, at load 137 on 16 cores) with nothing naming the load."
        ),
        (
            "Pass --wait (bounded by --wait-max) to start once the gates ahead are done, or "
            "--despite-gate if loading the machine under them is meant."
        ),
    ]
    return "\n      ".join(lines)
