"""lib/db_settings.py -- the pragmas a writable opening sets, and which file is a test's own
(GOH.FSEVQUIET; split from lib/db_core.py, whose callers use both through it).

throwaway(p, env)   P is under the suite run's root (tests/_db_on_disk.py): a memory journal, no
                    sync, small pages, so a test's database writes close to nothing (house rule 18)
apply(con, ...)     the settings of one writable connection
seed(seam, p, root), keep(...)   a fresh throwaway as a clone of the run's first one of this schema
                    and code, and that first one kept: 370 KB a test were written to make the same
                    empty file (tests/_db_on_disk.py names the directory, and the tests of the
                    making itself, which are never seeded)
"""

from __future__ import annotations

import contextlib
import ctypes
import hashlib
import os
import sqlite3
import sys
from collections.abc import Mapping
from pathlib import Path

THROWAWAY_ENV = "GATES_OF_HECK_DB_THROWAWAY"  # the suite run's root (tests/_suite_plugins.py)

SEED_ENV = "GATES_OF_HECK_DB_SEEDS"  # where the suite run keeps one fresh file per schema


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


def _seed_file(schema: Path, p: Path, root: Path) -> Path | None:
    """The run's fresh file of SCHEMA as ROOT's code makes it; None for what is never seeded."""
    here = os.environ.get(SEED_ENV)
    if not here or sys.platform != "darwin" or not throwaway(p):
        return None
    key = hashlib.sha256(f"{root}\0".encode())
    for f in sorted(schema.glob("*.sql")):
        key.update(f.name.encode() + b"\0" + f.read_bytes() + b"\0")
    return Path(here) / f"{key.hexdigest()[:20]}.db"


def _clone(src: Path, dst: Path) -> bool:
    """DST as a clone of SRC (clonefile(2)); False when it is not: no SRC, a DST, no clones."""
    return ctypes.CDLL(None).clonefile(os.fsencode(src), os.fsencode(dst), 0) == 0


def seed(schema: Path, p: Path, root: Path) -> bool:
    """P, which is not there, is the run's fresh file now; False: its opener makes it."""
    kept = _seed_file(schema, p, root)
    return kept is not None and _clone(kept, p)


def keep(schema: Path, p: Path, root: Path) -> None:
    """P, fresh and migrated, is the run's; the first one kept stays."""
    kept = _seed_file(schema, p, root)
    if kept is None or kept.exists():
        return
    mine = kept.with_suffix(f".{os.getpid()}")
    if _clone(p, mine):
        with contextlib.suppress(OSError):
            os.link(mine, kept)
        mine.unlink()
