#!/usr/bin/env python3
"""prune_kept.py <dir> <prefix> [keep] [idle-seconds] -- bound what a tool keeps on failure.

A red run keeps its logs on purpose, and nothing ever removed them: 619 `goh-local-ci.*` and 41
`goh-session-bench.*` in one temp dir (ZoneWM, 2026-10-06). This keeps the newest `keep` (20)
entries named `<prefix>*` and everything modified within `idle` seconds (3600) -- a live run writes
into its own directory, so a concurrent run is never removed -- and deletes the rest. Exit 0
always: pruning is housekeeping, never a verdict.
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


def prune(directory: str, prefix: str, keep: int = KEEP, idle: float = IDLE_S) -> list[str]:
    """The paths removed."""
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
    for mtime, path in entries[keep:]:
        if now - mtime < idle:
            continue
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, ignore_errors=True)
        else:
            try:
                os.unlink(path)
            except OSError:
                continue
        removed.append(path)
    return removed


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(0)
    args = sys.argv[1:]
    prune(args[0], args[1], *(int(a) for a in args[2:4]))
