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

import atexit
import contextlib
import ctypes
import errno
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

from _reused_tmp import reused_mkdtemp
from timing_bounds import patience

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
    clone_tree(template(factory, key, build), dst)
    return dst


def template(factory, key: str, build) -> Path:
    """The tree `build(path)` makes, built ONCE per session (per worker) and never handed out to
    write in: a caller clones it, or parts of it, with `clone_tree`."""
    built = _TEMPLATES.setdefault(id(factory), (factory, {}))[1]
    if key not in built:
        tree = factory.mktemp(f"template-{key}") / key
        _build_quietly(build, tree)
        built[key] = tree
    return built[key]


# GOH.TEMPLATEQUIET (2026-11-18): every commit a build makes detaches `git maintenance run`, which
# takes and drops .git/objects/maintenance.lock while the template is scanned and cloned (the
# 2026-10-08 race, fixed by hand in two fixtures and missed by the rest). The build runs under an
# XDG config that turns auto-maintenance and auto-gc off: git reads it for EVERY git the build
# spawns, and no env helper drops it (`hermetic_env(drop_git=True)` drops GIT_CONFIG_*, and
# `_fast_git` every GIT_*; neither touches XDG_CONFIG_HOME).
_QUIET_GIT_CONFIG = "[maintenance]\n\tauto = false\n[gc]\n\tauto = 0\n"


@contextlib.contextmanager
def quiet_git() -> Iterator[None]:
    """For the duration: every git started from this process reads a config with no background
    maintenance (the process's own environment is restored on exit)."""
    home = reused_mkdtemp("goh-quiet-git.")
    cfg = Path(home) / "git"
    cfg.mkdir()
    (cfg / "config").write_text(_QUIET_GIT_CONFIG)
    saved = os.environ.get("XDG_CONFIG_HOME")
    os.environ["XDG_CONFIG_HOME"] = home
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = saved
        shutil.rmtree(home, ignore_errors=True)


# Blocks (in a shell, so no test that reaches `template` is a polling one: the wait runs only when
# the quiet config did not hold, and tests/load_sensitive.py would mark every template user for it)
# until no git under $1 holds a background lock.
_WAIT_FOR_LOCKS = (
    'while [ -n "$(find "$1" \\( -name maintenance.lock -o -name gc.pid -o -name gc.log.lock \\) '
    '-print -quit)" ]; do sleep 0.02; done'
)


def _wait_quiescent(tree: Path) -> None:
    """Return once no git under `tree` holds a background lock: what a detached `git maintenance
    run` the build started anyway (a build that sets its own GIT_CONFIG_*) leaves behind. Bounded
    by the box's patience; a lock that never clears is a TimeoutExpired naming the command."""
    subprocess.run(
        ["sh", "-c", _WAIT_FOR_LOCKS, "sh", str(tree)],
        check=True, stdin=subprocess.DEVNULL, timeout=patience(30.0),
    )  # fmt: skip


def _keep_quiet(tree: Path) -> None:
    """Write the quiet config into every repository in `tree` (a work tree's .git, a bare origin):
    the clones inherit it, so a commit a TEST makes in one detaches no maintenance either."""
    for objects in Path(tree).rglob("objects"):
        git_dir = objects.parent
        config = git_dir / "config"
        if (git_dir / "refs").is_dir() and (git_dir / "HEAD").is_file() and config.is_file():
            with config.open("a", encoding="utf-8") as fh:
                fh.write(_QUIET_GIT_CONFIG)


def _build_quietly(build, tree: Path) -> None:
    """Build the template `tree` with background git off, make it quiescent and keep it so in every
    repository it holds, then refuse it if it points at its own directory."""
    with quiet_git():
        build(tree)
    _wait_quiescent(tree)
    _keep_quiet(tree)
    _refuse_self_reference(tree)


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


_PROCESS_TEMPLATES: dict[str, Path] = {}


def process_template(key: str, build) -> Path:
    """`template` for a helper called with only a path (no `tmp_path_factory`): built once per
    process in a temp dir of its own, removed when the process exits."""
    if key not in _PROCESS_TEMPLATES:
        home = Path(reused_mkdtemp("goh-template-" + key + "."))
        atexit.register(shutil.rmtree, home, True)
        tree = home / key
        _build_quietly(build, tree)
        _PROCESS_TEMPLATES[key] = tree
    return _PROCESS_TEMPLATES[key]
