#!/usr/bin/env python3
"""Show a gate's STEP lines from its log as they land, while the log keeps everything.

    lib/log_follow.py --pid PID LOG    # until PID is gone (or a TERM), then the rest, then exit

GOH.LANDPROGRESS (2026-11-14). `tools/gate_profile.sh` -- land.sh's gate -- writes the push gate's
whole output to a log and prints only the end, so a 40 min land under load read as silence. The log
stays the record; this prints the lines that say where the gate is: local CI's `[i/n]` step lines
and the section titles above them, the Rust coverage step's decision (GOH.BATCHPREFROM) and its
phases' ends (GOH.LANDLEAKS). It
is bounded by its owner, never by hope: it exits when PID (the profile) is gone or when it is
TERMed, draining what is already written first, so a killed profile leaves no follower behind
(tests/test_gate_profile.py).
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    import contextlib as _contextlib

    with _contextlib.suppress(ModuleNotFoundError):  # a copy outside any checkout: run it as is
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import argparse
import os
import re
import signal
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pid_owner

# local CI's `→ [1/7] cmd`, `✓ [1/7] cmd`, `✗ [7/7] FAILED: cmd`, `⚠ [3/7] ...`; section titles.
STEP = re.compile(r"^\s*(?:[→✓✗⚠] \[\d+/\d+\]|\s*(?:pre-push: gating|local CI —))")
# The Rust coverage step's decision, `↔ [rust] coverage: measuring -- WHY` (or `reused`), after
# any colour escapes: the lander names it per land (lib/landq_report.py coverage_said).
ESC = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
COVERAGE = re.compile(
    r"^\s*↔ \[rust\] coverage: (?:measuring -- |reused -- |not measured at a land)"
)
# A coverage phase's END, `✓ phase 'NAME': N s`: lib/land_profile.py reads it from the land's log,
# the gate's own log being removed with the land (GOH.LANDLEAKS).
PHASE = re.compile(r"^\s*✓ phase '.+': \d+ s\s*$")
POLL_S = 0.5


class _Stop(Exception):
    pass


def _show(chunk: str, carry: str) -> str:
    """Print each whole step line in `carry + chunk`; return the unfinished tail."""
    text = carry + chunk
    *whole, tail = text.split("\n")
    for line in whole:
        if STEP.match(line):
            print(line.strip(), flush=True)
        elif COVERAGE.match(plain := ESC.sub("", line)) or PHASE.match(plain):
            print(plain.strip(), flush=True)
    return tail


def follow(log: str, pid: int) -> int:
    def stop(_sig, _frame):
        raise _Stop

    signal.signal(signal.SIGTERM, stop)
    # The owner is stamped NOW, while it is the live process that started this follower, and
    # watched by the stamp (GOH.STRAYSIGNAL2): its pid handed to another process once it ended
    # would have kept the follow going for a stranger.
    owner = pid_owner.stamp(pid)
    carry, pos = "", 0
    try:
        while pid_owner.alive(owner):
            try:
                with open(log, encoding="utf-8", errors="replace") as fh:
                    fh.seek(pos)
                    chunk = fh.read()
                    pos = fh.tell()
                carry = _show(chunk, carry)
            except FileNotFoundError:
                pass
            time.sleep(POLL_S)
    except _Stop:
        pass
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    try:  # the drain: what landed between the last read and the stop
        with open(log, encoding="utf-8", errors="replace") as fh:
            fh.seek(pos)
            _show(fh.read() + "\n", carry)
    except FileNotFoundError:
        pass
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--pid", type=int, required=True, help="the owner; gone means stop")
    ap.add_argument("log")
    args = ap.parse_args(argv)
    return follow(args.log, args.pid)


if __name__ == "__main__":
    sys.exit(main())
