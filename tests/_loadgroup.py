"""`--dist loadgroup` that groups: an `xdist_group`'s tests run on ONE worker (GOH.XDISTGROUP).

THE CAUSE. pytest-xdist 3.8.0 (the latest release) told its scheduler a test's group by renaming
the test on the worker: `item._nodeid = f"{item.nodeid}@{group}"` (xdist/remote.py:245). pytest 9.2
reads a node's id from `self._id` (_pytest/nodes.py:290), so the write lands in an attribute nobody
reads, the worker sends plain ids, and LoadGroupScheduling sees no group at all: every one of the
suite's xdist_group markers was inert, and test_self_walk.py's module fixture -- two full
`structural` walks -- was built on three workers at once (six walks, 85 shellcheck children, load
98 on 16 cores, 2026-11-20 06:22).

UPSTREAM fixed it on master, unreleased: ffa67feff7 ("Keep loadgroup metadata separate from node
IDs") sends the group names beside the ids in `collectionfinish`, and the scheduler suffixes its
OWN copy (9844b0e1c3 reads them from the final collection). Test ids stay pytest's.

THIS IS THAT DESIGN ON 3.8.0, BY PUBLIC ROUTES ONLY. Each worker writes {node id: group} for its
final collection (`pytest_collection_finish`, tryfirst: before xdist's own sends the ids) as one
line appended to the run's ONE file, under its flock (GOH.FSEVQUIET: a file, a temp and a rename a
worker were 24 events a run), made by the controller and named in `workerinput`; the
controller's scheduler (`pytest_xdist_make_scheduler`, a LoadGroupScheduling whose
`add_node_collection` -- the Scheduling protocol's own method -- suffixes its copy) reads the file
of the worker whose ids it is handed. The worker writes before it sends, so the file is there.
Ids in reports, the duration table and the verdict records stay unsuffixed, as upstream's will.

A WORKER THAT WROTE NO MAP IS AN ERROR, never a silent no-group run. When xdist itself passes
`group_names`, this plugin stands down, and tests/test_loadgroup.py goes red to have it deleted.
"""

from __future__ import annotations

import fcntl
import inspect
import json
import os
import tempfile
from pathlib import Path

import pytest

_DIR_KEY = "goh_loadgroup_map"
_made: list[Path] = []  # the controller's map file, removed at unconfigure


def group(item) -> str | None:
    """The item's loadgroup unit name as xdist spells it: every xdist_group mark's name, sorted,
    joined by `_` (xdist/remote.py `pytest_collection_modifyitems`); None when it has none."""
    names = {
        str(m.args[0] if m.args else m.kwargs.get("name", "default"))
        for m in item.iter_markers("xdist_group")
    }
    return "_".join(sorted(names)) if names else None


def upstream_sends_groups() -> bool:
    """xdist's own LoadGroupScheduling takes `group_names` (upstream ffa67feff7, once released)."""
    from xdist.scheduler import LoadGroupScheduling

    return "group_names" in inspect.signature(LoadGroupScheduling.add_node_collection).parameters


def _loadgroup(config) -> bool:
    return getattr(config.option, "dist", None) == "loadgroup"  # no option at all: no xdist


def add_map(path: str | Path, worker: str, groups: dict[str, str]) -> None:
    with open(path, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)  # let go with the file, whole line written
        f.write(json.dumps([worker, groups]) + "\n")


def read_map(path: str | Path, worker: str) -> dict[str, str]:
    """The last map `worker` added (a crashed worker's replacement has an id of its own)."""
    with open(path, encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        maps = dict(json.loads(line) for line in f)
    return maps[worker]


def pytest_configure(config) -> None:
    """The controller of a loadgroup run makes the file its workers add their maps to."""
    if hasattr(config, "workerinput") or not _loadgroup(config) or upstream_sends_groups():
        return
    fd, path = tempfile.mkstemp(prefix="goh-loadgroup.")
    os.close(fd)
    _made[:] = [Path(path)]


def pytest_unconfigure(config) -> None:
    if not hasattr(config, "workerinput"):
        for d in _made:
            d.unlink(missing_ok=True)
        _made.clear()


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node) -> None:
    if _made:
        node.workerinput[_DIR_KEY] = str(_made[0])


@pytest.hookimpl(tryfirst=True)
def pytest_collection_finish(session) -> None:
    """A worker: {node id: group} of the collection it is about to send."""
    worker = getattr(session.config, "workerinput", None)
    if worker is None or not worker.get(_DIR_KEY):
        return
    groups = {item.nodeid: g for item in session.items if (g := group(item)) is not None}
    add_map(worker[_DIR_KEY], worker["workerid"], groups)


@pytest.hookimpl(optionalhook=True, tryfirst=True)
def pytest_xdist_make_scheduler(config, log):
    if not _made:
        return None
    from xdist.scheduler import LoadGroupScheduling

    base = _made[0]

    class Grouped(LoadGroupScheduling):
        """LoadGroupScheduling handed each worker's ids with its group suffixed, as upstream's."""

        def __init__(self, config, log=None) -> None:
            super().__init__(config, log)
            self.plain: dict[str, str] = {}  # suffixed id -> pytest's id

        def add_node_collection(self, node, collection) -> None:
            try:
                groups = read_map(base, node.gateway.id)
            except (OSError, ValueError, KeyError) as e:
                raise RuntimeError(
                    f"tests/_loadgroup.py: worker {node.gateway.id} wrote no group map ({e}): "
                    "under --dist loadgroup every xdist_group would be inert"
                ) from e
            suffixed = [f"{n}@{groups[n]}" if n in groups else n for n in collection]
            self.plain.update(zip(suffixed, collection, strict=True))
            super().add_node_collection(node, suffixed)

        def remove_node(self, node):
            """The crashed test's id as pytest spells it, never the scheduler's suffixed copy."""
            crashed = super().remove_node(node)
            return self.plain.get(crashed, crashed) if crashed else crashed

    return Grouped(config, log)
