"""lib/fast_tests.py -- the pre-step's cheap judges, by measured duration (GOH.PRESTEPFAILFAST).

WHY. The judges phase of the pre-step (tools/affected_tests.py `run`) is every tree reader's
reading tests: ~500 s of summed test time on goh's tree, ~330 nodes. A judge that costs 5 ms
went red only when the whole phase had finished: BATCHWINDOW's enqueue (2026-11-18) ran 473 s to
fail tests/test_wiring.py::test_every_script_has_a_row_in_the_map, a lib file with no
docs/map.md row; CRATEFLOOR and PRELANDTWICE went red late the same day on causes as cheap.

THE SET is the judges of an empty diff (`affected_tests.affected(root, [])`, the tree readers'
reading tests -- what runs on every land whatever it changes), cut to test functions, each
costed by the suite's own measurement: the duration table tests/_schedule.py merges at the end
of every top-level run (`cache()`, goh's state, outside the tree), summed over a function's
parametrized ids. Taken cheapest first while each costs at most EACH_S and the sum stays within
BUDGET_S. Never a hand list: a judge that grows slow leaves the set by its own measurement, a new
one joins once a top-level suite has timed it. An unmeasured test, or a cached id whose function
the file no longer defines (the table prunes by FILE only), is not chosen: a nodeid pytest cannot
find is a usage error, not a red.

The pre-step's other cheap phases live here too: `structural` (gates/structural.sh --full) and
`collects` (the whole suite collected when base code changes, GOH.BASECODESELECT).
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
from pathlib import Path

from tui.lib import counted, info

RECORD, REUSE = "GOH_LAND_VERDICTS_RECORD", "GOH_LAND_VERDICTS_REUSE"  # lib/affected_verdict.py

# Summed measured seconds of the set, and the most one test may cost to join it. The cut is a
# serial run (`-n 0`: an xdist bring-up costs ~7 s on this box, more than the set), so its wall
# is the sum plus one interpreter's start (measured: see docs/map.md's row).
BUDGET_S = 4.0
EACH_S = 0.5


def cache() -> Path:
    """The suite's duration table, keyed by pytest node id: GOH_TEST_DURATIONS when a caller names
    one, else goh's own state, `$XDG_STATE_HOME/goh/test-durations.json` (~/.local/state when
    unset). The ONE spelling of the path: tools/pytest.sh asks it here and hands it to its
    top-level run, the only writer (tests/_schedule.py `take`, GOH.DURATIONSSTALE)."""
    named = os.environ.get("GOH_TEST_DURATIONS")
    if named:
        return Path(named)
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "goh" / "test-durations.json"


def load() -> dict[str, float]:
    """{node id: seconds} from `cache()`; {} when there is none or it does not parse."""
    try:
        data = json.loads(cache().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: float(v) for k, v in data.items() if isinstance(v, (int, float))}


def functions(text: str) -> list[str]:
    """A test file's test functions as node ids relative to it (`test_x`, `TestY::test_z`)."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    fn = (ast.FunctionDef, ast.AsyncFunctionDef)
    out: list[str] = []
    for node in tree.body:
        if isinstance(node, fn) and node.name.startswith("test"):
            out.append(node.name)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            out += [f"{node.name}::{s.name}" for s in node.body
                    if isinstance(s, fn) and s.name.startswith("test")]  # fmt: skip
    return out


def _base(nodeid: str) -> str:
    """A cached id without its xdist group (`@g`) or its parameters (`[p]`)."""
    if nodeid.rfind("@") > nodeid.rfind("]"):
        nodeid = nodeid.rsplit("@", 1)[0]
    return nodeid.split("[", 1)[0]


def units(root: Path, chosen: list[str]) -> list[str]:
    """`chosen` (files, or `file::node` ids) as the test functions the files define now."""
    out: list[str] = []
    for entry in chosen:
        rel, _, node = entry.partition("::")
        try:
            defined = functions((root / rel).read_text(encoding="utf-8"))
        except OSError:
            continue
        out += [
            f"{rel}::{f}" for f in defined if not node or f == node or f.startswith(f"{node}::")
        ]
    return out


def pick(
    candidates: list[str],
    known: dict[str, float],
    budget: float = BUDGET_S,
    each: float = EACH_S,
) -> tuple[list[str], float]:
    """(the chosen node ids, their summed seconds): measured candidates, cheapest first, each at
    most `each`, while the sum stays within `budget`."""
    cost: dict[str, float] = {}
    for nodeid, s in known.items():
        cost[_base(nodeid)] = cost.get(_base(nodeid), 0.0) + s
    measured = sorted((cost[c], c) for c in set(candidates) if c in cost)
    chosen: list[str] = []
    total = 0.0
    for s, c in measured:
        if s > each or total + s > budget:
            break
        chosen.append(c)
        total += s
    return sorted(chosen), round(total, 3)


def run(root: Path, judges: dict[str, str]) -> int:
    """The cheapest of `judges` (tools/affected_tests.py's empty-diff set), run serially before
    the structural gates and every other phase; 1 on a red, else 0. Only pytest's 1 is a
    verdict: any other exit, or nothing measured, leaves the later phases to judge. They run
    these again -- a green land pays the set's own seconds -- and their record is the land's;
    this run writes none. Inside a goh suite nothing runs (tools/affected_tests.py says why)."""
    if os.environ.get("GATES_OF_HECK_SUITE_RUNNER") and not os.environ.get(
        "GATES_OF_HECK_SUITE_NESTED_OK"
    ):
        return 0
    nodes, measured = pick(units(root, list(judges)), load())
    if not nodes:
        info(f"pre-land: no fast judge measured in {cache()}")
        return 0
    info(f"pre-land: {counted(len(nodes), 'fast judge')} first ({measured} s measured, serial)")
    env = {k: v for k, v in os.environ.items() if k != "GOH_LAND_VERDICTS_RECORD"}
    rc = subprocess.run(
        ["bash", str(root / "tools" / "pytest.sh"), "-q", "-n", "0", *nodes],
        check=False, env=env, stdin=subprocess.DEVNULL,
    ).returncode  # fmt: skip
    if rc not in (0, 1):
        info(f"pre-land: the fast judges gave no verdict (exit {rc}) -- the full set decides")
    return 1 if rc == 1 else 0


def structural(root: Path) -> int:
    """The structural gates over the whole branch, before any test (GOH.PREPARITY): the 500-line
    cap, the roadmap, the seeds -- seconds, where the land gate met them after ~10 min of suite
    (GOH.AFFECTEDBINARY's land). This checkout's gates/structural.sh, as the land gate runs it."""
    info("pre-land: the structural gates over the branch first (gates/structural.sh --full)")
    script = Path(__file__).resolve().parent.parent / "gates" / "structural.sh"
    from checks._gitutil import local_env_vars  # git's own list of what binds it to one repo

    env = {k: v for k, v in os.environ.items() if k not in local_env_vars()}  # the ROOT's git
    return subprocess.run(
        ["bash", str(script), "--full"], cwd=root, env=env, check=False, stdin=subprocess.DEVNULL
    ).returncode


def collects(root: Path, changed: list[str]) -> int:
    """The WHOLE suite collected, when the diff changes a module pytest may import as it loads
    (lib/test_closure.py `base_code`, GOH.BASECODESELECT): a broken base module fails every test's
    load, and the set chooses only its readers, so the pre-step says so itself, in ~16 s serial,
    never by the luck of a judge loading it. No record, no reuse: a collection judges no test.
    Never inside a running goh suite (run's rule): pytest.sh refuses a nested one."""
    from lib import test_closure  # here: tests/_shard.py loads this module for its table alone
    from lib.step_timings import timed

    code = test_closure.Closure(root).base_code(changed)
    if not code or os.environ.get("GATES_OF_HECK_SUITE_RUNNER"):
        return 0
    info(f"pre-land: base code changed ({', '.join(code[:3])}{' ...' * (len(code) > 3)}) -- "
         "the whole suite collects first")  # fmt: skip
    quiet = 'set -o pipefail; "$@" | { grep -Ev "^tests/[^ ]+: [0-9]+$" || true; }'  # no count rows
    return timed(
        "pre-land: the whole suite collects",
        ["bash", "-c", quiet, "collect", "env", "-u", RECORD, "-u", REUSE, "bash",
         str(root / "tools" / "pytest.sh"), "--collect-only", "-q", "-n", "0"],
    )  # fmt: skip
