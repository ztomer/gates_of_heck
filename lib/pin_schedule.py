"""lib/pin_schedule.py -- WHEN a release's pin pushes run (GOH.PINSWEEP): one sweep a day, not one a
release.

    settings(root)                       (quiet, every) seconds, from the environment, then ROOT's
                                         .gatesrc, then the defaults
    due(created, quiet, every, anchor)   when the sweep made at CREATED starts its pushes

WHY. 62 sweeps of 2026-11-16..23 held one long gate slot for 28.9 h, and another release replaced 56
of the 61 that had a next one within 6 h: each ran twelve consumers' full push gates against a goh
nobody kept. A sweep waits until no release followed it for GOH_PIN_SWEEP_QUIET (6 h), or until
GOH_PIN_SWEEP_EVERY (24 h) has passed since the last sweep's pushes started (ANCHOR; with none yet,
since the oldest sweep was made, so a run of close releases still gets its first). A newer sweep's
push carries every older pin commit, so the older one leaves its repos to it (push_sweep_rows).
WALL CLOCK: a Mac that sleeps stops its monotonic one. Pinned by tests/test_pin_schedule.py.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_lanes  # lib/, beside this file: the one reading of a repo's .gatesrc key

QUIET, EVERY = "GOH_PIN_SWEEP_QUIET", "GOH_PIN_SWEEP_EVERY"
DEFAULTS = {QUIET: 6 * 3600.0, EVERY: 24 * 3600.0}


def _seconds(name: str, raw: str) -> float:
    try:
        n = float(raw)
    except ValueError:
        raise ValueError(f"{name}={raw!r} is not seconds") from None
    if not 0 <= n < float("inf"):
        raise ValueError(f"{name}={raw!r} is out of range (0 or more seconds)")
    return n


def settings(root: Path | str) -> tuple[float, float]:
    """A value that is no number of seconds raises, naming its key: never read as the default."""
    got = []
    for name, default in DEFAULTS.items():
        raw = os.environ.get(name) or gate_lanes._gatesrc(name, str(root))
        got.append(_seconds(name, raw) if raw.strip() else default)
    return got[0], got[1]


def due(created: float, quiet: float, every: float, anchor: float | None) -> float:
    """Never before CREATED."""
    if anchor is None:
        return created + quiet
    return min(created + quiet, max(created, anchor + every))
