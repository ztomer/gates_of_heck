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

ADAPTIVE, BOUNDED (GOH.ONEVERDICTTREE3). The fixed 120 s rarely caught a pre-step: 8 of the last 11
waits in lander.log closed on the bound with the other land still in its pre-step (~480 s).
A marker says when its pre-step STARTED (`at`, lib/landq_inflight.py mark), and the queue's own
done records say how long its repo's pre-steps take (`pre_s`, the median of the newest 20; 480 s
with none), so the lander knows when each is DUE (start + that + 30 s). The wait stretches to the
latest due marker, never past GOH_LANDQ_WINDOW_MAX (default 300 s) past the head's filing: a land
gate is ~850 s (lock to landed, docs/reuse.md), so a hold of at most 300 s that saves another
land a whole gate pays for itself whenever it catches one in three. A marker due later than that
is not waited for at all (GOH.WINDOWDUE: 27 of 35 windows closed on the base bound having caught
nothing, median 120 s, 58 min in all, each with a marker still in a pre-step due past the cap); one
with no `at`, whose due is unknown, gets the base bound; a lone land, none at all.
A `--no-pre` land
(lib/landq_batch.py `alone`, GOH.NOPREBATCH) never batches, so it waits for none either.

Each wait is one line in lander.log as it starts and one as it ends, naming the markers it waited
on and the seconds it took. Pinned by tests/test_landq_window.py, on a fake clock.
"""

from __future__ import annotations

import os
import statistics
import time
from collections.abc import Callable
from pathlib import Path

import landq_batch
import landq_inflight

from tui.lib import counted

ENV = "GOH_LANDQ_WINDOW"
DEFAULT_S = 120.0
MAX_ENV = "GOH_LANDQ_WINDOW_MAX"
MAX_S = 300.0
PRE_S = 480.0  # a repo's pre-step with no done record to measure it by (docs/reuse.md median)
SLACK_S = 30.0
NEWEST = 20
POLL_S = 2.0


def window() -> float:
    """GOH_LANDQ_WINDOW: the most seconds past the head's filing a land waits; 0 is off."""
    try:
        return max(0.0, float(os.environ.get(ENV) or DEFAULT_S))
    except ValueError:
        return DEFAULT_S


def most() -> float:
    """GOH_LANDQ_WINDOW_MAX: the stretched window's bound past the head's filing (module doc)."""
    try:
        return max(0.0, float(os.environ.get(MAX_ENV) or MAX_S))
    except ValueError:
        return MAX_S


def expected(q: Path, repo: object) -> float:
    """How long REPO's enqueue pre-steps take: the median `pre_s` of its newest done records."""
    took = []
    for path in sorted((q / "done").glob("*.json"), reverse=True) if (q / "done").is_dir() else []:
        rec = landq_batch.read(path)
        if rec.get("repo") == repo and isinstance(rec.get("pre_s"), (int, float)):
            took.append(float(rec["pre_s"]))
            if len(took) == NEWEST:
                break
    return statistics.median(took) if took else PRE_S


def due_inside(head_filed: float, waiting: list[dict], took: float) -> list[dict]:
    """The markers worth holding for (GOH.WINDOWDUE): one whose pre-step is DUE by the cap
    (start + TOOK + slack, never later than the head's filing + the most), and one that says no
    start, whose due is unknown. A marker due past the cap is not: the wait would close on the
    bound with it still in its pre-step (27 of 35 windows did, median 120 s, 58 min in all)."""
    cap = head_filed + max(window(), most())
    return [
        m for m in waiting
        if not isinstance(m.get("at"), (int, float)) or float(m["at"]) + took + SLACK_S <= cap
    ]  # fmt: skip


def deadline(head_filed: float, waiting: list[dict], took: float) -> float:
    """The window's end: the base bound, stretched to the latest marker due within the most; the
    head's own filing (no wait) when no marker of WAITING is due inside the window."""
    base = head_filed + window()
    if not (worth := due_inside(head_filed, waiting, took)):
        return head_filed
    due = [float(m["at"]) + took + SLACK_S for m in worth if isinstance(m.get("at"), (int, float))]
    return max([base, *due])


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


def others(q: Path, pending: list[Path], markers: list[dict] | None = None) -> list[dict]:
    """The live enqueue markers that would join the head's batch: another land of its repo whose
    request is not filed yet (a marker naming no repo is one whose repo is unknown: it counts).
    MARKERS: landq_inflight.live(q), read BEFORE `pending` was listed (`hold`); read now if none."""
    repo = landq_batch.read(pending[0]).get("repo")
    items = {r.get("item") or Path(str(repo)).name for r in _batchable(pending, repo)}
    markers = landq_inflight.live(q) if markers is None else markers
    return [m for m in markers
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
    """The pending list once the window closed (module doc), listed again after the markers were
    read: PENDING's head decides whether it waits at all.
    CLOCK and SLEEP default to time's own; a test passes its fake clock here, never by patching
    the time module, which every thread of the process reads (GOH.LANDQWINFLAKE)."""
    clock, sleep = clock or time.time, sleep or time.sleep
    head, bound, cap = landq_batch.read(pending[0]), window(), landq_batch.cap()
    if head.get("mode") != "land" or cap == 1 or bound == 0 or landq_batch.alone(head):
        return pending
    name, at, start, waited = pending[0].stem, filed(pending[0]), clock(), False
    took = expected(q, head.get("repo"))
    while True:
        # The markers FIRST, then the listing (GOH.LANDQWINFLAKE): an enqueue files its request,
        # then its marker goes, so a land whose marker is gone at this read is in the listing
        # below. Listed first, a land that filed and exited between the two reads was in neither:
        # the window closed on "no enqueue left" with a list that lacked it, and it went through
        # a second gate run of its own.
        markers = landq_inflight.live(q)
        if not (pending := sorted((q / "pending").glob("*.json"))):
            if waited:
                say(f"{name}: left pending/ while the batch waited -- {clock() - start:.0f}s")
            return pending
        waiting = others(q, pending, markers)
        end = deadline(at, waiting, took)
        size, left = len(_batchable(pending, head.get("repo"))), end - clock()
        why = ("no enqueue is left in its pre-step" if not waiting else
               "the batch is full" if size >= cap else
               f"none of {_names(waiting)} is due inside the window" if not due_inside(
                   at, waiting, took) else
               f"the bound ({end - at:.0f}s past its filing) is reached with {_names(waiting)} "
               "still in the pre-step" if left <= 0 else "")  # fmt: skip
        if why:
            if waited:
                say(f"{name}: batch window closed after {clock() - start:.0f}s, {why}: "
                    f"{counted(size, 'land')} of its repo pending")  # fmt: skip
            return pending
        if not waited:
            say(f"{name}: holding the batch up to {left:.0f}s for {_names(waiting)} in the "
                f"enqueue pre-step (GOH.BATCHWINDOW, {ENV}={bound:g}, {MAX_ENV}={most():g}, "
                f"its pre-steps take {took:.0f}s)")  # fmt: skip
        waited = True
        sleep(min(POLL_S, left))


def pending(
    q: Path,
    say: Callable[[str], None],
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> list[Path]:
    """The lander's pending requests in queue order, once the batch window closed (its one call);
    CLOCK and SLEEP as `hold` takes them (a test's fake clock, never a patch of the time module)."""
    listed = sorted((q / "pending").glob("*.json"))
    return hold(q, listed, say, clock, sleep) if listed else listed
