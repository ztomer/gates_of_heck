"""One shard of the suite: GOH_SUITE_SHARD=K/N runs the K-th of N disjoint parts (GOH.CIWALL).

WHY. Hosted CI ran the whole instrumented suite in one job on 3 cores: 2080-2526 s of a 49.6 min
median wall, every release. A public repo's parallel macOS jobs are free, so gate.yml runs the
suite as N matrix jobs, each `--goh-shard K/N` (tools/pytest.sh passes it from GOH_SUITE_SHARD),
and a final job merges their coverage parts (gates/_coverage_rust.sh, GOH_COV_PARTS_IN).

THE PARTITION IS A FUNCTION OF THE COLLECTION ALONE. Each shard is a separate machine that collects
the suite for itself, so the parts are disjoint and complete only if every shard computes the SAME
assignment: it reads nothing but the collected node ids and their xdist groups -- never the duration
table (a box's table is rewritten by every suite; two shards reading it at different moments would
split the suite two ways, dropping some tests and running others twice).

THE CONTROLLER READS IT, ONCE (tests/_schedule.py): `take()` pops it from the environment as it
pops the duration table, so no test's child inherits it, and every xdist worker is handed the
controller's read (`pytest_configure_node`) and keeps the same part.

THE UNIT IS XDIST LOADGROUP'S (tests/_schedule.py `_scope`): a test's `xdist_group`, else the test
itself. A group's members share state on purpose, so they move to one shard together. Units are
weighed by their test count and dealt longest-first to the lightest shard, ties by name: the same
LPT schedule _schedule.py uses inside a run, with counts where it has seconds.

tests/test_suite_shard.py proves, on a fixture collected by separate processes as CI collects it,
that the N parts cover every test exactly once and keep every group whole.
"""

from __future__ import annotations

import re
from collections import Counter

import pytest

ENV = "GOH_SUITE_SHARD"  # tools/pytest.sh's, popped by _schedule.take() like its table
_SPEC = re.compile(r"([1-9][0-9]*)/([1-9][0-9]*)")


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


def assign(units: dict[str, int], n: int) -> dict[str, int]:
    """Each unit's shard (1..n): heaviest first to the lightest shard, ties by name and index."""
    load = [0] * n
    out: dict[str, int] = {}
    for name in sorted(units, key=lambda u: (-units[u], u)):
        k = min(range(n), key=lambda i: (load[i], i))
        load[k] += units[name]
        out[name] = k + 1
    return out


def _group(item) -> str | None:
    mark = item.get_closest_marker("xdist_group")
    if mark is None:
        return None
    return str(mark.args[0] if mark.args else mark.kwargs.get("name", "default"))


def select(config, items, spec: str) -> None:
    """Keep, in place, the items of shard `spec` (K/N); the rest are deselected."""
    k, n = parse(spec)
    keyed = [(item, unit(item.nodeid, _group(item))) for item in items]
    shard = assign(Counter(u for _item, u in keyed), n)
    drop = [item for item, u in keyed if shard[u] != k]
    if drop:
        config.hook.pytest_deselected(items=drop)
    items[:] = [item for item, u in keyed if shard[u] == k]
