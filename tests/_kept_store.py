"""A content-addressed store a test keeps ACROSS sessions, in goh's real state (GOH.SELFWALKFILTER).

The suite's home is the run's own and emptied at exit (tests/suite_home.py), so a verdict cache in
it is cold at every run. A record keyed by EVERY input of its verdict -- the file's bytes and what
it can read, the tool's identity, the flags -- is the verdict a later session would compute, so it
may outlive the run: tests/_self_walk.py's shell-lint verdicts (`verdict_store`), the tree lint's
(tests/_tree_lint.py) and the parse findings (tests/_parse_findings.py, GOH.SHELLCHECKCACHE). Each
record is dropped after STORE_DAYS, so a store never grows past what a month of goh's sources held.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

import real_state_guard
import suite_home

STORE_DAYS = 30


def kept(name: str) -> Path:
    """The store NAME under the machine's real goh state, its stale records dropped."""
    # The REAL home: an xdist worker imports suite_home under the suite's own HOME, so its
    # REAL_HOME is the suite's; the marker carries the user's (the store died with the session).
    real_home = os.environ.get(suite_home.MARKER) or suite_home.REAL_HOME
    store = real_state_guard.real_state_dir(real_home) / name
    store.mkdir(parents=True, exist_ok=True)
    # The age is read off the filesystem's own stamp, not a clock call: nothing here waits or
    # measures, so a test reaching the store is no load-sensitive one (tests/load_sensitive.py).
    now = store / ".now"
    now.touch()
    cutoff = now.stat().st_mtime - STORE_DAYS * 86400
    for record in store.rglob("*.json"):
        with contextlib.suppress(OSError):
            if record.stat().st_mtime < cutoff:
                record.unlink()
    return store
