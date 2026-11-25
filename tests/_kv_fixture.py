"""goh.db and goh-cache.db in memory for the kv stores' tests (GOH.KVBLOBS): the seams'
`transaction` and `ro` on one connection each, so a store runs whole and no file is written."""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import db_core
import db_schema
import goh_cache_db
import goh_db

SEAMS = {"goh": goh_db, "goh-cache": goh_cache_db}
MADE: dict[tuple[str, int], bytes] = {}


def memory(mod, last: int = 9999) -> sqlite3.Connection:
    """MOD's file as it is made, in memory; LAST: an older goh's, its schema up to that file."""
    key = (mod.SEAM.name, last)
    # one thread at a time: a push sweep's pool of one (lib/pin_sweep.py) writes the test's
    con = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    if key not in MADE:
        con.executescript(db_core.BOOT)
        files = {n: p for n, p in db_schema.migrations(mod.SEAM).items() if n <= last}
        db_schema.fresh(con, files, "", set(), mod.SEAM.born if last == 9999 else ())
        MADE[key] = con.serialize()
    else:
        con.deserialize(MADE[key])
    return con


class Held:
    """The two databases by seam, and what a test may break: `down`, every cache call an error."""

    def __init__(self, last: int = 9999) -> None:
        self.goh = memory(goh_db, last)
        self.cache = memory(goh_cache_db)
        self.down = False

    def lay(self, monkeypatch) -> Held:
        for mod, con in ((goh_db, lambda: self.goh), (goh_cache_db, lambda: self.cache)):
            monkeypatch.setattr(mod, "ro", self._ro(mod, con))
            monkeypatch.setattr(mod, "transaction", self._transaction(mod, con))
        monkeypatch.setattr(goh_db, "known", goh_db.ro)
        return self

    def _check(self, mod) -> None:
        if self.down and mod is goh_cache_db:
            raise sqlite3.OperationalError("database is locked")

    def _ro(self, mod, con):
        @contextlib.contextmanager
        def ro(_at: Path | None = None, _named: bool = False) -> Iterator[sqlite3.Connection]:
            self._check(mod)
            try:
                yield con()
            finally:
                if con().in_transaction:
                    con().execute("ROLLBACK")

        return ro

    def _transaction(self, mod, con):
        @contextlib.contextmanager
        def transaction(_at: Path | None = None, _named: bool = False):
            self._check(mod)
            con().execute("BEGIN IMMEDIATE")
            try:
                yield con()
            except BaseException:
                con().execute("ROLLBACK")
                raise
            con().execute("COMMIT")

        return transaction


def kv(con: sqlite3.Connection, name: str, body: str, at: float = 1.0) -> None:
    """As code from before 0015 writes it."""
    con.execute("INSERT OR REPLACE INTO kv(name, body, at) VALUES (?, ?, ?)", (name, body, at))


def duration_kv(con: sqlite3.Connection, key: str, value: str, at: float = 1.0) -> None:
    con.execute("INSERT OR REPLACE INTO duration_kv(key, value, at) VALUES (?, ?, ?)",
                (key, value, at))  # fmt: skip
