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
    lock_order.check("goh_cache_db")  # innermost: nothing is waited on while a write holds it


_REFUSED: set[Path] = set()  # files a write was refused on in this process (read-only to it)


class Refused(db_core.NewerFile):
    """A write to a file this process was already refused on: a silent skipped write."""


@contextlib.contextmanager
def transaction(at: Path | None = None, named: bool = False) -> Iterator[sqlite3.Connection]:
    """One write to goh-cache.db (AT: a file a caller names, e.g. lib/cpu_window.py's; NAMED: the
    suite's runner named it, the one real-home file a suite may open, db_core.connect)."""
    p = Path(at or path())
    if p in _REFUSED:  # said once: a hot writer (the identity memo) must not say it per write
        raise Refused(f"{p}: refused earlier in this process")
    try:
        with db_core.transaction(SEAM, _order, at, named) as con:
            yield con
    except db_core.NewerFile as e:  # db_core said why at the open
        _REFUSED.add(p)
        raise Refused(str(e)) from e


@contextlib.contextmanager
def missable(what: str = "goh-cache.db") -> Iterator[None]:
    """A cache error inside the block is a miss (or a skipped write): one stderr line, never a
    raise. The caller's result keeps the value it had before the block. A table the file does not
    have yet is a silent miss: a lane's copy reads the shared file before main has migrated it
    (lib/db_core.py, GOH.LANEMIGRATE), and a hot path (the identity memo) would say so per ask."""
    try:
        yield
    except Refused:
        pass
    except (sqlite3.Error, OSError) as e:
        if not str(e).startswith("no such table"):
            db_core.warn(f"{what}: {e} (a miss)")


if __name__ == "__main__":
    sys.exit(db_core.main(SEAM, sys.argv[1:]))
