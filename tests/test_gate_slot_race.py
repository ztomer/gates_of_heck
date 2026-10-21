"""E39: the reader may not treat a half-written report as final.

The gate-slot wait report is TWO writes -- `lib/gate_slot.py` prints the header and then, for
each slot, the line that names its holder -- and `tui/lib.py`'s `info()` and `step()` are
separate flushed prints. A reader that polls the output and waits for the HEADER can read the
file between the two writes and see a state that does not yet carry the line it is about to
assert on. `tests/test_gate_slot.py` did that: a flake, not a test. The box's own verdict:
"a race that cannot be red-proven is not fixable in a release".

So it is red-proven HERE, deterministically. The instrumented writer (checks/_gate_slot_probe.py)
pauses between the report's two writes, which makes the window wide enough for a reader to
land in it EVERY time instead of once in a while. Two things are then required:

  1. the intermediate state EXISTS -- the header alone is visible. Without this the test would
     pass against a writer that has one write, and would prove nothing;
  2. the reading does not DEPEND on it -- waiting for the line the test asserts on returns a
     text that carries it, whatever the writer's write boundaries are.

The second is the fix's shape, and it is the same helper the suite uses
(`test_gate_slot.holder_line`), so a reading that goes back to waiting for the header goes red
here instead of flaking in the suite.
"""

from __future__ import annotations

import time

from _gate_slot_probe import Trial, instrumented_copy
from conftest import REPO_ROOT
from test_gate_slot import holder_line

# How wide the instrumented writer's window is. Long enough that a reader polling every
# millisecond lands inside it every time; short enough that the test costs nothing.
PAUSE_S = 0.05


def test_the_wait_survives_a_writer_that_pauses_between_its_writes(tmp_path):
    instrumented = instrumented_copy(REPO_ROOT, tmp_path / "instrumented", PAUSE_S)
    work = tmp_path / "work"
    work.mkdir()
    trial = Trial(instrumented, work, poll=0.001, holder_name="zinc-like", pause=PAUSE_S)
    try:
        trial.start()
        # 1. The intermediate state is real: the header alone, before the holder's line.
        assert trial.header_without_line(10.0), (
            "the instrumented writer did not open the window: the header and the holder's line"
            " arrived together, so this test would prove nothing"
        )
        # 2. The reading does not depend on the window: wait for the line, read the line.
        today = time.strftime("%Y-%m-%d")
        line = holder_line(trial, "zinc-like", trial.holder_pid(), today)
        assert "zinc-like" in line, line
        assert f"pid {trial.holder_pid()}" in line, line
        assert "since " + today in line, line
    finally:
        trial.close()
