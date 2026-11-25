"""The repository's land lock, from Python: one queue that tools/land.sh and goh ship both take
(GOH.CLASS.CHECKOUT, the slice after GOH.LANDSERIAL).

land.sh holds `goh-land.lock` in the common git dir from its rebase through its fast-forward. A ship
moved the same main with nothing held: its push, its release kit's commit (goh's own kit lands a
release commit through land.sh) and its install all read and wrote the main checkout while a lane's
land could fast-forward it underneath. So the ship worker holds the same lock for its pipeline, and
a land started meanwhile waits, naming the ship.

One lock, one file, one idiom: a kernel flock (released on any exit, never a stale pid file), the
holder's line written into the file for the next waiter. A land the ship's OWN release step runs is
the ship's land, not a rival: the worker exports OWNER_ENV naming the lock and its pid, and land.sh
skips the wait only when that pid is alive and is the one the lock file names (the holder line), so
an inherited variable from a dead ship takes the lock as usual.
Pinned by tests/test_ship_land_lock.py.

The repository's land lock from Python (GOH.CLASS.CHECKOUT): `goh-land.lock` in the common git dir,
the kernel flock tools/land.sh takes; the ship worker holds it for its pipeline, so a land waits
behind a ship (naming it) and a ship behind a land; GOH_LAND_LOCK_OWNER lets a land the ship's own
release kit runs proceed under the ship's hold, judged by `lib/land_lock.sh` `goh_land_lock_ours`
(the holder line's pid, still the process that wrote it). Pinned by
`test_entry_points_leave_no_strays.py`.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import os
from collections.abc import Callable, Iterator
from pathlib import Path

import gitrun
import lock_order

NAME = "goh-land.lock"
OWNER_ENV = "GOH_LAND_LOCK_OWNER"  # "<lock path> <pid>": the process that holds it for its children


def path(repo: Path) -> Path | None:
    """REPO's land lock (in the common git dir, so every worktree of it shares one), or None."""
    done = gitrun.run_reader(("rev-parse", "--path-format=absolute", "--git-common-dir"), repo=repo,
                             timeout=60)  # fmt: skip
    common = done.stdout.strip() if done.returncode == 0 else ""
    return Path(common) / NAME if common else None


@contextlib.contextmanager
def held(lock: Path | None, who: str, say: Callable[[str], None]) -> Iterator[None]:
    """Hold LOCK for the block (no lock, outside a git repository: nothing to hold). When another
    holds it, SAY once who, then block. The file names WHO while held; the child environment
    carries OWNER_ENV (see the module doc)."""
    if lock is None:
        yield
        return
    lock_order.check("land", lock)  # the order before any wait (GOH.LOCKORDER): refused, not queued
    with lock.open("a+", encoding="utf-8") as f:
        try:
            lock_order.flock(f, "land", wait=False, where=lock)
        except OSError:
            f.seek(0)
            say(f"waiting for the land lock: held by {f.readline().strip() or 'another land'} "
                f"({lock})")  # fmt: skip
            lock_order.flock(f, "land", where=lock)
        f.truncate(0)
        since = dt.datetime.now().strftime("%H:%M:%S")
        f.write(f"{who} (pid {os.getpid()}, since {since})\n")
        f.flush()
        before = os.environ.get(OWNER_ENV)
        os.environ[OWNER_ENV] = f"{lock} {os.getpid()}"
        try:
            with lock_order.holding("land", lock):
                yield
        finally:
            if before is None:
                os.environ.pop(OWNER_ENV, None)
            else:
                os.environ[OWNER_ENV] = before
