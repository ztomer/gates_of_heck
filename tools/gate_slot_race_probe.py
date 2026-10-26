#!/usr/bin/env python3
"""The race probe for the gate-slot wait report: can a reader see it between its writes?

    tools/gate_slot_race_probe.py                       # the real writer, 40 trials, 1 ms polls
    tools/gate_slot_race_probe.py --trials 200          # more trials
    tools/gate_slot_race_probe.py --poll 0.05           # the poll interval the suite's helper uses
    tools/gate_slot_race_probe.py --stamp               # measure the writer's window to the microsecond
    tools/gate_slot_race_probe.py --pause 0.05          # an instrumented writer that pauses
    tools/gate_slot_race_probe.py --pause 0.05 --shape legacy
                                                        # the instrumented writer, put BACK to the
                                                        # two-write shape: the calibration

WHAT IT MEASURES. The gate-slot wait report was two writes: `lib/gate_slot.py` printed the
header ("all N gate slots are held") and then, for each slot, the line that names its holder
("slot-0: repo (pid, since)"). A reader that polls the output and waits for the HEADER could
therefore read the file in the window between the two writes, and see a state that does not
yet carry the line it is about to assert on. A test that waits for the header and then requires
the holder's line is a flake: it passes until a poll lands in that window.

It is ONE write now (E59, 2026-10-21): `tui.lib`'s `report` writes the header and the details
together, so the intermediate state does not exist and there is nothing for a reader to land
in. The probe therefore measures two things, and both matter:

  * the READER's side -- how often a read saw the header without the holder's line. Zero is
    the answer the fix must produce, and the probe says it;
  * the WRITER's side -- whether the header and the holder's line arrived in ONE write. This
    is read off the instrumented writer's own marks, not inferred from the reader's luck.

`--pause SECONDS` runs an INSTRUMENTED writer: a copy of lib/ and tui/ whose write primitives
wait before every write, so the window is wide enough to be deterministic. The copy is a copy
-- the working tree is never touched. `--shape legacy` puts that copy BACK to the two-write
shape, which is how the probe is shown to still catch the race: an instrument that cannot
fail proves nothing.

The probe reports:

  trials        holder/waiter pairs run
  polls         reads of the waiter's output
  sightings     reads that saw the header and NOT the holder's line -- the intermediate state
  failures      trials whose FIRST sighting of the header lacked the holder's line. This is the
                number that matters: it is how often the current test shape would have failed.
  window        the writer's own gap between the two writes (--stamp), in microseconds
  one write     whether the header and the holder's line arrived in ONE write

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

from _gate_slot_probe import (
    Trial,
    instrumented_copy,
    marks_of,
    one_write,
    window_of,
)


def main(argv: list[str]) -> int:
    trials = 40
    poll = 0.001
    pause = 0.0
    stamp = False
    keep = False
    shape = "tree"
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
        elif flag == "--shape":
            shape = args.pop(0)
        elif flag == "--keep":
            keep = True
        else:
            raise SystemExit(f"unknown argument {flag!r}")

    root = REPO_ROOT
    tmp = Path(tempfile.mkdtemp(prefix="goh-race-probe."))
    try:
        if pause > 0 or stamp:
            root = instrumented_copy(REPO_ROOT, tmp / "instrumented", pause, shape=shape)

        polls = sightings = failures = failures_fixed = 0
        windows: list[float] = []
        one_writes = 0
        marked = 0
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
            one_writes += 1 if one_write(trial.stamps) else 0
            marked += 1 if marks_of(trial.stamps) else 0

        print(f"writer:    {f'instrumented, pause {pause:.3f} s' if pause else 'the real one'}")
        if shape != "tree":
            print(f"shape:     {shape}")
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
        elif marked:
            print(
                "window:    none -- the report is ONE write: there is no gap between two writes"
                " to measure"
            )
        else:
            print("window:    not measured (no stamps; run with --stamp)")
        if pause > 0 or stamp:
            print(
                f"one write: {one_writes} of {trials} trials wrote the header and the holder's"
                " line in ONE write"
            )
        return 0
    finally:
        if keep:
            print(f"kept:    {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
