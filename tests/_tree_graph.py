"""ONE dependency graph of the real tree per test process (GOH.SUITEDIET).

A selector pass over goh's own tree (tools/affected_tests.py `affected`/`readers`) builds a
lib/test_closure.py Closure and fills its caches -- imports, slices, reach -- for every test file:
13 s for the first pass, 10 s for each later one with a fresh graph, 3 s with the warm one
(measured 2026-11-22 at idle). A Closure's caches are by FILE, never by the change asked
(affected_tests.affected's GRAPH), so every real-tree test of one worker shares this one: the
scanner test of tests/test_affected_binary_reach.py ran three fresh passes (76 s under load).

The tree is read once per process, as each fresh graph read it once per pass: a test that edits
goh's own checkout is a defect of its own (tests/_tree_guard.py).
"""

from __future__ import annotations

import functools

from conftest import REPO_ROOT

from lib import test_closure


@functools.cache
def real() -> test_closure.Closure:
    """The worker's graph of REPO_ROOT, built on first use."""
    return test_closure.Closure(REPO_ROOT)
