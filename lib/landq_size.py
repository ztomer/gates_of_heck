"""How many lands one gate judges (GOH.ADAPTBATCH): the batch size k, from the repo's red rate.

WHY. GOH.LANDBATCH fixed the batch at 3 (GOH_LANDQ_BATCH). A batch of k is green only with
(1-p)^k, and every red bisects, so the size that pays depends on p, the chance a land is red at
its gate. Measured from the done records, counting only attempts a gate judged (`gated` in
lib/landq_report.py: an enqueue pre-step's red, a check's or a conflict's never reaches a batch):

    day    gated red      gates per landed item
    11-20   4 of 47 (9%)    1.05
    11-22  10 of 67 (15%)   0.98
    11-23  14 of 34 (41%)   1.90

THE RULE. p is the share red of the repo's newest WINDOW gated attempts. For each k up to MOST,
`expected` gives the exact expected gate runs and lands of a batch of k whose items are each red
with p, by running the lander's own bisect (lib/landq_batch.py `settle`, lib/landq_spec.py `split`:
halves, the second half gated on main beside the first when the machine has two gate slots, a tree
already gated red never gated again) over every red/green pattern of the k items. k is the
smallest whose gates per landed item is within SLACK of the least: a bigger batch that saves under
SLACK gate per land is not worth its deeper bisect. With two gate slots:

    p   0.00-0.03  0.06  0.09  0.12  0.15-0.27  0.30 and up   (one slot: 1 from 0.39)
    k   6          5     4     3     2          1

Fewer than LEAST gated attempts on record: DEFAULT, said.
An explicit GOH_LANDQ_BATCH still wins. The lander logs k and why at each pick, every record it
writes carries `k` and `k_why`, and `goh land --report` shows k per batch.

Pinned by tests/test_landq_size.py.

The batch size k (GOH.ADAPTBATCH): unless `GOH_LANDQ_BATCH` is set, the k a land request takes is
the one that minimises expected gates per landed item at its repo's red rate p over the newest 20
attempts a gate judged (`landq_report.gated`: an enqueue pre-step's, a check's or a conflict's red
never counts), computed exactly by running the lander's own bisect (halves, the second on main
beside the first, a tree known red not gated again) over every red/green pattern of k items: the
smallest k within 0.02 of the least, up to 6; 1 when p is about 0.30 or more; 3 with fewer than 10
on record. The lander logs k and why at each pick; each record carries `k` and `k_why`. Invoked by
`lib/landq_window.py`.
"""

from __future__ import annotations

import itertools
import os
from dataclasses import dataclass, field
from pathlib import Path

import admission
import land_records  # the land records' one door (GOH.STATEDB)
import landq_report

DEFAULT = 3  # the size with no history to read, and GOH.LANDBATCH's fixed one
MOST = 6  # beyond 6 a clean history saves under 0.03 gate per land a step; a red bisects deeper
WINDOW = 20  # the newest gated attempts p is read from
LEAST = 10  # fewer than this on record: DEFAULT
SLACK = 0.02  # gates per landed item a bigger batch must save


@dataclass(frozen=True)
class Size:
    k: int
    why: str


@dataclass
class _Run:
    """One bisect of a pattern: what landed (main), the gates, the trees known red."""

    red: tuple[bool, ...]
    parallel: bool
    main: frozenset[int] = frozenset()
    gates: int = 0
    spec: bool = False
    red_trees: set[frozenset[int]] = field(default_factory=set)

    def settle(self, items: list[int]) -> None:
        tree = self.main | frozenset(items)  # a stack's tree is main's content plus its items
        if tree not in self.red_trees:
            self.gates += 1
        if not any(self.red[i] for i in items):
            self.main = tree
            return
        if len(items) > 1:
            self.red_trees.add(tree)
            half = len(items) // 2
            self.split(items[:half], items[half:])

    def split(self, first: list[int], second: list[int]) -> None:
        tree = self.main | frozenset(second)
        if not self.parallel or self.spec or tree in self.red_trees:
            self.settle(first)
            self.settle(second)
            return
        self.spec, self.gates, base = True, self.gates + 1, self.main  # B on main, beside A
        self.settle(first)
        self.spec = False
        if self.main == base and not any(self.red[i] for i in second):
            self.main = tree  # B's own run was its gate
            return
        if self.main == base:
            self.red_trees.add(tree)
        self.settle(second)


def bisect(red: tuple[bool, ...], parallel: bool = True) -> tuple[int, int]:
    """(gate runs, items landed) of the lander's bisect of a stack whose items are RED or not."""
    run = _Run(red, parallel)
    run.settle(list(range(len(red))))
    return run.gates, len(run.main)


def expected(p: float, k: int, parallel: bool = True) -> tuple[float, float]:
    """(gate runs, items landed) expected of a batch of K items, each red with P."""
    gates = landed = 0.0
    for red in itertools.product((False, True), repeat=k):
        weight = 1.0
        for r in red:
            weight *= p if r else 1 - p
        g, n = bisect(red, parallel)
        gates, landed = gates + weight * g, landed + weight * n
    return gates, landed


def per_landed(p: float, k: int, parallel: bool = True) -> float:
    gates, landed = expected(p, k, parallel)
    return gates / landed if landed else float("inf")


def best(p: float, parallel: bool = True, most: int = MOST) -> int:
    """The smallest k whose gates per landed item is within SLACK of the least (module doc)."""
    costs = [per_landed(p, k, parallel) for k in range(1, most + 1)]
    least = min(costs)
    return next(k for k, c in enumerate(costs, 1) if c <= least + SLACK)


def history(q: Path, repo: str, n: int = WINDOW) -> list[bool]:
    """Red (True) or green, the newest N attempts of REPO a gate judged, newest first."""
    out: list[bool] = []
    keys = ("repo", "mode", "state", "why")
    for _, rec in land_records.records(q, reverse=True, keys=keys):  # newest enqueued first
        if rec.get("repo") == repo and rec.get("mode") == "land" and (landq_report.gated(rec)):
            out.append(rec.get("state") != "landed")
            if len(out) == n:
                break
    return out


def parallel_bisect() -> bool:
    """Whether a red's halves gate at once here (lib/landq_spec.py: not on ONE gate slot)."""
    try:
        return admission.configured()[1] != 1
    except ValueError:
        return True


def size(q: Path, repo: str) -> Size:
    """The batch size for REPO's next land, and why (module doc)."""
    raw = os.environ.get("GOH_LANDQ_BATCH", "").strip()
    if raw:
        try:
            return Size(max(1, int(raw)), f"GOH_LANDQ_BATCH={raw} is set")
        except ValueError:
            return Size(DEFAULT, f"GOH_LANDQ_BATCH={raw!r} is no number: the default {DEFAULT}")
    seen = history(q, repo)
    name = Path(repo).name
    if len(seen) < LEAST:
        return Size(DEFAULT, f"only {len(seen)} gated lands of {name} on record (fewer than "
                    f"{LEAST}): the default {DEFAULT}")  # fmt: skip
    reds, parallel = sum(seen), parallel_bisect()
    p = reds / len(seen)
    k = best(p, parallel)
    alt = f", {per_landed(p, DEFAULT, parallel):.2f} at {DEFAULT}" if k != DEFAULT else ""
    return Size(
        k,
        f"{reds} of the last {len(seen)} gated lands of {name} red (p {p:.2f}): "
        f"{per_landed(p, k, parallel):.2f} gates per landed item expected at k={k}{alt}",
    )
