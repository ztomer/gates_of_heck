"""A test's temp names make no file event nothing reads (GOH.FSEVQUIET).

* pytest links `<test>current` to each tmp_path it makes: two or three records a test, and nothing
  in goh reads one. Made no more in a run of the suite; a pytest a test starts keeps pytest's own.
* A clone a module's fixture holds (a `tmp_path_factory.mktemp`, removed only with the run) is put
  back and pooled when the module is done, as a test's is when its tmp_path goes
  (tests/_tree_pool.py): 12 workers' `gates` checkouts were 12,000 entries left for the runner to
  remove. Kept where a test of the process failed: it is the failure's evidence.

Registered by tests/_suite_plugins.py; pinned by tests/test_quiet_tmp.py.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import _tree_clone
import _tree_pool
from _pytest import pathlib as pytest_pathlib, tmpdir


def _no_link(root: Path, target: str, link_to: str | Path) -> None:
    """pytest's `_force_symlink`, not made."""


def install() -> None:
    tmpdir._force_symlink = pytest_pathlib._force_symlink = _no_link


def _paths(value: object) -> Iterator[Path]:
    if isinstance(value, Path):
        yield value
    elif isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, (tuple, list)):
        for part in value:
            yield from _paths(part)


def reclaim(value: object) -> int:
    """The clones a fixture's VALUE is, or holds, put back and pooled; how many."""
    done = 0
    for path in _paths(value):
        top = _tree_pool.real_of(path)
        done += _tree_pool.reclaim(top, _tree_clone._clone_part, _tree_clone._is_sealed)
    return done


def pytest_fixture_post_finalizer(fixturedef, request) -> None:
    if fixturedef.scope != "module" or request.session.testsfailed:
        return
    result = fixturedef.cached_result
    if result is not None and result[2] is None and "PYTEST_DEBUG_TEMPROOT" in os.environ:
        reclaim(result[0])
