#!/usr/bin/env python3
"""The land pre-step's deadline, scaled by the worker share it was admitted with (GOH.PRESTEPSHARE).

    pre_deadline.py run --mark FILE --floor 10m [--ceiling 25m] [--repo goh] -- COMMAND...
    pre_deadline.py show --workers N [--floor 10m] [--ceiling 25m] [--repo goh]

THE FAULT (2026-11-17). Since GOH.POOLSHARE a pre-step runs on its share of the cores, and one
368-file set took 650 s at a 5-worker share (load 8.5 -> 99.7) against 347 s at 12: the fixed 600 s
deadline (GOH_LAND_PRE_MAX) could not be met at 5, the pre-step ended "no verdict", and the lander
gated an unverified tip. Of the 7 lands after POOLSHARE in the ledger, 3 ended at the deadline
(pre_s 601, 602, 601).

THE RULE, decided by those numbers:

    deadline = min(CEILING, max(FLOOR, MARGIN * WORK / workers))

WORK is the worker-seconds the pre-step costs: a run's wall seconds times its share, the largest of
the newest WINDOW land rows that reached a verdict (pre_run_s * pre_workers, recorded by
tools/land.sh), else SEED_WORK_S = 347 s * 12 = 4164, the 368-file set at 12 workers (the costlier
of the two runs per worker: 650 * 5 = 3250). Worker-seconds and not cpu-s: on a box at load 99 a
worker's wall is several times its cpu, and the deadline is a wall clock. MARGIN 1.5 over the
largest seen. FLOOR is GOH_LAND_PRE_MAX (10m, the old deadline: a wide share keeps it). CEILING
(GOH_LAND_PRE_CEIL, 25m) stays under the enqueue's own 30 min bound (lib/land_prestep.py TIMEOUT)
with room for the slot wait. So the 368-file set gets 1250 s at 5 workers (650 s measured), 600 s
at 12 or 16, and 1500 s at 4 or fewer -- where a deadline is again possible, and says so.

A MINIMUM SHARE was the alternative and is rejected: it widens the one run admission just
narrowed, on the box POOLSHARE measured at load 213-237; the share stays, the clock follows it.

THE SHARE is read AFTER admission: this wrapper runs inside tools/load.py's slot, where
GOH_GATE_WORKERS is the admission's own count, so the deadline is never a guess made before a wait.
The command runs in a process group of its own, killed whole at the deadline (lib/load_tree.py),
and the exit is 124, load.py's EXIT_DEADLINE, which land.sh reads as no verdict. The wait is
lib/proc_deadline.py's, never the stdlib's timed wait (GOH.PRESTEPMSGS: on macOS that one reads a
child that ends after half its deadline as expired, its status lost), and the wrapper says nothing
of its own at the deadline: tools/land.sh's one line names the run, the wait and the limit apart.
The mark file gets `<start epoch> <workers> <deadline s>` as the command starts; at the deadline the
live pytest runs' CPU joins GOH_LAND_PRE_CPU's file before the kill (lib/pre_cpu.py, GOH.PREWIDTH).
Pinned by tests/test_pre_deadline.py.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # outside any checkout: nothing to re-run from
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import argparse
import math
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pre_cpu
import proc_deadline
import timing_ledger
import worker_share
from load_tree import kill_tree

SEED_WORK_S = 347.0 * 12  # the 368-file set at a 12-worker share, 2026-11-17
MARGIN = 1.5
WINDOW = 30  # the newest land rows read: an old, costlier set ages out
CEILING_S = 25 * 60
EXIT_DEADLINE = 124
UNITS = {"s": 1.0, "m": 60.0, "h": 3600.0}


def seconds(text: str) -> float:
    """`45`, `30s`, `10m`, `2h` -> seconds; anything that bounds nothing raises ValueError."""
    raw = text.strip().lower()
    scale = UNITS.get(raw[-1:], 1.0)
    value = float(raw[:-1] if raw[-1:] in UNITS else raw) * scale
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{text!r} is not a positive duration")
    return value


def work(rows: list[dict], repo: str) -> float:
    """The worker-seconds a pre-step costs: the largest verdict of the newest WINDOW runs."""
    seen = [
        float(r["pre_run_s"]) * float(r["pre_workers"])
        for r in rows
        if r.get("kind") == "land" and r.get("repo") == repo
        and isinstance(r.get("pre_run_s"), (int, float)) and isinstance(r.get("pre_workers"), int)
        and r["pre_workers"] > 0
    ][-WINDOW:]  # fmt: skip
    return max(seen) if seen else SEED_WORK_S


def deadline(work_s: float, workers: int, floor_s: float, ceiling_s: float = CEILING_S) -> int:
    """THE RULE (module doc): MARGIN * work over the share, within floor and ceiling."""
    scaled = MARGIN * work_s / max(1, workers)
    return math.ceil(min(max(ceiling_s, floor_s), max(floor_s, scaled)))


def _run(command: list[str], limit: int, mark: Path, workers: int) -> int:
    mark.write_text(f"{int(time.time())} {workers} {limit}\n", encoding="utf-8")
    child = subprocess.Popen(
        command, stdin=None, process_group=0
    )  # the pre-step's own (land.sh: /dev/null)

    def stop(number: int, _frame: object) -> None:
        kill_tree(child)
        os._exit(128 + number)

    for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(number, stop)
    status = proc_deadline.wait(child, limit)
    if status is None:  # the one outcome is tools/land.sh's line: no verdict, run and wait apart
        # What the killed pytest run cost, read while it lives (GOH.PREWIDTH, lib/pre_cpu.py).
        pre_cpu.at_deadline(child.pid, os.environ.get(pre_cpu.ENV))
        kill_tree(child)
        return EXIT_DEADLINE
    return 128 - status if status < 0 else status


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=("run", "show"))
    ap.add_argument("--floor", default="10m")
    ap.add_argument("--ceiling", default=f"{CEILING_S}s")
    ap.add_argument("--repo", default=timing_ledger.GOH_NAME)
    ap.add_argument("--workers", type=int, default=0, help="show: the share (run reads the slot's)")
    ap.add_argument("--mark", type=Path)
    cut = argv.index("--") if "--" in argv else len(argv)
    args, command = ap.parse_args(argv[:cut]), argv[cut + 1 :]
    try:
        floor_s, ceiling_s = seconds(args.floor), seconds(args.ceiling)
        workers = args.workers or worker_share.share()
    except ValueError as why:
        print(f"pre_deadline: {why}", file=sys.stderr)
        return 2
    limit = deadline(work(timing_ledger.read(), args.repo), workers, floor_s, ceiling_s)
    if args.cmd == "show":
        print(limit)
        return 0
    if not command or args.mark is None:
        print("pre_deadline: run needs --mark FILE -- COMMAND", file=sys.stderr)
        return 2
    return _run(command, limit, args.mark, workers)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
