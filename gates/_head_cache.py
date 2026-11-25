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

Prunes the HEAD export cache whenever `_from_head.sh` makes a new export: by LAST USE (each use
truncates the export's `.goh-used`), unused 7 days, or beyond the 20 most recently used and idle an
hour; an export used within the hour is never removed.
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


def use(path: str) -> None:
    """A use of the export at PATH by a process that outlives its start (lib/pin_sweep.py, hours
    asleep): its stamp now. A checkout has none."""
    try:
        os.utime(os.path.join(path, STAMPS[0]))
    except OSError:
        return


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


def _pid_owner():
    """lib/pid_owner.py, from gates/ beside it."""
    import sys

    lib = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import pid_owner

    return pid_owner


def _users(path: str, marker: str) -> list[str]:
    """The marker's lines, one user each: `<pid> <start time>` (a bare pid from an older goh)."""
    try:
        with open(os.path.join(path, marker), encoding="ascii") as handle:
            lines = handle.read().splitlines()
    except (OSError, ValueError):
        return []
    return [ln.strip() for ln in lines if ln.split() and ln.split()[0].isdigit()]


def running(path: str, marker: str | None) -> bool:
    """True when `path/<marker>` names a live process: the entry is in use, whatever its stamp says.
    The marker holds one pid per user: the crates of one repo share one coverage build, several at
    once (GOH.COVSHAREDBUILD), and any one alive keeps it. A reused pid keeps an entry one run too
    long; it can never lose one that is in use. Each user's start time is beside its pid
    (GOH.STRAYSIGNAL2), so a reused pid no longer keeps one at all."""
    if not marker:
        return False
    alive_line = _pid_owner().alive_line
    return any(alive_line(user) for user in _users(path, marker))


def _lock_order():
    """lib/lock_order.py, the one acquire path (GOH.LOCKORDER), from gates/ beside it."""
    import sys

    lib = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import lock_order

    return lock_order


def mark(path: str, marker: str, pid: int) -> None:
    """Add `pid` to `path/<marker>`, dropping pids no longer alive, under a lock: two crates of one
    repo starting together must not overwrite each other's pid. The lock is also the eviction's
    (`_evict`): a pruner renames the entry away while holding it, so a mark that waited on it checks
    it still holds the lock of the LIVE directory, and starts over in a fresh one if not -- a pid
    written into the tombstone would leave the build unmarked, evictable while it runs."""
    lock_order = _lock_order()
    lock_path = os.path.join(path, f"{marker}.lock")
    while True:
        os.makedirs(path, exist_ok=True)
        with open(lock_path, "a", encoding="ascii") as lock:
            lock_order.flock(lock, "head_cache")
            try:
                if not os.path.samestat(os.fstat(lock.fileno()), os.stat(lock_path)):
                    continue  # evicted while we waited: the path is a new directory now
            except FileNotFoundError:
                continue
            po = _pid_owner()
            others = [u for u in _users(path, marker) if u.split()[0] != str(pid)]
            live = [u for u in others if po.alive_line(u)]
            tmp = os.path.join(path, f"{marker}.{os.getpid()}")
            with open(tmp, "w", encoding="ascii") as handle:
                handle.write("".join(f"{u}\n" for u in [*live, po.stamp_line(pid) or str(pid)]))
            os.replace(tmp, os.path.join(path, marker))
            return


def _idle(path: str, used: float, now: float, grace: float, marker: str | None) -> bool:
    """Not in use. A cache whose writer records its users (`marker`) knows: an entry whose marker
    names no live pid is idle however recently it was stamped (EST.FSEVENTSCHURN: the grace spared
    every build a sweep finished in the last 10 minutes, 8.5 GiB held against 8). Without a marker
    -- another cache, or an entry from before the marker -- the recency guess stands."""
    if marker and os.path.exists(os.path.join(path, marker)):
        return not running(path, marker)
    return now - used > grace and not running(path, marker)


def _evict(path: str, marker: str | None, remove) -> bool:
    """Remove an idle entry; False when a run is using it after all. With a marker the decision is
    made under the marker lock that `mark` takes, not taken (LOCK_NB) means a run is recording
    itself right now: the entry is left alone. The entry is renamed away (`.build.*`, the leftover
    name a crashed pruner's tombstone is reaped under) before the lock goes, so no run can start
    building in a directory being deleted."""
    if not marker:
        remove(path)
        return True
    lock_order = _lock_order()
    head, name = os.path.split(path)
    tomb = os.path.join(head, f".build.evict-{name}-{os.getpid()}")
    try:
        with open(os.path.join(path, f"{marker}.lock"), "a", encoding="ascii") as lock:
            lock_order.flock(lock, "head_cache", wait=False)  # held: a run is marking it now
            if running(path, marker):
                return False
            os.rename(path, tomb)
    except OSError:
        return False
    remove(tomb)
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
    size=None,
) -> list[str]:
    """Remove what the policy above says; return what went.

    `stamps` and `remove` let another cache share the POLICY: lib/head_export.py's sibling exports
    are read-only trees, which `rmtree(ignore_errors=True)` would leave in place while reporting
    them gone, so it passes a remover that makes the tree writable first. `size` (an entry's
    bytes; `tree_bytes` by default) lets gates/_cov_cache.py walk each entry once."""
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
        if not _idle(path, used, now, grace, marker):
            continue
        if (now - used > TTL_DAYS * 86400 or rank > keep) and _evict(path, marker, remove):
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
            size or tree_bytes,
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
    size=tree_bytes,
) -> list[str]:
    """Least recently used first, until the cache fits `max_bytes`; never one in use (a live
    `marker` pid, or its marker lock held) and, without a marker, never one stamped within
    `grace`."""
    sizes = {path: size(path) for _used, path in exports}
    total = sum(sizes.values()) + size(os.path.join(cache, current))
    gone = []
    for used, path in reversed(exports):  # least recently used first
        if total <= max_bytes:
            break
        if not _idle(path, used, now, grace, marker) or not _evict(path, marker, remove):
            continue  # in use, or used a moment ago: the next one up may not be
        gone.append(path)
        total -= sizes[path]
    return gone
