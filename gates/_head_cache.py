"""Prune the HEAD export cache -- run by gates/_from_head.sh each time it makes a NEW export.

`~/.cache/goh/head/<sha>/` holds one export per commit a gate ran from. Every use truncates the
export's `.goh-used` (its last-use stamp; the export's own files are never rewritten), so an
export's age is the time since a gate last ran from it -- not since it was made, which let a
long-lived worktree's export go while that worktree still used it. An export goes when it is
unused for TTL_DAYS, or when it ranks beyond KEEP by last use AND is unused for IN_USE_S: an
export used within the hour may have a gate running from it right now, and is never removed.
Measured before the bound: 55 exports, 226 MB (2026-10-06). Pinned by
tests/test_head_export_prune.py.

A LIBRARY, not an entry point: it is part of the C4 mechanism itself (the export it prunes is
what every other entry point re-runs from), so `_from_head.sh` imports it and calls `prune`.
"""

from __future__ import annotations

import os
import shutil
import sys
import time

KEEP = 20  # the current export and the 19 most recently used others
TTL_DAYS = 7
IN_USE_S = 3600
BUILD_LEFTOVER_S = 86400  # a `.build.*` temp dir a crashed export left behind


def last_use(path: str) -> float:
    """The `.goh-used` stamp; an export from before the stamp falls back to `.goh-head`."""
    for name in (".goh-used", ".goh-head"):
        try:
            return os.stat(os.path.join(path, name)).st_mtime
        except OSError:
            continue
    return os.stat(path).st_mtime


def prune(cache: str, current: str, now: float | None = None) -> list[str]:
    """Remove what the policy above says; return what went."""
    now = time.time() if now is None else now
    exports, leftovers = [], []
    for entry in os.scandir(cache):
        if not entry.is_dir(follow_symlinks=False):
            continue
        if entry.name.startswith(".build."):
            leftovers.append(entry.path)
        elif not entry.name.startswith(".") and entry.name != current:
            exports.append((last_use(entry.path), entry.path))
    exports.sort(reverse=True)  # most recently used first
    gone = []
    for rank, (used, path) in enumerate(exports, start=2):  # the current export is rank 1
        idle = now - used
        if idle > TTL_DAYS * 86400 or (rank > KEEP and idle > IN_USE_S):
            shutil.rmtree(path, ignore_errors=True)
            gone.append(path)
    for path in leftovers:
        if now - os.stat(path).st_mtime > BUILD_LEFTOVER_S:
            shutil.rmtree(path, ignore_errors=True)
            gone.append(path)
    return gone
