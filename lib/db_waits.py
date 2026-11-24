"""lib/db_waits.py -- the lock-wait instrument of goh's SQLite seams (GOH.DBSEAM; lib/db_core.py).

Every write's BEGIN IMMEDIATE is timed by lib/db_core.py `transaction`, and its wait appended here
as one JSON row: to $GOH_TIMINGS (the step ledger's file) and to $GATES_OF_HECK_DB_WAITS, the
name a measurement across a whole suite sets (GOH.VERDICTDB step 5). A write that gave up at its
busy timeout is a row too, `busy`. Split from lib/db_core.py for its line cap. Pinned by
tests/test_db_core.py.
"""

from __future__ import annotations

import contextlib
import json
import os
import time

# The same rows for a measurement across a suite (GOH.VERDICTDB step 5): the suite drops GOH_TIMINGS
# from every worker and gate child (tests/conftest.py), and hermetic_env every GOH-prefixed key;
# this name survives both, so the waits of workers sharing one suite home's file are seen.
WAITS_ENV = "GATES_OF_HECK_DB_WAITS"


def row(db: str, wait_s: float, busy: bool = False) -> None:
    """The lock-wait instrument: one $GOH_TIMINGS (and $GATES_OF_HECK_DB_WAITS) line per BEGIN
    IMMEDIATE, `busy` when it gave up at its busy timeout (a failed write is dropped; the
    instrument never changes an outcome)."""
    outs = [o for o in (os.environ.get("GOH_TIMINGS"), os.environ.get(WAITS_ENV)) if o]
    if not outs:
        return
    line = {"label": f"db wait {db}", "ms": round(wait_s * 1000, 1), "rc": int(busy),
           "tier": "db", "parent": os.environ.get("GOH_TIMINGS_PARENT", "")[:300],
           "t": round(time.time(), 3), "db": db, "pid": os.getpid(),
           "wait_s": round(wait_s, 6), **({"busy": True} if busy else {})}  # fmt: skip
    for out in outs:
        with contextlib.suppress(OSError):
            fd = os.open(out, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
            try:
                os.write(fd, (json.dumps(line) + "\n").encode())
            finally:
                os.close(fd)
