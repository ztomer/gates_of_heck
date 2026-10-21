#!/usr/bin/env python3
"""The backstop: the minute job that kills the spinners whose owner is gone.

    tools/load_reap.py [--dir DIR]

`tools/load.py` reaps itself: it kills the spinners it started on the way out, and it watches for
its own owner dying and does the same then. What it cannot do is run at all when it is SIGKILLed
-- and a SIGKILLed owner is exactly how the three leaks of 2026-10-20 happened. So the machine
runs this: a launchd job (`com.ztomer.goh-load-reap`) every minute, reading the registry the tool
wrote, killing the pids in it whose parent is 1, and dropping the entries that are gone.

A registry can VANISH, though: the registry of an experiment that ran in a temporary directory is
pruned with that directory, and its spinner is left burning a core with nothing naming it (eight
of them, orphaned by a pytest that died, 2026-10-20). So the job also recognises the house's own
spinner shape -- EXACTLY that shape, orphaned only -- and stops those.

And a spinner need not be the house's at all (2026-10-21): twenty-four hand-rolled ones were
found burning four cores each, four and a half hours old, their owner long dead, and the
backstop knew only ONE command line -- so it left all of them alone. So the job asks one more
question, and it is the second RECORDED EXEMPTION: is this process an ORPHAN (parent 1) that has
BURNED a core (its CPU time is over the floor and still growing) on a busy loop (the code a
shell runs contains one)? Four questions, and none of them alone is enough -- a live parent, a
sleeper, a worker that has finished, and a program that is not a shell are all left alone.

It kills by PID: from its own registry, from the house's own spinner shape when the registry is
gone, or from a process whose shape is a busy loop burning a core. The shape lookups are the
RECORDED EXEMPTIONS in `kill_by_name_allow.json` (the house forbids killing by name,
`goh kill-by-name`): the lookup and the kill sit on one line, so the rule sees it, and every
candidate is read back and matched before it is touched. A spinner with a LIVE parent is an
experiment in progress and is left alone; a process that is not burning a loop was never ours.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import load_registry as reg  # noqa: E402
import load_reap  # noqa: E402


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="tools/load_reap.py",
        description="kill the load spinners whose owner is gone",
    )
    parser.add_argument("--dir", default=None, help="the load directory (default GOH_LOAD_DIR)")
    opts = parser.parse_args(argv)
    directory = Path(opts.dir).expanduser() if opts.dir else reg.load_dir()
    report = load_reap.Report()
    if directory.is_dir():
        report = load_reap.reap_all(directory)
    # The registry is one way to an orphan; the house's own shape is the other, and it does not
    # need a directory to be searched.
    orphans = load_reap.reap_orphans()
    report.killed.extend(orphans.killed)
    report.failed.extend(orphans.failed)
    # The third shape (2026-10-21): an orphaned process BURNING a core on a busy loop, whose
    # command line is not the house's own -- the hand-rolled spinners of that day, twenty-four of
    # them, four cores each. Named by its shape, killed by its pid, and recorded as an exemption.
    burning = load_reap.reap_burning_orphans()
    report.killed.extend(burning.killed)
    report.failed.extend(burning.failed)
    if report.killed or report.removed:
        killed = " ".join(str(pid) for pid in report.killed)
        print(
            f"load-reap: killed {killed or 'nothing'}; removed {len(report.removed)} registry file(s)"
        )
    if report.failed:
        print(
            f"load-reap: {len(report.failed)} spinner(s) survived: "
            f"{' '.join(str(pid) for pid in report.failed)}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
