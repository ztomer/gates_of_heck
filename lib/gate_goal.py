"""lib/gate_goal.py -- the land gate's 5-minute goal, read from what lands already record
(GOH.SUITETOTAL part 3, the done measure of GOH.GATEUNDER5).

    gate_goal.py            the reading, one line (what `goh land-profile` prints too)

THE READING. The median wall time of the newest `gate_goal.quiet_lands` finished lands whose load
at their start was quiet -- `run_ends` `land` rows (lib/run_ends.py, goh.db 0024), load1 per core
under lib/long_pole.py QUIET_LOAD, the line every quiet reading of the suite uses -- against
`gate_goal.seconds` (tests/test_work_ceiling.json: the owner's 5 minutes). Fewer quiet lands than
the window is NOT JUDGED, said with the count, never a pass and never a refusal.

THE REFUSAL. `goh ship` of a repo holding that file refuses while the median is over the goal,
naming the median, the window and the goal (lib/ship_item.py `work_refusal`). No timer of its own:
the walls are the lands'. Pinned by tests/test_gate_goal.py.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import json
import os
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import long_pole  # lib/: QUIET_LOAD, the suite's one quiet line
import run_ends
import worker_share

CONFIG = Path(__file__).resolve().parent.parent / "tests" / "test_work_ceiling.json"


@dataclass(frozen=True)
class Reading:
    goal_s: float
    window: int
    quiet: int  # quiet finished lands found, at most `window`
    median_s: float | None  # None: not judged

    @property
    def over(self) -> bool:
        return self.median_s is not None and self.median_s > self.goal_s

    def line(self) -> str:
        if self.median_s is None:
            return (f"gate goal: not judged: {self.quiet} of {self.window} quiet lands"
                    f" (goal {self.goal_s:g} s, GOH.GATEUNDER5)")  # fmt: skip
        verdict = "OVER" if self.over else "under"
        return (
            f"gate goal: the median land gate of the newest {self.window} quiet lands is"
            f" {self.median_s:.0f} s, {verdict} the goal {self.goal_s:g} s (GOH.GATEUNDER5)"
        )


def reading(rows: Iterable[Mapping], conf: Mapping, cores: int) -> Reading | None:
    """The reading of ROWS (run_ends rows, any order) under CONF's `gate_goal`; None without one."""
    goal = conf.get("gate_goal")
    if not goal:
        return None
    window = int(goal.get("quiet_lands", 20))
    lands = sorted((r for r in rows if r.get("kind") == "land" and r.get("outcome") == "finished"
                    and r.get("wall_s") is not None and r.get("load1") is not None),
                   key=lambda r: r.get("started") or 0, reverse=True)  # fmt: skip
    quiet = [float(r["wall_s"]) for r in lands if r["load1"] / cores < long_pole.QUIET_LOAD]
    quiet = quiet[:window]
    if len(quiet) < window:
        return Reading(float(goal["seconds"]), window, len(quiet), None)
    walls = sorted(quiet)
    mid = len(walls) // 2
    median = walls[mid] if len(walls) % 2 else (walls[mid - 1] + walls[mid]) / 2
    return Reading(float(goal["seconds"]), window, len(quiet), median)


def current(conf: Mapping | None = None) -> Reading | None:
    """The reading of goh.db's run_ends now (CONF: tests/test_work_ceiling.json)."""
    if conf is None:
        conf = json.loads(CONFIG.read_text(encoding="utf-8"))
    return reading(run_ends.rows(), conf, worker_share.cores())


if __name__ == "__main__":
    got = current()
    print(got.line() if got else "gate goal: no gate_goal configured")
