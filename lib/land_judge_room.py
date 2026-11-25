"""lib/land_judge_room.py -- may the land judge run a step now (GOH.ONEGATE)?

    none()             why it may not, "" when it may
    Wait(...)()        returns when it may; NoRoom when a land has waited past its bound

THE JUDGE IS SPARE WORK (2026-11-25: the Mac's CPU 0-2% idle, 284 of 314 lands owing ~320 s each).
A step starts only while `goh capacity` says room -- its reading, its rule, its settings
(lib/capacity.py `reading`, GOH_LANE_MIN_IDLE_PCT and the rest), never a second threshold -- and no
pre-step, land or enqueue is in flight: the pre lane's slots and the enqueues in their pre-step
(that reading's `presteps`), the land path's held slots (lib/land_share.py), a request pending or
running in the landing queue. A part that could not be read is no room HERE: for `goh capacity` it
is room, since a refusal there stops a lane; here nothing waits on the answer. Asked before every
step, with no slot held. Pinned by tests/test_land_judge_room.py.

GOH.ONEGATE: when the land judge may run a step: `goh capacity`'s own reading and rule, and no
pre-step, land or enqueue in flight; `Wait` polls it, bounded. Invoked by `land_judge.py`.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import capacity  # the lib directory, on the path from the line above
import land_share
import landq_inflight
import landq_state
import state_paths

POLL_S = 60.0  # between two looks: capacity's own rolling window
LAND_PATH = ("pre", "land", "ship")  # lib/gate_lanes.py classes


class NoRoom(Exception):
    """A land waited past its bound: what the last look said."""


def land_path(r: dict[str, Any], q: Path) -> str:
    """What of the land path is in flight, by the capacity reading R and the queue Q, or ""."""
    pre = r.get("presteps") or {}
    if pre.get("in_flight") or pre.get("held"):
        return "a pre-step is in flight"
    if landq_inflight.live(q):
        return "an enqueue is in flight"
    if landq_state.any_in(q, "pending", "running"):
        return "a land is in flight"
    directory = r["slots"]["dir"]
    held = land_share.held_classes(directory) if directory and os.path.isdir(directory) else []
    found = [c for c in held if c in LAND_PATH]
    return f"a {found[0]} gate holds a slot" if found else ""


def none(reading: Callable[[], dict[str, Any]] = capacity.reading) -> str:
    try:
        r = reading()
        if r["unread"]:
            return "unread: " + "; ".join(r["unread"])
        if not r["room"]["ok"]:
            return "; ".join(r["room"]["why"])
        return land_path(r, state_paths.landq_dir())
    except (OSError, ValueError, KeyError, sqlite3.Error) as e:
        return f"unread: {e.__class__.__name__}: {e}"


class Wait:
    """Called before each step. TOLD hears why it waits, once a wait, and "" when it may run. UNTIL:
    past it (CLOCK), no room is NoRoom; a step that has room runs whatever the age."""

    def __init__(self, until: float, told: Callable[[str], None], look: Callable[[], str] = none,
                 pause: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.time) -> None:  # fmt: skip
        self.until, self.told, self.look, self.pause, self.clock = until, told, look, pause, clock

    def __call__(self) -> None:
        said = False
        while why := self.look():
            if self.clock() >= self.until:
                raise NoRoom(why)
            if not said:
                self.told(why)
                said = True
            self.pause(POLL_S)
        self.told("")
