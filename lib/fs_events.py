"""fs_events.py -- the file events a command makes under some roots, and who made them.

GOH.FSCHURN2 (2026-11-20): fseventsd sat at 100-145% CPU and 11.9 GB while the goh suite ran, two
days after GOH.FSCHURN claimed the cut, and nothing measured it: the regression was felt, not seen.
fseventsd's work is one record per file event, so the count of records is the cost to watch. This
probe reads them the way any client of fseventsd does -- an FSEvents stream (CoreServices, through
ctypes, no sudo, file-level events) on the given roots -- while a command runs, and reports

* events: every (path, flags) record the stream delivered under the roots;
* dirs: directories created, and how many of their names were never seen before in the window;
* the top creators: each event's path mapped to who made it (`creator()`: the pytest test whose
  tmp_path it is, the `<prefix>.` of a mkdtemp name, the suite home's subtree), with shares.

    tools/fs_events.py run --root "$TMPDIR/goh.noindex" --root ~/.cache/goh -- tools/pytest.sh
    tools/fs_events.py run --json out.json --root DIR -- CMD...

Coalescing: fseventsd folds repeated changes to one path inside the stream's latency into one
record, so `events` is a lower bound on syscalls and an exact count of what the stream carried.
A dropped-events flag (the kernel's or this client's queue overflowed) is counted and reported:
a window with drops undercounts. macOS only; elsewhere the probe says it is unavailable (exit 3).
Pinned by tests/test_fs_events.py.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import re
import subprocess
import sys
import threading
import time
from collections import Counter
from typing import Self

UNAVAILABLE = 3

# FSEventStreamEventFlags (CoreServices/FSEvents.h)
MUST_SCAN_SUBDIRS = 0x1
USER_DROPPED = 0x2
KERNEL_DROPPED = 0x4
ITEM_CREATED = 0x100
ITEM_REMOVED = 0x200
ITEM_RENAMED = 0x800
ITEM_MODIFIED = 0x1000
ITEM_IS_FILE = 0x10000
ITEM_IS_DIR = 0x20000
# FSEventStreamCreateFlags
CREATE_NO_DEFER = 0x2
CREATE_FILE_EVENTS = 0x10
SINCE_NOW = 0xFFFFFFFFFFFFFFFF
UTF8 = 0x08000100

_RANDOM_TAIL = re.compile(r"^(.*?[._-])([A-Za-z0-9_]{6,8})$")
_PYTEST_SLOT = re.compile(r"^pytest-\d+$")
_TEST_DIR = re.compile(r"^(test_\w+?)(?:\d+|current)$")


def creator(path: str, roots: list[str]) -> str:
    """Who made `path`: a stable label for the subtree it sits in, derived from the path alone.

    Under a root, the first component that names its maker wins: a pytest tmp_path
    (`pytest-of-<u>/pytest-N/popen-gwK/<test name><n>`) is the test; a mkdtemp-style name
    (`goh-release-buffered.ab12cd34`) is its prefix with `*`; a reused per-worker dir
    (`goh-test-git.gw3.0`) is its prefix; anything else is the first two components."""
    rel = None
    for root in sorted(roots, key=len, reverse=True):
        if path == root or path.startswith(root.rstrip("/") + "/"):
            rel = path[len(root.rstrip("/")) + 1 :]
            label_root = re.sub(r"\.slot\d+$", ".slotN", os.path.basename(root.rstrip("/")))
            break
    if rel is None:
        return "(outside the roots)"
    parts = [p for p in rel.split("/") if p]
    out: list[str] = [label_root]
    for i, part in enumerate(parts):
        if re.match(r"^(goh-pytest|home)\.slot\d+$", part) or _PYTEST_SLOT.match(part):
            out.append(re.sub(r"\d+$", "N", part))
            continue
        if (
            part.startswith("pytest-of-")
            or re.match(r"^popen-gw\d+$", part)
            or part == "goh.noindex"
        ):
            continue
        m = _TEST_DIR.match(part)
        if (
            m
            and i > 0
            and (parts[i - 1].startswith("popen-gw") or _PYTEST_SLOT.match(parts[i - 1]))
        ):
            out.append(m.group(1))
            return "/".join(out)
        m = re.match(r"^(.*?)(gw\d+|main)\.\d+$", part)
        if m:
            out.append(m.group(1) + "<worker>")
            return "/".join(out)
        m = _RANDOM_TAIL.match(part)
        if m and m.group(1).endswith(".") and not m.group(2).isdigit() and m.group(2) != "noindex":
            out.append(m.group(1) + "*")
            return "/".join(out)
        out.append(part)
        if len(out) >= 4:
            break
    return "/".join(out)


class Tally:
    """Every record the stream delivered, folded as it arrives."""

    def __init__(self, roots: list[str]) -> None:
        self.roots = roots
        self.events = 0
        self.dirs_created = 0
        self.fresh_dir_names: set[str] = set()
        self.dropped = 0
        self.by_creator: Counter[str] = Counter()
        self.dirs_by_creator: Counter[str] = Counter()
        self.per_test: Counter[str] = Counter()
        self.lock = threading.Lock()

    def add(self, path: str, flags: int) -> None:
        with self.lock:
            if flags & (USER_DROPPED | KERNEL_DROPPED | MUST_SCAN_SUBDIRS):
                self.dropped += 1
                return
            self.events += 1
            who = creator(path, self.roots)
            self.by_creator[who] += 1
            if "/pytest-N/test_" in who:
                self.per_test[who.rsplit("/", 1)[1]] += 1
            if flags & ITEM_CREATED and flags & ITEM_IS_DIR:
                self.dirs_created += 1
                self.dirs_by_creator[who] += 1
                self.fresh_dir_names.add(path)

    def report(self, seconds: float, top: int = 10) -> dict:
        def shares(c: Counter[str]) -> list[dict]:
            total = sum(c.values()) or 1
            return [
                {"creator": k, "n": v, "share": round(v / total, 4)} for k, v in c.most_common(top)
            ]

        return {
            "seconds": round(seconds, 2),
            "events": self.events,
            "dirs_created": self.dirs_created,
            "distinct_dir_paths": len(self.fresh_dir_names),
            "dirs_per_s": round(self.dirs_created / seconds, 1) if seconds else 0.0,
            "events_per_s": round(self.events / seconds, 1) if seconds else 0.0,
            "dropped": self.dropped,
            "top_creators": shares(self.by_creator),
            "top_dir_creators": shares(self.dirs_by_creator),
            "per_test": dict(self.per_test.most_common(top)),
        }


class Stream:
    """An FSEvents stream on `roots`, file-level, delivering into a Tally on a dispatch queue."""

    def __init__(self, roots: list[str], tally: Tally, latency: float = 0.2) -> None:
        if sys.platform != "darwin":
            raise OSError("FSEvents is macOS only")
        cs = ctypes.CDLL(ctypes.util.find_library("CoreServices"))
        cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
        libc = ctypes.CDLL(None)
        self._cs, self._cf = cs, cf
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFArrayCreate.restype = ctypes.c_void_p
        cf.CFArrayCreate.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_long,
            ctypes.c_void_p,
        ]
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        callbacks = ctypes.c_void_p.in_dll(cf, "kCFTypeArrayCallBacks")
        self._cb_type = ctypes.CFUNCTYPE(
            None,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_char_p),
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_uint64),
        )

        def on_events(_stream, _info, n, paths, flags, _ids):  # the dispatch queue's thread
            for i in range(n):
                tally.add(os.fsdecode(paths[i]), flags[i])

        self._cb = self._cb_type(on_events)
        strs = [cf.CFStringCreateWithCString(None, os.fsencode(r), UTF8) for r in roots]
        arr = (ctypes.c_void_p * len(strs))(*strs)
        paths = cf.CFArrayCreate(None, arr, len(strs), ctypes.addressof(callbacks))
        cs.FSEventStreamCreate.restype = ctypes.c_void_p
        cs.FSEventStreamCreate.argtypes = [
            ctypes.c_void_p,
            self._cb_type,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint64,
            ctypes.c_double,
            ctypes.c_uint32,
        ]
        self._s = cs.FSEventStreamCreate(
            None, self._cb, None, paths, SINCE_NOW, latency, CREATE_FILE_EVENTS | CREATE_NO_DEFER
        )
        for s in strs:
            cf.CFRelease(s)
        cf.CFRelease(paths)
        if not self._s:
            raise OSError("FSEventStreamCreate failed")
        libc.dispatch_queue_create.restype = ctypes.c_void_p
        libc.dispatch_queue_create.argtypes = [ctypes.c_char_p, ctypes.c_void_p]
        self._q = libc.dispatch_queue_create(b"goh.fs_events", None)
        for name in ("FSEventStreamSetDispatchQueue",):
            getattr(cs, name).argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        for name in (
            "FSEventStreamStart",
            "FSEventStreamFlushSync",
            "FSEventStreamStop",
            "FSEventStreamInvalidate",
            "FSEventStreamRelease",
        ):
            getattr(cs, name).argtypes = [ctypes.c_void_p]
        cs.FSEventStreamStart.restype = ctypes.c_bool
        cs.FSEventStreamSetDispatchQueue(self._s, self._q)

    def __enter__(self) -> Self:
        if not self._cs.FSEventStreamStart(self._s):
            raise OSError("FSEventStreamStart failed")
        return self

    def __exit__(self, *exc) -> None:
        self._cs.FSEventStreamFlushSync(self._s)
        self._cs.FSEventStreamStop(self._s)
        self._cs.FSEventStreamInvalidate(self._s)
        self._cs.FSEventStreamRelease(self._s)


def measure(roots: list[str], cmd: list[str], top: int = 10) -> tuple[int, dict]:
    roots = [os.path.realpath(os.path.expanduser(r)) for r in roots]
    for r in roots:
        os.makedirs(r, exist_ok=True)
    tally = Tally(roots)
    t0 = time.monotonic()
    with Stream(roots, tally):
        rc = subprocess.call(cmd, stdin=None)  # the measured command is the caller's: its stdin
        time.sleep(0.5)  # the stream's latency, so the last records are flushed in
    rep = tally.report(time.monotonic() - t0, top)
    rep["rc"] = rc
    rep["roots"] = roots
    return rc, rep
