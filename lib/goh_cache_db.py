"""lib/goh_cache_db.py -- the seam for goh-cache.db, goh's disposable caches (GOH.DBSEAM).

    goh_cache_db.py path | migrate | version | dump TABLE [--json] | check

lib/db_core.py's API bound to this file; migrations in lib/goh_cache_schema/NNNN_name.sql, each
table bounded by a `-- bound:` header (lib/db_bounds.py): past it, the oldest rows are evicted.

    file      GOH_CACHE_DB (absolute), else $XDG_CACHE_HOME/goh/goh-cache.db, else
              ~/.cache/goh/goh-cache.db
    waits     busy_timeout 2 s (GOH_CACHE_DB_BUSY_MS); synchronous=NORMAL
    missing   every lookup misses and the file is recreated: slower, still correct
    errors    `missable()`: a cache error is a miss or a skipped write, with one stderr line
    budget    2 GB for the whole file
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
    name="goh-cache", env="GOH_CACHE_DB", xdg="XDG_CACHE_HOME", fallback=".cache",
    file="goh-cache.db", schema=Path(__file__).resolve().with_name("goh_cache_schema"),
    busy_env="GOH_CACHE_DB_BUSY_MS", busy_ms=2_000, synchronous="NORMAL", budget=2 << 30,
    over="evict",
)  # fmt: skip


def path() -> Path:
    return db_core.path(SEAM)


def connect(at: Path | None = None) -> contextlib.AbstractContextManager[sqlite3.Connection]:
    return db_core.connect(SEAM, at)


def ro(at: Path | None = None) -> contextlib.AbstractContextManager[sqlite3.Connection]:
    return db_core.ro(SEAM, at)


def migrate(con: sqlite3.Connection) -> set[int]:
    return db_core.migrate(con, SEAM)


def _order() -> None:
    lock_order.check("goh_cache_db")  # innermost: nothing is waited on while a write holds it


@contextlib.contextmanager
def transaction(at: Path | None = None) -> Iterator[sqlite3.Connection]:
    """One write to goh-cache.db (AT: a file a caller names, e.g. lib/cpu_window.py's)."""
    with db_core.transaction(SEAM, _order, at) as con:
        yield con


@contextlib.contextmanager
def missable(what: str = "goh-cache.db") -> Iterator[None]:
    """A cache error inside the block is a miss (or a skipped write): one stderr line, never a
    raise. The caller's result keeps the value it had before the block."""
    try:
        yield
    except (sqlite3.Error, OSError) as e:
        db_core.warn(f"{what}: {e} (a miss)")


if __name__ == "__main__":
    sys.exit(db_core.main(SEAM, sys.argv[1:]))
