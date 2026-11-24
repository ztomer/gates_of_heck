"""lib/goh_db.py -- the seam for goh.db, goh's durable state (GOH.DBSEAM).

    goh_db.py path | migrate | version | dump TABLE [--json] | check

The one door to goh.db: `path()`, `connect()`, `ro()`, `migrate(con)` and `transaction()` are
lib/db_core.py's, bound to this file. Its migrations are lib/goh_db_schema/NNNN_name.sql, one
file per migration (numbers are assigned per lane, applied by set difference), each table bounded
by a `-- bound:` header (lib/db_bounds.py): past it, the oldest rows move to goh-archive-YYYY.db.

    file      GOH_DB (absolute), else $XDG_STATE_HOME/goh/goh.db, else ~/.local/state/goh/goh.db
    waits     busy_timeout 30 s (GOH_DB_BUSY_MS); synchronous=FULL: it holds the land queue
    missing   a read is OSError; a corrupt file fails loudly and is never recreated
    budget    256 MB for the whole file (owner 2026-11-23: "need also size limit")

VIEWS ARE THE INTERFACE to other readers (handover plan (d)): goh publishes versioned views
(`lands_v1`, ...) in a migration and changes one only by adding `_v2` beside it.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # outside any checkout: nothing to re-run from
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import contextlib
import os
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db_core  # the lib directory, on the path from the line above
import lock_order

SEAM = db_core.Seam(
    name="goh", env="GOH_DB", xdg="XDG_STATE_HOME", fallback=".local/state", file="goh.db",
    schema=Path(__file__).resolve().with_name("goh_db_schema"), busy_env="GOH_DB_BUSY_MS",
    busy_ms=30_000, synchronous="FULL", budget=256 << 20, over="archive",
)  # fmt: skip


def path() -> Path:
    return db_core.path(SEAM)


def connect(
    at: Path | None = None, named: bool = False
) -> contextlib.AbstractContextManager[sqlite3.Connection]:
    return db_core.connect(SEAM, at, named)


def ro(
    at: Path | None = None, named: bool = False
) -> contextlib.AbstractContextManager[sqlite3.Connection]:
    return db_core.ro(SEAM, at, named)


def migrate(con: sqlite3.Connection) -> set[int]:
    return db_core.migrate(con, SEAM)


def _order() -> None:
    lock_order.check("goh_db")  # innermost: nothing is waited on while a write holds it


@contextlib.contextmanager
def transaction(at: Path | None = None, named: bool = False) -> Iterator[sqlite3.Connection]:
    """One write to goh.db (AT: a file a caller names, e.g. lib/durations.py's)."""
    with db_core.transaction(SEAM, _order, at, named) as con:
        yield con


if __name__ == "__main__":
    sys.exit(db_core.main(SEAM, sys.argv[1:]))
