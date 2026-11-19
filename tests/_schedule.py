"""Longest first: the suite's tests are ordered by what each took last time (imported by conftest).

THE TAIL. Summed test time over 12 workers is ~51 s, and the suite ran 62-67 s on a quiet box
(2026-10-06): xdist hands out work in collection order, so a 14 s test collected late starts when
the others are nearly done and the run waits for it alone. Ordering the longest first is the
classic LPT schedule -- the long ones start at once and the short ones fill in around them.

The order comes from the last run's own measurement, never from a list someone keeps: the
controller records every test's call+setup+teardown at session end to goh's own state OUTSIDE the
tree (the tree guard forbids writing in it), MERGED into what is there.

ONLY THE TOP-LEVEL RUN WRITES IT, AND READS IT (GOH.DURATIONSSTALE). tools/pytest.sh names the table
in GOH_TEST_DURATIONS (lib/fast_tests.py `cache()`, the one spelling of the path: the land
pre-step's fast set reads the same file), and `take()`, the first thing conftest does, pops it
from the environment: no xdist worker and no fixture's nested run under it ever sees it, so none
writes it. The table was a path this module spelled from XDG_CACHE_HOME until 2026-11-18; the suite
had run in a throwaway home since 2026-10-26 (tests/suite_home.py), so every run read an empty
table and wrote its timings into a home deleted at exit -- collection order, and a fast set that
never met a test added since. A run nobody named a table for (a bare `pytest`, a fixture's run)
keeps collection order and writes nothing. The CONTROLLER reads it once and hands that read to every
worker (`pytest_configure_node`), so every worker sorts the same table and collects the same order,
which xdist requires: the file is shared by every suite on the box, and workers that each read it
collected in different orders whenever another session's suite rewrote it between their starts
("Different tests were collected", 2026-10-08). A test not yet measured counts
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

import _shard
import pytest

UNKNOWN_S = 1.0
_KEY = "goh_test_durations"
ENV = "GOH_TEST_DURATIONS"
_read: dict[str, dict[str, float]] = {}  # this process's one read: the controller's, on a worker
_table: list[Path] = []  # the table this run owns, set by `take()` on the top-level run only
_SHARD_KEY = "goh_suite_shard"
_shard_spec: list[str] = []  # this run's shard (tests/_shard.py), set by `take()` likewise


def take(environ=os.environ) -> Path | None:
    """The top-level run's table, POPPED from `environ` so nothing this run starts inherits it."""
    path = environ.pop(ENV, "")
    _table[:] = [Path(path)] if path else []
    spec = environ.pop(_shard.ENV, "")  # one shard of hosted CI's suite (GOH.CIWALL)
    _shard_spec[:] = [spec] if spec else []
    return _table[0] if _table else None


_seen: dict[str, float] = defaultdict(float)


def _scope(nodeid: str) -> str:
    """xdist loadgroup's unit: the group after `@`, else the test itself."""
    return nodeid.split("@")[-1] if nodeid.rfind("@") > nodeid.rfind("]") else nodeid


def _load() -> dict[str, float]:
    if not _table:
        return {}
    try:
        data = json.loads(_table[0].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: float(v) for k, v in data.items() if isinstance(v, (int, float))}


def pytest_configure(config) -> None:
    """A worker takes the controller's read; the controller (or a run without xdist) reads now."""
    worker = getattr(config, "workerinput", None)
    _read[_KEY] = worker.get(_KEY, {}) if worker is not None else _load()
    if worker is not None:
        _shard_spec[:] = [worker[_SHARD_KEY]] if worker.get(_SHARD_KEY) else []


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node) -> None:
    """xdist, on the controller: every worker it starts sorts by the controller's one read."""
    node.workerinput[_KEY] = _read.get(_KEY, {})
    node.workerinput[_SHARD_KEY] = _shard_spec[0] if _shard_spec else ""


def pytest_collection_modifyitems(config, items) -> None:
    """Sort `items` in place: the longest unit of work first, ties by nodeid. A node id naming the
    checkout is refused: every per-node store (this table, the verdict records) is keyed by it, and
    such a node exists only in the checkout that collected it (GOH.SHIPTREEFULL: one carried pass
    with the land's checkout in its id kept every later full run of the tree from being full)."""
    root = str(config.rootpath)
    if named := [i.nodeid for i in items if root in i.nodeid]:
        raise pytest.UsageError(f"a node id names the checkout {root} (give it ids=): {named[0]}")
    if _shard_spec:
        _shard.select(config, items, _shard_spec[0])
    known = _read[_KEY] if _KEY in _read else _load()
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
    if hasattr(session.config, "workerinput") or not _seen or not _table:
        return
    root = Path(str(session.config.rootpath))
    merged = {**_load(), **{k: round(v, 3) for k, v in _seen.items()}}
    merged = {k: v for k, v in merged.items() if (root / k.split("::")[0]).is_file()}
    try:
        cache = _table[0]
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(merged, sort_keys=True), encoding="utf-8")
        tmp.replace(cache)
    except OSError:
        pass  # an unwritable table costs the next run its order, never its verdict
