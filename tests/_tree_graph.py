"""ONE dependency graph of the real tree per RUN (GOH.SUITEDIET, GOH.PYSLOWTESTS).

A selector pass over goh's own tree (tools/affected_tests.py `affected`/`readers`) builds a
lib/test_closure.py Closure and fills its caches -- imports, slices, reach -- for every test file:
13 s for the first pass, 10 s for each later one with a fresh graph, 3 s with the warm one
(measured 2026-11-22 at idle). A Closure's caches are by FILE, never by the change asked
(affected_tests.affected's GRAPH), so every real-tree test shares this one.

Each of a run's 12 workers filled its own (62-140 s of a loaded worker, about 725 s a run,
2026-11-25). The first to ask now fills it under the run's template lock (tests/_tree_clone.py
`_run_template`: flock, then one rename) and leaves its caches as ONE pickle, keyed by the bytes of
the tree; the others read it. Two things are left out, each cheap to make again and 38 MB to keep:
the files' texts (a Closure reads them when it is made) and their prose-blanked copies. A whole
selection asked by several files (`affected`) is kept the same way.

The tree is read once per run: a test that edits goh's own checkout is a defect of its own
(tests/_tree_guard.py). Pinned by tests/test_tree_graph.py.
"""

from __future__ import annotations

import functools
import hashlib
import os
import pickle
import sys
from pathlib import Path

import suite_tmp
from _tree_clone import _run_template
from conftest import REPO_ROOT

from lib import test_closure

sys.path.insert(0, str(REPO_ROOT / "tools"))
import affected_tests

# A Closure's caches that hold no file's whole text: what one worker fills and the rest read.
CACHES = (
    "_edges", "_named", "_uses", "_base", "_reach", "_bare", "_cuts", "_sliced", "_cut_code",
    "_below", "_loaded",
)  # fmt: skip


def _store() -> Path:
    """The run's own root (tests/suite_tmp.py): every worker's, gone with the run."""
    return Path(os.environ[suite_tmp.MARKER]) / "goh-graph"


def _identity(graph: test_closure.Closure) -> str:
    """The tree `graph` is of: its place, every code file's bytes, the undeclared readers."""
    h = hashlib.sha256(f"{graph.root}\0{sorted(graph.undeclared)}".encode())
    for rel, text in sorted(graph.texts.items()):
        h.update(f"\0{rel}\0{text}".encode("utf-8", "replace"))
    return h.hexdigest()[:16]


def _once(store: Path, key: str, build):
    """What `build()` returns, built by the first process of the run to ask for `key`."""
    made = _run_template(store, key, lambda path: path.write_bytes(pickle.dumps(build(), 5)))
    return pickle.loads(made.read_bytes())


def _fill(graph: test_closure.Closure) -> dict[str, object]:
    """The caches of `graph` once every test's closure is asked: what a selector pass asks first."""
    for rel in [p for p in graph.texts if p.startswith("tests/test_")]:
        graph.base_reach(rel)
        graph.reach(rel, runs=False)
    graph.load_reads()
    return {name: getattr(graph, name) for name in CACHES}


def shared(root: Path, store: Path) -> test_closure.Closure:
    """A graph of `root` with its caches filled, by this process or by another of the run's."""
    graph = test_closure.Closure(root)
    for name, held in _once(store, f"graph-{_identity(graph)}", lambda: _fill(graph)).items():
        setattr(graph, name, held)
    return graph


@functools.cache
def real() -> test_closure.Closure:
    """The run's graph of REPO_ROOT, read (or filled) on this worker's first use."""
    return shared(REPO_ROOT, _store())


@functools.cache
def _affected(changed: tuple[str, ...], enqueue: bool) -> dict[str, str]:
    key = hashlib.sha256(repr((changed, enqueue)).encode()).hexdigest()[:16]
    return _once(
        _store(),
        f"affected-{_identity(real())}-{key}",
        lambda: affected_tests.affected(REPO_ROOT, list(changed), enqueue, graph=real()),
    )


def affected(changed: list[str], enqueue: bool = False) -> dict[str, str]:
    """`affected_tests.affected` of the real tree, asked once a run: for a test that judges the
    SELECTION (a monkeypatched selector asks affected_tests itself)."""
    return dict(_affected(tuple(changed), enqueue))
