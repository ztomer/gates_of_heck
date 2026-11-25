"""A fixture directory a test copies is copied once a run and cloned (GOH.FSEVQUIET).

`shutil.copytree(<a directory of tests/fixtures>, dst)` made each file and removed it, in every test
that asked; the same call again is a clone of the first one's tree (pooled, tests/_tree_pool.py):
the files as copytree gives them, the same reads told to the read audit (tests/_read_audit.py). A
copy of any other tree, over what stands at `dst`, or with an option beyond `symlinks`, is
shutil's. Registered by tests/_suite_plugins.py. Pinned by tests/test_fixture_copies.py.
"""

from __future__ import annotations

import functools
import os
import shutil
import sys

from _tree_clone import _RUN_ROOT, clone_tree, process_template

FIXTURES = os.path.join(os.path.realpath(os.path.dirname(__file__)), "fixtures")


def _cloned(real, src, dst, symlinks=False, *more, **options):
    where = os.path.realpath(src)
    inside = where.startswith(FIXTURES + os.sep) and not os.path.islink(src)
    if more or options or not inside or not _RUN_ROOT or os.path.lexists(dst):
        return real(src, dst, symlinks, *more, **options)
    name = os.path.relpath(where, FIXTURES).replace(os.sep, "-")
    tree = process_template(
        f"fixture-{name}-{int(bool(symlinks))}", lambda t: real(src, t, symlinks), False
    )
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    clone_tree(tree, dst)
    for here, _dirs, files in os.walk(tree):
        sys.audit("os.scandir", os.path.normpath(os.path.join(src, os.path.relpath(here, tree))))
        for f in files:
            rel = os.path.relpath(os.path.join(here, f), tree)
            sys.audit("open", os.path.join(os.fspath(src), rel), "rb", os.O_RDONLY)
    return dst


def install() -> None:
    shutil.copytree = functools.partial(_cloned, shutil.copytree)
