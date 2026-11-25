"""A closure of goh a test copies is built once a run and cloned (GOH.FSEVQUIET).

`source_closure.copy_closure(dest, *paths)` of this checkout wrote its ~260 files (and worked the
closure out) for every test that asked; the same call again is a clone of the first one's tree: the
files as `copy2` gives them, the same list returned, the same reads told to the read audit
(tests/_read_audit.py). A copy of any other tree, or over what stands at `dest`, is made as before.
Registered by tests/_suite_plugins.py. Pinned by tests/test_closure_clones.py.
"""

from __future__ import annotations

import functools
import hashlib
import os
import sys
from pathlib import Path

from _tree_clone import _RUN_ROOT, clone_tree, process_template

REPO_ROOT = Path(__file__).resolve().parent.parent


def _place(src: Path, dst: Path) -> None:
    if dst.is_dir():
        for entry in sorted(src.iterdir()):
            _place(entry, dst / entry.name)
    else:
        clone_tree(src, dst)


def _cloned(real, dest, *paths, root=REPO_ROOT, extra=()):
    dest = Path(dest)
    if not _RUN_ROOT or Path(os.path.realpath(root)) != REPO_ROOT:
        return real(dest, *paths, root=root, extra=extra)
    key = hashlib.sha256("\0".join([*sorted({*paths, *extra})]).encode()).hexdigest()[:16]
    tree = process_template(
        f"closure-{key}", lambda t: real(t, *paths, root=root, extra=extra), False
    )
    files = sorted(
        os.path.relpath(os.path.join(d, f), tree) for d, _, fs in os.walk(tree) for f in fs
    )
    if any((dest / f).exists() for f in files):
        return real(dest, *paths, root=root, extra=extra)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _place(tree, dest)
    for rel in files:
        sys.audit("open", str(REPO_ROOT / rel), "rb", os.O_RDONLY)
    return files


def install() -> None:
    sys.path.insert(0, str(REPO_ROOT / "lib"))
    import source_closure

    real = source_closure.copy_closure
    source_closure.copy_closure = functools.partial(_cloned, real)
