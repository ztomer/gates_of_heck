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

`tools/load_reap.py` / `tools/load_reap.sh`: the backstop: the minute job that reads the load
registry and kills the pids in it whose parent is 1, that recognises the house's own spinner shape
-- EXACTLY that shape, orphaned only, owner gone -- for the orphans whose registry was pruned with
the directory that held it, and that recognises a HAND-ROLLED spinner -- an orphan that has burned
a core on a busy loop, whoever wrote it (2026-10-21: twenty-four of them, four cores each, four and
a half hours old, and the backstop knew only the house's own command line, so it left every one
alone) (`lib/load_reap.py`). The shell wrapper re-runs itself from an export of HEAD (C4), so an
unattended job never runs an uncommitted edit. Pinned by `test_load_reap.py`.

`tools/load.py`: `tools/load.py [N -- CMD...]`: the ONE way to make load on this machine, and it
reaps itself. It takes the registry slot (`GOH_LOAD_DIR`, default `~/.cache/goh/load`), refusing a
second experiment while one holds it; starts N spinners, REGISTERING each one (pid + start time)
BEFORE it is allowed to burn (a spinner waits on a release pipe, and exits if the pipe closes
first, so one that was never released never spins); runs the command under the load; kills exactly
the spinners it registered on the way out (EXIT/TERM/INT/HUP), VERIFIES that none survived, and
drops the registry. It also watches for its OWN owner dying and cleans up then. It is BOUNDED by
its own clock (G1c): a deadline is always in force (30 min unless `--max 90m` / `--max 2h` names
another), and on expiry the command's whole tree (its process group, and every descendant, one that
left the group too) and every spinner are killed and it exits 124 naming the deadline; a signal or
a dead owner kills the command's tree as well. N above the core count is refused unless
`--oversubscribe` is passed (`GOH_LOAD_NCPU` is the test seam). A command run at BACKGROUND
priority (G1f: `taskpolicy -b`, a QoS clamp, `nice`/`renice` >= 10, `chrt --idle`, after
env/sudo-style prefixes and inside `sh -c`) is refused before any spinner starts: N spinners starve
it by construction (`lib/load_priority.py`); `--background-ok <reason>` is the escape. A build tool
that takes a machine-wide lock (cargo, xcodebuild, swift, gradle, go, npm/pnpm install:
`lib/load_locks.py`, GOH.LOADLOCK) is refused the same way; build first, put only the prebuilt
binary under load; It refuses (exit 75) while another session's GATE is in flight
(`lib/gates_in_flight.py`, GOH.LOADX), naming repo, stage, pid and elapsed; `--wait` starts once
the gates in flight when it asked are done -- gates that start meanwhile are named, not waited on
-- bounded by its OWN `--wait-max` (default 15m, never `--max`); on expiry it names the gates still
ahead, the late ones and the load average (GOH.LOADWAIT). It prints the load average it started on
(the baseline, also in the registry) rather than waiting for a quiet one. `--despite-gate`
overrides, and its own ancestor gate does not count. To simulate a slow CI runner, throttle the
process under test (Chromium CPU throttling over CDP / Playwright), not the shared machine.
`--status` says who holds the slot; `--reap` clears a slot whose owner is gone. Pinned by
`test_load_tool.py`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import load_reap
import load_reap_log as log
import load_registry as reg


def main(argv: list[str]) -> int:
    sys.path.insert(0, str(ROOT))
    from tui.lib import counted, noun  # the count meets its noun (GOH.HEDGEOWN)

    parser = argparse.ArgumentParser(
        prog="tools/load_reap.py",
        description="kill the load spinners whose owner is gone",
    )
    parser.add_argument("--dir", default=None, help="the load directory (default GOH_LOAD_DIR)")
    parser.add_argument(
        "--log-path",
        default=None,
        help="the file launchd sends this job's output to: moved to <file>.1 once past the cap",
    )
    parser.add_argument(
        "--max-log-bytes", type=int, default=log.MAX_LOG_BYTES, help="the log's cap in bytes"
    )
    opts = parser.parse_args(argv)
    try:  # never inside a git work tree (GOH.STRAYLOCK): its lock would read as a change there
        directory = reg.checked(Path(opts.dir).expanduser()) if opts.dir else reg.load_dir()
    except reg.InWorkTree as why:
        print(f"load_reap: {why}", file=sys.stderr)
        return 2
    shapes = [
        # The house's own shape does not need a directory to be searched: the registry is one way
        # to an orphan, and this is the other.
        ("the house's spinner shape", load_reap.reap_orphans),
        # The third shape (2026-10-21): an orphaned process BURNING a core on a busy loop, whose
        # command line is not the house's own -- the hand-rolled spinners of that day,
        # twenty-four of them, four cores each. Named by its shape, killed by its pid, and
        # recorded as an exemption.
        ("burning orphans", load_reap.reap_burning_orphans),
        # The RECORD is settled LAST (2026-10-25), once every shape in the pass has acted: a
        # spinner whose parent the registry pass could not read is kept (unknown), and a shape
        # that ran after it killed it -- so the record named a dead pid for another pass, and the
        # next experiment was refused for a minute (the push gate of v0.49.1).
        (
            "registry",
            lambda: load_reap.reap_all(directory) if directory.is_dir() else load_reap.Report(),
        ),
    ]
    # Each shape runs ALONE (2026-10-25): one that dies must not take the others with it. Eleven
    # passes died on a lookup's timeout, on the loaded machine the backstop is for; a timeout is
    # now a stated blind spot (Report.blind), and any other fault is reported after the rest ran.
    report = load_reap.Report()
    faults: list[str] = []
    for name, shape in shapes:
        try:
            one = shape()
        except load_reap.Blind as blind:
            report.blind.append(f"{name}: {blind}")
            continue
        except Exception as error:  # noqa: BLE001 - one shape faulting must not hide the others: reported below, exit 1
            faults.append(f"{name}: {type(error).__name__}: {error}")
            continue
        report.killed.extend(one.killed)
        report.kills.extend(one.kills)
        report.failed.extend(one.failed)
        report.removed.extend(one.removed)
        report.blind.extend(one.blind)
    # EVERY line is stamped, and a kill line says who, what and why (GOH.REAPLOG): a pass that
    # kills and removes nothing writes nothing, so the log does not grow once a minute for no reason.
    for kill in report.kills:
        print(log.kill_line(kill))
    if report.removed:
        print(
            log.line(f"removed {len(report.removed)} registry {noun(len(report.removed), 'file')}")
        )
    for blind in report.blind:
        print(log.line(f"not searched this pass -- {blind}; the next pass asks again"))
    if report.failed:
        survivors = " ".join(str(pid) for pid in report.failed)
        print(
            log.line(f"{counted(len(report.failed), 'spinner')} survived: {survivors}"),
            file=sys.stderr,
        )
    for fault in faults:
        print(log.line(fault), file=sys.stderr)
    sys.stdout.flush()
    sys.stderr.flush()
    if opts.log_path:
        log.rotate(Path(opts.log_path).expanduser(), opts.max_log_bytes)
    return 1 if faults else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
