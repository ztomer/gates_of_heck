"""The load slot when it is taken: wait for it, or refuse naming the holder and the line to paste.

GOH.LOADREFUSE (2026-11-11, docs/decisions/D-0002): a refusal that said only "another load
experiment already holds the slot" was stepped around twice in one day by running the gate bare,
which loads the machine just the same and is tracked by nothing. So the right path is the easy one:

* `--wait` (bounded by `--wait-max`) waits for the slot as well as for the gates in flight;
* a run with NO spinners (`load.py 0 -- <gate>`) waits for the slot by default: it measures
  nothing, so waiting costs no experiment its meaning, and refusing it only sends it round;
* a refusal names the holder (pid, start, spinners, command) and prints the exact rerun line,
  `--wait` added, for an experiment with spinners that did not ask to wait.
"""

from __future__ import annotations

import os
import shlex
import time
from collections.abc import Callable, Sequence
from pathlib import Path

import load_registry as reg

POLL_S = 0.25  # how often a waiting run asks for the slot again
TOOL = Path(__file__).resolve().parent.parent / "tools" / "load.py"
SHARED_DIR = "gates"  # one file per 0-spinner run sharing the slot (never `*.json` at the top)
PENDING_NAME = "pending.experiment"  # an experiment waiting for the gates ahead of it
GATES_ENV = "GOH_LOAD_GATES"
# How many 0-spinner runs share the slot (D-0003, measured 2026-11-11 on 16 cores, two rounds: a
# CPU-bound gate at -n 12 ran 20.9/24.1 s alone, 39.8/39.2 s two at once, 56.4/56.5 s three at
# once, 74.8/73.4 s four at once -- throughput is flat from 1 to 4, so sharing costs a gate nothing
# but removes the queue behind a holder that waits on a network or a lock. At four, all four runs
# of one round failed (rc=1) where three never did: three is the bound.)
DEFAULT_GATES = 3


def waits(count: int, asked: bool) -> bool:
    """Whether a run waits for a taken slot: asked to, or it starts no spinner (D-0002)."""
    return asked or count == 0


def gate_limit() -> int:
    """How many gates share the slot: GOH_LOAD_GATES, else DEFAULT_GATES; `0` is no bound."""
    raw = os.environ.get(GATES_ENV, "").strip()
    if not raw:
        return DEFAULT_GATES
    if not raw.isdigit():
        raise ValueError(f"{GATES_ENV} must be a non-negative integer of gates (got {raw!r})")
    return int(raw)


def shared_path(directory: Path, pid: int | None = None) -> Path:
    """The place a 0-spinner run holds in the shared slot."""
    return directory / SHARED_DIR / f"{os.getpid() if pid is None else pid}.json"


def pending_path(directory: Path) -> Path:
    return directory / PENDING_NAME


def is_shared(path: Path) -> bool:
    return path.parent.name == SHARED_DIR


def is_pending(path: Path) -> bool:
    return path.name == PENDING_NAME


def _live(path: Path) -> bool:
    """A place is live while its owner runs; a dead owner's file is pruned (under the lock)."""
    data = reg.read(path)
    if data is not None and not reg.owner_gone(data):
        return True
    reg.remove(path)
    return False


def live_gates(directory: Path) -> list[Path]:
    """The 0-spinner runs sharing the slot now, the dead ones pruned."""
    where = directory / SHARED_DIR
    if not where.is_dir():
        return []
    with reg.locked(directory):
        return [p for p in sorted(where.glob("*.json")) if p.is_file() and _live(p)]


def _pending_other(directory: Path) -> Path | None:
    path = pending_path(directory)
    if not path.exists() or not _live(path):
        return None
    data = reg.read(path) or {}
    return None if data.get("owner_pid") == os.getpid() else path


def take_shared(directory: Path, data: dict, limit: int) -> Path:
    """Take a place in the shared slot: refused (FileExistsError naming the holder) while an
    experiment holds the slot or waits for it, or `limit` gates already share it."""
    with reg.locked(directory):
        taken = reg.registry_files(directory)
        if taken:
            raise FileExistsError(str(taken[0]))
        if (pending := _pending_other(directory)) is not None:
            raise FileExistsError(str(pending))
        live = live_gates(directory)
        if limit and len(live) >= limit:
            raise FileExistsError(str(live[0]))
        path = shared_path(directory)
        path.parent.mkdir(parents=True, exist_ok=True)
        reg.create(path, data)
        return path


def take_alone(directory: Path, path: Path, data: dict, mark: bool) -> None:
    """Take the slot alone (an experiment with spinners). While gates share it, refuse naming
    one, and when `mark` (the run waits) leave the pending mark that makes a gate arriving
    behind this one wait for it: a stream of gates cannot starve an experiment."""
    with reg.locked(directory):
        taken = reg.registry_files(directory)
        if taken:
            raise FileExistsError(str(taken[0]))
        if (pending := _pending_other(directory)) is not None:
            raise FileExistsError(str(pending))
        live = live_gates(directory)
        if live:
            if mark and not pending_path(directory).exists():
                reg.create(pending_path(directory), data)
            raise FileExistsError(str(live[0]))
        reg.create(path, data)
        clear_pending(directory)


def clear_pending(directory: Path) -> None:
    """Drop this process's pending mark (it took the slot, or stopped waiting)."""
    path = pending_path(directory)
    with reg.locked(directory):
        data = reg.read(path)
        if data is not None and data.get("owner_pid") == os.getpid():
            reg.remove(path)


def rerun_line(argv: Sequence[str]) -> str:
    """The command to paste: this tool, `--wait`, then the arguments it was given (an existing
    `--wait` dropped, so it is said once)."""
    rest = [a for a in argv if a != "--wait"]
    return f"python3 {shlex.quote(str(TOOL))} --wait {shlex.join(rest)}"


def claim(
    take: Callable[[], None], wait: bool, wait_s: float, say: Callable[[str], None]
) -> tuple[Path | None, float]:
    """`take()` the slot; while it raises FileExistsError and `wait` holds, ask again until
    `wait_s` has passed. Returns (the holder's registry when refused, seconds waited)."""
    start = time.monotonic()
    poll = POLL_S
    told = False
    while True:
        try:
            take()
            return None, time.monotonic() - start
        except FileExistsError as taken:
            waited = time.monotonic() - start
            if not wait or waited >= wait_s:
                return Path(str(taken)), waited
            if not told:
                say(f"load: waiting for the slot (at most {wait_s:g}s): {taken}")
                told = True
            time.sleep(min(poll, max(0.0, wait_s - waited)))


def refusal(holder: Path, described: str, argv: Sequence[str], waited: bool, wait_s: float) -> str:
    """Why the slot is not ours, who has it, and the one line that gets on."""
    if waited:
        head = f"load: --wait-max of {wait_s:g}s expired; the slot is still held: {holder}"
        way = "Wait longer: the same line with a larger --wait-max."
    else:
        head = f"load: another load experiment already holds the slot: {holder}"
        way = f"Rerun to wait for it (bounded by --wait-max):\n        {rerun_line(argv)}"
    return "\n      ".join([
        head,
        f"held by {described}",
        (
            "One experiment at a time: a second load measures a machine twice as busy, and "
            "neither can be told from the other afterwards."
        ),
        way,
        (
            "If that experiment is gone: tools/load.py --reap (or wait for the backstop, which "
            "reads the same registry every minute)."
        ),
    ])  # fmt: skip


__all__ = [
    "DEFAULT_GATES",
    "GATES_ENV",
    "POLL_S",
    "claim",
    "clear_pending",
    "gate_limit",
    "is_pending",
    "is_shared",
    "live_gates",
    "pending_path",
    "refusal",
    "rerun_line",
    "shared_path",
    "take_alone",
    "take_shared",
    "waits",
]
