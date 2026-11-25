"""The load tool's command tree, killed whole: its process group and every descendant.

Split out of tools/load.py (GOH.LOADWAIT, 2026-11-08) when the tool reached its 500-line cap; the
behaviour is G1c's (2026-10-25: a 10h14m run nobody ended) and is pinned by test_load_tool.py.

`tools/load.py`'s command tree, killed whole: SIGTERM to its process group and to every descendant
snapshotted BEFORE anything dies (one that left the group too), then SIGKILL to those whose start
time still matches (a recycled pid is not ours). Split out of `load.py` at its 500-line cap
(GOH.LOADWAIT). Pinned by `test_load_tool.py`.
"""

from __future__ import annotations

import contextlib
import signal
import subprocess
import time

import pid_owner

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


def _signal_tree(proc: subprocess.Popen, tree: list[pid_owner.Stamp], number: int) -> None:
    """The command's group through its handle, then every stamped descendant still that process."""
    pid_owner.signal_child_group(proc, number)
    with contextlib.suppress(OSError):
        proc.send_signal(number)
    for held in tree:
        pid_owner.signal(held, number)


def kill_tree(proc: subprocess.Popen) -> None:
    """Kill the command's whole tree: its process group, and every descendant -- one that left
    the group with a session of its own too -- snapshotted BEFORE anything dies, because a child
    whose parent is killed is re-parented to launchd and is no longer anyone's descendant.

    Each descendant is STAMPED when seen (lib/pid_owner.py): a number the kernel handed to
    somebody else between the sighting and the kill is not ours to kill (GOH.STRAYSIGNAL)."""
    tree = [s for s in map(pid_owner.stamp, _descendants(proc.pid)) if s is not None]
    _signal_tree(proc, tree, signal.SIGTERM)
    give_up = time.monotonic() + GRACE_S
    while time.monotonic() < give_up:
        if proc.poll() is not None and not any(pid_owner.same(s) for s in tree):
            break
        time.sleep(0.05)
    _signal_tree(proc, tree, signal.SIGKILL)
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
