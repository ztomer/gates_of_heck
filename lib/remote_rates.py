"""lib/remote_rates.py -- each side's measured pace, what lib/remote_lane.py's split reads
(GOH.REMOTESHARE; split out and put in rows by GOH.KVBLOBS).

    load_rates()        the rates the next split reads
    put(tests, change)  one land's: its tests' seconds, and the paces CHANGEd, in a transaction
    fill(), unfilled()  `remote_rates`, the one document they were, into PACES and the rows

PACES are four numbers of goh.db's duration_kv, beside the duration table (lib/durations.py); the
tests' seconds are goh-cache.db's remote_secs (lib/cache_rows.py). PACES are written after the rows,
so a kill in between leaves the document in force, and they are there only when it is in the rows.
The document itself stays (an older copy of goh's) and is read no more.
Pinned by tests/test_cache_rows.py.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cache_rows  # lib/, on the path from the line above
import durations

P = "remote_"
RATES = "remote_rates"  # duration_kv: the document the paces and the tests' seconds were
PACES = tuple(P + k for k in ("mac_width", "worker_width", "overhead_s", "ratio"))
SMOOTH = 0.5  # each land's measurement weighs half against the rates before it


def blend(old: float, new: float) -> float:
    return old * (1 - SMOOTH) + new * SMOOTH


@dataclass
class Rates:
    """Each side's measured pace, smoothed over lands: the Mac's width (its estimated serial
    seconds over its wall), the worker's (its junit's case seconds over the suite's wall), the
    worker's fixed overhead (probe, sync, build: its wall less the suite's), its per-test seconds,
    and the ratio of its seconds to the Mac's for one test (a test it never ran is the Mac's times
    that). The defaults are the first wired land's (2026-11-22: 57 files the table puts at 364
    Mac-serial seconds took 139 s at -n 10, about 2.6 Mac cores, against roughly 8 from the Mac's -n
    12)."""

    mac_width: float = 8.0
    worker_width: float = 2.6
    overhead_s: float = 20.0
    ratio: float = 1.0
    tests: dict[str, float] = field(default_factory=dict)

    def worker_s(self, node: str, mac_s: float) -> float:
        own = self.tests.get(node)
        if own is None:
            mine = [v for k, v in self.tests.items() if k.split("::")[0] == node]
            own = sum(mine) if mine else None
        return mac_s * self.ratio if own is None else own


def load_rates() -> Rates:
    """The rates the next split reads, the defaults when there are none: the paces and remote_secs,
    or the one document they were until `fill` has put it there."""
    if was := durations.scalars(PACES):
        return Rates(**_paces(was), tests=cache_rows.secs())
    return _rates(durations.get(RATES))


def _paces(got: dict[str, float]) -> dict[str, float]:
    return {k.removeprefix(P): v for k, v in got.items()}


def unfilled() -> bool:
    return not durations.scalars(PACES) and isinstance(durations.get(RATES), dict)


def fill() -> bool:
    """`remote_rates` into remote_secs, then PACES, once; False when the cache did not take it.
    The document stays (an older copy's) and is read no more."""
    if not unfilled():
        return True
    old = _rates(durations.get(RATES))
    if not cache_rows.put_secs(old.tests, durations.written(RATES)):
        return False
    durations.update_scalars(
        PACES, lambda was: {k: getattr(old, k.removeprefix(P)) for k in PACES} | was
    )
    return True


def _rates(data: object) -> Rates:
    if not isinstance(data, dict):
        return Rates()
    try:
        return Rates(**{k: data[k] for k in Rates.__dataclass_fields__ if k in data})
    except TypeError:
        return Rates()


def put(tests: dict[str, float], change: Callable[[Rates], Rates]) -> None:
    """Nothing of it when the cache takes no row: the paces would hide the document's."""
    if not fill() or not cache_rows.put_secs(tests):
        return

    def fold(was: dict[str, float]) -> dict[str, float]:
        new = change(Rates(**_paces(was)))
        return {k: getattr(new, k.removeprefix(P)) for k in PACES}

    durations.update_scalars(PACES, fold)
