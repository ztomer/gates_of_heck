"""The stall ceiling of `lib/pgid_cpu.py` by a clock the test drives (GOH.COVPHASESTALL).

An idle group (no CPU, nothing runnable) expires `--stall` after exactly that many ticks; CPU or a
runnable thread is progress and never does. The real-sleep version of this lived in the
coverage-phase test, where at load ~114 it expired in a prefix the box starved. No wall second
passes here, so the box's load cannot matter.
"""

import subprocess
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))


class _TickingStep:
    """A step that never exits; each `wait` call is one poll and moves the fake clock by `tick`."""

    def __init__(self, clock: list[float], tick: float) -> None:
        self.clock, self.tick = clock, tick

    def wait(self, timeout: float | None = None) -> int:
        self.clock[0] += self.tick
        raise subprocess.TimeoutExpired("step", timeout or 0)


def _expiry(monkeypatch, samples, tick: float = 1.0, stall: int = 3, wall: int = 500):
    """What `pgid_cpu.wait_for_exit` says for a step whose group reads `samples(n)` at poll n,
    on a clock this test moves (GOH.COVPHASESTALL): no wall second passes, so load cannot matter."""
    import pgid_cpu

    clock = [1000.0]
    polls = [0]

    def sample(_pgid: int):
        polls[0] += 1
        return samples(polls[0])

    # only pgid_cpu's own `time` is the fake: the module's clock is shared with pytest's otherwise
    monkeypatch.setattr(pgid_cpu, "time", types.SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(pgid_cpu, "pgid_cpu_and_runnable", sample)
    got = pgid_cpu.wait_for_exit(
        _TickingStep(clock, tick), 1, wall, "step", cpu_timeout=None, stall=stall
    )
    return got, polls[0]


def test_a_stalled_group_expires_the_stall_ceiling_on_a_fake_clock(monkeypatch) -> None:
    """The stall ceiling by a clock the test drives: an idle group (no CPU, nothing runnable)
    expires it after exactly `stall` ticks, as the old coverage-phase test asserted with a real
    `sleep` and a real 3 s; a group that spends CPU, or runs, never does; the wall backstop
    still fires when progress never stops."""
    got, polls = _expiry(monkeypatch, lambda n: (0.0, 0))
    assert got is not None and "(stalled)" in got and "after 3s" in got, got
    assert polls == 3, polls  # idle from poll 1: three ticks of 1 s
    # CPU rising every poll is progress, so is a runnable thread: no stall in 50 ticks
    assert _expiry(monkeypatch, lambda n: (float(n), 0), wall=50)[0] == (
        "TIMED OUT after 50s: step"
    )
    assert _expiry(monkeypatch, lambda n: (0.0, 1), wall=50)[0] == "TIMED OUT after 50s: step"
    # a starved PREFIX (CPU for 10 polls) then idle: the stall counts from the last progress
    got, polls = _expiry(monkeypatch, lambda n: (float(min(n, 10)), 0))
    assert got is not None and "(stalled)" in got, got
    assert polls == 13, polls
