"""lib/db_settings.py -- the pragmas a writable opening sets, and which file is a test's own
(GOH.FSEVQUIET; split from lib/db_core.py, whose callers use both through it).

throwaway(p, env)   P is under the suite run's root (tests/_db_on_disk.py): a memory journal, no
                    sync, small pages, so a test's database writes close to nothing (house rule 18)
apply(con, ...)     the settings of one writable connection
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Mapping
from pathlib import Path

THROWAWAY_ENV = "GATES_OF_HECK_DB_THROWAWAY"  # the suite run's root (tests/_suite_plugins.py)


def throwaway(p: Path, env: Mapping[str, str] | None = None) -> bool:
    """P is a test's own file, under the suite run's root: it journals in memory, syncs nothing and
    has small pages. Never a shared file, nor the suite home's."""
    root = (os.environ if env is None else env).get(THROWAWAY_ENV)
    return bool(root) and Path(os.path.realpath(p)).is_relative_to(os.path.realpath(root))


def apply(con: sqlite3.Connection, p: Path, busy_ms: int, synchronous: str, fresh: bool) -> None:
    con.execute(f"PRAGMA busy_timeout={busy_ms}")
    mine = throwaway(p)
    if fresh:  # before the first table: a file's vacuum mode and page size are fixed at creation
        if mine:
            con.execute("PRAGMA page_size=512")
        con.execute("PRAGMA auto_vacuum=INCREMENTAL")
    con.execute(f"PRAGMA journal_mode={'MEMORY' if mine else 'WAL'}")
    con.execute(f"PRAGMA synchronous={'OFF' if mine else synchronous}")
