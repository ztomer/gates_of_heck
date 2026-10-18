#!/usr/bin/env python3
"""The machine-wide gate slots: a counting semaphore over N flock'd files. Driven by lib/gate_slot.sh.

    lib/gate_slot.py default                       # the default N on this machine
    lib/gate_slot.py nested --dir D                # exit 0 (and say so) inside a gate holding a slot
    lib/gate_slot.py hold --dir D [--slots N] --owner PID --repo NAME --handshake FIFO [--poll S]

THE INCIDENT (2026-10-18). Every repo's pre-push runs goh's push gate, and several sessions pushed at
once with nothing limiting them. Two pushes failed on load, not on their changes: zinc's gate was
killed mid-test (exit 144), and games' Release rebuild built 240 of 814 objects inside a 900 s step
limit while the 15-minute load average was 49.6. A step ceiling measured on a quiet box is a lie on
a box running five cold gates.

THE SEMAPHORE. Slot i is the file `<dir>/slot-<i>`, i < N. A gate holds a slot while it holds an
exclusive flock(2) on one of those files; the files are never removed (removing a flock file under a
holder hands the next caller a fresh, unlocked inode). The lock is the flock, never the content: the
content is the holder's record (`pid<TAB>repo<TAB>epoch`), written after the lock is taken, read only
for the wait message.

THE HOLDER IS THIS PROCESS, not the gate's shell. A flock on a shell fd is inherited by everything
the gate starts, and a gate starts daemons (sccache's server outlives the build that spawned it): an
inherited fd would keep the slot held after the gate is gone, until every slot leaked and every push
in the estate waited forever. Python opens its fds non-inheritable (PEP 446) and spawns nothing, so
the lock lives exactly as long as this process. It ends when the gate's shell ends -- it polls for
its parent changing (re-parented on the owner's death, which no pid reuse can fake) -- or when the
shell's cleanup TERMs it, or when anything SIGKILLs it: on every path the kernel drops the flock with
the last fd. The cost, stated: an operator who `kill -9`s the gate's shell alone frees the slot while
the shell's orphaned children may still run.

NESTING. A gate running inside a gate that holds a slot (a metarepo gate running a child's push
gate, land.sh -> push gate) must not wait on a slot its own parent holds: with every slot held by
parents waiting on their children, nothing would ever move. The parent exports GOH_GATE_SLOT=
"<slot file> <owner pid>"; a child whose ancestry contains that owner, and whose slot is still held,
takes no slot of its own. A test environment strips GOH_* -- so, failing the variable, a child whose
ancestry contains the recorded owner of ANY held slot in the same directory is nested too. A
variable naming a pid that is not an ancestor (exported by hand, inherited by a detached process)
is ignored: it never bypasses the semaphore.

Pinned by tests/test_gate_slot.py.
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

import argparse
import fcntl
import os
import subprocess
import sys
import time

if __name__ == "__main__":
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from tui.lib import info, step

REPORT_EVERY_S = 30.0


def default_slots() -> int:
    """One gate per four cores, at least one: a cold gate's build and test steps each use every core."""
    return max(1, (os.cpu_count() or 1) // 4)


def slot_path(directory: str, i: int) -> str:
    return os.path.join(directory, f"slot-{i}")


def _open(path: str) -> int:
    return os.open(path, os.O_RDWR | os.O_CREAT, 0o644)


def try_take(path: str) -> int | None:
    """An fd holding the exclusive lock on `path`, or None when it is held."""
    fd = _open(path)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def held(path: str) -> bool:
    """Whether someone holds `path` now (asked by taking and dropping the lock at once)."""
    fd = try_take(path)
    if fd is None:
        return True
    os.close(fd)
    return False


def record(path: str) -> tuple[int, str, float] | None:
    try:
        with open(path, encoding="utf-8") as f:
            pid, repo, since = f.read().strip().split("\t")
        return int(pid), repo, float(since)
    except (OSError, ValueError):
        return None


def _ago(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 60}m {s % 60:02d}s" if s >= 60 else f"{s}s"


def describe(path: str, now: float) -> str:
    rec = record(path)
    if rec is None:
        return f"{os.path.basename(path)}: held (no record yet)"
    pid, repo, since = rec
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(since))
    return f"{os.path.basename(path)}: {repo} (pid {pid}, since {stamp}, {_ago(now - since)})"


def ancestors() -> set[int]:
    """This process's ancestry, from one `ps` listing (a spawn per level costs a process each)."""
    out = subprocess.run(
        ["ps", "-axo", "pid=,ppid="],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    ).stdout
    parent: dict[int, int] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            parent[int(parts[0])] = int(parts[1])
    seen: set[int] = set()
    pid = os.getppid()
    while pid > 1 and pid not in seen:
        seen.add(pid)
        pid = parent.get(pid, 0)
    return seen


def _slot_files(directory: str) -> list[str]:
    try:
        names = sorted(n for n in os.listdir(directory) if n.startswith("slot-"))
    except OSError:
        return []
    return [os.path.join(directory, n) for n in names]


def nested(directory: str) -> str | None:
    """The slot this process's ancestry holds in `directory`, described; None when it holds none."""
    mine = ancestors()
    claim = os.environ.get("GOH_GATE_SLOT", "").rsplit(" ", 1)
    if len(claim) == 2 and claim[1].isdigit():
        path, pid = claim[0], int(claim[1])
        if os.path.dirname(path) == directory and pid in mine and held(path):
            return f"{os.path.basename(path)} (pid {pid}, GOH_GATE_SLOT)"
    for path in _slot_files(directory):
        rec = record(path)
        if rec is not None and rec[0] in mine and held(path):
            return f"{os.path.basename(path)} (pid {rec[0]}, a recorded holder above this gate)"
    return None


def acquire(directory: str, slots: int, poll: float, owner: int) -> tuple[int, int, float]:
    """(slot index, fd holding it, seconds waited). Says who holds the slots while it waits."""
    start = time.monotonic()
    next_report = 0.0
    while True:
        for i in range(slots):
            fd = try_take(slot_path(directory, i))
            if fd is not None:
                return i, fd, time.monotonic() - start
        waited = time.monotonic() - start
        if waited >= next_report:
            now = time.time()
            if next_report == 0.0:
                info(
                    f"all {slots} gate slots are held -- waiting for one (GOH_GATE_SLOTS={slots}, {directory}):"
                )
            else:
                info(f"waited {_ago(waited)} for a gate slot; still held:")
            for i in range(slots):
                step(describe(slot_path(directory, i), now))
            next_report += REPORT_EVERY_S
        if not _alive(owner):
            sys.exit(3)
        time.sleep(poll)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def hold(args: argparse.Namespace) -> int:
    start_parent = os.getppid()
    os.makedirs(args.dir, exist_ok=True)
    # The handshake first: the shell is reading it, and polls this process's liveness meanwhile.
    shake = os.open(args.handshake, os.O_WRONLY)
    slots = args.slots if args.slots else default_slots()
    idx, fd, waited = acquire(args.dir, slots, args.poll, args.owner)
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, f"{args.owner}\t{args.repo}\t{time.time():.0f}\n".encode())
    os.write(shake, f"{idx} {slots} {waited:.0f} {slot_path(args.dir, idx)}\n".encode())
    os.close(shake)
    # Hold nothing of the caller's open past here: a pipe this process kept would make whoever reads
    # the gate's output wait for the slot's release instead of the gate's end.
    devnull = os.open(os.devnull, os.O_RDWR)
    for n in (0, 1, 2):
        os.dup2(devnull, n)
    while os.getppid() == start_parent and _alive(args.owner):
        time.sleep(args.poll)
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("default")
    n = sub.add_parser("nested")
    n.add_argument("--dir", required=True)
    h = sub.add_parser("hold")
    h.add_argument("--dir", required=True)
    h.add_argument("--slots", type=int, default=0)
    h.add_argument("--owner", type=int, required=True)
    h.add_argument("--repo", required=True)
    h.add_argument("--handshake", required=True)
    h.add_argument("--poll", type=float, default=0.5)
    args = ap.parse_args(argv)
    if args.cmd == "default":
        print(default_slots())
        return 0
    if args.cmd == "nested":
        found = nested(os.path.realpath(args.dir))
        if found is None:
            return 1
        print(found)
        return 0
    args.dir = os.path.realpath(args.dir)
    return hold(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
