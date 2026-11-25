"""A test's goh.db journals in memory, but for the tests of WAL, of locking and of a crash.

Every database under the run's root is a throwaway (lib/db_core.py `throwaway`, GOH.FSEVQUIET): no
-wal and -shm made and removed at each open, nothing synced, small pages. tests/db_on_disk.json
names the tests whose subject IS the journal on disk, each with why; they run as production does.
The list only shrinks: tests/test_db_on_disk.py holds its length and every name in it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

ENV = "GATES_OF_HECK_DB_THROWAWAY"  # lib/db_core.py THROWAWAY_ENV
ON_DISK: dict[str, str] = json.loads(Path(__file__).with_name("db_on_disk.json").read_text())


def pytest_configure(config) -> None:
    root = os.environ.get("PYTEST_DEBUG_TEMPROOT")
    if root:  # an outer run's, when this is a session its test started: one mode for one file
        os.environ.setdefault(ENV, root)


@pytest.fixture(autouse=True)
def _db_journal(request, monkeypatch):
    if request.node.nodeid.split("[")[0] in ON_DISK:
        monkeypatch.delenv(ENV, raising=False)
