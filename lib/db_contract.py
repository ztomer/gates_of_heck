"""lib/db_contract.py -- `goh state contract N`: a schema file no open applies (GOH.OLDWRITERS).

A contract (`-- applied-by: goh state contract`, always `-- old-writers: refuse`) drops what older
copies of goh write, so it is a person's act, once: an open that applied it would be retried by
every open while it refuses (a row not yet moved), and would shut older copies out unasked.

    contract(seam, n)   apply number N; the copy's path. Refused (ContractRefused, nothing changed)
                        when N is not shipped, not by command, already applied, or a lower shipped
                        number is not applied: contracts go in order.

UNDER THE WRITE LOCK, the copy first: `goh-before-NNNN-<microseconds>.db` beside the file, by
SQLite's backup API from a second connection, so it holds every committed write and none can come
between it and the contract. It is checked and synced before the first statement; the newest KEEP
stay. The statements, the schema_version row, user_version and meta commit in one transaction. A
contract that refuses leaves the file as it was and no copy.
Pinned by tests/test_db_contract.py.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db_core  # lib/, on the path from the line above
import db_schema

if TYPE_CHECKING:
    from collections.abc import Callable

    from db_core import Seam

KEEP = 3


class ContractRefused(RuntimeError):
    """The contract was not applied; the file is as it was."""


def copies(seam: Seam) -> list[Path]:
    """The copies taken before contracts, newest last."""
    stem = f"{seam.name}-before-"
    got = db_core.path(seam).parent.glob(f"{stem}[0-9][0-9][0-9][0-9]-*.db")
    return sorted(got, key=lambda p: p.stem.removeprefix(stem).split("-")[1])


def _why_not(con: sqlite3.Connection, seam: Seam, n: int) -> str | None:
    known, have = db_schema.migrations(seam), set(db_schema.version(con)["applied"])
    if n not in known:
        return f"this goh ships no number {n}"
    if not db_schema.by_command(known[n].read_text(encoding="utf-8")):
        return f"{known[n].stem} is applied by an open, not by command"
    if n in have:
        return f"{known[n].stem} is applied already"
    if lower := sorted(k for k in known if k < n and k not in have):
        return f"{', '.join(known[k].stem for k in lower)} is not applied: contracts go in order"
    return None


def copy(seam: Seam, n: int) -> Path:
    """The committed file copied, checked and synced, while the caller holds its write lock."""
    src = db_core.path(seam)
    to = src.with_name(f"{seam.name}-before-{n:04d}-{time.time_ns() // 1000:018d}.db")
    for left in src.parent.glob(f"{seam.name}-before-*.tmp"):  # a killed contract's
        left.unlink()
    tmp = to.with_suffix(".tmp")
    live = sqlite3.connect(f"{src.as_uri()}?mode=ro", uri=True)
    out = sqlite3.connect(tmp)
    try:
        live.backup(out)
        out.execute("PRAGMA journal_mode=DELETE")  # one file: no -wal beside it
        if (said := out.execute("PRAGMA quick_check").fetchone()[0]) != "ok":
            raise ContractRefused(f"the copy {tmp} is not whole: {said}")
    finally:
        out.close()
        live.close()
    fd = os.open(tmp, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    tmp.rename(to)
    return to


def contract(seam: Seam, n: int, check: Callable[[], None] = lambda: None) -> Path:
    """Number N applied, the file copied first; the copy. ContractRefused, NotOfRecord or NewerFile
    when it is not applied."""
    p = db_core.path(seam)
    if db_core.shared(p) and not db_core.of_record():
        raise db_core.NotOfRecord(f"{p}: only the code of record contracts the shared file "
                                  f"({db_core.why_not_of_record()})")  # fmt: skip
    if not p.is_file():
        raise ContractRefused(f"{p}: no {seam.file}")
    stamp, to = db_core.writer(), None  # its git: a subprocess, never inside the transaction
    try:
        with db_core.transaction(seam, check) as con:
            if why := _why_not(con, seam, n):
                raise ContractRefused(f"{seam.file}: {why}")
            to = copy(seam, n)
            try:
                db_schema.apply(con, n, db_schema.migrations(seam)[n], stamp, safe=False)
            except sqlite3.IntegrityError as e:
                raise ContractRefused(f"{seam.file}: number {n} refuses: {e}") from e
            db_schema.stamp_rows(con, seam, stamp, stamp)
    except BaseException:
        if to is not None:
            with contextlib.suppress(FileNotFoundError):
                to.unlink()
        raise
    for old in copies(seam)[:-KEEP]:
        old.unlink()
    return to


def main(n: int, db: str) -> int:
    import goh_cache_db
    import goh_db
    import lock_order

    from tui.lib import err, ok

    seam, lock = {"goh": (goh_db.SEAM, "goh_db"),
                  "goh-cache": (goh_cache_db.SEAM, "goh_cache_db")}[db]  # fmt: skip
    try:
        to = contract(seam, n, lambda: lock_order.check(lock))
    except (ContractRefused, sqlite3.Error, OSError) as e:
        err(f"contract {n}: not applied: {e}")
        return 1
    ok(f"contract {n}: applied; the file as it was: {to}")
    return 0
