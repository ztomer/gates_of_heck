"""lib/state_append.py -- the one append path for goh's bounded single-file ledgers
(GOH.LEDGERBOUND).

    append(path, data)  DATA appended to PATH in one write, holding PATH's sidecar lock SHARED
                        (`.<name>.lock` beside it, lock `state_file` of lib/lock_order.tsv).
    trimming(path)      the same lock taken EXCLUSIVE as a try-lock (never waits); yields whether it
                        was got. lib/state_bounds.py trims under it, writing the kept tail to a temp
                        file and renaming it over PATH: a writer opens PATH only once it holds the
                        lock, so no row is appended to the inode the rename replaces.

Pinned by tests/test_state_files_bound.py.

GOH.LEDGERBOUND: the one append path for a bounded state ledger: `append(path, data)` writes
holding the sidecar `.<name>.lock` (lock `state_file`) shared; `trimming(path)` is its exclusive
try-lock, which `state_bounds.trim` renames the kept tail over the file under. Used by
`timing_ledger.py`, `selection_miss.py`, `hooks/claude/lane_room.py`. Invoked by
`lib/state_bounds.py`.
"""

from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import Iterator
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lock_order  # the one acquire path


def lock_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.lock")


def append(path: Path, data: bytes) -> None:
    """DATA appended to PATH under its lock, shared with every other writer; raises OSError."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path(path), "a") as lk:
        lock_order.flock(lk, "state_file", shared=True)  # released when the file closes
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)


@contextlib.contextmanager
def trimming(path: Path) -> Iterator[bool]:
    """PATH's lock held alone, when no writer holds it (True), else nothing held (False)."""
    with open(lock_path(path), "a") as lk:
        try:
            lock_order.flock(lk, "state_file", wait=False)
        except OSError:
            yield False
            return
        yield True
