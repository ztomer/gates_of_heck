"""THE BYTES EACH TEST LEAVES WRITTEN (GOH.FSEVQUIET): the SSD's half of tests/_fs_churn.py.

A file event says a path changed, not how much: 100 MB through one `dd` is two events. So, in each
process that runs tests, what a test's own tmp_path holds when its teardown starts is summed once:

* `bytes`: the logical size of every file the test made there (a hard link once, a symlink none);
* `cloned`: the size of those that are clones of a file OUTSIDE it (clonefile(2): a template's),
  which wrote metadata only. A clone is told by its clone id's reference count being more than the
  files of the tmp_path that carry the id; clones made within it are counted once, as bytes.

A lower bound by construction: a file the test wrote and removed, or wrote over, and whatever it
wrote outside its tmp_path, is in `device_bytes` only -- the whole machine's write counters
(IOBlockStorageDriver) over the run. No process's rusage: it loses the children, which write most.
On only where _fs_churn counts; xdist workers hand their sums to the controller when they go down.
Pinned by tests/test_fs_bytes.py.
"""

from __future__ import annotations

import ctypes
import os
import re
import stat
import struct
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pytest
from timing_bounds import patience

KEY = "gates_of_heck_fs_bytes"  # the controller -> a worker (on), a worker -> it (the sums)
_ON: list[bool] = [False]
_BYTES: Counter[str] = Counter()
_CLONED: Counter[str] = Counter()
LOST: list[str] = []  # the workers that went down with their readings: what they ran reads as none
DIRS: dict[str, str] = {}  # a test's tmp_path, as lib/fs_events.py `own_dir` spells it -> node id

# <sys/attr.h>: getattrlist(2) with the extended common attributes.
_FSOPT = 0x1 | 0x20  # NOFOLLOW | ATTR_CMN_EXTENDED
_CLONEID, _CLONE_REFCNT = 0x100, 0x1000


class _AttrList(ctypes.Structure):
    _fields_ = (
        ("bitmapcount", ctypes.c_uint16), ("reserved", ctypes.c_uint16),
        ("common", ctypes.c_uint32), ("vol", ctypes.c_uint32), ("dir", ctypes.c_uint32),
        ("file", ctypes.c_uint32), ("fork", ctypes.c_uint32),
    )  # fmt: skip


_LIBC = ctypes.CDLL(None, use_errno=True) if sys.platform == "darwin" else None
_WANT = _AttrList(5, 0, 0, 0, 0, 0, _CLONEID | _CLONE_REFCNT)
_BUF = ctypes.create_string_buffer(64)


def clone_of(path: str) -> tuple[int, int]:
    """(clone id, files that share it) of `path`; (0, 1) where the filesystem has no clones."""
    if _LIBC is None:
        return 0, 1
    if _LIBC.getattrlist(os.fsencode(path), ctypes.byref(_WANT), _BUF, 64, _FSOPT) != 0:
        return 0, 1
    size, ident, refs = struct.unpack_from("=IQI", _BUF.raw)
    return (ident, refs) if size >= 16 else (0, 1)


def written(root: Path | str) -> tuple[int, int]:
    """(bytes, cloned) of the files under `root`, as the module says."""
    total = cloned = 0
    inodes: set[int] = set()
    shared: dict[int, list[int]] = defaultdict(lambda: [0, 0, 0])  # id -> size, refs, seen here
    for here, _dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(here, name)
            try:
                st = os.lstat(path)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode) or not st.st_size or st.st_ino in inodes:
                continue
            inodes.add(st.st_ino)
            ident, refs = clone_of(path)
            if ident and refs > 1:
                of = shared[ident]
                of[0], of[1], of[2] = max(of[0], st.st_size), refs, of[2] + 1
            else:
                total += st.st_size
    for size, refs, here in shared.values():
        if refs > here:
            cloned += size * here
        else:
            total += size
    return total, cloned


def device_bytes() -> int | None:
    """Every storage device's bytes written since boot; None where there is no such counter."""
    if sys.platform != "darwin":
        return None
    try:
        out = subprocess.run(
            ["/usr/sbin/ioreg", "-rc", "IOBlockStorageDriver", "-w0"],
            capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL,
            timeout=patience(30.0),
        ).stdout  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return None
    return sum(int(n) for n in re.findall(r'"Bytes \(Write\)"=(\d+)', out))


def start() -> None:
    """This process (the controller, or a lone session) counts; its workers are told to."""
    _ON[0] = True
    _BYTES.clear()
    _CLONED.clear()
    DIRS.clear()
    LOST.clear()


def finish(top: int) -> dict:
    """The run's report, and counting off."""
    _ON[0] = False
    return {
        "bytes": sum(_BYTES.values()),
        "cloned_bytes": sum(_CLONED.values()),
        "bytes_per_test": dict(_BYTES.most_common(top)),
        "cloned_per_test": dict(_CLONED.most_common(top)),
        "all_bytes": dict(_BYTES),
    }


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node) -> None:
    node.workerinput[KEY] = _ON[0]


def pytest_sessionstart(session) -> None:
    if getattr(session.config, "workerinput", {}).get(KEY):
        start()


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_teardown(item) -> None:
    tmp = getattr(item, "funcargs", {}).get("tmp_path") if _ON[0] else None
    if tmp is None:
        return
    total, cloned = written(tmp)
    test = item.nodeid
    worker = getattr(item.config, "workerinput", {}).get("workerid")  # never an outer run's env
    DIRS[f"{f'popen-{worker}' if worker else ''}/{tmp.name}"] = test
    if total:
        _BYTES[test] += total
    if cloned:
        _CLONED[test] += cloned


def pytest_sessionfinish(session) -> None:
    if _ON[0] and hasattr(session.config, "workerinput"):
        session.config.workeroutput[KEY] = [dict(_BYTES), dict(_CLONED), DIRS]


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node, error) -> None:
    sums = getattr(node, "workeroutput", {}).get(KEY, ())
    if _ON[0] and not sums:
        LOST.append(str(node.gateway.id))
    for into, part in zip((_BYTES, _CLONED, DIRS), sums, strict=False):
        into.update(part)


def summary(rep: dict, top: int = 20) -> list[str]:
    """The run's bytes, and the `top` tests by them, as lines for the terminal."""
    dev = rep.get("device_bytes")
    lines = [
        f"→ bytes: {rep['bytes'] / 1e6:.1f} MB left written in the tests' tmp_paths,"
        f" {rep['cloned_bytes'] / 1e6:.1f} MB cloned"
        + (f"; every device, all writers: {dev / 1e6:.0f} MB" if dev is not None else "")
    ]
    for test, n in list(rep["bytes_per_test"].items())[:top]:
        lines.append(f"  {n / 1e6:9.2f} MB {test}")
    return lines
