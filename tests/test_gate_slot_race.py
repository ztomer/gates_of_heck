"""E59: the report is ONE write, so the reader cannot see a state that is not final.

E39 fixed the TEST side: the wait is for the line the test asserts on. E39 also found that
this was not enough, and named why: the WRITER produced the report in two writes -- the
header, then the line that names each holder -- so a reader could land between them and read
a state the writer had not finished. The window was 56 us, and a reader polling every
millisecond landed in it (1 of 20 trials against the real writer; 20 of 20 against the
instrumented one).

No test-side rule can prevent that: the soundness of a wait depends on the writer's write
ORDER, which a scanner reading the test cannot know. So the fix is on the writer's side --
the report is written ONCE (`tui/lib.py`'s `report`, and `tui/lib.sh`'s), so the intermediate
state does not exist at all.

THE PROBE, AND WHAT IT IS PROVED AGAINST. The instrumented writer (checks/_gate_slot_probe.py)
pauses before its writes, so the window a reader can land in is wide enough to be caught EVERY
time instead of once in a while. Three things are required, and all three are here:

  1. the reader NEVER sees a half-written report -- the intermediate state does not exist;
  2. the WRITER's own stream says the header and the holder's line arrived in ONE write
     (read off the instrumented writer's marks, not inferred from the reader's luck);
  3. the probe CAN still see the old two-write report -- an instrument that cannot fail
     proves nothing, so the legacy shape is restored in the copy and shown to be caught.

The third arm is the calibration: with it removed, arms 1 and 2 would pass against a probe
that was simply blind.
"""

from __future__ import annotations

import time

from _gate_slot_probe import Trial, instrumented_copy, one_write
from conftest import REPO_ROOT
from test_gate_slot import holder_line

# How wide the instrumented writer's window is. Long enough that a reader polling every
# millisecond lands inside it every time; short enough that the test costs nothing.
PAUSE_S = 0.05

# How many holder/waiter pairs the reader is let at the report. One would be an anecdote.
TRIALS = 3


def test_the_report_is_one_write_and_the_reader_cannot_see_it_in_pieces(tmp_path):
    instrumented = instrumented_copy(REPO_ROOT, tmp_path / "instrumented", PAUSE_S)
    sightings = failures = fixed_failures = 0
    one_writes = 0
    for n in range(TRIALS):
        work = tmp_path / f"work-{n}"
        work.mkdir()
        trial = Trial(instrumented, work, poll=0.001, holder_name="zinc-like", pause=PAUSE_S)
        try:
            _polls, seen, failed, failed_fixed = trial.run()
        finally:
            trial.close()
        sightings += seen
        failures += failed
        fixed_failures += failed_fixed
        one_writes += 1 if one_write(trial.stamps) else 0
        if n == 0:
            # E39's reading still works: the wait is for the line, and the text it returns
            # carries every substring the assertions read.
            today = time.strftime("%Y-%m-%d")
            line = holder_line(trial, "zinc-like", trial.holder_pid(), today)
            assert "zinc-like" in line, line
            assert f"pid {trial.holder_pid()}" in line, line
            assert "since " + today in line, line
    assert sightings == 0, (
        f"the reader saw the header without the holder's line {sightings} times: the report is"
        " still written in pieces"
    )
    assert failures == 0, f"{failures} trials read a half-written report as final"
    assert fixed_failures == 0, "the reading that waits for the line did not carry the line"
    assert one_writes == TRIALS, (
        f"the header and the holder's line arrived in ONE write in {one_writes} of {TRIALS}"
        " trials: the report is not one write"
    )


def test_the_probe_can_still_see_the_old_two_write_report(tmp_path):
    """The calibration: the OLD shape, restored in the copy, is caught every time.

    Without this arm the two above would pass against a probe that was simply blind -- and a
    blind instrument is how a race ships.
    """
    instrumented = instrumented_copy(REPO_ROOT, tmp_path / "legacy", PAUSE_S, shape="legacy")
    work = tmp_path / "work"
    work.mkdir()
    trial = Trial(instrumented, work, poll=0.001, holder_name="zinc-like", pause=PAUSE_S)
    try:
        trial.start()
        assert trial.header_without_line(10.0), (
            "the probe did not open the window on the two-write shape: it would prove nothing"
        )
    finally:
        trial.close()
