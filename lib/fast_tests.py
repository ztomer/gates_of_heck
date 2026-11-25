"""lib/fast_tests.py -- the pre-step's cheap judges, by measured duration (GOH.PRESTEPFAILFAST).

WHY. The judges phase of the pre-step (tools/affected_tests.py `run`) is every tree reader's
reading tests: ~500 s of summed test time on goh's tree, ~330 nodes. A judge that costs 5 ms
went red only when the whole phase had finished: BATCHWINDOW's enqueue (2026-11-18) ran 473 s to
fail tests/test_wiring.py::test_every_script_has_a_row_in_the_map, a lib file with no
docs/map.md row; CRATEFLOOR and PRELANDTWICE went red late the same day on causes as cheap.

THE SET is the judges of an empty diff (`affected_tests.affected(root, [])`, the tree readers'
reading tests -- what runs on every land whatever it changes), cut to test functions, each costed by
the suite's own measurement: the duration table tests/_schedule.py records at the end of every
top-level run (`cache()`, goh.db's `durations` view, outside the tree), summed over a function's
parametrized ids. Taken cheapest first while each costs at most EACH_S and the sum stays within
BUDGET_S. Never a hand list: a judge that grows slow leaves the set by its own measurement, a new
one joins once a top-level suite has timed it. An unmeasured test, or a cached id whose function the
file no longer defines (the table prunes by FILE only), is not chosen: a nodeid pytest cannot find
is a usage error, not a red.

The pre-step's other cheap phases live here too: `structural` (gates/structural.sh --full) and
`collects` (the whole suite collected when base code changes, GOH.BASECODESELECT).

GOH.PRESTEPFAILFAST: the land pre-step's CHEAP judges, run first: the judges of an empty diff
(`tools/affected_tests.py` `affected(root, [])`) cut to test functions and costed by the suite's
own duration table (`cache()`: `GOH_TEST_DURATIONS`, else goh.db, the one spelling of the path;
`tools/pytest.sh` hands it to its top-level run, the only writer, whose readings
`tests/_schedule.py` records through `durations.py`, GOH.DURATIONSSTALE, GOH.DURATIONSDB), cheapest
first while each costs at most `EACH_S` (0.5 s) and the sum stays within `BUDGET_S` (4 s);
unmeasured tests and cached ids the file no longer defines are not chosen. `run` runs them serially
(`-n 0`) before the structural gates, a red (pytest's 1) the only verdict: a missing map row on
goh's tree was refused by `tools/land.sh`'s pre-step in 10.7 s and 15.6 s at load ~25, where
BATCHWINDOW's took 473 s; a green land pays the set's run, 8-17 s at that load (2026-11-18).

`tools/affected_tests.py`: the pre-land test set, chosen from the diff (GOH.TREETESTSFIRST): every
`tests/test_*.py` that names a changed path (the path, or each of its components as a quoted
literal) or imports a changed module (ast), the changed tests, the tests whose TRANSITIVE closure
holds a changed file or a file whose code names one (imports at any depth and a script a test or
helper names with what it sources, read by `lib/source_closure.py`; prose masked by
`checks/_prose_mask.py`; what `tests/conftest.py` loads for every test is the base and chooses
nothing, GOH.AFFECTEDTRANSITIVE), and, for a change to the binary's build inputs (`crates/`,
`Cargo.toml`, `Cargo.lock`, `rust-toolchain.toml` and each file a crate embeds, e.g.
`lib/busyloop_shapes.json`), every test that runs the goh binary (`lib/binary_runners.py`,
GOH.AFFECTEDBINARY: GRACESHAPES' scanner change was green here and red in the gate on
`test_check_moving_tags.py`) whose subcommands the change reaches (`lib/crate_reach.py`,
GOH.AFFECTEDBINARY2) -- at the enqueue only those whose stored profile reaches it
(`lib/bin_reach.py`, GOH.BINREACH, audited one land in ten by `lib/bin_reach_audit.py`) --, and
every tree reader (a `test_every_`/`test_no_` test, a test or tests/ helper that lists or walks the
tree, plus `ALWAYS`). `--why` says why each is chosen; `--run` runs them with `tools/pytest.sh` (an
empty set runs nothing). A tree reader the diff does not choose runs only its reading tests, as
node ids (`reader_nodes`: a test_every_/test_no_ test, or one whose source reaches a walk through
the file's own names, fixtures and decorators; the whole file when they cannot be told apart,
GOH.LANDPREDUP). A test that runs a tool with `cwd=REPO_ROOT` is a tree reader too
(`test_gate_temp_leaks.py` runs `structural.sh --full` over the checkout). `--run` runs the JUDGES
first, in a pytest run of their own -- the tree readers' chosen nodes, which judge every file or
key whatever the diff names -- then the rest, the two records joined into one; before all of it,
the cheap judges the duration table measures (`lib/fast_tests.py`, serial, GOH.PRESTEPFAILFAST: a
red one is refused in seconds, not after the ~500 s judges phase); with `--structural`,
`gates/structural.sh --full` runs before both (GOH.PREPARITY: one run of both reached the 600 s
deadline at load 175 with no verdict and two judges it held went red in the land, ADAPTLANES; the
500-line cap met only the land gate, AFFECTEDBINARY). After the tests, `--run` runs the release
canary when a consumer names a changed path (`lib/consumer_reads.py`, GOH.LANDMISS1119:
GOH.PYTESTPIN's pre-step was green and the land gate's canary red). `tools/land.sh` runs it as
goh's `GOH_LAND_PRE` (`--run --structural`), at enqueue and before the land lock and the gate.
Pinned by `test_affected_tests.py`, `test_prestep_judges.py`, `test_prestep_fast.py`,
`test_affected_binary.py`, `test_affected_binary_reach.py` and `test_land_verdicts.py`.
"""

from __future__ import annotations

import ast
import os
import subprocess
from pathlib import Path

from tui.lib import counted, info

RECORD, REUSE = "GOH_LAND_VERDICTS_RECORD", "GOH_LAND_VERDICTS_REUSE"  # lib/affected_verdict.py

# Summed measured seconds of the set, and the most one test may cost to join it. The cut is a
# serial run (`-n 0`: an xdist bring-up costs ~7 s on this box, more than the set), so its wall
# is the sum plus one interpreter's start (measured: this module's header).
BUDGET_S = 4.0
EACH_S = 0.5


def _durations():
    """lib/durations.py, imported when first asked: this module is base code (every pytest run
    loads it), the database seam is not."""
    try:
        from lib import durations
    except ImportError:  # imported as `fast_tests`, with lib/ itself on the path
        import durations  # type: ignore[no-redef]
    return durations


def cache() -> Path:
    """The suite's duration table: goh.db (GOH.DURATIONSDB, lib/durations.py), the file
    GOH_TEST_DURATIONS names when a caller names one, else lib/goh_db.py `path()`. The ONE
    spelling of it: tools/pytest.sh asks it here and hands it to its top-level run, the only
    writer (tests/_schedule.py `take`, GOH.DURATIONSSTALE)."""
    return _durations().path()


def load(kind: str = "dur", db: Path | None = None) -> dict[str, float]:
    """{node id: seconds} of the table KIND (lib/durations.py KINDS: `dur` the duration table,
    `pole` the long-pole table, each test's quiet seconds, GOH.LONGPOLELOAD, `pole_cpu` their CPU
    seconds, GOH.LONGPOLEUNMEASURED) from `db` (`cache()` unless named); {} when there is none."""
    return _durations().load(kind, db)


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
