"""How the empty-scope sweep runs ONE gate: under the house's clocks, never the wall alone.

Not named check_*: check_empty_scope requires every check_* to FAIL over an empty tree.

THE CLASS (GOH.CANARYLOAD, GOH.CANARYFLAKE). The sweep gave each gate TIMEOUT seconds of WALL time.
At load 80-150 a gate needing a few seconds of CPU (Adv360-Pro-ZMK's check_probe_bite: ~10 s at
load 45, nine nested probes) was starved past it; the sweep said "gave NO verdict" and exited 1,
and the release canary read that red step as the consumer's empty-tree gate turned red ("fix the
findings") for a tree that passed on the re-run -- 2026-11-12 CadGoose, 2026-11-14 Adv360 twice.
A starved gate was not slow; the machine was. So the bound is the house's (DD-770,
`stepcmd::gate_bounds`): a STALL (the gate's process group spent no CPU and had nothing
runnable for the nominal bound) and a wall BACKSTOP four times it. No CPU budget of its own: the
house's is the whole machine for the bound (ncpu x it), which the backstop always reaches first.
The one that fires is named in the house's own words (`TIMED OUT after Ns`), with what the gate
spent, so a reader -- and the canary -- can tell a starved run from a finding.
tests/test_empty_scope_starved.py.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import killtree
import pgid_cpu

# The house's words for an expired bound (lib/bounded_run.py, stepcmd.rs); the canary keys on them.
TIMED_OUT = "TIMED OUT after"
WALL_FACTOR = 4  # stepcmd::gate_bounds: the wall backstop is four times the nominal ceiling


@dataclass
class Ran:
    returncode: int
    stdout: str
    stderr: str


def bounds(timeout: int) -> tuple[int, int]:
    """(wall backstop, stall) for a nominal `timeout`, as stepcmd::gate_bounds derives them, so no
    gate that fitted the old wall bound on a quiet machine can trip either."""
    return timeout * WALL_FACTOR, timeout


def _read(fh) -> str:
    fh.seek(0)
    return fh.read().decode("utf-8", "replace")


def run_gate(argv: list[str], cwd: str, env: dict, timeout: int, label: str):
    """(Ran, None) when the gate exited; (None, why) when a bound fired, `why` starting TIMED_OUT.

    Output goes to unlinked files, not pipes: nothing is read while the gate runs, and a full pipe
    would stall it into a timeout of the sweep's own making. Its own session, so the whole group is
    stopped on expiry (killtree: TERM, then KILL) and its CPU is the group's."""
    wall, stall = bounds(timeout)
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as errs:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=errs,
            start_new_session=True,
        )
        pgid = killtree.session_pgid(proc)
        t0 = time.monotonic()
        expired = pgid_cpu.wait_for_exit(proc, pgid, wall, label, cpu_timeout=None, stall=stall)
        if expired is None:
            return Ran(proc.returncode, _read(out), _read(errs)), None
        sample = pgid_cpu.pgid_cpu_and_runnable(pgid)
        spent = sample[0] if sample else 0.0
        killtree.stop_group(pgid, proc=proc)
        proc.wait()
        took = time.monotonic() - t0
        if expired.endswith("(stalled)"):
            return None, f"{expired}, {spent:.1f} CPU-s in {took:.0f}s wall"
        return None, (
            f"{expired} -- starved: {spent:.1f} CPU-s in {took:.0f}s wall, the machine's load, "
            "not this gate's verdict"
        )
