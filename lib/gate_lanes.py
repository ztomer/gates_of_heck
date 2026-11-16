"""The gate slots' lanes, classes and waiter tickets (GOH.SLOTFAIR): who takes a freed slot.

lib/gate_slot.py's semaphore says HOW MANY gates run; this says WHICH waiter goes next. Imported by
lib/gate_slot.py (`acquire`, `slot_path`) and lib/admission.py (through gate_slot). Pinned by
tests/test_gate_slot_fair.py.
"""

from __future__ import annotations

import contextlib
import os
import time

import lock_order

# THE LANES AND THE CLASSES (GOH.SLOTFAIR, 2026-11-16). One lane of N slots for every gate was a
# priority inversion: a commit gate (`tools/gate.sh --staged`, minutes at most) waited 38+ min
# behind a 49-min cargo test and a 15-min push gate holding both slots, and queue commits timed out
# at 600 s the same way. A gate's CLASS picks its lane and its rank in the lane's queue:
#   short  the commit gate (gates/commit_gate.sh marks its gate.sh): the SHORT lane, N slots of its
#          own that no long gate takes, so it waits only behind other commit gates
#   ship   a push gate (gates/push_gate.sh) or a land (tools/land.sh): the long lane, ranked first
#   work   everything else (a hand-run `gate.sh --full`, an agent's `load.py 0 -- cargo test`):
#          the long lane, after every ship waiter (land and push over the rest, D-0008)
LANES = {"long": "slot", "short": "short"}
CLASSES = {"short": ("short", 0), "ship": ("long", 0), "work": ("long", 1)}
DEFAULT_CLASS = "work"


def lane_of(gate_class: str) -> tuple[str, int]:
    """(lane, rank) of a gate class; ValueError naming the classes for any other word."""
    if gate_class not in CLASSES:
        raise ValueError(f"GOH_GATE_CLASS must be one of {', '.join(CLASSES)} (got {gate_class!r})")
    return CLASSES[gate_class]


WAITING = "waiting"  # <dir>/waiting/: one ticket file per live waiter


class Ticket:
    """A waiter's place in its lane's queue (GOH.SLOTFAIR): `waiting/<lane>-<rank>-<seq>-<pid>`.

    THE DEFECT IT ENDS (2026-11-15/16). A freed slot went to whichever waiter's try-lock came
    first: a monitor push waited 20m30s while six gates that asked after it each took a slot. Now
    only the FIRST live ticket of the lane -- lowest (rank, seq) -- may take a slot; `seq` is the
    system-wide monotonic clock at arrival (ns), so arrival order is the order, and a ship ticket
    (rank 0) goes ahead of every work ticket (rank 1).

    LIVENESS IS THE FLOCK, as for the slots: the waiter holds an exclusive lock on its ticket for as
    long as it lives, so a ticket nobody holds belongs to a dead waiter and is reaped by the first
    reader that can lock it -- a SIGKILLed waiter never blocks the queue. The file is locked BEFORE
    it has its name (made under a temporary name, locked, then hard-linked in), so no reader can
    see a live ticket unlocked and reap it."""

    def __init__(self, directory: str, lane: str, rank: int) -> None:
        self.dir = os.path.join(directory, WAITING)
        self.lane = lane
        os.makedirs(self.dir, exist_ok=True)
        self.key = (rank, time.monotonic_ns(), os.getpid())
        self.name = f"{lane}-{rank}-{self.key[1]:020d}-{self.key[2]}"
        self.path = os.path.join(self.dir, self.name)
        draft = os.path.join(self.dir, f".draft-{self.name}")
        self.fd = os.open(draft, os.O_RDWR | os.O_CREAT, 0o644)
        lock_order.flock(self.fd, "gate_slot", wait=False)  # fresh inode: never taken
        os.link(draft, self.path)
        os.unlink(draft)

    def ahead(self) -> list[str]:
        """The live tickets of this lane ahead of this one, first first; dead ones are reaped."""
        try:
            names = os.listdir(self.dir)
        except OSError:
            return []
        found = []
        for name in names:
            parts = name.split("-")
            if len(parts) != 4 or parts[0] != self.lane or name == self.name:
                continue
            try:
                key = (int(parts[1]), int(parts[2]), int(parts[3]))
            except ValueError:
                continue
            if key < self.key and _live_ticket(os.path.join(self.dir, name)):
                found.append((key, name))
        return [name for _, name in sorted(found)]

    def leave(self) -> None:
        with contextlib.suppress(OSError):
            os.unlink(self.path)
        os.close(self.fd)


def _live_ticket(path: str) -> bool:
    """Whether a waiter holds the ticket at `path`; one nobody holds is a dead waiter's, removed."""
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError:
        return False  # reaped meanwhile
    try:
        lock_order.flock(fd, "gate_slot", wait=False)
    except BlockingIOError:
        return True
    else:
        try:
            if os.stat(path).st_ino == os.fstat(fd).st_ino:  # names are never reused (seq, pid)
                os.unlink(path)
        except OSError:
            pass
        return False
    finally:
        os.close(fd)
