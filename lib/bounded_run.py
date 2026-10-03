#!/usr/bin/env python3
"""Run one step under a wall-clock CEILING, in its own session, and sweep its whole subtree.

    lib/bounded_run.py [--timeout N] [--grace N] [--label TEXT] -- CMD [ARG...]
    lib/bounded_run.py --cat TIMEOUT -- CMD [ARG...]     # print `TIMED OUT after Ns` on stderr

EXIT. 0/1/2.. as the command returned · **124** on expiry (the GNU `timeout` convention, which
`gates/local_ci.sh` already speaks) · 2 on a usage error.

WHY THIS EXISTS, and the measurement that put it here rather than in a comment
---------------------------------------------------------------------------
`gates/local_ci.sh` had a per-step ceiling (`GOH_LCI_TIMEOUT`) and swept the timed-out step with
`pkill -P "$pid"` — DIRECT children only. A test step is a chain: `cargo test` → the test binary →
the server the test spawned. Measured on this machine, 2026-10-03, with the exact shape
`bash -c 'sleep 400 & wait'`:

    pkill -TERM -P $pid ; pkill -KILL -P $pid   →  2 grandchildren STILL ALIVE
    killpg(getpgid(pid), SIGKILL)              →  NOTHING SURVIVED

So the mechanism meant to unstick a hung step left running precisely the processes that make a
later step hang: the orphan holding the cargo build lock. The bound existed and did not do the
thing it is for, which is the same shape as the defect it was bolted onto — a wait with nothing
bounded and nothing reported.

`lib/killtree.py` already knew the answer (own session + `killpg`) for the Python callers. This is
the same discipline exposed to the shell gates, so the two runners cannot disagree about what a
timeout does.

BOUNDED, AND REPORTED. Expiry prints `TIMED OUT after Ns` on stderr and returns 124 -- never
silence. A ceiling nobody gave a number is a suggestion; a ceiling that expires quietly is the
2026-10-03 incident again with a shorter fuse.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time

# GNU timeout's exit code for "the ceiling expired". `gates/local_ci.sh` already prints
# "TIMED OUT" on 124 and keeps the log; a different number would be a different contract in two
# places.
TIMEOUT_EXIT = 124

DEFAULT_TIMEOUT = 900
DEFAULT_GRACE = 5
# How often the ceiling is checked. 0.2 s is short enough that a 900 s ceiling is honest to within
# a fifth of a second, and long enough that the poll is noise next to the step it is bounding.
POLL = 0.2


def _descendants(root: int) -> list[int]:
    """Every live pid below `root`, by walking the process table. Reporting, not killing.

    Used only to say what was still running when the ceiling expired -- a step that times out with
    three survivors named is a step whose leak is now visible, which is the point of the canary.
    """
    try:
        out = subprocess.run(
            ["ps", "-Ao", "pid=,ppid="], capture_output=True, text=True, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return []
    children: dict[int, list[int]] = {}
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            children.setdefault(int(parts[1]), []).append(int(parts[0]))
    found, stack = [], [root]
    while stack:
        for kid in children.get(stack.pop(), []):
            found.append(kid)
            stack.append(kid)
    return found


def _sweep(pgid: int | None, pid: int, grace: float) -> list[int]:
    """TERM the whole group, then KILL it. Returns the pids that were running under it.

    `os.killpg` FIRST and the direct pid only as a fallback: on a platform without a process group
    (or with the group already gone) the direct kill still stops the step, which is the half that
    must never be skipped. The survivors are REPORTED rather than dropped -- a step that times out
    with three processes named is a step whose leak just became visible, and that is the whole
    reason the ceiling is not a silent kill.
    """
    before = set(_descendants(pid)) | {pid}

    def signal_group(sig: int) -> None:
        if pgid is not None and hasattr(os, "killpg"):
            try:
                os.killpg(pgid, sig)
                return
            except (OSError, ProcessLookupError):
                pass
        try:
            os.kill(pid, sig)
        except (OSError, ProcessLookupError):
            pass

    signal_group(signal.SIGTERM)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline and _descendants(pid):
        time.sleep(POLL)
    signal_group(signal.SIGKILL)
    return sorted(before - set(_descendants(pid)) - {pid})


def run(argv: list[str], timeout: int, grace: float, label: str, output=None) -> int:
    """Run `argv` with a ceiling. Returns the command's code, or 124.

    `output` is where the step's stdout/stderr go: a path (opened for append) or a file object, or
    `None` to INHERIT this process's.

    **Inheriting is a trap when a leak is possible, and it was measured.** A leaked child keeps the
    write end of an inherited pipe open, so a caller reading that pipe to EOF blocks until the
    child exits — which, for a leaked server, is never. Measured 2026-10-03: a canary whose whole
    job is to report a leaked `sleep 1201` hung for 1201 s reporting it, inside the test that
    asserted the report. `gates/local_ci.sh` is unaffected (it redirects each step to a log FILE),
    and `wrap` passes `DEVNULL` for the same reason: a reporter must not be made to wait by the
    thing it is reporting.
    """
    stream = None
    if isinstance(output, str):
        stream = open(output, "ab")  # noqa: SIM115  # closed in the finally below
    elif output is not None:
        stream = output
    try:
        try:
            proc = subprocess.Popen(  # noqa: S603  # argv, never a shell string: the caller's words
                argv,
                stdin=subprocess.DEVNULL,
                stdout=stream if stream is not None else None,
                stderr=subprocess.STDOUT if stream is not None else None,
                start_new_session=True,  # POSIX; its own group, so killpg covers the tree
            )
        except OSError as exc:
            print(f"bounded_run: cannot start {label or argv[0]}: {exc}", file=sys.stderr)
            return 127
        try:
            pgid: int | None = os.getpgid(proc.pid)
        except OSError:
            pgid = None
        deadline = time.monotonic() + timeout
        while proc.poll() is None:
            if time.monotonic() >= deadline:
                survivors = _sweep(pgid, proc.pid, grace)
                try:
                    proc.wait(timeout=grace)
                except subprocess.TimeoutExpired:
                    pass
                print(f"TIMED OUT after {timeout}s: {label or ' '.join(argv)}", file=sys.stderr)
                if survivors:
                    print(
                        f"  swept {len(survivors)} process(es) that were still running under it: "
                        + ", ".join(str(p) for p in survivors[:20]),
                        file=sys.stderr,
                    )
                return TIMEOUT_EXIT
            time.sleep(POLL)
        return proc.returncode
    finally:
        if isinstance(output, str) and stream is not None:
            stream.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="ceiling in seconds")
    ap.add_argument("--grace", type=int, default=DEFAULT_GRACE, help="TERM->KILL grace, seconds")
    ap.add_argument("--label", default="", help="what to call the step in the message")
    args, rest = ap.parse_known_args(argv)
    if rest and rest[0] == "--":
        rest = rest[1:]
    if not rest:
        ap.error("no command: bounded_run.py -- CMD [ARG...]")
    if args.timeout <= 0:
        print(f"bounded_run: --timeout must be positive (got {args.timeout})", file=sys.stderr)
        return 2
    return run(rest, args.timeout, args.grace, args.label)


if __name__ == "__main__":
    sys.exit(main())
