#!/usr/bin/env python3
"""The race probe for the gate-slot wait report: can a reader see it between its writes?

    tools/gate_slot_race_probe.py                       # the real writer, 40 trials, 1 ms polls
    tools/gate_slot_race_probe.py --trials 200          # more trials
    tools/gate_slot_race_probe.py --poll 0.05           # the poll interval the suite's helper uses
    tools/gate_slot_race_probe.py --stamp               # measure the writer's window to the microsecond
    tools/gate_slot_race_probe.py --pause 0.05          # an instrumented writer that pauses

WHAT IT MEASURES. The gate-slot wait report is two writes: `lib/gate_slot.py` prints the
header ("all N gate slots are held") and then, for each slot, the line that names its holder
("slot-0: repo (pid, since)"). A reader that polls the output and waits for the HEADER can
therefore read the file in the window between the two writes, and see a state that does not yet
carry the line it is about to assert on. A test that waits for the header and then requires the
holder's line is a flake: it passes until a poll lands in that window.

The probe runs the REAL holder and the REAL waiter (lib/gate_slot.sh, lib/gate_slot.py, the
house TUI) and reads the waiter's output the way the suite's helper does, at a poll interval
you choose. It reports:

  trials        holder/waiter pairs run
  polls         reads of the waiter's output
  sightings     reads that saw the header and NOT the holder's line -- the intermediate state
  failures      trials whose FIRST sighting of the header lacked the holder's line. This is the
                number that matters: it is how often the current test shape would have failed.
  window        the writer's own gap between the two writes (--stamp), in microseconds

`--pause SECONDS` runs an INSTRUMENTED writer: a copy of lib/ and tui/ whose gate_slot.py
sleeps between the header write and the holder line, so the window is wide enough to be
deterministic. The copy is a copy -- the working tree is never touched.

The probe is a measurement, not a gate: it says what it saw. What it saw is the input to the
decision, and the decision is a commit. The kit it drives is checks/_gate_slot_probe.py; the
suite's regression test drives the same kit.
"""

from __future__ import annotations

import shutil
import statistics
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "checks"))

from _gate_slot_probe import Trial, instrumented_copy, window_of  # noqa: E402


def main(argv: list[str]) -> int:
    trials = 40
    poll = 0.001
    pause = 0.0
    stamp = False
    keep = False
    args = list(argv[1:])
    while args:
        flag = args.pop(0)
        if flag == "--trials":
            trials = int(args.pop(0))
        elif flag == "--poll":
            poll = float(args.pop(0))
        elif flag == "--pause":
            pause = float(args.pop(0))
        elif flag == "--stamp":
            stamp = True
        elif flag == "--keep":
            keep = True
        else:
            raise SystemExit(f"unknown argument {flag!r}")

    root = REPO_ROOT
    tmp = Path(tempfile.mkdtemp(prefix="goh-race-probe."))
    try:
        if pause > 0 or stamp:
            root = instrumented_copy(REPO_ROOT, tmp / "instrumented", pause)

        polls = sightings = failures = failures_fixed = 0
        windows: list[float] = []
        for n in range(trials):
            work = tmp / f"trial-{n}"
            work.mkdir(parents=True)
            trial = Trial(root, work, poll, pause=pause)
            try:
                p, s, f, ff = trial.run()
            finally:
                trial.close()
            polls += p
            sightings += s
            failures += f
            failures_fixed += ff
            windows.extend(window_of(trial.stamps))

        print(f"writer:    {'instrumented, pause %.3f s' % pause if pause else 'the real one'}")
        print(f"poll:      {poll * 1000:.3f} ms")
        print(f"trials:    {trials}")
        print(f"polls:     {polls}")
        print(f"sightings: {sightings}  (a read that saw the header without the holder's line)")
        print(
            f"failures:  {failures}  (the CURRENT shape: wait for the header, then require the line)"
        )
        print(
            f"failures:  {failures_fixed}  (the FIXED shape: wait for the line, then require the line)"
        )
        if windows:
            print(
                f"window:    {statistics.median(windows):.1f} us median"
                f" (min {min(windows):.1f}, max {max(windows):.1f})"
            )
        else:
            print("window:    not measured (no stamps; run with --stamp)")
        return 0
    finally:
        if keep:
            print(f"kept:    {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
