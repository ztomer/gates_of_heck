#!/usr/bin/env python3
"""prune_kept.py <dir> <prefix> [keep] [idle-s] [--max-bytes N] -- bound what runs keep on failure.

A red run keeps its logs on purpose, and nothing ever removed them: 619 `goh-local-ci.*` and 41
`goh-session-bench.*` in one temp dir (ZoneWM, 2026-10-06). This keeps the newest `keep` (20)
entries named `<prefix>*` and everything modified within `idle` seconds (3600) -- a live run writes
into its own directory, so a concurrent run is never removed -- and deletes the rest. Exit 0
always: pruning is housekeeping, never a verdict.

`--max-bytes` bounds the kept entries by SIZE as well as count: newest first, the first entry that
would take the kept total past N goes, and so does everything older. A count alone held eleven kept
`goh-pytest.*` roots of ~5 GiB each, 55.3 GiB of TMPDIR (2026-11-12, GOH.PYTESTTMP). An entry is
live, and never touched, while anything in its top three levels moved within `idle` seconds or the
pid its owner wrote to `.goh-owner` is alive (a long suite only writes deep in its own tree).

`--backstop` is the other half: a trap or an atexit does not run on SIGKILL, so any of our own
`goh-*` entries untouched for 12 hours is a killed run's and goes. local_ci.sh runs it on every
push and the suite's controller at every session start (tests/test_temp_claimable.py), one spelling.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    try:
        __import__("_from_head").reexec(__file__)
    except ModuleNotFoundError:  # a copy outside any checkout: nothing to re-run from
        pass
    del _sys.path[0]

import os
import shutil
import sys
import time

KEEP = 20
IDLE_S = 3600
BACKSTOP_IDLE_S = 12 * 3600
OWNER = ".goh-owner"  # the pid of the run that owns an entry, while it runs (gates/_pytest_root.sh)
ACTIVITY_DEPTH = 3


def _owner_alive(path: str) -> bool:
    try:
        with open(os.path.join(path, OWNER), encoding="ascii") as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True  # someone else's process, but alive
    except OSError:
        return False
    return True


def _activity(path: str, mtime: float) -> float:
    """The newest mtime of `path` and the directories in its top ACTIVITY_DEPTH levels: a pytest
    run creates `pytest-of-<user>/pytest-N/popen-gwK/<test>` as it goes, so its root's own mtime
    can be hours old while it is busy."""
    newest = mtime
    level = [path]
    for _ in range(ACTIVITY_DEPTH):
        nxt = []
        for d in level:
            try:
                with os.scandir(d) as it:
                    for e in it:
                        if e.is_dir(follow_symlinks=False):
                            try:
                                newest = max(newest, e.stat(follow_symlinks=False).st_mtime)
                            except OSError:
                                continue
                            nxt.append(e.path)
            except OSError:
                continue
        level = nxt
    return newest


def _tree_bytes(path: str) -> int:
    """Allocated bytes under `path` (st_blocks, as du counts), symlinks not followed."""
    total = 0
    for dirpath, dirnames, filenames in os.walk(path):
        for n in (*dirnames, *filenames):
            try:
                total += os.lstat(os.path.join(dirpath, n)).st_blocks * 512
            except OSError:
                continue
    return total


def _remove(path: str) -> bool:
    if os.path.isdir(path) and not os.path.islink(path):
        _make_writable(path)
        shutil.rmtree(path, ignore_errors=True)
        return not os.path.lexists(path)  # not removed: do not claim it was
    try:
        os.unlink(path)
    except OSError:
        return False
    return True


def _make_writable(path: str) -> None:
    """`u+rwx` on a tree, so `rmtree` can empty it: a consumer test's read-only fixture (a directory
    without `u+w`) otherwise left a kept `goh-pytest.*` root for ever, silently (2026-11-12)."""
    for dirpath, dirnames, _files in os.walk(path):
        for d in (dirpath, *(os.path.join(dirpath, n) for n in dirnames)):
            try:
                if not os.path.islink(d):
                    os.chmod(d, 0o700 | os.lstat(d).st_mode)
            except OSError:
                continue


def prune(
    directory: str,
    prefix: str,
    keep: int = KEEP,
    idle: float = IDLE_S,
    max_bytes: int | None = None,
) -> list[str]:
    """The paths removed: past the newest `keep` or `max_bytes` kept in all; never a live one."""
    try:
        names = [n for n in os.listdir(directory) if n.startswith(prefix)]
    except OSError:
        return []
    entries = []
    for n in names:
        path = os.path.join(directory, n)
        try:
            entries.append((os.lstat(path).st_mtime, path))
        except OSError:
            continue
    entries.sort(reverse=True)
    now = time.time()
    removed = []
    kept = 0
    kept_bytes = 0
    full = False  # the size budget is spent: everything older goes
    for mtime, path in entries:
        isdir = os.path.isdir(path) and not os.path.islink(path)
        if now - (_activity(path, mtime) if isdir else mtime) < idle or (
            isdir and _owner_alive(path)
        ):
            kept += 1  # live: a run in flight owns it; it counts, its growing size does not
            continue
        if not full and kept < keep:
            if max_bytes is None:
                kept += 1
                continue
            size = _tree_bytes(path) if isdir else os.lstat(path).st_blocks * 512
            if kept_bytes + size <= max_bytes:
                kept += 1
                kept_bytes += size
                continue
            full = True
        if _remove(path):
            removed.append(path)
    return removed


def backstop(directory: str) -> list[str]:
    """Every `goh-*` entry no run has touched for BACKSTOP_IDLE_S: what a killed run left."""
    return prune(directory, "goh-", keep=0, idle=BACKSTOP_IDLE_S)


if __name__ == "__main__":
    if sys.argv[2:3] == ["--backstop"]:
        backstop(sys.argv[1])
        sys.exit(0)
    args = sys.argv[1:]
    cap = None
    if "--max-bytes" in args:
        i = args.index("--max-bytes")
        try:
            cap = int(args[i + 1])
        except (IndexError, ValueError):
            sys.exit(0)  # housekeeping never fails its caller
        del args[i : i + 2]
    if len(args) < 2:
        sys.exit(0)
    prune(args[0], args[1], *(int(a) for a in args[2:4]), max_bytes=cap)
