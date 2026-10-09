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

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    try:
        __import__("_from_head").reexec(__file__)
    except ModuleNotFoundError:  # a copy outside any checkout: nothing to re-run from
        pass
    del _sys.path[0]

import os
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import killtree  # noqa: E402  # beside this file
import step_timings  # noqa: E402  # beside this file; run as a script AND imported by orphan_canary

# GNU timeout's exit code for "the ceiling expired". `gates/local_ci.sh` already prints
# "TIMED OUT" on 124 and keeps the log; a different number would be a different contract in two
# places.
TIMEOUT_EXIT = 124

DEFAULT_TIMEOUT = 900
# The one value that means "throw the step's output away". Named rather than passed as
# `subprocess.DEVNULL` from a caller, because a caller that spells it inline re-spells it, and two
# spellings of one intent is how a gate ends up capturing output somewhere nobody looks.
DEVNULL_SENTINEL = "DEVNULL"
DEFAULT_GRACE = 5
# How often the TERM->KILL grace checks for survivors. Only the timeout path polls; a step that
# exits on its own is waited on, not polled (tests/test_bounded_run.py pins that nothing sleeps).
POLL = 0.2


def _process_table() -> list[tuple[int, int, int]]:
    """`(pid, ppid, pgid)` for every live process, from ONE `ps`.

    One read serves both questions the exit sample asks -- who is in the step's group, and who is
    below its pid. They were two `ps` spawns per step, per gate run, in every consumer (measured
    2026-10-05; `tests/test_bounded_run.py` pins the count).
    """
    try:
        out = subprocess.run(
            ["ps", "-Ao", "pid=,ppid=,pgid="], capture_output=True, text=True, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) == 3 and all(p.isdigit() for p in parts):
            rows.append((int(parts[0]), int(parts[1]), int(parts[2])))
    return rows


def _descendants(pid: int, table: list[tuple[int, int, int]] | None = None) -> list[int]:
    """Every live pid below `pid`, by walking the process table. Reporting, not killing.

    Sampled at the moment the step exits, this is what turns "the step leaked something" from an
    inference into a measurement, and it is the only attribution that survives two gates running at
    once on the same machine.
    """
    children: dict[int, list[int]] = {}
    for kid, parent, _ in _process_table() if table is None else table:
        children.setdefault(parent, []).append(kid)
    found, stack = [], [pid]
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


class Outcome:
    """What a bounded run ended up as.

    `descendants` is the point. Sampled at the MOMENT the step exits, it is the exact set of
    processes that were under it — which is the only attribution that cannot be fooled. The
    alternative, "a new process whose ppid is 1", is machine-global: two gates running at once, or
    one gate's eight pytest workers, cross-report each other's orphans. Measured 2026-10-03 under
    this repo's own parallel suite — every run reported the other worker's leak, three tests red for
    a reason that had nothing to do with what they measured. Ancestry is a fact; a pid's parentage
    after reparenting is a rumour.
    """

    __slots__ = ("code", "descendants", "timed_out")

    def __init__(self, code: int, descendants: list[int], timed_out: bool) -> None:
        # A plain class, not a frozen dataclass: `dataclasses` pulls in `inspect`, ~4 ms of import on
        # every gate step's path (2026-10-06). Immutability is kept by __setattr__ below.
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "descendants", descendants)
        object.__setattr__(self, "timed_out", timed_out)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"Outcome is immutable: {name}")

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Outcome) and (
            self.code,
            self.descendants,
            self.timed_out,
        ) == (other.code, other.descendants, other.timed_out)

    __hash__ = None

    def __repr__(self) -> str:
        return (
            f"Outcome(code={self.code!r}, descendants={self.descendants!r}, "
            f"timed_out={self.timed_out!r})"
        )

    @property
    def survivors(self) -> list[int]:
        """Descendants still alive after the step exited — the leaks."""
        return [pid for pid in self.descendants if _alive(pid)]


def group_members(pgid: int, table: list[tuple[int, int, int]] | None = None) -> list[int]:
    """Every live pid in `pgid`'s process GROUP.

    This, not the descendant walk, is what makes the leak measurement exact. A leaked child is
    REPARENTED the instant its parent exits -- measured: `sleep 422` with `ppid 1` -- so a descendant
    walk taken at exit finds nothing, and the canary reported a clean run while the server ran on.
    The process GROUP survives reparenting: the step was started with `start_new_session`, so
    everything it spawned inherited that pgid and keeps it, and one `ps -Ao pid=,pgid=` names them
    all. Measured on the same shape: `64886 1 64885 /bin/sleep 422`, pgid intact.

    The limit, stated: a child that calls `setsid()` leaves the group and is invisible here. A
    daemonizing test helper is a different design, and it is named in the docs rather than guessed
    at.
    """
    rows = _process_table() if table is None else table
    return [pid for pid, _, group in rows if group == pgid and pid != pgid]


def _group_empty(pgid: int) -> bool:
    """True when no process is left in `pgid`: `killpg(pgid, 0)` fails with ESRCH. EPERM means a
    member exists that is not ours to signal -- not empty."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class _Stopped(Exception):  # a signal, not an error
    def __init__(self, sig: int) -> None:
        super().__init__(sig)
        self.sig = sig


# The signals that stop a GATE. A step in its own session never sees them -- Ctrl-C goes to the
# terminal's foreground group, which the step left, and a TERM to this wrapper used to kill the
# wrapper alone -- so the step ran on, unowned: the orphan this file exists to prevent, made by it
# (tests/test_bounded_run_interrupt.py). So the wrapper takes them, sweeps the step's group, and
# returns 128+N. A signal IGNORED when this process started (nohup, a non-job-control shell's
# background job and SIGINT) stays ignored: that is the caller's decision, not ours to undo.
STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)


class _Stops:
    """A stop is HELD while the wrapper cannot act on it, not raised:

    * while `Popen` is starting the step -- raised there, it unwinds out of `Popen` before the
      wrapper holds the step, so there is nothing to sweep and the step runs on (seen under load);
    * while a stop is already sweeping -- a second Ctrl-C, which people press, raised mid-sweep and
      abandoned it with the group half-signalled.

    `release()` raises a held stop the moment the step is the wrapper's. Both windows are pinned in
    tests/test_bounded_run_interrupt.py by stopping inside `Popen` itself."""

    def __init__(self) -> None:
        self.holding = True
        self.pending: int | None = None

    def handler(self, sig: int, _frame: object) -> None:
        if self.holding:
            self.pending = self.pending or sig
            return
        raise _Stopped(sig)

    def release(self) -> None:
        self.holding = False
        if self.pending is not None:
            raise _Stopped(self.pending)


def _trap_stops(stops: _Stops) -> dict[int, object]:
    import threading  # only here: the CLI path is always the main thread, and pays nothing

    if threading.current_thread() is not threading.main_thread():
        return {}  # only the main thread may set handlers; a pool worker's caller owns them
    previous = {}
    for sig in STOP_SIGNALS:
        current = signal.getsignal(sig)
        if current is not signal.SIG_IGN:
            previous[sig] = signal.signal(sig, stops.handler)
    return previous


def _restore(previous: dict[int, object]) -> None:
    for sig, handler in previous.items():
        signal.signal(sig, handler)


def run_step(
    argv: list[str],
    timeout: int,
    grace: float,
    label: str,
    output: object | None = None,
) -> Outcome:
    """Run `argv` under a ceiling; report the code and whatever it left running.

    With `GOH_TIMINGS` set, the step's wall time lands there as one JSON line
    (lib/step_timings.py), with the CPU its reaped tree spent (`cpu_ms`, BACKLOG 4.4: the box is
    never quiet, so the wall measures the box too), and the child learns its parent's label.
    """
    t0, cpu0 = time.monotonic(), step_timings.children_cpu_ms()
    outcome = _run_step(argv, timeout, grace, label, output)
    step_timings.record(
        label or " ".join(argv),
        (time.monotonic() - t0) * 1000,
        outcome.code,
        "step",
        cpu_ms=step_timings.children_cpu_ms() - cpu0,
    )
    return outcome


def _run_step(
    argv: list[str],
    timeout: int,
    grace: float,
    label: str,
    output: object | None = None,
) -> Outcome:
    stream = None
    merged = False  # stderr folds into stdout only when both are going to the same file
    # `None` INHERITS, and it must. The first version collapsed `None` and the DEVNULL sentinel into
    # one branch, so `goh_step` -- whose entire contract is capturing the step's output into its log --
    # captured nothing: every failing step reported "failed" with an empty log and no failure lines.
    # Caught by tests/test_tui_integration.py (`goh_step names the failures buried above the tail`).
    if output is None:
        stream = None
    elif output == DEVNULL_SENTINEL:
        stream = subprocess.DEVNULL
    elif isinstance(output, str):
        stream = open(output, "ab")  # noqa: SIM115  # closed in the finally below
        merged = True
    else:
        stream = output
    proc = None
    pgid: int | None = None
    stops = _Stops()
    previous = _trap_stops(stops)
    try:
        try:
            proc = subprocess.Popen(  # noqa: S603  # argv, never a shell string: the caller's words
                argv,
                stdin=subprocess.DEVNULL,
                stdout=stream,
                stderr=subprocess.STDOUT if merged else stream,
                env=step_timings.child_env(label or " ".join(argv)),
                start_new_session=True,  # POSIX; its own group, so killpg covers the tree
            )
        except OSError as exc:
            stops.holding = False
            print(f"bounded_run: cannot start {label or argv[0]}: {exc}", file=sys.stderr)
            return Outcome(code=127, descendants=[], timed_out=False)
        pgid = killtree.session_pgid(proc)  # known, never asked for: getpgid of a zombie is ESRCH
        stops.release()
        # WAIT, never poll. `wait(timeout=)` returns the instant the child exits; the 0.2 s poll it
        # replaced charged every step up to a fifth of a second of pure latency -- measured
        # 2026-10-05 as most of a structural run's wall time on a small tree.
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _sweep(pgid, proc.pid, grace)
            try:
                proc.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                pass
            print(f"TIMED OUT after {timeout}s: {label or ' '.join(argv)}", file=sys.stderr)
            return Outcome(
                code=TIMEOUT_EXIT, descendants=_descendants(pid=proc.pid), timed_out=True
            )
        timed_out = False
        # Sampled HERE, at exit, by PROCESS GROUP rather than by ancestry: a leaked child has already
        # been reparented to init, so the walk below finds nothing where the group still finds it.
        if pgid is not None and _group_empty(pgid):
            # The common case, asked in ONE syscall: nothing is left in the group, so there is
            # nothing to name and no `ps` of the whole machine to read (2026-10-06: 15.7 ms per step
            # over 1141 processes -- kernel work of the kind that serializes across sessions).
            return Outcome(code=proc.returncode or 0, descendants=[], timed_out=False)
        table = _process_table()
        left = [
            p for p in (group_members(pgid, table) if pgid is not None else []) if p != proc.pid
        ]
        left += [p for p in _descendants(proc.pid, table) if p not in left]
        left = sorted(set(left))
        if left and stream is None:
            print(
                f"  left {len(left)} process(es) running under it: "
                + ", ".join(str(p) for p in left[:20]),
                file=sys.stderr,
            )
        return Outcome(code=proc.returncode or 0, descendants=left, timed_out=timed_out)
    except _Stopped as stop:
        stops.holding = True  # a second stop must not abandon this sweep
        if proc is not None:
            _sweep(pgid, proc.pid, grace)
            try:
                proc.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                pass
        name = signal.Signals(stop.sig).name
        print(
            f"STOPPED by {name}: {label or ' '.join(argv)} -- its process group was swept",
            file=sys.stderr,
        )
        return Outcome(code=128 + stop.sig, descendants=[], timed_out=False)
    finally:
        _restore(previous)
        if merged:
            stream.close()


def run(argv: list[str], timeout: int, grace: float, label: str, output=None) -> int:
    """Run `argv` with a ceiling. Returns the command's code, or 124.

    `output` is where the step's stdout/stderr go: a path (opened for append) or a file object, or
    `None` to INHERIT this process's.

    **Inheriting is a trap when a leak is possible, and it was measured.** A leaked child keeps the
    write end of an inherited pipe open, so a caller reading that pipe to EOF blocks until the
    child exits — which, for a leaked server, is never. Measured 2026-10-03: a canary whose whole
    job is to report a leaked `sleep 1201` hung for 1201 s reporting it, inside the test that
    asserted the report. `gates/local_ci.sh` is unaffected (it redirects each step to a log FILE),
    and `wrap` passes a path for the same reason: a reporter must not be made to wait by the
    thing it is reporting.
    """
    return run_step(argv, timeout, grace, label, output).code


def _fast_args(argv: list[str]) -> tuple[int, int, str, list[str]] | None:
    """The one shape every gate passes -- `--timeout N [--grace N] [--label L] -- CMD...`, each flag
    once, N a positive integer -- parsed without `argparse`, whose import is ~5 ms on every gate
    step's path. Anything else returns None and goes through argparse, so every error message
    and every unusual spelling behaves exactly as before."""
    seen: dict[str, str] = {}
    i = 0
    while i < len(argv) and argv[i] != "--":
        flag = argv[i]
        if flag not in ("--timeout", "--grace", "--label") or flag in seen or i + 1 >= len(argv):
            return None
        seen[flag] = argv[i + 1]
        i += 2
    rest = argv[i + 1 :]
    if i >= len(argv) or not rest:
        return None
    try:
        timeout = int(seen.get("--timeout", str(DEFAULT_TIMEOUT)))
        grace = int(seen.get("--grace", str(DEFAULT_GRACE)))
    except ValueError:
        return None
    if timeout <= 0 or not all(seen.get(f, "0").isdigit() for f in ("--timeout", "--grace")):
        return None
    return timeout, grace, seen.get("--label", ""), rest


def main(argv=None) -> int:
    fast = _fast_args(sys.argv[1:] if argv is None else list(argv))
    if fast is not None:
        timeout, grace, label, rest = fast
        return run(rest, timeout, grace, label)
    import argparse  # the unusual spelling, an error, or --help: the full parser

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
