"""The load tool's command tree, killed whole: its process group and every descendant.

Split out of tools/load.py (GOH.LOADWAIT, 2026-11-08) when the tool reached its 500-line cap; the
behaviour is G1c's (2026-10-25: a 10h14m run nobody ended) and is pinned by test_load_tool.py.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time

import load_registry as reg

GRACE_S = 2.0  # how long a SIGTERMed process is given before SIGKILL


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


def shell_status(status: int) -> int:
    """A child's `Popen` status as a shell reports it (GOH.LOADRC): `wait()` gives -N for a death
    by signal N, and `sys.exit(-15)` is the byte 241, which nothing reads as "killed by SIGTERM".
    The shell's number is 128+N, so a refused push or a red gate run through this tool fails the
    caller the same way it fails unwrapped."""
    return 128 - status if status < 0 else status
