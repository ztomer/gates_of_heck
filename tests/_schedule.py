"""Longest first: the suite's tests are ordered by what each took last time (imported by conftest).

THE TAIL. Summed test time over 12 workers is ~51 s, and the suite ran 62-67 s on a quiet box
(2026-10-06): xdist hands out work in collection order, so a 14 s test collected late starts when
the others are nearly done and the run waits for it alone. Ordering the longest first is the
classic LPT schedule -- the long ones start at once and the short ones fill in around them.

The order comes from the last run's own measurement, never from a list someone keeps: the
controller records every test's call+setup+teardown at session end to a cache OUTSIDE the tree
(the tree guard forbids writing in it), and each worker sorts by it at collection. Same file, same
sort, so every worker collects the same order (xdist requires it). A test not yet measured counts
as UNKNOWN_S. An xdist_group is one unit of work, so its members move together, by their sum.
Ordering cannot change a verdict: the suite already runs in any order under xdist.

Registered as a PLUGIN from conftest's pytest_configure, not imported by name: a conftest that
imports a hook shadows its own hook of the same name (it happened to pytest_configure, 2026-10-06).
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path

UNKNOWN_S = 1.0


def _cache() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "goh" / "test-durations.json"


_seen: dict[str, float] = defaultdict(float)


def _scope(nodeid: str) -> str:
    """xdist loadgroup's unit: the group after `@`, else the test itself."""
    return nodeid.split("@")[-1] if nodeid.rfind("@") > nodeid.rfind("]") else nodeid


def _load() -> dict[str, float]:
    try:
        data = json.loads(_cache().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: float(v) for k, v in data.items() if isinstance(v, (int, float))}


def pytest_collection_modifyitems(items) -> None:
    """Sort `items` in place: the longest unit of work first, ties by nodeid."""
    known = _load()
    if not known:
        return
    weight: dict[str, float] = defaultdict(float)
    for item in items:
        weight[_scope(item.nodeid)] += known.get(item.nodeid, UNKNOWN_S)
    items.sort(key=lambda i: (-weight[_scope(i.nodeid)], _scope(i.nodeid), i.nodeid))


def pytest_runtest_logreport(report) -> None:
    _seen[report.nodeid] += report.duration


def pytest_sessionfinish(session) -> None:
    """Controller only. A partial run (`-k`, one file) MERGES what it measured; an entry whose test
    file is gone is pruned."""
    if hasattr(session.config, "workerinput") or not _seen:
        return
    root = Path(str(session.config.rootpath))
    merged = {**_load(), **{k: round(v, 3) for k, v in _seen.items()}}
    merged = {k: v for k, v in merged.items() if (root / k.split("::")[0]).is_file()}
    try:
        cache = _cache()
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(merged, sort_keys=True), encoding="utf-8")
        tmp.replace(cache)
    except OSError:
        pass  # an unwritable cache costs the next run its order, never its verdict
