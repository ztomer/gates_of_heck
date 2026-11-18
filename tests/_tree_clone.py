"""`clone_tree(src, dst)`: a private copy of a fixture template, in one syscall where it can be.

GOH.FIXTURECOST (2026-11-17): fixtures that build the same tree for every test (a committed copy of
gates/ checks/ lib/ tui/, a repository with a commit) build it ONCE per worker and clone it per
test.
On APFS, clonefile(2) copies a whole directory tree copy-on-write in one call (lib/: ~2 ms against
~65 ms for shutil.copytree, measured); elsewhere, or across volumes, it is shutil.copytree. Either
way the copy is the test's own: tests/test_tree_clone.py writes everything a test can into one clone
and reads the other and the template unchanged.
"""

from __future__ import annotations

import ctypes
import errno
import os
import shutil
import sys
from pathlib import Path

# clonefile(2) cannot serve these: another volume, a filesystem without clones. Any other error is
# a real one and is raised.
_FALL_BACK = {errno.EXDEV, errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS}


def _clonefile():
    if sys.platform != "darwin":
        return None
    fn = getattr(ctypes.CDLL(None, use_errno=True), "clonefile", None)
    if fn is not None:
        fn.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint32)
        fn.restype = ctypes.c_int
    return fn


_CLONEFILE = _clonefile()


def copytree_clone(src: Path | str, dst: Path | str) -> None:
    """The portable copy: what `clone_tree` does where clonefile(2) is not available."""
    if os.path.lexists(dst):
        raise FileExistsError(errno.EEXIST, "clone target exists", str(dst))
    shutil.copytree(src, dst, symlinks=True)


def clone_tree(src: Path | str, dst: Path | str) -> None:
    """Copy the tree `src` to `dst` (which must not exist): clonefile(2), else copytree."""
    if os.path.lexists(dst):
        raise FileExistsError(errno.EEXIST, "clone target exists", str(dst))
    if _CLONEFILE is not None:
        if _CLONEFILE(os.fsencode(src), os.fsencode(dst), 0) == 0:
            return
        err = ctypes.get_errno()
        if err not in _FALL_BACK:
            raise OSError(err, os.strerror(err), str(dst))
    copytree_clone(src, dst)


# id(session factory) -> (that factory, {key: the built template}). A fixture defined in a test
# module and IMPORTED by others (`from test_proven import goh`) cannot name a session fixture of its
# own without every importer importing that too; `from_template` needs only pytest's own
# session-scoped `tmp_path_factory`. The factory is held, so its id() cannot be reused by another.
_TEMPLATES: dict[int, tuple[object, dict[str, Path]]] = {}


def template_of(factory, key: str) -> Path | None:
    """The template `from_template` built for `key` in this session, if any."""
    return _TEMPLATES.get(id(factory), (None, {}))[1].get(key)


def from_template(factory, key: str, build, dst: Path) -> Path:
    """`dst` as a clone of the tree `build(path)` makes, built ONCE per session (per worker) under
    `factory` (the session's `tmp_path_factory`). `build` must make the same tree every time: it is
    what each test would have built for itself, and nothing the test then writes reaches it."""
    built = _TEMPLATES.setdefault(id(factory), (factory, {}))[1]
    if key not in built:
        tree = factory.mktemp(f"template-{key}") / key
        build(tree)
        _refuse_self_reference(tree)
        built[key] = tree
    clone_tree(built[key], dst)
    return dst


def _refuse_self_reference(tree: Path) -> None:
    """A template that records a path INTO its own private directory (a remote URL, a sibling bare
    origin, a worktree's gitdir, a config value) hands every clone a pointer back to shared state:
    a write through it lands there, and every later test sees it. Such a fixture stays per-test;
    this refuses it at build time. The directory is `tree`'s parent, which `from_template` made
    for this template alone, so any mention of it is one."""
    home = Path(tree).parent
    names = {os.fsencode(str(home)), os.fsencode(str(home.resolve()))}
    for path in Path(tree).rglob("*"):
        if path.is_file() and not path.is_symlink():
            data = path.read_bytes()
            if any(n in data for n in names):
                raise ValueError(
                    f"template {tree} names its own directory in {path}: not cloneable"
                )
