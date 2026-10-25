#!/usr/bin/env python3
"""The ONE way to make load on this machine -- and it reaps itself.

    tools/load.py 8 -- ./my_test.sh        # eight spinners, the command, then no spinner
    tools/load.py --max 2h 8 -- ./long.sh  # the same, with a longer deadline than the default
    tools/load.py --status                 # who holds the slot
    tools/load.py --reap                   # clear a slot whose owner is gone

THE CLASS (2026-10-20). An agent that had to reproduce a timing defect spawned its own busy loops
(`for j in $(seq 1 12); do (while :; do :; done) & done`), and the shell that owned them was
killed before it reached its own cleanup. The spinners were re-parented to launchd (ppid 1) and
kept burning a core each for hours -- 24 for 3h12m (load average 206), then 12 for 26m, then 8 for
28m -- and the load was blamed on other agents. Three times in one day.

WHAT THIS TOOL DOES, IN THE ORDER THAT MATTERS.

  1. It REFUSES to start while another experiment holds the slot (one registry file, created
     with O_EXCL semantics): two load experiments at once measure a machine that is twice as
     busy, and neither can be told from the other afterwards.
  2. It REGISTERS every spinner it starts -- the pid, and the process's start time, because a
     pid alone is not an identity -- in that registry, BEFORE the spinner is allowed to burn.
  3. It REAPS on the way out: EXIT, TERM, INT and HUP all kill exactly the pids it registered,
     and it VERIFIES afterwards that none of them is alive.
  4. It REAPS ITSELF when its own owner dies: a watchdog notices that the parent is gone (the
     shell that ran it was killed) and cleans up, so the common leak cannot happen at all.
  5. It is BACKED BY a registry, not by a memory: a launchd job reads the registry every minute
     and kills the pids whose parent is 1. That is the case a trap cannot cover -- this process
     SIGKILLed, so nothing here ran.

  6. It is BOUNDED by its own clock (G1c, 2026-10-25). `load.py 32 -- taskpolicy -b loop.sh` -- a
     starvation proof wrapping `swift test` three times -- ran 10h14m at load average ~154: the
     background-priority test starved under 32 spinners on 16 cores and hung, and nothing ended
     it. So a deadline is ALWAYS in force (DEFAULT_MAX_S unless `--max` names another), and on
     expiry the command's whole tree -- its process group, and every descendant, including one
     that left the group -- and every spinner are killed, and the tool exits EXIT_DEADLINE naming
     the deadline. And more spinners than cores is refused unless `--oversubscribe` says it is
     meant: N > ncpu starves the thing under test, which is how a proof becomes a hang.

The command runs in a process group of its own, so the group can be killed whole; a command that
reads from the terminal is therefore not supported (a background group that reads a tty stops).

A spinner is released only AFTER it is registered: it waits on a FIFO for a release byte, and if
the pipe closes first (this process died before releasing it) the read fails and the spinner exits
without ever having burned a core.
"""

from __future__ import annotations

import argparse
import math
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import load_registry as reg  # noqa: E402
import load_reap  # noqa: E402

EXIT_BUSY = 3  # the slot is taken
EXIT_LEAK = 70  # a spinner outlived the cleanup: the experiment's verdict is not trustworthy
EXIT_DEADLINE = 124  # the deadline expired (the number `timeout(1)` uses)
DEFAULT_MAX_S = 1800.0  # the deadline when none is named: 30 minutes
UNITS = {"s": 1.0, "m": 60.0, "h": 3600.0}
GRACE_S = 2.0
RELEASE_S = 5.0  # how long a spinner may wait for its release byte


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="tools/load.py",
        description="run a command under N busy loops, and leave nothing behind",
    )
    parser.add_argument("--dir", default=None, help="the load directory (default GOH_LOAD_DIR)")
    parser.add_argument("--status", action="store_true", help="print who holds the slot")
    parser.add_argument("--reap", action="store_true", help="kill the orphans a dead owner left")
    parser.add_argument(
        "--max",
        default=None,
        help=f"the deadline: seconds, or 90m / 2h (default {DEFAULT_MAX_S:g}s); on expiry the "
        "command and the spinners are killed and the tool exits 124",
    )
    parser.add_argument(
        "--oversubscribe",
        action="store_true",
        help="allow more spinners than cores (refused without it)",
    )
    parser.add_argument("rest", nargs=argparse.REMAINDER, help="N -- COMMAND [ARG...]")
    return parser.parse_args(argv)


def parse_duration(text: str) -> float:
    """A deadline in seconds from `45`, `30s`, `90m` or `2h`; anything that bounds nothing raises."""
    raw = text.strip().lower()
    scale = 1.0
    if raw and raw[-1] in UNITS:
        scale = UNITS[raw[-1]]
        raw = raw[:-1]
    try:
        value = float(raw) * scale
    except ValueError:
        raise ValueError(f"--max {text!r} is not a duration (seconds, or 90m / 2h)") from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"--max {text!r} bounds nothing: a deadline is a positive, finite time")
    return value


def cores() -> int:
    """The cores spinners compete for. GOH_LOAD_NCPU is the seam that tests both directions."""
    seam = os.environ.get("GOH_LOAD_NCPU", "").strip()
    if seam:
        return max(1, int(seam))
    return os.cpu_count() or 1


def _descendants(root: int) -> list[int]:
    """Every live descendant of `root`, read from the process table (children before none)."""
    try:
        listing = subprocess.run(
            ["ps", "-ax", "-o", "pid=,ppid="],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=10,
            check=False,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    children: dict[int, list[int]] = {}
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            children.setdefault(int(parts[1]), []).append(int(parts[0]))
    found: list[int] = []
    frontier = [root]
    while frontier:
        pid = frontier.pop()
        for child in children.get(pid, []):
            if child not in found:
                found.append(child)
                frontier.append(child)
    return found


def _signal_tree(group: int, pids: list[int], number: int) -> None:
    try:
        os.killpg(group, number)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    for pid in pids:
        try:
            os.kill(pid, number)
        except (ProcessLookupError, PermissionError, OSError):
            pass


def kill_tree(proc: subprocess.Popen) -> None:
    """Kill the command's whole tree: its process group, and every descendant -- one that left
    the group with a session of its own too -- snapshotted BEFORE anything dies, because a child
    whose parent is killed is re-parented to launchd and is no longer anyone's descendant."""
    tree = [(pid, reg.pid_start(pid) or "") for pid in _descendants(proc.pid)]
    pids = [pid for pid, _start in tree]
    _signal_tree(proc.pid, [proc.pid, *pids], signal.SIGTERM)
    give_up = time.monotonic() + GRACE_S
    while time.monotonic() < give_up:
        if proc.poll() is not None and not any(reg.pid_alive(pid) for pid in pids):
            break
        time.sleep(0.05)
    # The pid is checked against the start time it had: a number the kernel handed to somebody
    # else in the meantime is not ours to kill.
    same = [pid for pid, start in tree if not start or reg.pid_start(pid) == start]
    _signal_tree(proc.pid, same, signal.SIGKILL)
    try:
        proc.wait(timeout=GRACE_S)
    except subprocess.TimeoutExpired:
        pass


def _directory(opts: argparse.Namespace) -> Path:
    if opts.dir:
        return Path(opts.dir).expanduser()
    return reg.load_dir()


def _describe(data: dict) -> str:
    pids = data.get("pids")
    count = len(pids) if isinstance(pids, list) else 0
    command = data.get("command")
    shown = " ".join(str(part) for part in command) if isinstance(command, list) else ""
    return f"owner pid {data.get('owner_pid')} (started {data.get('owner_start')}), {count} spinner(s), command: {shown}"


def _status(directory: Path) -> int:
    path = reg.registry_path(directory)
    if not path.exists():
        print(f"load: no experiment holds {path}")
        return 0
    data = reg.read(path)
    if data is None:
        print(f"load: {path} is unreadable; remove it by hand if no experiment is running")
        return 1
    print(f"load: {path} holds an experiment: {_describe(data)}")
    return 0


def _reap(directory: Path) -> int:
    report = load_reap.reap_all(directory)
    print(f"load: reap: {report.summary()}")
    return 0


def _stop(procs: list[subprocess.Popen]) -> list[int]:
    """Kill the spinners this tool started, and report the ones that survived."""
    for proc in procs:
        if proc.poll() is None:
            try:
                proc.terminate()
            except (ProcessLookupError, OSError):
                pass
    deadline = time.monotonic() + GRACE_S
    for proc in procs:
        try:
            proc.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
    survivors: list[int] = []
    for proc in procs:
        if proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=GRACE_S)
            except subprocess.TimeoutExpired:
                pass
        if proc.poll() is None:
            survivors.append(proc.pid)
    return survivors


class Experiment:
    """One load experiment: the spinners, the registry, and the ways it can end."""

    def __init__(
        self, directory: Path, count: int, command: list[str], max_s: float = DEFAULT_MAX_S
    ) -> None:
        self.directory = directory
        self.count = count
        self.command = command
        self.max_s = max_s
        self.child: subprocess.Popen | None = None
        self.registry = reg.registry_path(directory)
        self.procs: list[subprocess.Popen] = []
        self.fifos: list[Path] = []
        self.data: dict = {}
        self.done = False
        self.lock = threading.RLock()

    def claim(self) -> None:
        """Take the slot, refusing one that is taken."""
        taken = reg.registry_files(self.directory)
        if taken:
            raise FileExistsError(str(taken[0]))
        now = time.time()
        self.data = {
            "version": reg.VERSION,
            "owner_pid": os.getpid(),
            "owner_start": reg.pid_start(os.getpid()) or "",
            "started_at": now,
            "max_s": self.max_s,
            "deadline_at": now + self.max_s,
            "command": list(self.command),
            "pids": [],
        }
        reg.create(self.registry, self.data)

    def _release(self, fifo: Path) -> None:
        """Open the release pipe for writing and hand the spinner its byte.

        The spinner blocks in `read` until a writer appears, so it cannot burn before it is
        registered; if this process dies first, the pipe closes, the read fails, and the spinner
        exits without ever having burned a core.
        """
        deadline = time.monotonic() + RELEASE_S
        while True:
            try:
                handle = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
                break
            except OSError:
                if time.monotonic() > deadline:
                    return
                time.sleep(0.01)
        try:
            os.write(handle, b"\n")
        except OSError:
            pass
        finally:
            os.close(handle)

    def start(self) -> None:
        """Start the spinners, registering each one BEFORE it is allowed to burn."""
        for index in range(self.count):
            # The pipe's NAME carries the owner (this process's pid), and it is the house's ONE
            # definition of that name (load_registry.release_name): the backstop reads the owner
            # out of it when the registry is gone, so the two have to agree by construction.
            fifo = self.directory / reg.release_name(os.getpid(), index)
            reg.remove(fifo)
            os.mkfifo(fifo)
            self.fifos.append(fifo)
            proc = subprocess.Popen(
                # The spinner waits for its release byte on the FIFO, and exits if the pipe
                # closes first: a spinner that was never released never spins. Once released it
                # burns a core exactly like the raw form the guard refuses. The shape is the
                # house's ONE definition (load_registry.SPINNER_COMMAND), because the backstop
                # has to recognise an orphan whose registry is gone.
                ["/bin/sh", "-c", reg.spinner_command(fifo)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self.procs.append(proc)
            self.data["pids"].append({"pid": proc.pid, "start": reg.pid_start(proc.pid) or ""})
            reg.write(self.registry, self.data)
            self._release(fifo)
        pids = " ".join(str(proc.pid) for proc in self.procs)
        print(f"load: {len(self.procs)} spinner(s) started: {pids}", flush=True)

    def abort(self) -> list[int]:
        """The experiment is cut short (deadline, signal, a dead owner): the command's tree goes
        too, because a command left running is the 10h14m run all over again."""
        if self.child is not None and not self.done:
            kill_tree(self.child)
        return self.finish()

    def finish(self) -> list[int]:
        """Kill the spinners, verify, and drop the registry unless one survived.

        ONE cleanup, and a second caller WAITS for it (2026-10-25): the owner-watch thread and the
        main thread can both arrive here -- the watch aborts the command, the main thread wakes on
        its death -- and a second caller that returned at once on `done` went on to `os._exit`,
        killing the first one's cleanup half-way. A re-entrant lock, because a signal handler runs
        on the main thread and may arrive while the main thread is already inside.
        """
        with self.lock:
            if self.done:
                return []
            self.done = True
            survivors = _stop(self.procs)
            for fifo in self.fifos:
                reg.remove(fifo)
            if survivors:
                self.data["pids"] = [{"pid": pid, "start": ""} for pid in survivors]
                reg.write(self.registry, self.data)
            else:
                reg.remove(self.registry)
            return survivors


def _watch_parent(experiment: Experiment, parent: int) -> None:
    """The owner died: clean up, because nothing else will."""
    while True:
        time.sleep(0.25)
        if os.getppid() != parent:
            experiment.abort()
            os._exit(0)


def _install_handlers(experiment: Experiment) -> None:
    def handler(number: int, _frame: object) -> None:
        experiment.abort()
        os._exit(128 + number)

    for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(number, handler)


def main(argv: list[str]) -> int:
    opts = _parse(argv)
    directory = _directory(opts)
    if opts.status:
        return _status(directory)
    if opts.reap:
        return _reap(directory)
    rest = list(opts.rest)
    if rest and rest[0] == "--":
        rest = rest[1:]
    if len(rest) < 2:
        print("usage: tools/load.py [options] N -- COMMAND [ARG...]", file=sys.stderr)
        return 2
    try:
        count = int(rest[0])
    except ValueError:
        print(f"load: N must be a number, not {rest[0]!r}", file=sys.stderr)
        return 2
    command = rest[1:]
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("usage: tools/load.py [options] N -- COMMAND [ARG...]", file=sys.stderr)
        return 2
    if count < 0:
        print("load: N must not be negative", file=sys.stderr)
        return 2
    try:
        max_s = DEFAULT_MAX_S if opts.max is None else parse_duration(opts.max)
    except ValueError as error:
        print(f"load: {error}", file=sys.stderr)
        return 2
    ncpu = cores()
    if count > ncpu and not opts.oversubscribe:
        print(
            f"load: {count} spinners on {ncpu} core(s) starves the command under test (a starved "
            "test hangs, and a hang is not a measurement); pass --oversubscribe if that is meant",
            file=sys.stderr,
        )
        return 2

    experiment = Experiment(directory, count, command, max_s)
    try:
        experiment.claim()
    except FileExistsError as error:
        other = Path(str(error))
        data = reg.read(other) or {}
        print(f"load: another load experiment already holds the slot: {other}", file=sys.stderr)
        print(f"      {_describe(data)}", file=sys.stderr)
        print(
            "      One experiment at a time: a second load measures a machine twice as busy, and "
            "neither can be told from the other afterwards.",
            file=sys.stderr,
        )
        print(
            "      If that experiment is gone: tools/load.py --reap (or wait for the backstop, "
            "which reads the same registry every minute).",
            file=sys.stderr,
        )
        return EXIT_BUSY
    except OSError as error:
        print(f"load: cannot take the slot: {error}", file=sys.stderr)
        return 2

    parent = os.getppid()
    _install_handlers(experiment)
    if parent != 1:
        # A tool whose owner is gone has nobody to clean up after it: it does that itself.
        threading.Thread(target=_watch_parent, args=(experiment, parent), daemon=True).start()
    try:
        experiment.start()
    except OSError as error:
        experiment.finish()
        print(f"load: cannot start the spinners: {error}", file=sys.stderr)
        return 2
    try:
        # A group of its own, so the deadline can kill the command whole.
        experiment.child = subprocess.Popen(command, stdin=None, process_group=0)
    except FileNotFoundError:
        experiment.finish()
        print(f"load: no such command: {command[0]}", file=sys.stderr)
        return 127
    try:
        status = experiment.child.wait(
            timeout=max(0.0, experiment.data["deadline_at"] - time.time())
        )
    except subprocess.TimeoutExpired:
        survivors = experiment.abort()
        print(
            f"load: deadline of {max_s:g}s expired: the command (pid {experiment.child.pid}), its "
            f"process tree and the spinners were killed; pass --max to give it longer",
            file=sys.stderr,
        )
        if survivors:
            print(f"load: {len(survivors)} spinner(s) outlived the cleanup", file=sys.stderr)
        return EXIT_DEADLINE
    except KeyboardInterrupt:
        experiment.abort()
        return 130
    survivors = experiment.finish()
    if survivors:
        print(
            f"load: {len(survivors)} spinner(s) outlived the cleanup: "
            f"{' '.join(str(pid) for pid in survivors)}; the registry keeps them and the backstop "
            f"kills them.",
            file=sys.stderr,
        )
        return EXIT_LEAK
    print("load: spinners stopped, registry cleared", flush=True)
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
