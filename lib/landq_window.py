"""The landing queue's batch window (GOH.BATCHWINDOW): a land waits, briefly, for the lands still
in their enqueue pre-step, so a batched cycle goes through ONE gate.

WHY. Batched cycles (owner 2026-11-18) tell every lane to enqueue at once, but each enqueue runs
its own pre-step first (lib/land_prestep.py, ~448 s under load) and the pre-steps finish apart.
In cycle 1 GOH.EARLYCONFLICT filed 32 s before GOH.E2EARMED (pending 01795015361761014000 against
01795015393316479000); the lander took EARLYCONFLICT alone at once, and E2EARMED waited a whole
second gate behind it.

THE WAIT. Before the lander picks its batch (`pending` here, then lib/landq_batch.py pick), when
the queue's head is a land and the batch may take more than one (GOH_LANDQ_BATCH > 1), it waits
while `<q>/enqueuing/` (lib/landq_inflight.py) holds a live marker of ANOTHER land of its repo:
not one whose request is already filed (an enqueue files, starts the lander, then exits, so its
own marker outlives its filing by milliseconds), and never a marker whose pid is dead or past its
`until` (landq_inflight.live deletes those). It stops at the first of: no such marker is left,
the batch is full, or WINDOW seconds past the head's filing (its name is its filing time). A head
filed longer ago than that -- it waited behind a running gate -- does not wait at all.

    GOH_LANDQ_WINDOW  the bound in seconds (default 120); 0 turns the wait off

Each wait is one line in lander.log as it starts and one as it ends, naming the markers it waited
on and the seconds it took. Pinned by tests/test_landq_window.py, on a fake clock.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

import landq_batch
import landq_inflight

from tui.lib import counted

ENV = "GOH_LANDQ_WINDOW"
DEFAULT_S = 120.0
POLL_S = 2.0


def window() -> float:
    """GOH_LANDQ_WINDOW: the most seconds past the head's filing a land waits; 0 is off."""
    try:
        return max(0.0, float(os.environ.get(ENV) or DEFAULT_S))
    except ValueError:
        return DEFAULT_S


def filed(path: Path) -> float:
    """When PATH was filed: its name's nanosecond prefix (tools/landq.py enqueue), else mtime."""
    try:
        return int(path.name.partition("-")[0]) / 1e9
    except ValueError:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0


def _batchable(pending: list[Path], repo: object) -> list[dict]:
    reqs = [landq_batch.read(p) for p in pending]
    return [r for r in reqs if r.get("mode") == "land" and r.get("repo") == repo]


def others(q: Path, pending: list[Path]) -> list[dict]:
    """The live enqueue markers that would join the head's batch: another land of its repo whose
    request is not filed yet (a marker naming no repo is one whose repo is unknown: it counts)."""
    repo = landq_batch.read(pending[0]).get("repo")
    items = {r.get("item") or Path(str(repo)).name for r in _batchable(pending, repo)}
    return [m for m in landq_inflight.live(q)
            if m.get("repo") in (None, "", repo) and m.get("item") not in items]  # fmt: skip


def _names(markers: list[dict]) -> str:
    return ", ".join(str(m.get("item")) for m in markers)


def hold(
    q: Path,
    pending: list[Path],
    say: Callable[[str], None],
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> list[Path]:
    """The pending list once the window closed (module doc): PENDING itself when nobody waited.
    CLOCK and SLEEP default to time's own, looked up per call (a test's fake replaces them)."""
    clock, sleep = clock or time.time, sleep or time.sleep
    head, bound, cap = landq_batch.read(pending[0]), window(), landq_batch.cap()
    if head.get("mode") != "land" or cap == 1 or bound == 0:
        return pending
    name, deadline, start, waited = pending[0].stem, filed(pending[0]) + bound, clock(), False
    while True:
        waiting = others(q, pending)
        size, left = len(_batchable(pending, head.get("repo"))), deadline - clock()
        why = ("no enqueue is left in its pre-step" if not waiting else
               "the batch is full" if size >= cap else
               f"the bound ({bound:g}s past its filing) is reached with {_names(waiting)} "
               "still in the pre-step" if left <= 0 else "")  # fmt: skip
        if why:
            if waited:
                say(f"{name}: batch window closed after {clock() - start:.0f}s, {why}: "
                    f"{counted(size, 'land')} of its repo pending")  # fmt: skip
            return pending
        if not waited:
            say(f"{name}: holding the batch up to {left:.0f}s for {_names(waiting)} in the "
                f"enqueue pre-step (GOH.BATCHWINDOW, {ENV}={bound:g})")  # fmt: skip
        waited = True
        sleep(min(POLL_S, left))
        if not (pending := sorted((q / "pending").glob("*.json"))):
            say(f"{name}: left pending/ while the batch waited -- {clock() - start:.0f}s")
            return pending


def pending(q: Path, say: Callable[[str], None]) -> list[Path]:
    """The lander's pending requests in queue order, once the batch window closed (its one call)."""
    listed = sorted((q / "pending").glob("*.json"))
    return hold(q, listed, say) if listed else listed
