"""A batch's ratchet phase: the tree-wide judges on the stacked tree, before its gate
(GOH.STACKRATCHETFIRST).

WHY. Batch 01795449296573240000 (ROADMAPIDS, NOPREBATCH, FSEVCHURN) gated 40 min, went red on
tests/test_self_walk.py -- ROADMAPIDS's new roadmap rule found 252 in goh, seeded 0 -- and spent two
more gates bisecting. The tree-wide judges are the reds that depend on the STACK, not on one item
(29 of 140 reds since 2026-11-20 were ratchets): each pre-step judged its item on an older main.
They cost minutes on the stacked tree; the gate costs 20-40.

WHAT RUNS. Before a stack of two or more lands gates (lib/landq_batch.py `run`), it is stacked as
the gate's would be (`Batch.stack`, the same rebase) and the stack's tip gets `goh land --check`'s
run (lib/land_check.py `run`, its diff coverage left to the gate): the land gate's cheap steps
(structural --full among them), the guard tests, and the whole-tree judges the enqueue's pre-step
chooses for the stack's diff against main (tools/affected_tests.py --only judges: test_self_walk
and the test_every_/test_no_ ratchets) plus the table judges.

EVICTION. Green: the stack gates as before. Red: the judges that refused are run again on each lower
item's stacked tip, the lowest first, and on main below the lowest (a checkout of its own); the
first tip they are red on, over one (or main) they are green on, is the item whose OWN diff turns
them red. It is red with its own log (the phase's output under a header naming the batch), its
branch goes back to its enqueued commit as any red item's does, and the phase runs again on the
rest, restacked without it. Main red, or a run with no verdict (a timeout, a node a tree lacks):
nothing is evicted and the gate decides as before -- the phase removes only an item it proved red.
Pinned by tests/test_landq_ratchet.py.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

import gitrun
import land_check

if TYPE_CHECKING:
    from landq_batch import Batch, Item

PROBE_TIMEOUT = 600  # one re-run of the refused judges on one tree
NO_GUARD = "no guard declared"  # land_check.run's line for a tree that declares none


def first(b: Batch, live: list[Item]) -> list[Item]:
    """LIVE after the ratchet phase: stacked, each item whose own diff turns a judge red evicted."""
    while len(live) > 1:
        stacked = b.stack(live)  # a conflict drops one, as the gate's own stacking does
        if len(stacked) < 2:
            return stacked
        top, said = stacked[-1], []
        t0 = time.monotonic()
        reqs = [it.req for it in stacked]  # what its reds may waive (GOH.RULEINFLIGHT)
        refused = land_check.run(Path(top.req["worktree"]), b.q, said.append, top.req["repo"],
                                 tables=True, cov=False, reqs=reqs)  # fmt: skip
        took = f"{time.monotonic() - t0:.0f} s"
        if not refused:
            if not any(NO_GUARD in line for line in said):
                b.kit.say(f"batch {b.id}: ratchet phase green on {top.tip[:8]} in {took}")
            return stacked
        b.kit.say(f"batch {b.id}: ratchet phase red on {top.tip[:8]} in {took}: "
                  f"{', '.join(refused[:3])}")  # fmt: skip
        culprit = find(b, stacked, refused)
        if culprit is None:
            b.kit.say(f"batch {b.id}: no item's own diff is proved to turn it red (main red, or a "
                      "run had no verdict) -- the gate decides")  # fmt: skip
            return stacked
        evict(b, culprit, refused, said)
        live = [it for it in stacked if it is not culprit]
    return live


def find(b: Batch, stacked: list[Item], refused: list[str]) -> Item | None:
    """The lowest item whose stacked tip REFUSED is red on, over a green one (or main); None
    when main is red too, or a run reached no verdict."""
    for i, it in enumerate(stacked[:-1]):
        red = red_on(Path(it.req["worktree"]), refused)
        if red is None:
            return None
        if red:
            return it if i or main_green(b, refused) else None
    return stacked[-1]


def red_on(root: Path, refused: list[str]) -> bool | None:
    """Whether REFUSED is red on the tree at ROOT: its test nodes (those whose file the tree has)
    and, when a step refused, the cheap steps; None for no verdict."""
    env = land_check._env(root)
    nodes = [n for n in refused if n.startswith("tests/") and (root / n.split("::")[0]).is_file()]
    jobs = [["bash", "-c", s] for s in land_check.steps(root)] * any(
        not n.startswith("tests/") for n in refused
    )
    if nodes:
        serial = ["-n", "0"] if (root / "tests" / "_shard.py").is_file() else []
        jobs.append([env["GOH_PY"], "-m", "pytest", "-q", "-p", "no:cacheprovider", *serial,
                     "--", *nodes])  # fmt: skip
    red = False
    for argv in jobs:
        rc, _, _ = land_check._run(argv, root, env, PROBE_TIMEOUT)
        kind = "guard tests" if "pytest" in argv else "steps"
        if rc in land_check.NO_VERDICT[kind]:
            return None
        red = red or rc != 0
    return red


def main_green(b: Batch, refused: list[str]) -> bool:
    """REFUSED is green on main's tree, in a checkout of its own (removed after)."""
    sha = b.git(b.main, "rev-parse", "HEAD")
    wt = b.q / "ratchet-main" / f"{os.getpid()}-{time.time_ns()}"
    wt.parent.mkdir(parents=True, exist_ok=True)
    try:
        add = gitrun.run_writer(("worktree", "add", "-q", "--detach", str(wt), sha), repo=b.main,
                                deadline_s=120)  # fmt: skip
        return add is not None and add.returncode == 0 and red_on(wt, refused) is False
    finally:
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            gitrun.run_writer(("worktree", "remove", "--force", str(wt)), repo=b.main,
                              deadline_s=120)  # fmt: skip
        shutil.rmtree(wt, ignore_errors=True)


def evict(b: Batch, it: Item, refused: list[str], said: list[str]) -> None:
    """IT red with its own log: the phase's output under a header naming the batch."""
    names = ", ".join(refused[:3]) + (f" and {len(refused) - 3} more" if len(refused) > 3 else "")
    why = f"ratchet: its diff turns {names} red on the stack (batch {b.id}'s ratchet phase)"
    head = f"batch {b.id}: red in the ratchet phase, evicted before the gate: {', '.join(refused)}"
    with it.log.open("a") as f:
        f.write("\n".join([head, *said]) + "\n")
    b.outcome(it, "red", it.log, why=why)
    b.kit.say(f"batch {b.id}: {it.path.stem} evicted -- {why}")
