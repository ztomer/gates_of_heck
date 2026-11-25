"""The load slot an EXPERIMENT takes: wait for it, or refuse naming the holder and the rerun line.

A run with no spinners (a gate) never takes it: since GOH.ONEADMISSION (2026-11-16, D-0008) it is
admitted where every gate is, a gate slot (lib/admission.py `admit`). The shared places of
D-0003 (`gates/<pid>.json`, their bound of three, the pending mark) were a second counter for the
same CPU: a land waited 900 s on one while gate slots sat free.

GOH.LOADREFUSE (2026-11-11, docs/decisions/D-0002): a refusal that said only "another load
experiment already holds the slot" was stepped around twice in one day by running the gate bare,
which loads the machine just the same and is tracked by nothing. So the right path is the easy one:

* `--wait` (bounded by `--wait-max`) waits for the slot as well as for the gates in flight;
* a run with NO spinners (`load.py 0 -- <gate>`) waits by default (for a gate slot, now): it
  measures nothing, so waiting costs no experiment its meaning, and refusing it only sends it
  round;
* a refusal names the holder (pid, start, spinners, command) and prints the exact rerun line,
  `--wait` added, for an experiment with spinners that did not ask to wait.

The load slot an EXPERIMENT takes (GOH.LOADREFUSE, D-0002): a run with spinners holds it alone;
`--wait` waits for it under `--wait-max`, and a refusal names the holder and prints the exact rerun
line with `--wait`. A run with no spinners is a gate and never takes it: it is admitted by a gate
slot (`admission.py` `admit`, GOH.ONEADMISSION, D-0008, which retired D-0003's shared places). A
wait that ends without running the command exits nonzero (EXIT_BUSY) naming the holder: a wrapped
`git push` that never ran is never green. The registry record carries `why` and `cwd`
(GOH.LOADWHY). Pinned by `test_load_refusal.py`, `test_one_admission.py`, `test_load_why.py`.
"""

from __future__ import annotations

import os
import shlex
import time
from collections.abc import Callable, Sequence
from pathlib import Path

import load_registry as reg
import lock_order

POLL_S = 0.25  # how often a waiting run asks for the slot again
TOOL = Path(__file__).resolve().parent.parent / "tools" / "load.py"


def waits(count: int, asked: bool) -> bool:
    """Whether a run waits for a taken slot: asked to, or it starts no spinner (D-0002)."""
    return asked or count == 0


def take_alone(directory: Path, path: Path, data: dict) -> None:
    """Take the slot alone (an experiment with spinners): refused, naming the holder, while
    another experiment holds it. Gates are not here: a run with no spinners is admitted by a gate
    slot (lib/admission.py `admit`, GOH.ONEADMISSION, D-0008), and an experiment holds off on the
    gates in flight, the held slots among them (lib/gates_in_flight.py)."""
    with reg.locked(directory):
        taken = reg.registry_files(directory)
        if taken:
            raise FileExistsError(str(taken[0]))
        reg.create(path, data)


def rerun_line(argv: Sequence[str]) -> str:
    """The command to paste: this tool, `--wait`, then the arguments it was given (an existing
    `--wait` dropped, so it is said once)."""
    rest = [a for a in argv if a != "--wait"]
    return f"python3 {shlex.quote(str(TOOL))} --wait {shlex.join(rest)}"


def record(command: Sequence[str], max_s: float, why: str = "") -> dict:
    """A run's registry record, its deadline counted from now (after any wait for the slot).

    `why` is the timing defect this load reproduces (GOH.LOADWHY), and `cwd` the directory its
    owner ran in: the two facts a reader of the registry needs to say whose load it is and why
    (lib/load_live.py)."""
    now = time.time()
    return {
        "version": reg.VERSION,
        "owner_pid": os.getpid(),
        "owner_start": reg.pid_start(os.getpid()) or "",
        "started_at": now,
        "max_s": max_s,
        "deadline_at": now + max_s,
        "command": list(command),
        "why": why,
        "cwd": str(Path.cwd()),
        "pids": [],
    }


def holder_above(environ: dict[str, str] | None = None) -> int | None:
    """The pid of an ANCESTOR holding the load slot (its GOH_HELD_LOCKS mark), or None.

    GOH.LOCKNEST: a 0-spinner run inside a holder is that holder's (lib/lock_order.tsv's "load"
    row): it measures nothing and adds no load its holder did not already account for. Taking a
    place of its own counted it twice against the places' bound (D-0003, retired by D-0008), and
    inside an experiment that holds the slot alone it waited --wait-max on its own parent. A mark
    whose pid is not an ancestor (exported by hand, inherited by a detached process) shares
    nothing."""
    marks = [pid for name, pid in lock_order.held(environ) if name == "load"]
    if not marks:
        return None
    above = lock_order.ancestors()
    return next((pid for pid in marks if pid in above), None)


def claim(
    take: Callable[[], None], wait: bool, wait_s: float, say: Callable[[str], None]
) -> tuple[Path | None, float]:
    """`take()` the slot; while it raises FileExistsError and `wait` holds, ask again until
    `wait_s` has passed. Returns (the holder's registry when refused, seconds waited).

    THE LOCK ORDER (GOH.LOCKORDER, lib/lock_order.tsv): the slot ranks after the land lock, so a
    run that would WAIT here inside a later lock's holder -- a land queued on the land lock inside
    the slot was the 2026-11-15 deadlock -- raises OSError naming both, before it waits. Taken,
    the slot is marked held in this process's environment (GOH_HELD_LOCKS), which the command it
    runs inherits: this process holds it until it exits."""
    if wait and (why := lock_order.refusal("load")):
        raise OSError(why)
    start = time.monotonic()
    poll = POLL_S
    told = False
    while True:
        try:
            take()
            os.environ[lock_order.ENV] = lock_order.with_held("load", os.getpid())
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
    "POLL_S",
    "claim",
    "holder_above",
    "record",
    "refusal",
    "rerun_line",
    "take_alone",
    "waits",
]
