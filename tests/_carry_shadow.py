"""The gate's carry rule and what it ran, measured (GOH.CARRYBASECODE, GOH.CARRYSHADOW).

THE RULE (`choose`, the test-once design 2.2a; owner ruling 2026-11-22, "Approve CARRYBASECODE
now"): a land's suite carries a pass from another tree across a diff unless the diff holds what
pytest's own start reads whole -- SUITE_WIDE, a conftest.py, the data the base opens as it loads
(`Closure.load_reads`), and every base file BASE_TABLE classes other than base code (the workflow
the suite takes its environment from) or not at all. A base-code change -- any other of the ~125
files of `Closure.base()` -- is judged by its readers: `affected()` in the kept record's selection
mode (its `mode`, lib/pre_reuse.py `stamp`; one with none was the enqueue's). The set that runs is
F. Before, any base file carried nothing: 27 of 75 commits touched one, and the gate re-ran the
whole suite (13,098 tests) for most lands. The whole-suite backstop for such a change is the idle
suite on main's tip and the ship's full run (lib/idle_suite.py: a red full run files a fault item
naming the lands since the last green one, and lib/selection_miss.py's shadow records what F
missed).

THE MEASURE (`Shadow`, kept from GOH.CARRYSHADOW, when the rule was proposed and only measured):

    Shadow   per source the plugin carries from, the nodes the rule carries, settled by the audit
             decision the carry got: its P is what the gate skips, plus a source the audit
             SHADOWS (lib/reuse_audit.py, one use in ten: its carried nodes run anyway)
    line()   at session end: F = every node that ran or was skipped, less P; the run is what ran;
             a red outside F is a shadowed node red here -- F missed it, inside the gate

The line goes to `<record>.why`; lib/land_gate.sh keeps it out of the suite's reuse decision
(`land_suite_why`) and sums it into the land's ledger row (`land_shadow`: `shadow_f`, `shadow_ran`,
`shadow_red_out`, `shadow_red`), red gates included, which `goh land-profile --last N` prints
(lib/land_spd.py `shadows`). One Closure per plan (`one_graph`). Pinned by
tests/test_carry_shadow.py and tests/test_base_table.py.
"""

from __future__ import annotations

import contextlib
import inspect
import json
import sys
from pathlib import Path

SUITE_WIDE = ("pyproject.toml", "tools/pytest.sh", "gates/_pytest_root.sh")
LINE = "shadow:"  # the `.why` line's head (lib/land_gate.sh `land_shadow`)
ENQUEUE, GATE = "enqueue", "gate"
# The base files' classes (design 2.3; tests/test_base_table.py keeps one row per file of
# Closure.base(), each naming a file that exists): `suite-wide`, `load-read`, `base-code`. Only a
# `base-code` row is judged by its readers; a base file with no row is wide.
BASE_TABLE = "tests/base_files.tsv"
SUITE, LOAD, BASE_CODE = "suite-wide", "load-read", "base-code"

_SHARED: list[dict[int, tuple]] = []  # table id -> (table, its Closure), inside `one_graph` only


@contextlib.contextmanager
def one_graph():
    """Inside it, each table's Closure is built once and shared (the plugin's plan: one tree)."""
    _SHARED.append({})
    try:
        yield
    finally:
        _SHARED.pop()


def _table(root: Path, table):
    if table is not None:
        return table
    sys.path.insert(0, str(root / "tools"))
    try:
        import affected_tests
    finally:
        sys.path.pop(0)
    return affected_tests


def _graph(root: Path, table):
    if not _SHARED:
        return table.Closure(root)
    if id(table) not in _SHARED[-1]:
        _SHARED[-1][id(table)] = (table, table.Closure(root))
    return _SHARED[-1][id(table)][1]


def _affected(table, root: Path, changed: list[str], enqueue: bool | None, graph) -> set[str]:
    """TABLE's `affected` (ENQUEUE None: in its own default mode) on GRAPH; a table older than the
    shared graph (GOH.CARRYSHADOW), or than the modes, is asked as it can be."""
    mode = {} if enqueue is None else {"enqueue": enqueue}
    params = inspect.signature(table.affected).parameters
    if "graph" in params:
        return set(table.affected(root, changed, **mode, graph=graph))
    return set(table.affected(root, changed, **(mode if "enqueue" in params else {})))


def _suite_wide(changed: list[str]) -> list[str]:
    return [p for p in changed if p in SUITE_WIDE or Path(p).name == "conftest.py"]


def base_table(root: Path) -> dict[str, str]:
    """{base file: its class} from BASE_TABLE at ROOT (the design's 2.3 table); {} when the tree
    has none, so every base file of an older tree is unclassified, and wide."""
    try:
        lines = (root / BASE_TABLE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    rows = (ln.split("\t") for ln in lines if ln.strip() and not ln.startswith("#"))
    return {r[0]: r[1] for r in rows if len(r) >= 2}


def choose(root: Path, changed: list[str], table=None,
           mode: str | None = None) -> tuple[set[str] | None, str]:  # fmt: skip
    """(what tools/affected_tests.py -- or `table`, one loaded apart -- runs for `changed` on root,
    asked in MODE (a kept record's `mode_of`; None: the table's own default): files and node ids,
    why); None when the change is read whole as pytest starts -- SUITE_WIDE, a conftest.py, the
    load-read data, a base file BASE_TABLE classes other than base code or not at all -- so nothing
    carries across it. A base-code change is judged by its readers (GOH.CARRYBASECODE)."""
    table = _table(root, table)
    graph = _graph(root, table)
    loads = graph.load_reads() if hasattr(graph, "load_reads") else {}
    if wide := _suite_wide(changed) + [p for p in changed if p in loads]:
        return None, f"{wide[0]} is read whole as pytest starts"
    classes, base = base_table(root), graph.base()
    if wide := [p for p in changed if p in base and classes.get(p) != BASE_CODE]:
        got = classes.get(wide[0])
        return None, (
            f"{wide[0]} is loaded before every test"
            + (f" ({got} in {BASE_TABLE})" if got else f", unclassed in {BASE_TABLE}")
        )
    return _affected(table, root, changed, None if mode is None else mode == ENQUEUE, graph), ""


def mode_of(record: str) -> str:
    """The selection mode of the kept record at RECORD (`mode`); the enqueue's if it names none."""
    try:
        got = json.loads(Path(record).read_text(encoding="utf-8")).get("mode")
    except (OSError, ValueError, AttributeError):
        got = None
    return got if got in (ENQUEUE, GATE) else ENQUEUE


class Shadow:
    """The plan's skip set P, source by source, with the audit's shadowed sources (module doc)."""

    def __init__(self, plan: dict, record: str) -> None:
        self.plan, self.mode = plan, mode_of(record)
        self.skip: set[str] = set(plan.get("skipped", ()))  # P
        self.taken: set[str] = set(self.skip)  # every node the gate's plan skipped at some point
        self.pending: set[str] | None = None
        self.on = True  # the audit's `carry` state, read once (`audit`)

    def audit(self, audit) -> None:
        """The audit's state, for a source the gate's own carry never asked it about."""
        with contextlib.suppress(Exception):
            self.on = audit.state("carry").get("state") == "on"

    def same(self, nodes: set[str]) -> None:
        """A source of this very tree: what the gate's own record lets it take."""
        self._settle(None)
        self.pending = set(nodes)

    def other(self, got: set[str] | None, carry) -> None:
        """A source of another tree: GOT is the gate's choice for its diff (`choose`), CARRY(got)
        the gate's `carried` over its record."""
        self._settle(None)
        self.pending = set() if got is None else set(carry(got))

    def decide(self, decision: str) -> None:
        """The audit's decision for this source's carry."""
        self._settle(decision)
        if decision == "off":
            self.on = False

    def took(self, nodes) -> None:
        self.taken |= set(nodes)

    def _settle(self, decision: str | None) -> None:
        if self.pending is None:
            return
        nodes, self.pending = self.pending - self.skip, None
        if (decision or ("reuse" if self.on else "off")) in ("reuse", "shadow"):
            self.skip |= nodes  # a source the audit shadows: its carried nodes run, outside F

    def settle(self) -> None:
        self._settle(None)

    def counts(self, seen: set[str], failed: set[str]) -> dict:
        """{"f", "ran", "red_out": [nodes]} against the gate's final skip (module doc)."""
        self.settle()
        gate = set(self.plan["skip"] if "skip" in self.plan else self.plan.get("skipped", ()))
        prop = self.skip - (self.taken - gate)  # a node the gate dropped again (coverage) runs
        return {"f": len((seen | gate) - prop), "ran": len(seen - gate),
                "red_out": sorted(failed & prop)}  # fmt: skip


def line(got: dict, mode: str) -> str:
    """The `.why` line: `shadow: F <f> of <ran> run, <k> red outside F (<mode> mode)[: nodes]`."""
    red = got["red_out"]
    more = f" (+{len(red) - 5} more)" if len(red) > 5 else ""
    named = f": {' '.join(red[:5])}{more}" if red else ""
    return (f"{LINE} F {got['f']} of {got['ran']} run, {len(red)} red outside F "
            f"({mode} mode){named}")  # fmt: skip


def write(record: str, shadow: Shadow, seen: set[str], failed: set[str]) -> None:
    """Append SHADOW's line to `<record>.why`; never fails a gate."""
    text = line(shadow.counts(seen, failed), shadow.mode)
    with contextlib.suppress(OSError), open(f"{record}.why", "a", encoding="utf-8") as f:
        f.write(text + "\n")
