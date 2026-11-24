"""lib/land_judges.py -- the judges `goh land --check` runs for a diff, and their budget.

Split from lib/land_check.py at its headroom line (GOH.RULESLEFT). THE CHOICE (GOH.LANDCHECK2,
GOH.TABLESEARLY): of what the enqueue's pre-step would choose for the diff (the tree's own
tools/affected_tests.py), its whole-tree judges and the table judges, each at most JUDGE_EACH_S by
the suite's measurement. THE BUDGET: the diff chooses them, and a change to a helper most tree
readers reach chooses most of the suite's judges -- GOH.RULESLEFT's own 11 files
(tests/_drift_guard.py, lib/proc_deadline.py) chose 1599, 2873 s by the suite's measurement, and
all 8 shards ran past their 538 s deadline at load 65-88. Scoping to the diff is already the
choice and bounds nothing for such a diff, so the check runs them up to JUDGE_BUDGET_S measured
seconds, the table judges first (the most frequent red class), then the cheapest; the rest it
names by count, left to the enqueue's pre-step, which judges every one (run, or
carried from main's verdicts). 60 s a shard over 8: the
quiet deadline (land_check.STEP_TIMEOUT, 300 s) holds five times a shard's measured share.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from lib import fast_tests, land_reds

UNMEASURED_S = 1.0  # the cost of a test the suite has not measured yet
JUDGE_EACH_S = 120.0  # the most one judge may cost, by measurement
JUDGE_BUDGET_S = 480.0  # the most the judges may cost in all: 8 shards x 60 s
SELECT_TIMEOUT = 120


def costs() -> dict[str, float]:
    """The suite's measured seconds per test function (its parameters and groups summed)."""
    cost: dict[str, float] = {}
    for nodeid, s in fast_tests.load().items():
        cost[fast_tests._base(nodeid)] = cost.get(fast_tests._base(nodeid), 0.0) + s
    return cost


def chosen_by_diff(root: Path, judges: bool = False) -> list[str]:
    """What the enqueue's pre-step would choose for ROOT's diff against main: the tree's own
    tools/affected_tests.py in its enqueue mode (JUDGES: its first run alone, the whole-tree
    judges); [] for a tree without one, or when it fails."""
    tool = root / "tools" / "affected_tests.py"
    if not tool.is_file():
        return []
    from lib import land_check  # its checkout env; land_check imports this module first

    env = {**land_check._env(root), "GOH_LAND_PRE_ONLY": "1"}
    argv = [env["GOH_PY"], str(tool), "--root", str(root), "--base", "main"]
    argv += ["--only", "judges"] if judges else []
    try:
        res = subprocess.run(argv, cwd=root, env=env, capture_output=True, text=True, check=False,
                             stdin=subprocess.DEVNULL, timeout=SELECT_TIMEOUT)  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return []
    lines = res.stdout.splitlines() if res.returncode == 0 else []
    return [ln.strip() for ln in lines if ln.startswith("tests/")]


def judge_tests(root: Path) -> list[str]:
    """Every judge ROOT's change can break (GOH.LANDCHECK2): of the pre-step's choice for its diff,
    its whole-tree judges (`chosen_by_diff` JUDGES) and the test functions lib/land_reds.py's rule
    calls table judges (GOH.TABLESEARLY), each at most JUDGE_EACH_S by the suite's measurement (an
    unmeasured one runs); `budgeted` cuts them to JUDGE_BUDGET_S in all."""
    cost = costs()
    judges = set(fast_tests.units(root, chosen_by_diff(root, judges=True)))
    found = [n for n in fast_tests.units(root, chosen_by_diff(root)) if land_reds.judges_a_table(n)]
    return sorted(n for n in judges | set(found) if cost.get(n, UNMEASURED_S) <= JUDGE_EACH_S)


def budgeted(nodes: list[str], budget: float | None = None) -> tuple[list[str], list[str]]:
    """(the judges run, those left to the pre-step) of NODES within BUDGET measured seconds
    (JUDGE_BUDGET_S): the table judges first, then the rest, each part cheapest first, an
    unmeasured one at UNMEASURED_S; one that does not fit is left and a cheaper one may still."""
    cost, room = costs(), JUDGE_BUDGET_S if budget is None else budget
    order = sorted(
        nodes, key=lambda n: (not land_reds.judges_a_table(n), cost.get(n, UNMEASURED_S), n)
    )
    ran: list[str] = []
    left: list[str] = []
    for node in order:
        took = cost.get(node, UNMEASURED_S)
        (ran if took <= room else left).append(node)
        room -= took if took <= room else 0.0
    return sorted(ran), sorted(left)
