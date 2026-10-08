"""A wall-clock upper bound in a test: placed by the FAILURE it rules out, stretched by the box.

A bound picked as "a few times what the pass takes on my box" flakes on a box other sessions
load, and refuses pushes for reasons unrelated to the change pushed: 15 s -> 63.8 s at load 38,
a speedup of 2.98 against 3.0 at load 55 (2026-10-08). A bound has two ends. The pass path,
`expected_s` on a quiet box, is stretched by the box's load (`_drift_guard.load_factor()`) and
SLACK. The failure the test exists to rule out -- a timeout that never fires, a sleep run to its
end -- takes `failure_s`, and the bound never passes FAILURE_SHARE of it, or the test cannot tell
the two apart. A test whose failure is not well clear of its pass (FAILURE_SHARE x failure_s <
SLACK x expected_s) is a design error, refused here: lengthen the failure.

A bound that DISCRIMINATES two passes (parallel vs serial) is not this: derive it from a control
measured in the same run (tests/test_session_bench.py, test_goh_prefetch.py).
`tests/test_timing_bounds.py` refuses a literal upper bound on an elapsed time anywhere in tests/.
"""

from __future__ import annotations

from _drift_guard import load_factor

SLACK = 3.0
FAILURE_SHARE = 0.75


def bound(expected_s: float, failure_s: float) -> float:
    """The most a pass may take on this box now."""
    if FAILURE_SHARE * failure_s < SLACK * expected_s:
        raise ValueError(
            f"a {failure_s:g} s failure is not clear of a {expected_s:g} s pass: lengthen it"
        )
    return min(expected_s * SLACK * load_factor(), FAILURE_SHARE * failure_s)


def assert_sooner(elapsed: float, expected_s: float, failure_s: float, what: str) -> None:
    limit = bound(expected_s, failure_s)
    assert elapsed < limit, (
        f"{what}: took {elapsed:.1f} s against {limit:.1f} s (a {expected_s:g} s pass x"
        f"{SLACK:g} x{load_factor():.2g} for the box's load; the failure takes {failure_s:g} s)"
    )
