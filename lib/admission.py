"""THE ADMISSION: the one entry every gate on this machine comes in by (GOH.ONEADMISSION, D-0008).

    admit(dir, slots, owner, repo)   # lib/gate_slot.py's holder, for a shell gate (gate_slot.sh)
    admit_here(label, max_wait)      # tools/load.py with no spinners: a land's gate, in-process
    run_admitted(label, wait_s, say) # the same, with what load.py says and its exit on expiry

Memory first (lib/mem_admit.py), then a gate slot (lib/gate_slot.py's semaphore), then the holder's
record. Until 2026-11-16 a land's gate took a load place (tools/load.py 0, three of them) and a
gate slot inside it -- two counters for one CPU -- and waited 900 s on the place while gate slots
sat free. A suite run by hand (tools/pytest.sh outside a gate) comes in here too, through
lib/gate_slot.sh with a bound (GOH.ADMITSUITE). Still apart: the shellcheck tokens, which count
CHILDREN, not gates (docs/decisions/D-0010), and the bench lock's registry (GOH.ADMITREGISTRY).
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass

import gate_lanes
import gate_slot
import lock_order


def admit(
    directory: str,
    slots: int,
    owner: int,
    repo: str,
    *,
    poll: float = 0.5,
    watch: gate_slot.OwnerWatch | None = None,
    max_wait: float | None = None,
    gate_class: str = gate_lanes.DEFAULT_CLASS,
) -> tuple[int, int, float, str] | None:
    """THE ADMISSION (GOH.ONEADMISSION): memory first, then a gate slot, then the holder's record.
    (slot index, fd holding it, seconds waited, slot file), or None when the owner is gone or
    `max_wait` passed. Every gate, land and `load.py 0` run comes in here -- the shell's holder
    (lib/gate_slot.py `hold`) and tools/load.py (`admit_here`) -- so a land and a push gate wait
    for the SAME slots. `gate_class` picks the lane and the rank in its queue (lib/gate_slot.py
    LANES, GOH.SLOTFAIR): a commit gate (`short`) never waits behind a long gate.

    MEMORY BEFORE A SLOT (GOH.MEMADMIT): a slot counts gates, not the memory they start into; on
    2026-11-16 gates kept starting into a machine already swapping, until a forced restart.
    Bounded (lib/mem_admit.py); the wait counts as the slot's, before every step's ceiling."""
    import mem_admit  # lib/: the reading (a seam for the suite, GOH_MEM_READING)

    lane, rank = gate_lanes.lane_of(gate_class)

    def owner_alive() -> bool:
        return watch.alive() if watch else gate_slot.alive(owner)

    mem_waited = mem_admit.wait(f"the gate in {repo}", alive=owner_alive)
    if not owner_alive():
        return None
    left = None if max_wait is None else max(0.0, max_wait - mem_waited)
    got = gate_slot.acquire(
        directory, slots, poll, owner, watch, max_wait=left, lane=lane, rank=rank
    )
    if got is None:
        return None
    idx, fd, waited = got
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    # The repository key last: the lock order scopes a `repo` lock by it (lib/lock_order.py).
    key = lock_order.repo_key()
    name = " ".join(repo.split())  # one field of a tab-separated record
    os.write(fd, f"{owner}\t{name}\t{time.time():.0f}\t{key}\n".encode())
    return idx, fd, waited + mem_waited, gate_slot.slot_path(directory, idx, lane)


@dataclass
class Admitted:
    """What `admit_here` did: `state` is `off` (GOH_GATE_SLOTS=0), `nested` (an ancestor holds a
    slot: `said` names it), `taken` (slot `index` of `slots`, after `waited` s) or `expired`
    (`said` names the holders)."""

    state: str
    said: str = ""
    index: int = 0
    slots: int = 0
    waited: float = 0.0
    gate_class: str = gate_lanes.DEFAULT_CLASS


CLASS_ENV = "GOH_GATE_CLASS"  # short | ship | work (lib/gate_slot.py CLASSES); empty: work
_HELD: list[int] = []  # the fds this process holds its slot by: open until it exits


def configured(environ: dict[str, str] | None = None) -> tuple[str, int]:
    """(slot directory, slot count) the environment names, lib/gate_slot.sh's rules: the count is
    GOH_GATE_SLOTS (empty: `default_slots()`, `0`: off), the directory GOH_GATE_SLOT_DIR."""
    env = os.environ if environ is None else environ
    raw = env.get("GOH_GATE_SLOTS", "").strip()
    if raw and not raw.isdigit():
        raise ValueError(f"GOH_GATE_SLOTS must be a non-negative integer of gates (got {raw!r})")
    directory = env.get("GOH_GATE_SLOT_DIR", "").strip() or gate_slot.default_dir()
    return os.path.realpath(directory), int(raw) if raw else gate_slot.default_slots()


def admit_here(repo: str, max_wait: float | None = None) -> Admitted:
    """Admit THIS process as a gate (tools/load.py with no spinners): a slot held for as long as it
    runs (the fd is non-inheritable, PEP 446, so no child keeps it), marked for its children the
    way lib/gate_slot.sh marks a shell's (GOH_GATE_SLOT, GOH_HELD_LOCKS), so the gate it runs
    takes no second slot. OSError when the lock order refuses the wait."""
    # The class is the caller's word for THIS admission (tools/land.sh says `ship`), consumed first
    # so the gate it runs -- nested in this slot, or under no slot at all -- does not inherit it.
    gate_class = os.environ.pop(CLASS_ENV, "").strip() or gate_lanes.DEFAULT_CLASS
    lane, _ = gate_lanes.lane_of(gate_class)
    directory, slots = configured()
    if slots == 0:
        return Admitted("off")
    os.makedirs(directory, exist_ok=True)
    if (found := gate_slot.nested(directory)) is not None:
        return Admitted("nested", found)
    if why := lock_order.refusal("gate_slot"):
        raise OSError(why)
    taken = admit(directory, slots, os.getpid(), repo, max_wait=max_wait, gate_class=gate_class)
    if taken is None:
        now = time.time()
        return Admitted(
            "expired",
            "; ".join(
                gate_slot.describe(gate_slot.slot_path(directory, i, lane), now)
                for i in range(slots)
            ),
        )
    idx, fd, waited, path = taken
    _HELD.append(fd)
    os.environ["GOH_GATE_SLOT"] = f"{path} {os.getpid()}"
    os.environ[lock_order.ENV] = lock_order.with_held("gate_slot", os.getpid())
    # THE WIDTH (GOH.POOLSHARE): the gate this process runs gets cores / the gates admitted now,
    # this one included, in every runner's own variable (lib/worker_share.py RUNNER_ENV).
    import worker_share

    workers = worker_share.divide(worker_share.cores(), worker_share.admitted(directory))
    os.environ.update(worker_share.runner_env(workers))  # never an inherited share: another gate's
    return Admitted("taken", index=idx, slots=slots, waited=waited, gate_class=gate_class)


def run_admitted(label: str, wait_s: float, say: Callable[[str], None]) -> bool:
    """Admit this process as a gate (`admit_here`, bounded by `wait_s`) and SAY how; False, with
    the holders on stderr, when the wait expired. OSError/ValueError: refused before any wait."""
    got = admit_here(label, max_wait=wait_s)
    if got.state == "expired":
        print(
            f"load: --wait-max of {wait_s:g}s expired; every gate slot is still held: {got.said}"
            "\n      Wait longer: the same line with a larger --wait-max.",
            file=sys.stderr,
        )
        return False
    if got.state == "nested":
        say(f"load: inside a gate that holds {got.said} -- not taking another")
    elif got.state == "taken":
        slot = f"{got.index + 1}/{got.slots}"
        say(
            f"load: gate slot {slot} taken after {got.waited:.0f}s "
            f"(GOH_GATE_SLOTS, class {got.gate_class}); "
            f"workers {os.environ.get('GOH_GATE_WORKERS', '?')} (GOH.POOLSHARE)"
        )
    else:
        say("load: gate slots off (GOH_GATE_SLOTS=0)")
    return True
