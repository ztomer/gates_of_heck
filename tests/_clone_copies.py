"""A FILE A TEST COPIES IS CLONED (GOH.FSEVQUIET): `shutil.copyfile`, `copy`, `copy2` and `copytree`
in the suite's own processes write no bytes where clonefile(2) can serve them.

shutil copies DATA on macOS (fcopyfile(3), COPYFILE_DATA), so each test that stood a built `goh`
in a tree of its own wrote 38.8 MB of it again, and a copied export its 16 MB. The copy is the same
file: its bytes, the mode and times `copyfile` gives (a fresh file's: copy2's copystat follows), the
audit events the read audit reads (tests/_read_audit.py). Only where nothing stands at the target
and the source is a regular file; every other case, error and platform is shutil's own.
Registered by tests/_suite_plugins.py. Pinned by tests/test_clone_copies.py.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys

from _tree_clone import _CLONEFILE

_COPYFILE = shutil.copyfile
_UMASK = os.umask(0o022)
os.umask(_UMASK)


def _cloned(src, dst) -> bool:
    """Whether `dst` is now a clone of the regular file `src`; nothing is at `dst` if not."""
    try:
        if os.path.lexists(dst):
            return False
        st = os.stat(src)
    except (OSError, TypeError, ValueError):
        return False
    if not stat.S_ISREG(st.st_mode) or not st.st_size or not os.access(src, os.R_OK):
        return False
    real = os.path.realpath(src)  # a symlink is followed, as copyfile's default does
    sys.audit("shutil.copyfile", src, dst)
    if _CLONEFILE(os.fsencode(real), os.fsencode(dst), 0) != 0:
        return False
    sys.audit("open", src, "rb", os.O_RDONLY)
    os.chmod(dst, 0o666 & ~_UMASK)
    os.utime(dst)
    return True


def copyfile(src, dst, *, follow_symlinks=True):
    if (follow_symlinks or not os.path.islink(src)) and _cloned(src, dst):
        return dst
    return _COPYFILE(src, dst, follow_symlinks=follow_symlinks)


def install() -> None:
    if _CLONEFILE is not None:
        shutil.copyfile = copyfile
