"""The proposed carry rule, asked beside the gate's own, measured, never obeyed (GOH.CARRYSHADOW).

THE GATE'S RULE (`choose`): a land's suite carries a pass from another tree across a diff only when
the diff holds nothing pytest loads before every test -- no file of `Closure.base()` (121 files) --
and then skips what tools/affected_tests.py, asked in the GATE's mode, does not choose. 27 of 75
commits touched the base, so the gate re-ran the whole suite for 6 of 10 lands (2026-11-22).

THE PROPOSED RULE (`proposed`, GOH.CARRYBASECODE, docs: the test-once design 2.2a): wide only for
what pytest's own start reads whole -- SUITE_WIDE, a conftest.py, the data the base opens as it
loads (`Closure.load_reads`) -- and otherwise `affected()` in the enqueue record's selection mode
(the record's `mode`, lib/pre_reuse.py `stamp`; one with none was the enqueue's), which holds a base
file's readers. Whether to obey it is the owner's ruling, so the gate keeps its rule and measures:

    Shadow   per source the plugin carries from, the nodes the proposed rule would carry, settled by
             the same audit decision the gate's own carry got (a source the gate took nothing from
             is settled by the audit's state, read once, so no use is counted twice). Its P is what
             the proposed run would skip; the gate's own skip is A
    line()   at session end: F, what the proposed rule would run, = every node that ran or was
             skipped, less P; the run is what ran; a red outside F is a node red here that P holds
             -- the proposed rule would have carried a pass across a change that broke it

The line goes to `<record>.why`; lib/land_gate.sh keeps it out of the suite's reuse decision
(`land_suite_why`) and sums it into the land's ledger row (`land_shadow`: `shadow_f`, `shadow_ran`,
`shadow_red_out`, `shadow_red`), red gates included, which `goh land-profile --last N` prints
(lib/land_spd.py `shadows`). Cheap by construction: one Closure per plan (`one_graph`), shared by
the gate's choice and the proposed one, so the proposed `affected()` reads a graph already built.
Pinned by tests/test_carry_shadow.py.
"""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

SUITE_WIDE = ("pyproject.toml", "tools/pytest.sh", "gates/_pytest_root.sh")
LINE = "shadow:"  # the `.why` line's head (lib/land_gate.sh `land_shadow`)
ENQUEUE, GATE = "enqueue", "gate"

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
    try:
        return set(table.affected(root, changed, **mode, graph=graph))
    except TypeError:
        return set(table.affected(root, changed, **mode))


def _suite_wide(changed: list[str]) -> list[str]:
    return [p for p in changed if p in SUITE_WIDE or Path(p).name == "conftest.py"]


def choose(root: Path, changed: list[str], table=None) -> tuple[set[str] | None, str]:
    """(what tools/affected_tests.py -- or `table`, one loaded apart -- runs for `changed` on root:
    files and node ids, why); None when the change is suite-wide, so nothing carries across it."""
    table = _table(root, table)
    graph = _graph(root, table)
    wide = _suite_wide(changed) + [p for p in changed if p in graph.base()]
    if wide:
        return None, f"{wide[0]} is loaded before every test"
    return _affected(table, root, changed, None, graph), ""


def proposed(root: Path, changed: list[str], mode: str,
             table=None) -> tuple[set[str] | None, str]:  # fmt: skip
    """`choose` under the proposed rule (module doc): None only for SUITE_WIDE, a conftest.py and
    the load-read data; else `affected()` in MODE, a base file judged by its readers."""
    table = _table(root, table)
    graph = _graph(root, table)
    loads = graph.load_reads() if hasattr(graph, "load_reads") else {}
    if wide := _suite_wide(changed) + [p for p in changed if p in loads]:
        return None, f"{wide[0]} is read whole as pytest starts"
    return _affected(table, root, changed, mode == ENQUEUE, graph), ""


def mode_of(record: str) -> str:
    """The selection mode of the kept record at RECORD (`mode`); the enqueue's if it names none."""
    try:
        got = json.loads(Path(record).read_text(encoding="utf-8")).get("mode")
    except (OSError, ValueError, AttributeError):
        got = None
    return got if got in (ENQUEUE, GATE) else ENQUEUE


class Shadow:
    """The proposed plan's skip set P, source by source beside the gate's (module doc)."""

    def __init__(self, plan: dict, record: str) -> None:
        self.plan, self.mode = plan, mode_of(record)
        self.skip: set[str] = set(plan.get("skipped", ()))  # P
        self.taken: set[str] = set(self.skip)  # every node the gate's plan skipped at some point
        self.pending: set[str] | None = None
        self.on = True  # the audit's `carry` state, read once (`audit`)
        self.broken = ""  # why the proposed rule could not be asked: this run measured nothing

    def audit(self, audit) -> None:
        """The audit's state, for a source the gate's own carry never asked it about."""
        with contextlib.suppress(Exception):
            self.on = audit.state("carry").get("state") == "on"

    def same(self, nodes: set[str]) -> None:
        """A source of this very tree: the proposed rule takes what the gate's takes."""
        self._settle(None)
        self.pending = set(nodes)

    def other(self, root: Path, changed: list[str] | None, carry) -> str:
        """A source of another tree: CARRY(chosen) is the gate's `carried` over its record."""
        self._settle(None)
        try:
            got, why = (
                proposed(root, changed, self.mode) if changed is not None else (None, "no diff")
            )
        except Exception as e:  # noqa: BLE001 -- the instrument never fails the gate it watches
            self.broken = self.broken or f"{e.__class__.__name__}: {e}"
            got, why = None, self.broken
        self.pending = set() if got is None else set(carry(got))
        return why

    def decide(self, decision: str) -> None:
        """The audit's decision for this source's gate carry: the proposed one obeys it too."""
        self._settle(decision)
        if decision == "off":
            self.on = False

    def took(self, nodes) -> None:
        self.taken |= set(nodes)

    def _settle(self, decision: str | None) -> None:
        if self.pending is None:
            return
        nodes, self.pending = self.pending - self.skip, None
        if (decision or ("reuse" if self.on else "off")) == "reuse":
            self.skip |= nodes

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
    """Append SHADOW's line to `<record>.why` -- `shadow: not measured (why)` when the proposed rule
    could not be asked, which tools/land.sh counts as no measurement; never fails a gate."""
    text = (f"{LINE} not measured ({shadow.broken})" if shadow.broken
            else line(shadow.counts(seen, failed), shadow.mode))  # fmt: skip
    with contextlib.suppress(OSError), open(f"{record}.why", "a", encoding="utf-8") as f:
        f.write(text + "\n")
