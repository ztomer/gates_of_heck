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
A dropped-events flag (the kernel's or this client's queue overflowed) marks the window
UNMEASURED (GOH.FSDROPVERDICT): its report carries `unmeasured` (why) and `dropped`, never a count,
because a window with drops undercounts and a count from it read as a verdict (395 of 400 planted
dirs at load 9, 148 of 400 in a land run, 2026-11-23). A window ends when the stream has DELIVERED
through the FSEventsGetCurrentEventId() taken at the command's end (`Stream.drain`), not after a
fixed sleep: FlushSync only flushes what fseventsd has received, and fseventsd lags under load. A
drain that does not get there in its bound is unmeasured too. macOS only; elsewhere the probe says
it is unavailable (exit 3). Under load fseventsd can also lose a run of records with no flag at
all (d255-d344 of 400 planted dirs, end mark delivered, 2026-11-23): a clean window is a lower
bound, so a caller that planted a known count checks it read at least that (tests/test_fs_churn.py).
Pinned by tests/test_fs_events.py and tests/test_fs_churn.py.
"""

from __future__ import annotations

import contextlib
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
# The window's end marker (`Stream.drain`): written under a root, never counted.
DRAIN_MARK = ".goh-fs-events-drain"
DRAIN_TIMEOUT = 30.0  # seconds a drain waits for fseventsd before the window is unmeasured
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
        self.drops: list[dict] = []  # the first drop records, path and flags: which queue, where
        self.by_creator: Counter[str] = Counter()
        self.dirs_by_creator: Counter[str] = Counter()
        self.per_test: Counter[str] = Counter()
        # A test's events inside a HEAD export it made (`<its HOME>/.cache/goh/head/<sha>`,
        # gates/_from_head.sh): a child spawned without GOH_LIVE copies the whole goh tree.
        self.head_export: Counter[str] = Counter()
        # Why the window is no count (GOH.FSDROPVERDICT): set by a drain that did not complete.
        self.undelivered = ""
        self.target_id = 0  # FSEventsGetCurrentEventId() at the command's end (`Stream.drain`)
        self.delivered_id = 0  # the highest event id the stream delivered
        self.marks: set[str] = set()  # the window marks the stream delivered
        self.started = True  # a Stream clears it until its start mark is delivered
        self.lock = threading.Lock()

    def add(self, path: str, flags: int, event_id: int = 0) -> None:
        with self.lock:
            self.delivered_id = max(self.delivered_id, event_id)
            if os.path.basename(path).startswith(DRAIN_MARK):
                self.marks.add(path)
                if f"{DRAIN_MARK}.start." in path:
                    self.started = True
                return
            if not self.started:  # made before the window: fseventsd's lag, not the command's
                return
            if flags & (USER_DROPPED | KERNEL_DROPPED | MUST_SCAN_SUBDIRS):
                self.dropped += 1
                if len(self.drops) < 5:
                    self.drops.append({"path": path, "flags": hex(flags)})
                return
            self.events += 1
            who = creator(path, self.roots)
            self.by_creator[who] += 1
            if "/pytest-N/test_" in who:
                self.per_test[who.rsplit("/", 1)[1]] += 1
                if "/.cache/goh/head/" in path:
                    self.head_export[who.rsplit("/", 1)[1]] += 1
            if flags & ITEM_CREATED and flags & ITEM_IS_DIR:
                self.dirs_created += 1
                self.dirs_by_creator[who] += 1
                self.fresh_dir_names.add(path)

    def unmeasured(self) -> str:
        """Why this window is no count, or "" when it is one."""
        if self.dropped:
            return (
                f"the instrument dropped events ({self.dropped} drop records, first"
                f" {self.drops[0]['path']} {self.drops[0]['flags']}): it undercounts"
            )
        return self.undelivered

    def report(self, seconds: float, top: int = 10) -> dict:
        why = self.unmeasured()
        if why:  # never a count: a reading the instrument marks incomplete is no verdict
            return {
                "seconds": round(seconds, 2),
                "unmeasured": why,
                "dropped": self.dropped,
                "drops": list(self.drops),
                "through_event_id": self.target_id,
                "delivered_through": self.delivered_id,
            }

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
            "head_export_events": sum(self.head_export.values()),
            "head_export_per_test": dict(self.head_export.most_common(top)),
            "through_event_id": self.target_id,
            "delivered_through": self.delivered_id,
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

        def on_events(_stream, _info, n, paths, flags, ids):  # the dispatch queue's thread
            for i in range(n):
                tally.add(os.fsdecode(paths[i]), flags[i], ids[i])
            self._delivered.set()

        self._tally, self._roots = tally, roots
        self._delivered = threading.Event()
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
        cs.FSEventsGetCurrentEventId.restype = ctypes.c_uint64
        cs.FSEventsGetCurrentEventId.argtypes = []
        cs.FSEventStreamSetDispatchQueue(self._s, self._q)

    def __enter__(self) -> Self:
        """Start the window: the stream runs, and counts from the delivery of a start mark on.
        fseventsd lags the kernel, so a stream started SINCE_NOW is still handed events made
        before it (a previous run's removal under the same root read as 400 events, 2026-11-23);
        the mark is the kernel-ordered line between them and the window's own."""
        self._marks: list[str] = []
        with self._tally.lock:
            self._tally.started = False
        if not self._cs.FSEventStreamStart(self._s):
            raise OSError("FSEventStreamStart failed")
        self._wait_for_mark("start", DRAIN_TIMEOUT)
        return self

    def drain(self, timeout: float = DRAIN_TIMEOUT) -> bool:
        """End the window: wait (at most `timeout` s) until the stream has delivered through the
        system's event id NOW (FSEventsGetCurrentEventId, recorded as `through_event_id`). That
        id is fseventsd's, which lags the kernel under load, and this stream sees only its roots'
        events, so the proof is a mark file written under a root after the command: fseventsd
        delivers in the kernel's order, so the mark's delivery proves every earlier event under
        the roots was delivered (an id past the target is not: 248 of 400 dirs came with one,
        2026-11-23). False, and the tally unmeasured with why, when it does not."""
        self._tally.target_id = int(self._cs.FSEventsGetCurrentEventId())
        return self._wait_for_mark("end", timeout)

    def _wait_for_mark(self, kind: str, timeout: float) -> bool:
        tally = self._tally
        mark = ""
        for root in self._roots:
            path = os.path.join(root, f"{DRAIN_MARK}.{kind}.{os.getpid()}.{id(self)}")
            try:
                with open(path, "w"):
                    pass
            except OSError:
                continue
            mark = path
            self._marks.append(mark)  # removed once the stream stops: its removal is never seen
            break
        deadline = time.monotonic() + timeout
        while mark:
            self._cs.FSEventStreamFlushSync(self._s)
            with tally.lock:
                if mark in tally.marks:
                    return True
                if tally.dropped:  # unmeasured already: a dropped queue may never send the mark
                    return False
            left = deadline - time.monotonic()
            if left <= 0:
                break
            self._delivered.clear()
            self._delivered.wait(min(left, 0.05))
        how = "" if mark else f" (no root took the {kind} mark)"
        with tally.lock:
            tally.started = True  # count what comes, but the window is no count
            tally.undelivered = tally.undelivered or (
                f"the stream had not delivered the window's {kind} mark (through event"
                f" {tally.delivered_id}, the command ended at {tally.target_id}) {timeout:g} s"
                f" after it was written{how}"
            )
        return False

    def __exit__(self, *exc) -> None:
        self._cs.FSEventStreamFlushSync(self._s)
        self._cs.FSEventStreamStop(self._s)
        self._cs.FSEventStreamInvalidate(self._s)
        self._cs.FSEventStreamRelease(self._s)
        for mark in getattr(self, "_marks", []):
            with contextlib.suppress(OSError):
                os.unlink(mark)


def measure(roots: list[str], cmd: list[str], top: int = 10) -> tuple[int, dict]:
    roots = [os.path.realpath(os.path.expanduser(r)) for r in roots]
    for r in roots:
        os.makedirs(r, exist_ok=True)
    tally = Tally(roots)
    t0 = time.monotonic()
    with Stream(roots, tally) as stream:
        rc = subprocess.call(cmd, stdin=None)  # the measured command is the caller's: its stdin
        stream.drain()  # delivered through the command's end, or the window is unmeasured
    rep = tally.report(time.monotonic() - t0, top)
    rep["rc"] = rc
    rep["roots"] = roots
    return rc, rep
