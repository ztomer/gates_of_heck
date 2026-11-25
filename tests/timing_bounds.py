"""A wall-clock upper bound in a test: placed by the FAILURE it rules out, stretched by the box.

A bound picked as "a few times what the pass takes on my box" flakes on a box other sessions
load, and refuses pushes for reasons unrelated to the change pushed: 15 s -> 63.8 s at load 38,
a speedup of 2.98 against 3.0 at load 55 (2026-10-08). The failure the test exists to rule out --
a timeout that never fires, a sleep run to its end -- takes `failure_s` or more at ANY load, so
the bound is FAILURE_SHARE of it and reads no load. It used to be the pass stretched by a model of
the load, and no model held: a 1.5 s start read 9.7 s at load 40 where the run queue said x2.5
(GOH.TEMPLEAKSETUP), so load alone turned a pass red far from any failure.
A test whose failure is not well clear of its pass (FAILURE_SHARE x failure_s < CLEAR x expected_s)
is a design error, refused here: lengthen the failure, or order two events (a child that waits for
a file the test writes) and time nothing. How long a phase may take at all is the drift guard's.

A GIVE-UP deadline ("wait until X appears, then assert") has no false pass to guard against: a
longer wait can only cost time on a failure. `patience(seconds)` stretches it by the load; a fixed
10 s wait for a fixture's file failed a release gate at load 117 (2026-10-08). Wait with `GiveUp`,
which re-reads the load at every check: a deadline computed once (`now + patience(10)`) is
stretched by the load when the wait BEGAN, and a push that loaded the box mid-wait failed two
bench_lock tests that pass alone (2026-10-10).

A bound that DISCRIMINATES two passes (parallel vs serial) is not this: derive it from a control
measured in the same run (tests/test_session_bench.py, test_goh_prefetch.py).
`tests/test_timing_bounds.py` refuses a literal upper bound on an elapsed time anywhere in tests/.
"""

from __future__ import annotations

import time

from _drift_guard import load_factor, thread_stretch

# THE QOS CLAMP (GOH.SUITEDRIFTBUILD). A lane or work gate's suite runs under `taskpolicy -c
# utility`, which the run queue cannot see: x25-35 on a clamped thread where `load_factor` said
# x3.2. Every give-up here stretches by the larger of the run queue and the stretch last measured
# on this process's own thread (`thread_stretch`).


def stretch() -> float:
    """How much slower than quiet this box runs this process now: run queue or measured clamp."""
    return max(load_factor(), thread_stretch())


CLEAR = 5.0  # 1.5 s read 9.7 s, x6.5, at load 40: a failure under 6.7 passes' time is no test
FAILURE_SHARE = 0.75


def bound(expected_s: float, failure_s: float) -> float:
    """The most a pass may take: short of the failure by a quarter of it, at any load."""
    if FAILURE_SHARE * failure_s < CLEAR * expected_s:
        raise ValueError(
            f"a {failure_s:g} s failure is not clear of a {expected_s:g} s pass: lengthen it"
        )
    return FAILURE_SHARE * failure_s


def assert_sooner(elapsed: float, expected_s: float, failure_s: float, what: str) -> None:
    limit = bound(expected_s, failure_s)
    assert elapsed < limit, (
        f"{what}: took {elapsed:.1f} s against {limit:.1f} s (a pass takes {expected_s:g} s on "
        f"a quiet box; the failure takes {failure_s:g} s at any load)"
    )


def patience(seconds: float) -> float:
    """How long a wait whose failure is "never" gives up after, on this box now."""
    return seconds * stretch()


class GiveUp:
    """A give-up deadline of `seconds` on a quiet box, stretched by the load at every check."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.start = time.monotonic()

    def passed(self) -> bool:
        return time.monotonic() - self.start >= patience(self.seconds)
