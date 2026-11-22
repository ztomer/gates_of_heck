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
as UNKNOWN_S. An xdist_group is one unit of work, so its members move together, by their sum; the
group is read from the marks (tests/_loadgroup.py `group`), never from a node id.
Ordering cannot change a verdict: the suite already runs in any order under xdist.

THE LONG-POLE TABLE (GOH.LONGPOLELOAD) rides the same path: beside the duration table
(lib/fast_tests.py `poles`), written by the same top-level run, read by the same controller and
handed to every worker the same way. Its entries are each test's quiet seconds, the sum of the
`goh_pole` its phases' reports carry (tests/_drift_guard.py), which the long-pole ratchet judges
(lib/long_pole.py). A report without one (a run without the drift guard) adds nothing to it.

PRUNED BY COLLECTED NODE ID (GOH.DURATIONSPRUNE). Both tables drop an entry whose test file is gone,
and, for every file this run collected WHOLE, each id of that file it did not collect: a test
renamed or parametrized left its old id behind (a stale 61 s test_land_check skewed the order and
the ratchet) while only the file was checked. A file is whole when it yielded at least one item,
no argument named a node inside it (`f.py::t`), and nothing deselected one of its items (-k, -m,
--deselect, --lf, a shard, a carried run: every plugin's deselection goes through pytest's own
`pytest_deselected`). Whoever collects records it (an xdist worker hands it to the controller in
`workeroutput`; every worker collects the same set, so the controller takes the union).

JUDGES FIRST IN A LAND'S ONE RUN (GOH.LANDONERUN). The lander runs one pytest, the gate's suite
(the pre-land beside it is gone, lib/land_par.sh), and tools/pytest.sh names it
GATES_OF_HECK_SUITE_JUDGES_FIRST with --maxfail: the tree readers (tools/affected_tests.py
`phases`' JUDGES, which judge every file whatever the diff) are handed out before the rest, so a
red judge stops the run in seconds, as the pre-step's own judges-first run did. Popped by `take()`
like the table; the controller asks the checkout's selector once and hands the list to every worker.

Registered as a PLUGIN from conftest's pytest_configure, not imported by name: a conftest that
imports a hook shadows its own hook of the same name (it happened to pytest_configure, 2026-10-06).
It registers tests/_loadgroup.py in turn, so every run that orders by group also schedules by it.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import _loadgroup
import _shard
import pytest

UNKNOWN_S = 1.0
_KEY = "goh_test_durations"
_POLE_KEY = "goh_test_poles"
ENV = "GOH_TEST_DURATIONS"
_read: dict[str, dict[str, float]] = {}  # this process's one read: the controller's, on a worker
_table: list[Path] = []  # the table this run owns, set by `take()` on the top-level run only
_SHARD_KEY = "goh_suite_shard"
_shard_spec: list[str] = []  # this run's shard (tests/_shard.py), set by `take()` likewise
JUDGES_ENV, _JUDGE_KEY = "GATES_OF_HECK_SUITE_JUDGES_FIRST", "goh_judges_first"
_judges_first: list[bool] = []  # a land's one run (tools/pytest.sh), set by `take()` likewise


def take(environ=os.environ) -> Path | None:
    """The top-level run's table, POPPED from `environ` so nothing this run starts inherits it."""
    path = environ.pop(ENV, "")
    _table[:] = [Path(path)] if path else []
    spec = environ.pop(_shard.ENV, "")  # one shard of hosted CI's suite (GOH.CIWALL)
    _shard_spec[:] = [spec] if spec else []
    _judges_first[:] = [True] if environ.pop(JUDGES_ENV, "") else []
    return _table[0] if _table else None


_seen: dict[str, float] = defaultdict(float)
_poles: dict[str, float] = defaultdict(float)
_collected: set[str] = set()  # node ids this run's collection kept
_narrowed: set[str] = set()  # files a deselection or a node-id argument cut short
_COLLECTED_KEY, _NARROWED_KEY = "goh_collected_ids", "goh_narrowed_files"


def _poles_path(table: Path) -> Path:
    """The long-pole table beside `table`: lib/fast_tests.py `poles`, its one spelling."""
    root = Path(__file__).resolve().parent.parent  # THIS checkout's, never a PYTHONPATH's
    sys.path[:0] = [str(root / "lib"), str(root)]
    try:
        import fast_tests
    finally:
        del sys.path[:2]
    return fast_tests.poles(table)


def _scope(item) -> str:
    """xdist loadgroup's unit: the test's group (tests/_loadgroup.py `group`, the name the
    scheduler is handed), else the test itself. Node ids carry no `@group` suffix: xdist 3.8.0's
    suffix never landed under pytest 9.2, and the fix keeps ids pytest's (GOH.XDISTGROUP)."""
    g = _loadgroup.group(item)
    return "@" + g if g is not None else item.nodeid


def judges(root: Path) -> list[str]:
    """The checkout's tree readers, by its own tools/affected_tests.py (`phases`' JUDGES), loaded
    from its path: an `affected_tests` imported by name may be another checkout's."""
    path = root / "tools" / "affected_tests.py"
    spec = importlib.util.spec_from_file_location("goh_judges", path)
    assert spec is not None and spec.loader is not None, root
    selector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(selector)
    return sorted(selector.tree_readers(root))


def _load(poles: bool = False) -> dict[str, float]:
    if not _table:
        return {}
    try:
        path = _poles_path(_table[0]) if poles else _table[0]
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: float(v) for k, v in data.items() if isinstance(v, (int, float))}


def pytest_configure(config) -> None:
    """A worker takes the controller's read; the controller (or a run without xdist) reads now."""
    worker = getattr(config, "workerinput", None)
    _read[_KEY] = worker.get(_KEY, {}) if worker is not None else _load()
    _read[_POLE_KEY] = worker.get(_POLE_KEY, {}) if worker is not None else _load(poles=True)
    if worker is not None:
        _shard_spec[:] = [worker[_SHARD_KEY]] if worker.get(_SHARD_KEY) else []
        _read[_JUDGE_KEY] = worker.get(_JUDGE_KEY, [])
    else:
        _read[_JUDGE_KEY] = judges(Path(str(config.rootpath))) if _judges_first else []
    if not config.pluginmanager.has_plugin("goh-loadgroup"):  # loadgroup that groups
        config.pluginmanager.register(_loadgroup, "goh-loadgroup")
    if _shard_spec:  # the part is its OWN hook (GOH.CIRED013913), never this module's
        config.pluginmanager.register(_shard.Plugin(_shard_spec[0]), "goh-suite-shard")


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node) -> None:
    """xdist, on the controller: every worker it starts sorts by the controller's one read."""
    node.workerinput[_KEY] = _read.get(_KEY, {})
    node.workerinput[_POLE_KEY] = _read.get(_POLE_KEY, {})
    node.workerinput[_SHARD_KEY] = _shard_spec[0] if _shard_spec else ""
    node.workerinput[_JUDGE_KEY] = _read.get(_JUDGE_KEY, [])


def pytest_collection_modifyitems(config, items) -> None:
    """Sort `items` in place: a land's judges first (GOH.LANDONERUN), then the longest unit of work
    first, ties by nodeid. A node id naming the
    checkout is refused: every per-node store (this table, the verdict records) is keyed by it, and
    such a node exists only in the checkout that collected it (GOH.SHIPTREEFULL: one carried pass
    with the land's checkout in its id kept every later full run of the tree from being full)."""
    root = str(config.rootpath)
    if named := [i.nodeid for i in items if root in i.nodeid]:
        raise pytest.UsageError(f"a node id names the checkout {root} (give it ids=): {named[0]}")
    known = _read[_KEY] if _KEY in _read else _load()
    judge = set(_read.get(_JUDGE_KEY, ()))
    if not known and not judge:
        return
    weight: dict[str, float] = defaultdict(float)
    for item in items:
        weight[_scope(item)] += known.get(item.nodeid, UNKNOWN_S)
    first = {_scope(i) for i in items if i.nodeid.split("::")[0] in judge}  # a group moves whole
    items.sort(key=lambda i: (_scope(i) not in first, -weight[_scope(i)], _scope(i), i.nodeid))


def pytest_deselected(items) -> None:
    _narrowed.update(i.nodeid.split("::")[0] for i in items)


def pytest_collection_finish(session) -> None:
    """Record what was collected and which files an argument named a node inside."""
    config = session.config
    _collected.update(i.nodeid for i in session.items)
    root, here = Path(config.rootpath), Path(config.invocation_params.dir)
    for arg in config.args:
        path, sep, _ = arg.partition("::")
        if sep:
            with contextlib.suppress(ValueError):  # a path outside the root has no node id here
                _narrowed.add((here / path).resolve().relative_to(root.resolve()).as_posix())
    if hasattr(config, "workeroutput"):  # an xdist worker: its controller collects nothing
        config.workeroutput[_COLLECTED_KEY] = sorted(_collected)
        config.workeroutput[_NARROWED_KEY] = sorted(_narrowed)


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node, error) -> None:
    out = getattr(node, "workeroutput", {})
    _collected.update(out.get(_COLLECTED_KEY, ()))
    _narrowed.update(out.get(_NARROWED_KEY, ()))


def pytest_runtest_logreport(report) -> None:
    _seen[report.nodeid] += report.duration
    pole = getattr(report, "goh_pole", None)
    if isinstance(pole, (int, float)):
        _poles[report.nodeid] += pole


def pytest_sessionfinish(session) -> None:
    """Controller only. A partial run (`-k`, one file) MERGES what it measured; an entry whose test
    file is gone is pruned, and one a whole-file collection did not contain."""
    if hasattr(session.config, "workerinput") or not _seen or not _table:
        return
    root = Path(str(session.config.rootpath))
    whole = {k.split("::")[0] for k in _collected} - _narrowed
    _write(_table[0], _load(), _seen, root, whole)
    poles = _poles_path(_table[0])
    if _poles or poles.is_file():
        _write(poles, _load(poles=True), _poles, root, whole)


def _write(
    cache: Path, old: dict[str, float], seen: dict[str, float], root: Path, whole: set[str]
) -> None:
    """`seen` merged over `old` into `cache`, every entry whose test file is gone pruned, and every
    id of a file in `whole` that this run did not collect."""

    def keep(k: str) -> bool:
        f = k.split("::")[0]
        return (root / f).is_file() and (f not in whole or k in _collected)

    merged = {**old, **{k: round(v, 3) for k, v in seen.items()}}
    merged = {k: v for k, v in merged.items() if keep(k)}
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(merged, sort_keys=True), encoding="utf-8")
        tmp.replace(cache)
    except OSError:
        pass  # an unwritable table costs the next run its order, never its verdict
