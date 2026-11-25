"""Wait for a child within a deadline, without `Popen.wait(timeout=)` (GOH.PRESTEPMSGS).

THE FAULT (2026-11-21/22). CPython 3.15's `Popen._wait` on macOS waits on a kqueue and, when the
EXIT event arrives but `waitpid(WNOHANG)` still says 0 (a process that is exiting is not yet
reapable, seconds under load 30-70), falls back to a polling loop with `endtime -= elapsed`: the
time already waited is taken off the deadline a second time, so a child that ends after more than
HALF of its timeout is a `TimeoutExpired` at the moment it ends. Two enqueue pre-steps were
"killed by their deadline" at 879 s of 1500 s and 804 s of 1560 s, their green verdict already
kept, their real exit status thrown away. `Popen.poll()` (one `waitpid(WNOHANG)`) has no such
path, so the house waits by it: `wait(proc, seconds)`. Pinned by tests/test_prestep_outcome.py.

WAIT FOR A CHILD WITHIN A DEADLINE (GOH.PRESTEPMSGS): `wait(proc, seconds)` polls `Popen.poll()`
and gives the status, or None when the child still runs at the deadline (one last ask first: the
exit wins). CPython 3.15's `Popen.wait(timeout=)` on macOS counts the time already waited twice
when the child's exit event precedes its being reapable, so a child ending after half its deadline
read as expired, status lost (two enqueue pre-steps, 879 s of 1500 s, 804 s of 1560 s). `stretch()`
times the drift guard's fixed loop on the caller's thread: what the QoS clamp and the run queue
cost it, which a deadline stretches by (GOH.CHECKGIVEUP300). Used by `tools/load.py`,
`pre_deadline.py`, `bounded_run.py`, `landq_spec.py`, `land_check.py`, `pin_sweep.py` and the MCP
wire check; `tests/test_prestep_outcome.py` pins every other timed wait to a grace or a poll.
"""

from __future__ import annotations

import subprocess
import time

POLL_S = 0.05  # a child's end is seen within this; far below anything a deadline bounds


def wait(proc: subprocess.Popen, seconds: float) -> int | None:
    """The child's exit status, or None when it still runs after `seconds` (monotonic).

    The exit wins over the clock: at the deadline the child is asked once more, so one that ended
    in the last instant is not called late."""
    end = time.monotonic() + max(0.0, seconds)
    while (status := proc.poll()) is None:
        left = end - time.monotonic()
        if left <= 0:
            return proc.poll()
        time.sleep(min(POLL_S, left))
    return status


def expired(max_s: float, data: dict, pid: int) -> str:
    """tools/load.py's one line for a deadline: the seconds the command RAN and the seconds it
    WAITED for its slot, apart (the deadline counts the run: `data` is the run's registry record,
    `started_at` its admission and `waited_s` the wait before it)."""
    return (
        f"load: deadline of {max_s:g}s expired: ran {time.time() - data['started_at']:.0f} s "
        f"(waited {data.get('waited_s', 0):.0f} s for the slot): the command (pid {pid}), its "
        "process tree and the spinners were killed; pass --max to give it longer"
    )


# THE SLOWDOWN A DEADLINE STRETCHES BY (GOH.CHECKGIVEUP300). A QoS clamp is invisible to the run
# queue: under `taskpolicy -c utility` a thread ran x25-35 where the load average said x3.2
# (GOH.SUITEDRIFTBUILD) and `goh land --check`'s guard jobs gave up at a fixed 300 s. goh sets
# that clamp only when asked since GOH.QOSCLAMP; a caller may still run under one, and the run
# queue costs a thread too. `stretch` times one fixed loop (REFERENCE_CPU_S of one
# core of the owner's box; tests/_drift_guard.py's probe is this same loop) on the caller's
# thread, the median of STRETCH_SAMPLES, never below 1: what the clamp and the run queue cost now.
REFERENCE_CPU_S = 0.016
STRETCH_SAMPLES = 5


def spin() -> int:
    """The fixed loop both probes time: REFERENCE_CPU_S of one core of the owner's box."""
    x = 0
    for i in range(400_000):
        x += i * i % 7
    return x


def _timed_spin() -> float:
    t0 = time.perf_counter()
    spin()
    return time.perf_counter() - t0


def stretch() -> float:
    """How many times longer than a quiet core of the owner's box this thread runs CPU work now."""
    walls = sorted(_timed_spin() for _ in range(STRETCH_SAMPLES))
    return max(1.0, walls[len(walls) // 2] / REFERENCE_CPU_S)
