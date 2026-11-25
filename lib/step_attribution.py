#!/usr/bin/env python3
"""lib/step_attribution.py -- every timed step's wall time is named by the rows under it.

    step_attribution.py check FILE...   # each gap on its own line; exit 1 when there is one

WHY (GOH.RELEASELANDGATE). v0.139.4's ship took 650 s in its release step; its timings file named
"release: land (its release gate)" as 160 s, and the 94 rows under it added up to 6 s. The other
154 s was a pre-step pytest run of 925 tests that wrote no row: the profile said where a quarter of
the step went by leaving it out. A profile that cannot be wrong that way says so on every row.

THE RULE. A row is attributed by the rows naming it their parent (lib/step_timings.py nests by
label, GOH_TIMINGS_PARENT): a row none names is a LEAF, one named thing, and attributes itself. A
row some name is a PARENT, and its children's wall time, summed, must reach FLOOR (90%) of its own,
unless what is left is under MIN_GAP_S: a 2 s phase with 0.5 s of interpreter start is no finding.
Children that ran side by side can sum past their parent; the sum is capped at it, so a parallel
fan-out over a gap still hides the gap -- the rows carry no start time, and this is the most a sum
can say. A label run more than once (one check in two gates) pools its runs and its children.

GOH.RELEASELANDGATE: every timed step's seconds are named by the rows under it. `gaps(rows)` finds
each parent row whose children (rows naming it their parent) sum under 90% of its wall time by more
than 5 s (a leaf attributes itself; side-by-side children are capped at their parent); `check
FILE...` prints each and exits 1. `lib/ship_profile.py` prints them under `goh ship --status`'s
table.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # outside any checkout: nothing to re-run from
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import step_timings  # lib/, beside this file: the rows' one reader

FLOOR = 0.90
MIN_GAP_S = 5.0


def gaps(rows: list[dict], floor: float = FLOOR, min_gap_s: float = MIN_GAP_S) -> list[dict]:
    """{label, ms, named_ms, children} for each parent row whose children name under FLOOR of its
    wall time, by more than MIN_GAP_S; costliest gap first."""
    own: dict[str, float] = defaultdict(float)
    under: dict[str, float] = defaultdict(float)
    count: dict[str, int] = defaultdict(int)
    for r in rows:
        ms = float(r.get("ms") or 0.0)
        own[str(r.get("label", ""))] += ms
        if parent := str(r.get("parent") or ""):
            under[parent] += ms
            count[parent] += int(r.get("calls") or 1)
    found = []
    for label, kids in under.items():
        if label not in own:
            continue  # a parent outside this file (the caller's own row): nothing to hold it to
        total, named = own[label], min(kids, own[label])
        gap = total - named
        if named < floor * total and gap > min_gap_s * 1000:
            found.append({"label": label, "ms": total, "named_ms": named, "children": count[label]})
    return sorted(found, key=lambda g: g["named_ms"] - g["ms"])


def describe(g: dict) -> str:
    return (
        f"{g['label']}: {g['ms'] / 1000:.1f} s, of which its {g['children']} rows name "
        f"{g['named_ms'] / 1000:.1f} s ({g['named_ms'] / g['ms']:.0%}); "
        f"{(g['ms'] - g['named_ms']) / 1000:.1f} s is unattributed"
    )


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] != "check":
        print("usage: step_attribution.py check FILE...", file=sys.stderr)
        return 2
    bad = 0
    for path in argv[1:]:
        for g in gaps(step_timings.load(path)):
            print(f"{path}: {describe(g)}")
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
