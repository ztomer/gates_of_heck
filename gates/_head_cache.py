"""Prune the HEAD export cache -- run by gates/_from_head.sh each time it makes a NEW export.

`~/.cache/goh/head/<sha>/` holds one export per commit a gate ran from. Every use truncates the
export's `.goh-used` (its last-use stamp; the export's own files are never rewritten), so an
export's age is the time since a gate last ran from it -- not since it was made, which let a
long-lived worktree's export go while that worktree still used it. An export goes when it is
unused for TTL_DAYS, or when it ranks beyond KEEP by last use AND is unused for IN_USE_S: an
export used within the hour may have a gate running from it right now, and is never removed.
Measured before the bound: 55 exports, 226 MB (2026-10-06). Pinned by
tests/test_head_export_prune.py.

The binary store (`bin/by-stamp/`, gates/_goh_bin.sh) is pruned by the same policy with a smaller
`keep`: one directory per build, the same `.goh-used` stamp, and one used within the hour never goes.
So are build-goh.sh's per-root source exports (`~/.cache/goh/build/`, keep 20) and the rust coverage
build cache (`~/.cache/goh/llvm-cov/`, keep 200 and a byte budget).

A count bounds entries, not bytes. The coverage cache held 334 entries and 11 GiB, every one inside
its week, and build-goh's exports were never pruned at all: 675 entries, 8.7 GiB, one per HEAD
export goh had ever been built from (2026-10-17, the day the disk filled). `max_bytes` evicts the
least recently used past the budget, under the same rule: one used within the hour never goes.

The hour is a guess at "in use", and a guess is a bound only when nobody uses the machine
(GOH.CACHEBOUND, 2026-11-13): a coverage sweep builds ~40 projects inside one hour, every entry
"used within the hour", and the cache held 21 GiB against an 8 GiB budget. A cache whose writer
RECORDS its user passes `marker` (a file holding the pid of the process using the entry): an entry
whose pid is alive is never removed, and `grace` (seconds) shrinks the recency guess to cover only
the gap before the marker lands. Without a marker the hour stands.

A LIBRARY, not an entry point: it is part of the C4 mechanism itself (the export it prunes is
what every other entry point re-runs from), so `_from_head.sh` imports it and calls `prune`.
"""

from __future__ import annotations

import os
import shutil
import time

KEEP = 20  # the current export and the 19 most recently used others
TTL_DAYS = 7
IN_USE_S = 3600
BUILD_LEFTOVER_S = 86400  # a `.build.*` temp dir a crashed export left behind


STAMPS = (".goh-used", ".goh-head")  # last-use stamp first, then the marker it falls back to


def _rmtree(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)


def last_use(path: str, stamps: tuple[str, ...] = STAMPS) -> float:
    """The `.goh-used` stamp; an export from before the stamp falls back to `.goh-head`."""
    for name in stamps:
        try:
            return os.stat(os.path.join(path, name)).st_mtime
        except OSError:
            continue
    return os.stat(path).st_mtime


def running(path: str, marker: str | None) -> bool:
    """True when `path/<marker>` names a live process: the entry is in use, whatever its stamp says.
    A reused pid keeps an entry one run too long; it can never lose one that is in use."""
    if not marker:
        return False
    try:
        with open(os.path.join(path, marker), encoding="ascii") as handle:
            pid = int(handle.read().split()[0])
        os.kill(pid, 0)
    except PermissionError:
        return True  # alive, and not ours
    except (OSError, ValueError, IndexError):
        return False
    return True


def tree_bytes(path: str) -> int:
    """Bytes on disk under `path` (allocated blocks, links not followed)."""
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_blocks * 512
            except OSError:
                continue
    return total


def prune(
    cache: str,
    current: str,
    now: float | None = None,
    keep: int = KEEP,
    max_bytes: int | None = None,
    stamps: tuple[str, ...] = STAMPS,
    remove=_rmtree,
    grace: float = IN_USE_S,
    marker: str | None = None,
) -> list[str]:
    """Remove what the policy above says; return what went.

    `stamps` and `remove` let another cache share the POLICY: lib/head_export.py's sibling exports
    are read-only trees, which `rmtree(ignore_errors=True)` would leave in place while reporting
    them gone, so it passes a remover that makes the tree writable first."""
    now = time.time() if now is None else now
    exports, leftovers = [], []
    for entry in os.scandir(cache):
        if not entry.is_dir(follow_symlinks=False):
            continue
        if entry.name.startswith(".build."):
            leftovers.append(entry.path)
        elif not entry.name.startswith(".") and entry.name != current:
            exports.append((last_use(entry.path, stamps), entry.path))
    exports.sort(reverse=True)  # most recently used first
    gone = []
    for rank, (used, path) in enumerate(exports, start=2):  # the current export is rank 1
        idle = now - used
        if running(path, marker):
            continue
        if idle > TTL_DAYS * 86400 or (rank > keep and idle > grace):
            remove(path)
            gone.append(path)
    for path in leftovers:
        if now - os.stat(path).st_mtime > BUILD_LEFTOVER_S:
            remove(path)
            gone.append(path)
    if max_bytes is not None:
        gone += _over_budget(
            cache,
            current,
            [e for e in exports if e[1] not in gone],
            now,
            max_bytes,
            remove,
            grace,
            marker,
        )
    return gone


def _over_budget(
    cache: str,
    current: str,
    exports,
    now: float,
    max_bytes: int,
    remove=_rmtree,
    grace: float = IN_USE_S,
    marker: str | None = None,
) -> list[str]:
    """Least recently used first, until the cache fits `max_bytes`; never one in use (a live
    `marker` pid) and never one stamped within `grace`."""
    sizes = {path: tree_bytes(path) for _used, path in exports}
    total = sum(sizes.values()) + tree_bytes(os.path.join(cache, current))
    gone = []
    for used, path in reversed(exports):  # least recently used first
        if total <= max_bytes:
            break
        if now - used <= grace or running(path, marker):
            continue  # in use, or used a moment ago: the next one up may not be
        remove(path)
        gone.append(path)
        total -= sizes[path]
    return gone
