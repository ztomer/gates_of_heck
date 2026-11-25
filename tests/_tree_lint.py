"""goh's own tree is shellchecked ONCE per suite session, in setup (GOH.CIRED01392).

A cold `goh shell-lint` of goh's tree is every one of its shell files (114) through shellcheck: 68 s
of CPU on the owner's box. The suite home is the session's, shared by its xdist workers and emptied
at exit (tests/suite_home.py), so the first test of a session to lint the tree in place paid that in
its CALL -- and at the hosted runner's one token (crates/goh/src/shellcheck_slots.rs, before
GOH.CIRED01392) `test_structural_full_leaves_nothing_in_tmpdir` took 224 s and 300 s against its
60 s ceiling (release CI, v0.139.2 and v0.139.3), whichever test scheduling put first.

`tree_lint` records the tree's verdicts in the session's goh-cache.db (the suite home's, every
worker's: crates/goh/src/verdict_cache.rs writes `shell_verdicts` rows) under an flock, so one
worker pays and the rest find them; a test that lints the tree in place requests it and its gate
reads them. A test that asserts its gate recorded nothing copies the file first (`private_copy`):
every worker writes the session's, so a verdict another test records during the call would read as
this one's (the first land of GOH.CIRED01392 went red on "shellchecked 2 files" that way).

The session's rows are also KEPT across sessions (tests/_kept_store.py, GOH.VERDICTDB V2): the
controller seeds the suite's goh-cache.db from the real file at the session's start and merges the
session's `shell_verdicts` rows back once at its end, so a session lints only the shell files whose
bytes (or sources, tools, rc files) are new. No worker writes the real file.
"""

from __future__ import annotations

import fcntl
import sqlite3
import subprocess
from pathlib import Path

import _kept_store
import _setup_work
import pytest
from conftest import REPO_ROOT, hermetic_env

# One shell file's shellcheck: 80.8 CPU s for the 114 of a cold tree (2026-11-23), rounded up.
FILE_S = 0.8


def shell_verdicts() -> Path:
    """The session's goh-cache.db: where a child with this HOME reads and writes its verdicts."""
    return _kept_store._cache().path()


def records(db: Path | None = None, checker: str = "shell-lint") -> set[str]:
    """CHECKER's verdict rows in `db` (default: the session's), by their row key."""
    db = db or shell_verdicts()
    if not db.is_file():
        return set()
    with _kept_store._cache().ro(db) as con:
        try:
            rows = con.execute("SELECT key FROM shell_verdicts WHERE checker=?", (checker,))
            return {r[0] for r in rows}
        except sqlite3.OperationalError:  # no table yet: nothing recorded
            return set()


@pytest.fixture(scope="session")
def tree_lint(goh) -> Path:
    store = shell_verdicts()
    store.parent.mkdir(parents=True, exist_ok=True)
    before = records()
    with (store.parent / "tree-lint.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # one worker lints; the others wait, then read
        r = subprocess.run([str(goh), "shell-lint"], cwd=REPO_ROOT, capture_output=True, text=True,
                           env=hermetic_env(), stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert records(), "goh shell-lint recorded no verdict in the session's goh-cache.db"
    _setup_work.did(len(records() - before), FILE_S)  # what this session had to lint, by count
    return store


def private_copy(db: Path, dest: Path) -> Path:
    """`db` copied whole (SQLite's backup, so its WAL comes along) to `dest`/goh-cache.db, a
    GOH_CACHE_DB of its own; the copy's path."""
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / "goh-cache.db"
    with _kept_store._cache().ro(db) as src:
        to = sqlite3.connect(out)
        try:
            src.backup(to)
        finally:
            to.close()
    return out
