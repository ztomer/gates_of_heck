"""One shard of the suite: GOH_SUITE_SHARD=K/N runs the K-th of N disjoint parts (GOH.CIWALL).

WHY. Hosted CI ran the whole instrumented suite in one job on 3 cores: 2080-2526 s of a 49.6 min
median wall, every release. A public repo's parallel macOS jobs are free, so gate.yml runs the
suite as N matrix jobs, each `--goh-shard K/N` (tools/pytest.sh passes it from GOH_SUITE_SHARD),
and a final job merges their coverage parts (gates/_coverage_rust.sh, GOH_COV_PARTS_IN).

THE PARTITION IS A FUNCTION OF THE CHECKOUT ALONE. Each shard is a separate machine that collects
the suite for itself, so the parts are disjoint and complete only if every shard computes the SAME
assignment: it reads nothing but the collected node ids, their xdist groups and the COMMITTED
weights, tests/shard_weights.json -- never the box's duration table (rewritten by every suite; two
shards reading it at different moments would split the suite two ways, dropping some tests and
running others twice). Loads are summed in node-id order, so a shard that collected in another
order (the box's longest-first sort) deals the same part.

THE CONTROLLER READS IT, ONCE (tests/_schedule.py): `take()` pops it from the environment as it
pops the duration table, so no test's child inherits it, and every xdist worker is handed the
controller's read (`pytest_configure_node`) and keeps the same part.

THE UNIT IS XDIST LOADGROUP'S (tests/_schedule.py `_scope`): a test's `xdist_group`, else the test
itself. A group's members share state on purpose, so they move to one shard together. Units are
dealt longest-first to the lightest shard, ties by name: the same LPT schedule _schedule.py uses
inside a run.

UNITS ARE WEIGHED BY MEASURED SECONDS (GOH.CIWALL2). They were weighed by test COUNT, and
v0.139.13's four shards held 1641-1646 tests each and ran 527, ~650, 863 and 1104 s of pytest: one
test takes 0.01 s and another 100 s. shard_weights.json holds each test FILE's measured seconds and
each test of at least SLOW_S by its node id (352 of 6644; by file alone, a file's one slow test and
its fast ones weighed alike, and the parts read 1.07x the mean). A listed test weighs its own
seconds; the rest of a file share what its listed tests leave of the file's; a file the weights
never measured weighs UNKNOWN_S a test, as _schedule's order does. Dealt by the box's own table
(2026-11-19, 6644 tests), the four parts read 1311/1262/1292/1346 s (the largest 1.03x the mean)
where the count split read 945/1368/1207/1692 s (1.30x). `python3 tests/_shard.py --refresh`
rewrites the weights from this box's duration table after a full suite (lib/fast_tests.py
`cache()`); stale weights cost balance, never a test.

tests/test_suite_shard.py proves, on a fixture collected by separate processes as CI collects it,
that the N parts cover every test exactly once and keep every group whole.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import _loadgroup
import pytest

ENV = "GOH_SUITE_SHARD"  # tools/pytest.sh's, popped by _schedule.take() like its table
_SPEC = re.compile(r"([1-9][0-9]*)/([1-9][0-9]*)")
WEIGHTS = Path(__file__).with_name("shard_weights.json")
UNKNOWN_S = 1.0  # a test of a file the weights never measured (tests/_schedule.py's own)
SLOW_S = 3.0  # a test this slow is weighed by its own node id


def parse(spec: str) -> tuple[int, int]:
    """`K/N` as (K, N), 1 <= K <= N; anything else is a usage error naming the spec."""
    m = _SPEC.fullmatch(spec.strip())
    if not m or not 1 <= int(m.group(1)) <= int(m.group(2)):
        raise pytest.UsageError(f"{ENV}={spec!r}: give K/N with 1 <= K <= N (e.g. 2/4)")
    return int(m.group(1)), int(m.group(2))


def unit(nodeid: str, group: str | None) -> str:
    """The unit of work a test belongs to: its xdist group, else itself (an `@group` suffix the
    loadgroup scheduler appended is dropped, so the unit reads the same before and after)."""
    if group:
        return "@" + group
    base = nodeid.split("@")[0] if nodeid.rfind("@") > nodeid.rfind("]") else nodeid
    return base


def weights(path: Path = WEIGHTS) -> dict[str, float]:
    """{test file: measured seconds}, the checkout's committed weights; {} when unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: float(v) for k, v in data.items() if isinstance(v, (int, float)) and v > 0}


def measure(table: dict[str, float], keep) -> dict[str, float]:
    """The weights a duration table ({node id: seconds}) makes: each file `keep` accepts by its
    summed seconds, and each of its tests of at least SLOW_S by its own."""
    out: dict[str, float] = defaultdict(float)
    for nodeid, secs in table.items():
        out[nodeid.split("::")[0]] += secs
        if secs >= SLOW_S:
            out[nodeid] = secs
    return {
        k: round(v, 2) for k, v in sorted(out.items()) if keep(k.split("::")[0]) and round(v, 2) > 0
    }


def _base(nodeid: str) -> str:
    """The node id without the `@group` suffix xdist's loadgroup scheduler appends."""
    return nodeid.split("@")[0] if nodeid.rfind("@") > nodeid.rfind("]") else nodeid


def costs(nodeids: list[str], table: dict[str, float]) -> dict[str, float]:
    """Each collected test's weight under `table` (module docstring): its own seconds when listed,
    else an even share of what its file's listed tests leave, else UNKNOWN_S."""
    bases = {n: _base(n) for n in nodeids}
    unlisted: Counter = Counter()
    files = {b.split("::")[0] for b in bases.values()}
    left = {f: table[f] for f in sorted(files) if f in table}  # a file's seconds its listed leave
    for n in sorted(nodeids):
        f = bases[n].split("::")[0]
        if bases[n] in table:
            if f in left:
                left[f] -= table[bases[n]]
        else:
            unlisted[f] += 1
    out = {}
    for n in nodeids:
        b = bases[n]
        f = b.split("::")[0]
        if b in table:
            out[n] = table[b]
        elif f in table:
            out[n] = max(left[f], 0.0) / unlisted[f]
        else:
            out[n] = UNKNOWN_S
    return out


def assign(units: dict[str, float], n: int) -> dict[str, int]:
    """Each unit's shard (1..n): heaviest first to the lightest shard, ties by name and index."""
    load = [0] * n
    out: dict[str, int] = {}
    for name in sorted(units, key=lambda u: (-units[u], u)):
        k = min(range(n), key=lambda i: (load[i], i))
        load[k] += units[name]
        out[name] = k + 1
    return out


def _group(item) -> str | None:
    """The item's group as the scheduler is handed it (tests/_loadgroup.py, the one spelling)."""
    return _loadgroup.group(item)


def select(config, items, spec: str, table: dict[str, float] | None = None) -> None:
    """Keep, in place, the items of shard `spec` (K/N), dealt by `table` (default: the committed
    weights); the rest are deselected."""
    k, n = parse(spec)
    table = weights() if table is None else table
    keyed = [(item, unit(item.nodeid, _group(item))) for item in items]
    cost = costs([item.nodeid for item in items], table)
    load: dict[str, float] = defaultdict(float)
    for item, u in sorted(keyed, key=lambda pair: pair[0].nodeid):
        load[u] += cost[item.nodeid]
    shard = assign(load, n)
    drop = [item for item, u in keyed if shard[u] != k]
    if drop:
        config.hook.pytest_deselected(items=drop)
    items[:] = [item for item, u in keyed if shard[u] == k]


class Plugin:
    """The part, as a hook of its own (GOH.CIRED013913), registered by `_schedule.pytest_configure`
    only when this run was handed a shard. It was a branch inside
    `_schedule.pytest_collection_modifyitems` reading module state `take()` sets, so a test calling
    that function on fake items was SHARDED when the suite ran as a part (`SimpleNamespace` has no
    `get_closest_marker`: three red shards of v0.139.13): a verdict that depended on which machine
    ran it. Now the ordering function has no shard to read, and a test calling it cannot meet one.
    It runs last, after the order and the checkout-id refusal have seen every collected test, so
    each shard refuses the same ids."""

    def __init__(self, spec: str) -> None:
        self.spec = spec

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, config, items) -> None:
        select(config, items, self.spec)


if __name__ == "__main__":  # `python3 tests/_shard.py --refresh`: the weights from this box's table
    if sys.argv[1:] != ["--refresh"]:
        sys.exit("usage: python3 tests/_shard.py --refresh")
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    from lib import fast_tests

    measured = fast_tests.load()
    if not measured:
        sys.exit(f"tests/_shard.py: no duration table at {fast_tests.cache()}: run the suite first")
    got = measure(measured, lambda f: (root / f).is_file())
    WEIGHTS.write_text(json.dumps(got, indent=0, sort_keys=True) + "\n", encoding="utf-8")
    files = {k: v for k, v in got.items() if "::" not in k}
    slow = len(got) - len(files)
    print(f"{WEIGHTS}: {len(files)} files, {sum(files.values()):.0f} s, {slow} slow tests")
