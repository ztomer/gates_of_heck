"""A clone a test is done with is the next test's (GOH.FSEVQUIET).

Cloning a template is three file events; removing the clone is one for each of its entries (546 for
a goh checkout, 12 for a one-commit repository), and that was most of what the suite's fixtures
cost. A clone of a run-wide template (tests/_tree_clone.py) in a passed test's tmp_path is put back
as the template is and renamed into the run's pool; the next `clone_tree` of that template renames
it out. No two tests ever hold it at once, and what a test gets is what a fresh clone is.

Told by: every entry's name and kind, a file's mode, size and modification time against the
template's, and its ctime against the moment the test was given the tree. A ctime is the kernel's:
no write, chmod, rename over, link or utime leaves it old, whatever the modification time was set
to. Judged when the test ends and again when the next one takes it (a process that outlived its
test). What changed is removed and cloned again from the template; a tree that costs as much to put
back as to remove is removed, as is one with a database in it.

Off where clonefile(2) is not, and outside a run. Registered by tests/_suite_plugins.py; pinned by
tests/test_tree_pool.py.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import stat
from collections.abc import Callable
from pathlib import Path

import _quiet_pack

TEMPLATES = ("/goh-templates/", "/goh-process-templates/")
_ROOT: list[str] = []
_OUT: dict[str, tuple[str, int, int]] = {}  # a clone given out -> its template, stamp, inode
_SHAPES: dict[str, dict[str, tuple]] = {}  # template -> {path in it: what a clone has there}
_N = [0]
Clone = Callable[[str, str], None]
Sealed = Callable[[str], bool]


def _kind(st: os.stat_result) -> tuple:
    if stat.S_ISDIR(st.st_mode):
        return ("dir",)
    return (stat.S_IFMT(st.st_mode), stat.S_IMODE(st.st_mode), st.st_size, st.st_mtime_ns)


def _tree(top: str) -> dict[str, os.stat_result]:
    out, todo = {}, [""]
    while todo:
        here = todo.pop()
        with os.scandir(os.path.join(top, here)) as entries:
            for e in entries:
                rel = os.path.join(here, e.name)
                out[rel] = st = e.stat(follow_symlinks=False)
                if stat.S_ISDIR(st.st_mode):
                    todo.append(rel)
    return out


def _shape(src: str, sealed: Sealed) -> dict[str, tuple]:
    """What a clone of the template SRC holds; a directory's mode apart, a sealed one's (the
    template's alone) as a clone has it."""
    if src not in _SHAPES:
        _SHAPES[src] = {
            rel: ("dir", 0o755 if sealed(os.path.join(src, rel)) else stat.S_IMODE(st.st_mode))
            if stat.S_ISDIR(st.st_mode)
            else _kind(st)
            for rel, st in _tree(src).items()
        }
    return _SHAPES[src]


def real_of(path: object) -> str:
    """PATH with its parents resolved (the run's root is /var's and /private/var's), itself not."""
    parent, name = os.path.split(os.path.abspath(os.fspath(path)))
    return os.path.join(os.path.realpath(parent), name)


def eligible(src: str) -> bool:
    """A template of this run: under its root, or under the owner's run that keeps the templates of
    every pytest run of it (lib/build_once.py GOH_BUILD_RUN, GOH.BUILDONCE)."""
    run = os.environ.get("GOH_BUILD_RUN", "")
    roots = (_ROOT[0], os.path.realpath(run)) if _ROOT and run else tuple(_ROOT)
    return any(src.startswith(r + os.sep) for r in roots) and any(t in src for t in TEMPLATES)


def _changes(src: str, dst: str, stamp: int, sealed: Sealed) -> tuple[list[str], list[str], int]:
    """(what to remove of the clone DST, what SRC has that it then has not -- and each directory
    whose mode is another --, the entries DST has)."""
    want, have = _shape(src, sealed), _tree(dst)
    gone, modes = [], []
    for rel, st in have.items():
        old = want.get(rel)
        if old is None or (old[0] == "dir") != stat.S_ISDIR(st.st_mode):
            gone.append(rel)
        elif old[0] == "dir":
            if stat.S_IMODE(st.st_mode) != old[1]:
                modes.append(rel)
        elif st.st_ctime_ns >= stamp or _kind(st) != old:
            gone.append(rel)
    out = set(gone)
    return gone, modes + [rel for rel in want if rel not in have or rel in out], len(have)


def _restore(src: str, dst: str, stamp: int, clone: Clone, sealed: Sealed) -> bool:
    """DST as a clone of SRC is, or False: it costs less to remove."""
    gone, made, n = _changes(src, dst, stamp, sealed)
    top = 0o755 if sealed(src) else stat.S_IMODE(os.lstat(src).st_mode)
    if stat.S_IMODE(os.lstat(dst).st_mode) != top:
        os.chmod(dst, top)
    if len(gone) + len(made) + 4 >= n:
        return False
    for rel in gone:
        path = os.path.join(dst, rel)
        if not os.path.lexists(path):  # under one already removed
            continue
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.unlink(path)
    for rel in sorted(made):  # a parent before what it holds
        if not os.path.lexists(os.path.join(dst, rel)):
            clone(os.path.join(src, rel), os.path.join(dst, rel))
    for rel in made:
        kind = _shape(src, sealed)[rel]
        if kind[0] == "dir":
            os.chmod(os.path.join(dst, rel), kind[1])
    return True


def take(src: str, dst: str, clone: Clone, sealed: Sealed) -> bool:
    """Whether a clone of the template SRC that another test is done with is at DST now."""
    if not eligible(src):
        return False
    home = _home(src)
    with contextlib.suppress(FileNotFoundError):
        for name in sorted(os.listdir(home)):
            try:
                os.rename(os.path.join(home, name), dst)
            except FileNotFoundError:
                if not os.path.isdir(os.path.dirname(dst)):
                    raise  # DST's parent is missing: the caller's error
                continue  # another worker's now
            if _restore(src, dst, int(name.split(".")[0]), clone, sealed):
                given(src, dst)
                return True
            shutil.rmtree(dst)
    return False


def origin(src: str, sealed: Sealed) -> str:
    """The part of a template SRC is, where SRC is an untouched part of a clone given out: a clone
    of it is the template's, so it is pooled too. SRC where it is none."""
    for top, (its, stamp, _inode) in _OUT.items():
        if (src == top or src.startswith(top + os.sep)) and not os.path.islink(src):
            part = its + src[len(top) :]
            with contextlib.suppress(OSError):  # no part of the template, or no directory
                if not any(_changes(part, src, stamp, sealed)[:2]):
                    return part
    return src


def in_parts(src: str) -> bool:
    """Whether SRC is a clone given out that its test changed, or holds some: cloned entry by entry,
    what is untouched in it is the template's."""
    known = src in _OUT or any(os.path.dirname(top) == src for top in _OUT)
    return known and os.path.isdir(src) and not os.path.islink(src)


def _now(top: str) -> tuple[int, int]:
    """(the kernel's moment, TOP's inode): what is done in TOP from here on is later."""
    os.chmod(top, stat.S_IMODE(os.lstat(top).st_mode))
    st = os.lstat(top)
    return st.st_ctime_ns, st.st_ino


def given(src: str, dst: str) -> None:
    """DST is a whole, fresh clone of SRC from here on."""
    if eligible(src) and os.path.isdir(dst) and not os.path.islink(dst):
        _OUT[dst] = (src, *_now(dst))


def _home(src: str) -> str:
    return os.path.join(_ROOT[0], "goh-tree-pool", hashlib.sha256(src.encode()).hexdigest()[:16])


def whole(dst: str, sealed: Sealed) -> str:
    """The template DST is an untouched clone of, or ""."""
    src, stamp, inode = _OUT.get(dst, ("", 0, 0))
    if src and os.lstat(dst).st_ino == inode and not any(_changes(src, dst, stamp, sealed)[:2]):
        return src
    return ""


def put(dst: str, clone: Clone, sealed: Sealed) -> bool:
    """Whether the clone DST is put back and in the pool; what is left of it if not is the
    caller's to remove."""
    src, stamp, inode = _OUT.pop(dst)
    try:
        if any(r.endswith((".db", ".sqlite")) for r in _shape(src, sealed)):
            return False
        if os.lstat(dst).st_ino != inode or not _restore(src, dst, stamp, clone, sealed):
            return False
        os.makedirs(_home(src), exist_ok=True)
        _N[0] += 1
        os.rename(dst, os.path.join(_home(src), f"{_now(dst)[0]}.{os.getpid()}.{_N[0]}"))
    except OSError:
        return False
    return True


def reclaim(path: str, clone: Clone, sealed: Sealed) -> int:
    """The clones PATH is or holds (a passed test's tmp_path, about to be removed) put back and
    pooled; how many. The innermost first: one made inside another is no part of it."""
    mine = (d for d in _OUT if d == path or d.startswith(path + os.sep))
    under = sorted(mine, key=len, reverse=True)
    return sum([put(dst, clone, sealed) for dst in under])


def install(root: str, clone: Clone, sealed: Sealed) -> None:
    from _pytest import tmpdir

    _ROOT[:] = [os.path.realpath(root)]
    real = shutil.rmtree

    def rmtree(path: Path, *a, **kw) -> None:
        _quiet_pack.drop(real_of(path))
        reclaim(real_of(path), clone, sealed)
        real(path, *a, **kw)

    tmpdir.rmtree = rmtree
