"""The gate slots' lanes, classes and waiter tickets (GOH.SLOTFAIR): who takes a freed slot.

lib/gate_slot.py's semaphore says HOW MANY gates run; this says WHICH waiter goes next. Imported by
lib/gate_slot.py (`acquire`, `slot_path`) and lib/admission.py (through gate_slot). Pinned by
tests/test_gate_slot_fair.py.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import time

import lock_order

# THE LANES AND THE CLASSES (GOH.SLOTFAIR, 2026-11-16; GOH.SLOTPRIORITY, 2026-11-21). One lane of N
# slots for every gate was a priority inversion: a commit gate (`tools/gate.sh --staged`, minutes at
# most) waited 38+ min behind a 49-min cargo test and a 15-min push gate holding both slots. Then
# a second one inside the long lane: every push gate was `ship`, ranked with a land, so on
# 2026-11-21 a land's gate waited 161 s behind the coordinator's `.loop` push gate and a consumer
# lane's `tools/ci_hook.sh` (10 min), and earlier lands 293 s behind the pin sweep's consumer
# pushes -- and lands are the critical path. A gate's CLASS picks its lane and its rank in the
# lane's queue, and the CALLER declares it (GOH_GATE_CLASS, consumed by the admission); it is
# never inferred from a command line:
#   short  the commit gate (gates/commit_gate.sh marks its gate.sh) and a land's pre-step: the SHORT
#          lane, N slots of its own that no long gate takes, so it waits only behind other shorts
#   land   a land's gate (lib/land_gate.sh): the long lane, ranked first
#   ship   `goh ship`'s full suite (lib/ship_full_run.py) and its push (tools/ship.py)
#   lane   a gate run in a loop lane's worktree (GOH.LANECLASS): derived, not asked of each lane --
#          a gate whose checkout is under the lanes root ($LOOPCTL_LANES_ROOT, else
#          $XDG_STATE_HOME/loop/lanes, else ~/.local/state/loop/lanes) is `lane` unless its caller
#          declares another class (a land's gate says `land` there, `goh ship` says `ship`)
#   work   everything else, the DEFAULT and the lowest: the pin sweep's consumer pushes, any push
#          gate nobody classed (another repo's, the coordinator's), a `gate.sh --full`, a hand run
# Within a rank a freed slot goes to the longest waiter; a waiter older than the STARVATION BOUND
# (`GOH_GATE_SLOT_STARVE_MIN`, minutes) outranks every class, oldest first, so a lower class waits
# behind higher ones for at most that long.
LANES = {"long": "slot", "short": "short"}
CLASSES = {
    "short": ("short", 0),
    "land": ("long", 0),
    "ship": ("long", 1),
    "lane": ("long", 2),
    "work": ("long", 3),
}
DEFAULT_CLASS = "work"
CLASS_ENV = "GOH_GATE_CLASS"  # the caller's word for one admission
LANES_ROOT_ENV = "LOOPCTL_LANES_ROOT"  # the loop's lane worktrees (GOH.LANECLASS)
STARVE_ENV = "GOH_GATE_SLOT_STARVE_MIN"
STARVE_MIN = 15.0  # the bound when nothing names one: a land gate's ~12 min, and a margin
PROMOTED = -1  # the rank a starved ticket takes: ahead of every class


def lanes_root(environ: dict[str, str] | None = None) -> str:
    """The loop's lanes root, resolved: $LOOPCTL_LANES_ROOT, else $XDG_STATE_HOME/loop/lanes, else
    ~/.local/state/loop/lanes (the spelling of loopctl's own)."""
    env = os.environ if environ is None else environ
    named = env.get(LANES_ROOT_ENV, "").strip()
    if not named:
        state = env.get("XDG_STATE_HOME", "").strip() or os.path.join(
            os.path.expanduser("~"), ".local", "state"
        )
        named = os.path.join(state, "loop", "lanes")
    return os.path.realpath(named)


def default_class(checkout: str, environ: dict[str, str] | None = None) -> str:
    """The class a gate takes its slot by: the caller's GOH_GATE_CLASS if it declared one; else
    `lane` when `checkout` is under the lanes root (GOH.LANECLASS); else `work`."""
    env = os.environ if environ is None else environ
    if declared := env.get(CLASS_ENV, "").strip():
        return declared
    root = lanes_root(env)
    here = os.path.realpath(checkout)
    return "lane" if here == root or here.startswith(root + os.sep) else DEFAULT_CLASS


def lane_of(gate_class: str) -> tuple[str, int]:
    """(lane, rank) of a gate class; ValueError naming the classes for any other word."""
    if gate_class not in CLASSES:
        raise ValueError(f"GOH_GATE_CLASS must be one of {', '.join(CLASSES)} (got {gate_class!r})")
    return CLASSES[gate_class]


def class_of(lane: str, rank: int) -> str:
    """The class a ticket's (lane, rank) was taken by; `?` for one no class names."""
    return next((c for c, lr in CLASSES.items() if lr == (lane, rank)), "?")


def _gatesrc(key: str, root: str = ".") -> str:
    """KEY as ROOT's .gatesrc sets it, read in a child (tools/land.sh's `gatesrc_var`), else ""."""
    if not os.path.isfile(os.path.join(root, ".gatesrc")):
        return ""
    prog = 'unset "$1"; . ./.gatesrc >/dev/null 2>&1 </dev/null; printf %s "${!1-}"'
    try:
        res = subprocess.run(["bash", "-c", prog, "gatesrc", key], cwd=root, capture_output=True,
                             text=True, stdin=subprocess.DEVNULL, timeout=30,
                             check=False)  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return res.stdout if res.returncode == 0 else ""


def starve_ns(environ: dict[str, str] | None = None, root: str = ".") -> int:
    """The starvation bound in ns: `GOH_GATE_SLOT_STARVE_MIN` minutes, from the environment, else
    the waiter's repository's .gatesrc (ROOT, its working directory), else STARVE_MIN. Each waiter
    writes its own bound into its ticket, so every reader orders the queue alike. ValueError for
    anything but a positive number."""
    env = os.environ if environ is None else environ
    raw = (env[STARVE_ENV] if STARVE_ENV in env else _gatesrc(STARVE_ENV, root)).strip()
    try:
        minutes = float(raw) if raw else STARVE_MIN
    except ValueError:
        minutes = -1.0
    if not minutes > 0:
        raise ValueError(f"{STARVE_ENV} must be a positive number of minutes (got {raw!r})")
    return int(minutes * 60e9)


def parse_ticket(name: str) -> tuple[str, int, int, int, int | None] | None:
    """(lane, rank, seq, pid, promote-at ns) of a ticket's name, None for any other file. A ticket
    written before the bound (four fields) is never promoted."""
    parts = name.split("-")
    if len(parts) not in (4, 5) or parts[0] not in LANES:
        return None
    try:
        nums = [int(p) for p in parts[1:]]
    except ValueError:
        return None
    return parts[0], nums[0], nums[1], nums[2], nums[3] if len(nums) == 4 else None


def effective(rank: int, seq: int, pid: int, promote: int | None, now: int) -> tuple[int, int, int]:
    """A ticket's place at `now`: its rank, or PROMOTED once past its bound; then age."""
    return (PROMOTED if promote is not None and now >= promote else rank, seq, pid)


WAITING = "waiting"  # <dir>/waiting/: one ticket file per live waiter


class Ticket:
    """A waiter's place in its lane's queue (GOH.SLOTFAIR):
    `waiting/<lane>-<rank>-<seq>-<pid>-<promote>`.

    THE DEFECT IT ENDS (2026-11-15/16). A freed slot went to whichever waiter's try-lock came
    first: a monitor push waited 20m30s while six gates that asked after it each took a slot. Now
    only the FIRST live ticket of the lane -- lowest (rank, seq) -- may take a slot; `seq` is the
    system-wide monotonic clock at arrival (ns), so arrival order is the order, and a higher class
    (lower rank: land 0, ship 1, lane 2, work 3) goes ahead of every lower one -- until a ticket's
    `promote` time (arrival plus the starvation bound, written into its name by its own waiter, so
    every reader orders the queue the same way) passes, when it goes ahead of every class.

    LIVENESS IS THE FLOCK, as for the slots: the waiter holds an exclusive lock on its ticket for as
    long as it lives, so a ticket nobody holds belongs to a dead waiter and is reaped by the first
    reader that can lock it -- a SIGKILLed waiter never blocks the queue. The file is locked BEFORE
    it has its name (made under a temporary name, locked, then hard-linked in), so no reader can
    see a live ticket unlocked and reap it."""

    def __init__(
        self, directory: str, lane: str, rank: int, clock=time.monotonic_ns, bound_ns: int = 0
    ) -> None:
        self.dir = os.path.join(directory, WAITING)
        self.lane = lane
        self.clock = clock  # a seam: the suite's fake clock (system-wide monotonic ns otherwise)
        os.makedirs(self.dir, exist_ok=True)
        seq = clock()
        self.rank, self.seq, self.pid = rank, seq, os.getpid()
        self.promote = seq + (bound_ns or starve_ns())
        self.name = f"{lane}-{rank}-{seq:020d}-{self.pid}-{self.promote:020d}"
        self.path = os.path.join(self.dir, self.name)
        draft = os.path.join(self.dir, f".draft-{self.name}")
        self.fd = os.open(draft, os.O_RDWR | os.O_CREAT, 0o644)
        lock_order.flock(self.fd, "gate_slot", wait=False)  # fresh inode: never taken
        os.link(draft, self.path)
        os.unlink(draft)

    @property
    def key(self) -> tuple[int, int, int]:
        """This ticket's place now (`effective`): its rank until its bound passes, then its age."""
        return effective(self.rank, self.seq, self.pid, self.promote, self.clock())

    def ahead(self) -> list[str]:
        """The live tickets of this lane ahead of this one NOW, first first; dead ones are reaped.
        Class then age; a ticket past its starvation bound before every class (GOH.SLOTPRIORITY)."""
        try:
            names = os.listdir(self.dir)
        except OSError:
            return []
        now = self.clock()
        mine = effective(self.rank, self.seq, self.pid, self.promote, now)
        found = []
        for name in names:
            got = parse_ticket(name)
            if got is None or got[0] != self.lane or name == self.name:
                continue
            key = effective(*got[1:], now)
            if key < mine and _live_ticket(os.path.join(self.dir, name)):
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
