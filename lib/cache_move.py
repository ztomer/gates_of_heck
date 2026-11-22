#!/usr/bin/env python3
"""Demote a cold cache directory to the removable tier and promote it back (GOH.CACHEEVICT).

A cargo build dir or an llvm-cov entry the evictor picks is MOVED, not deleted, when the large tier
(lib/cache_tier.py) has room: measured 2026-11-22, a 4.3 GiB, 10,537-file build dir went out in
11.5 s and came back in 12.4 s with cargo still reporting every unit Fresh, against a 300-440 s
cold rebuild. The copy keeps every mtime (cargo's freshness reads them) and every mode bit.

Every write on the drive goes through a directory fd opened after the resolver's checks (`dir_fd=`),
never a path resolved again, so a drive that leaves mid-copy fails the copy with ENOENT or EIO
instead of landing it on the internal disk. A copy goes to a `.part` name and is renamed when
whole; the source is removed only after that. A demoted directory sits at
`<root>/demoted/<kind>/<sha256(internal path)[:24]>/`, with `.goh-origin` beside it naming the
internal path, so promotion needs no index.

usage: cache_move.py promote PATH [--kind K]   bring PATH back from the large tier if it is there
       cache_move.py promote-for CARGO_DIR       the workspace's build dir, before a cargo run
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # a copy outside any checkout: run as is
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import contextlib
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cache_tier  # lib/, beside this file

DEMOTED = "demoted"
ORIGIN = ".goh-origin"
try:
    import posix

    _FCOPY = posix._COPYFILE_DATA if hasattr(posix, "_fcopyfile") else None
except (ImportError, AttributeError):  # not macOS: a plain copy
    posix, _FCOPY = None, None


def slot(path: str) -> str:
    """The large-tier name of an internal path: stable identity, never a session id."""
    return hashlib.sha256(os.path.abspath(path).encode()).hexdigest()[:24]


def _copy_file(sfd: int, name: str, dfd: int, st: os.stat_result) -> None:
    src = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=sfd)
    try:
        dst = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IMODE(st.st_mode) | 0o200,
                      dir_fd=dfd)  # fmt: skip
        try:
            if _FCOPY is not None:
                posix._fcopyfile(src, dst, _FCOPY)  # APFS clone or kernel copy, as shutil does
            else:
                with open(src, "rb", closefd=False) as fin, open(dst, "wb", closefd=False) as fout:
                    shutil.copyfileobj(fin, fout, 1 << 20)
            os.fchmod(dst, stat.S_IMODE(st.st_mode))
            os.utime(dst, ns=(st.st_atime_ns, st.st_mtime_ns))
        finally:
            os.close(dst)
    finally:
        os.close(src)


def copy_tree_fd(sfd: int, dfd: int, skip: frozenset[str] = frozenset()) -> int:
    """Copy the directory open at `sfd` into the (empty) directory open at `dfd`, both by fd; the
    bytes copied. Symlinks are copied as links; directory mtimes are set after their contents."""
    total = 0
    for entry in os.scandir(sfd):
        if entry.name in skip:
            continue
        st = entry.stat(follow_symlinks=False)
        if stat.S_ISLNK(st.st_mode):
            os.symlink(os.readlink(entry.name, dir_fd=sfd), entry.name, dir_fd=dfd)
            os.utime(entry.name, ns=(st.st_atime_ns, st.st_mtime_ns), dir_fd=dfd,
                     follow_symlinks=False)  # fmt: skip
        elif stat.S_ISDIR(st.st_mode):
            os.mkdir(entry.name, 0o700, dir_fd=dfd)
            csfd = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=sfd)
            cdfd = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dfd)
            try:
                total += copy_tree_fd(csfd, cdfd)
                os.chmod(entry.name, stat.S_IMODE(st.st_mode), dir_fd=dfd)
                os.utime(entry.name, ns=(st.st_atime_ns, st.st_mtime_ns), dir_fd=dfd,
                         follow_symlinks=False)  # fmt: skip
            finally:
                os.close(csfd)
                os.close(cdfd)
        elif stat.S_ISREG(st.st_mode):
            _copy_file(sfd, entry.name, dfd, st)
            total += st.st_size
    return total


def _open_dir(name: str, dir_fd: int, create: bool) -> int:
    if create:
        with contextlib.suppress(FileExistsError):
            os.mkdir(name, 0o755, dir_fd=dir_fd)
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd)


def _rm_at(dir_fd: int, name: str) -> None:
    """Remove `name` under `dir_fd` (shutil.rmtree's own dir_fd form: no path re-resolved)."""
    try:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if stat.S_ISDIR(st.st_mode):
        shutil.rmtree(name, dir_fd=dir_fd)
    else:
        os.unlink(name, dir_fd=dir_fd)


def _kind_fd(root_fd: int, kind: str, create: bool) -> int:
    dfd = _open_dir(DEMOTED, root_fd, create)
    try:
        return _open_dir(kind, dfd, create)
    finally:
        os.close(dfd)


def demote(path: str, root_fd: int, kind: str) -> int:
    """Move the internal directory `path` under the large tier open at `root_fd`; bytes moved.
    Raises (and leaves `path` whole) when any write on the drive fails."""
    name, part = slot(path), slot(path) + ".part"
    kfd = _kind_fd(root_fd, kind, create=True)
    try:
        _rm_at(kfd, part)
        _rm_at(kfd, name)
        sfd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            pfd = _open_dir(part, kfd, create=True)
            try:
                moved = copy_tree_fd(sfd, pfd)
                fd = os.open(ORIGIN, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644, dir_fd=pfd)
                os.write(fd, os.path.abspath(path).encode() + b"\n")
                os.close(fd)
            finally:
                os.close(pfd)
        finally:
            os.close(sfd)
        os.rename(part, name, src_dir_fd=kfd, dst_dir_fd=kfd)
    finally:
        os.close(kfd)
    shutil.rmtree(path)
    return moved


def demoted(path: str, root_fd: int, kind: str) -> bool:
    try:
        kfd = _kind_fd(root_fd, kind, create=False)
    except OSError:
        return False
    try:
        return _exists_at(kfd, slot(path))
    finally:
        os.close(kfd)


def _exists_at(dir_fd: int, name: str) -> bool:
    try:
        return stat.S_ISDIR(os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode)
    except OSError:
        return False


def promote(path: str, root_fd: int, kind: str) -> int:
    """Bring `path` back from the large tier to its internal path; bytes moved, 0 when it is not
    there or `path` already exists (a live dir always wins over a demoted copy)."""
    if os.path.lexists(path):
        return 0
    try:
        kfd = _kind_fd(root_fd, kind, create=False)
    except OSError:
        return 0
    try:
        name = slot(path)
        if not _exists_at(kfd, name):
            return 0
        parent, base = os.path.split(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)  # the internal disk: always present
        part = os.path.join(parent, f".{base}.promote-{os.getpid()}")
        shutil.rmtree(part, ignore_errors=True)
        os.mkdir(part, 0o755)
        sfd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=kfd)
        dfd = os.open(part, os.O_RDONLY | os.O_DIRECTORY)
        try:
            moved = copy_tree_fd(sfd, dfd, frozenset({ORIGIN}))
        finally:
            os.close(sfd)
            os.close(dfd)
        os.rename(part, path)
        _rm_at(kfd, name)
        return moved
    finally:
        os.close(kfd)


def _cargo_build_dir(cargo_dir: str) -> str | None:
    """The build dir cargo names for the workspace at `cargo_dir`; None when cargo cannot say."""
    try:
        r = subprocess.run(["cargo", "metadata", "--format-version", "1", "--no-deps", "--offline"],
                           cwd=cargo_dir, capture_output=True, text=True, timeout=60, check=False,
                           stdin=subprocess.DEVNULL)  # fmt: skip
        return json.loads(r.stdout).get("build_directory") if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def promote_for(cargo_dir: str, build_dir=_cargo_build_dir) -> str:
    """Before a cargo run in `cargo_dir`: its build dir back from the large tier if demoted there.
    Cheap when nothing is demoted (no cargo asked, no diskutil); one line said, '' when quiet."""
    verdict = cache_tier.resolve(cache_tier.internal_root(), own_uuid=False)
    if not verdict.external:
        return ""
    fd = cache_tier.open_root(verdict)
    try:
        try:
            kfd = _kind_fd(fd, "build", create=False)
        except OSError:
            return ""
        try:
            if not [n for n in os.listdir(kfd) if not n.endswith(".part")]:
                return ""
        finally:
            os.close(kfd)
        target = build_dir(cargo_dir)
        if not target:
            return ""
        moved = promote(target, fd, "build")
    finally:
        os.close(fd)
    return (
        f"promote: {target}: {moved / (1 << 30):.2f} GiB back from the large tier" if moved else ""
    )


def main(argv: list[str]) -> int:
    if "--help" in argv or "-h" in argv:
        print(__doc__.rsplit("usage: ", 1)[1].strip())
        return 0
    if len(argv) == 2 and argv[0] == "promote-for":
        said = promote_for(argv[1])
        if said:
            print(said, file=sys.stderr)
        return 0
    if len(argv) < 2 or argv[0] != "promote":
        print(__doc__.rsplit("usage: ", 1)[1].strip(), file=sys.stderr)
        return 2
    kind = argv[argv.index("--kind") + 1] if "--kind" in argv else "build"
    verdict = cache_tier.resolve(cache_tier.internal_root(), own_uuid=False)
    if not verdict.external:
        print(f"promote: no large tier ({verdict.reason})")
        return 0
    fd = cache_tier.open_root(verdict)
    try:
        moved = promote(argv[1], fd, kind)
    finally:
        os.close(fd)
    print(f"promote: {argv[1]}: {moved / (1 << 30):.2f} GiB back" if moved else
          f"promote: {argv[1]}: nothing demoted")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
