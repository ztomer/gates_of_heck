"""A test's goh.db journals in memory, but for the tests of WAL, of locking and of a crash.

Every database under the run's root is a throwaway (lib/db_core.py `throwaway`, GOH.FSEVQUIET): no
-wal and -shm made and removed at each open, nothing synced, small pages. tests/db_on_disk.json
names the tests whose subject IS the journal on disk, each with why; they run as production does.
The list only shrinks: tests/test_db_on_disk.py holds its length and every name in it.

The suite home's own goh.db and goh-cache.db are every worker's, and stay WAL. Each open of one by
the only process that had it made its -wal and -shm and each close removed them (GOH.FSEVQUIET: 30
file events a test of the land's); each worker holds both open, so they are made once a run.
"""

from __future__ import annotations

import functools
import json
import os
import sqlite3
from pathlib import Path

import pytest

ENV = "GATES_OF_HECK_DB_THROWAWAY"  # lib/db_settings.py THROWAWAY_ENV


@functools.cache
def on_disk() -> dict[str, str]:
    """The list, read at the first test: no data file is read while pytest loads."""
    return json.loads(Path(__file__).with_name("db_on_disk.json").read_text())


def pytest_configure(config) -> None:
    root = os.environ.get("PYTEST_DEBUG_TEMPROOT")
    if root:  # an outer run's, when this is a session its test started: one mode for one file
        os.environ.setdefault(ENV, root)


_HELD: list[sqlite3.Connection] = []
_SOUGHT = {"goh.db", "goh-cache.db"}
_SUITE = Path(os.path.expanduser("~"))  # the suite's own home (tests/suite_home.py), never a test's


def hold(path: Path) -> sqlite3.Connection:
    """A connection to the WAL database at `path` that keeps its -wal and -shm made, and no lock."""
    con = sqlite3.connect(f"{path.as_uri()}?mode=rw", uri=True, isolation_level=None)
    con.execute("SELECT count(*) FROM sqlite_master").fetchone()  # the first read opens them
    return con


@pytest.hookimpl(trylast=True)
def pytest_runtest_teardown(item) -> None:
    """Once the suite home has one (never made for this: its first writer gives it its settings)."""
    for var, name in (("XDG_STATE_HOME", "goh.db"), ("XDG_CACHE_HOME", "goh-cache.db")):
        if name in _SOUGHT and (base := os.environ.get(var)):
            path = Path(base) / "goh" / name
            if path.is_relative_to(_SUITE) and path.is_file() and path.stat().st_size:
                _HELD.append(hold(path))
                _SOUGHT.discard(name)


def pytest_unconfigure(config) -> None:
    while _HELD:
        _HELD.pop().close()


@pytest.fixture(autouse=True)
def _db_journal(request, monkeypatch):
    if request.node.nodeid.split("[")[0] in on_disk():
        monkeypatch.delenv(ENV, raising=False)
