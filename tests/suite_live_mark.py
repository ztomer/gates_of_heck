"""The live suite's mark in its checkout's git directory (a session fixture).

`goh-suite.live` names the suite controller's pid, so gates/_from_head.sh refuses a gate the suite
started with a HAND-BUILT environment -- `env={"PATH": ..., "HOME": ...}` carries neither GOH_LIVE
nor the suite's markers, and its gate quietly judged HEAD's export (GOH.FSCHURN4). The outermost
live run keeps the mark; a nested session leaves an outer one's alone. It is never removed: a
finished run's mark names a dead pid, which no live gate descends from. Pinned by
tests/test_hand_built_env_live.py.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "lib") not in sys.path:
    sys.path.append(str(REPO_ROOT / "lib"))
import pid_owner  # noqa: E402  (lib/: a saved pid is judged by its stamp, GOH.STRAYSIGNAL2)
import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def live_suite_mark() -> None:
    """Written at each process's first test, never while pytest loads (nothing the base opens may
    move with a checkout's .git, tests/test_base_data_reads.py). An xdist worker writes its
    controller's pid (its parent), so every worker's gates descend from the one pid; the
    controller's mark is removed when that controller is gone (a dead pid refuses nothing)."""
    gitdir = REPO_ROOT / ".git"
    if gitdir.is_file():
        first = gitdir.read_text(encoding="utf-8").splitlines()[0]
        gitdir = Path(first.removeprefix("gitdir: "))
        gitdir = gitdir if gitdir.is_absolute() else REPO_ROOT / gitdir
    if not gitdir.is_dir():
        return
    mark = gitdir / "goh-suite.live"
    try:
        if pid_owner.alive_line(mark.read_text()):
            return  # this run's (another worker wrote it) or an outer live run's
    except OSError:
        pass
    suite = os.getppid() if os.environ.get("PYTEST_XDIST_WORKER") else os.getpid()
    mark.write_text(f"{pid_owner.stamp_line(suite) or suite}\n")
