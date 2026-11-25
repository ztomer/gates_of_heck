"""A re-enqueue of an item supersedes the earlier enqueue's pre-step; `goh land --cancel ITEM` does
the same on demand (GOH.REENQUEUECANCEL, 2026-11-22).

WHY. The enqueue's pre-step runs up to 25 min in one of the machine's two gate slots and in a
checkout slot of its own. A lane that fixed its tip and enqueued again left the first pre-step
running: GOH.BINREACH's later filed a second request for the replaced sha c36145ad (deleted by
hand), and GOH.FSCEILINGRED killed pid 43359 and process group 56002 by hand, there being no cancel.
Each orphan held a gate slot while other lanes waited 900 s and ended with no verdict.

    supersede(q, item)  every live enqueue of ITEM in its pre-step (lib/landq_inflight.py markers),
                        older than the caller: its tombstone first (`bail`: it files nothing, even
                        if a signal lands late), then the enqueuer and the pre-step's process group
                        stopped by their RECORDED identity (pid and birth, lib/pid_owner.py; never
                        by name), TERM then KILL, then its checkout slot freed (lib/land_slot.py)
    cancel(q, item)     the same as a command: 0 when one was stopped, 3 when none was running

A pre-step whose stamp no longer matches (its pid reused) is not signalled: nothing of ours is
there. Pinned by tests/test_landq_supersede.py.

A re-enqueue of an item supersedes the earlier enqueue's pre-step, and `goh land --cancel ITEM`
does the same on demand (GOH.REENQUEUECANCEL): a tombstone beside the in-flight marker (an enqueuer
that finds its own files nothing), then the enqueuer and the pre-step's process group stopped by
their recorded pid and birth (TERM, then KILL; never by name), then the checkout slot freed.
Invoked by `lib/landq_background.py`.
"""

from __future__ import annotations

import contextlib
import importlib
import os
import signal
import time
from pathlib import Path

land_slot = importlib.import_module("land_slot")
landq_inflight = importlib.import_module("landq_inflight")
landq_state = importlib.import_module("landq_state")
pid_owner = importlib.import_module("pid_owner")
_tui = importlib.import_module("tui.lib")
info, ok, warn = _tui.info, _tui.ok, _tui.warn

GRACE_S = 5.0  # a TERMed pre-step's time to clean up (git's lock files) before KILL


def _until_gone(alive, limit: float) -> bool:
    end = time.monotonic() + limit
    while alive():
        if time.monotonic() > end:
            return False
        time.sleep(0.05)
    return True


def _stop_group(pre: dict | None) -> bool:
    """The pre-step's group as stamped at its spawn: TERM, then KILL. True when it is gone."""
    try:
        born = (int(pre["born"][0]), int(pre["born"][1]))  # type: ignore[index]
        s = pid_owner.Stamp(int(pre["number"]), born, int(pre["group"]))  # type: ignore[index]
    except (TypeError, KeyError, ValueError, IndexError):
        return True  # no pre-step spawned yet: the enqueuer's own stop is the whole stop
    for sig, limit in ((signal.SIGTERM, GRACE_S), (signal.SIGKILL, 2.0)):
        if not pid_owner.signal_group(s, sig):
            return True  # gone, or the number is a stranger's now: nothing of ours to stop
        if _until_gone(lambda: pid_owner.signal_group(s, 0), limit):
            return True
    return False


def _stop(q: Path, path: Path, m: dict) -> bool:
    """One enqueue stopped and its slot freed; False when part of it would not stop."""
    landq_inflight.supersede_mark(path)
    enqueuer = pid_owner.from_line(str(m.get("pid_stamp") or m["pid"]))
    pid_owner.signal(enqueuer, signal.SIGTERM)  # first: a pre-step killed under it reads as red
    group_gone = _stop_group(m.get("pre"))
    if not _until_gone(lambda: pid_owner.alive(enqueuer), GRACE_S):
        pid_owner.signal(enqueuer, signal.SIGKILL)
    stopped = group_gone and _until_gone(lambda: pid_owner.alive(enqueuer), 2.0)
    if not stopped:
        warn(f"enqueue pid {m['pid']} of {m.get('item')} did not stop; its slot is left held")
        return False
    if (slot := m.get("slot")) and m.get("repo"):
        land_slot.release(q, Path(m["repo"]), slot["worktree"], slot["token"])
    landq_inflight.clear(path)
    return True


def supersede(q: Path, item: str) -> list[dict]:
    """Every live earlier enqueue of ITEM stopped (module doc); the markers of those stopped."""
    if not item:
        return []  # no item, no identity: two lanes of one repo are not one enqueue
    me = os.getpid()
    out = []
    for m in landq_inflight.live(q, item):
        if int(m["pid"]) == me:
            continue
        path = landq_state.name(q, landq_inflight.DIR, f"{m['pid']}-{landq_inflight.slug(item)}")
        what = f"{item}'s earlier enqueue (pid {m['pid']})"
        info(f"goh land: superseding {what}: its pre-step stopped, its slot freed, nothing filed")
        if _stop(q, path, m):
            out.append(m)
    return out


def cancel(q: Path, item: str) -> int:
    """`goh land --cancel ITEM`: 0 when an enqueue in its pre-step was stopped, 3 when none ran."""
    with contextlib.suppress(OSError):
        q.mkdir(parents=True, exist_ok=True)
    if stopped := supersede(q, item):
        pid = stopped[0]["pid"]
        ok(f"goh land: cancelled {item}: its pre-step stopped (pid {pid}), nothing filed")
        return 0
    info(f"goh land: nothing to cancel: no enqueue of {item} is in its pre-step")
    return 3
