"""The suite's own temp root (`goh-suite-tmp.*`) never outlives its run (GOH.ENVDUMP).

A bare `pytest` makes the root in tests/suite_tmp.py and removed it with `atexit` -- which a
SIGKILL, a SIGTERM or a SIGHUP never runs. A run killed mid-way left its root in the user's temp
dir (2026-11-12 17:10: one held a test's dump of the whole environment, session tokens included),
and only the 12-hour backstop sweep would ever have removed it. So the root has a reaper: a small
process in its own session that holds the read end of a pipe whose write end only the suite holds,
and removes the root when that pipe closes -- which the kernel does however the suite ends.

Each arm starts a process that enters suite_tmp the way a bare pytest's conftest does, with a temp
dir of the test's own, then ends it one way and waits for the root to go.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import suite_tmp
from conftest import hermetic_env
from timing_bounds import GiveUp

TESTS = Path(__file__).resolve().parent

ENTER = (
    "import os, signal, sys, time\n"
    "signal.signal(signal.SIGHUP, signal.SIG_DFL)\n"
    f"sys.path.insert(0, {str(TESTS)!r})\n"
    "import suite_tmp\n"
    "print(os.environ['TMPDIR'], flush=True)\n"
    "if sys.argv[1] == 'exit':\n"
    "    sys.exit(0)\n"
    "if sys.argv[1] == 'fail':\n"
    "    raise SystemExit(1)\n"
    "time.sleep(300)\n"
)


def _run(tmp_path: Path, how: str, end) -> Path:
    """Start a run that enters suite_tmp, hand it and its root to `end`, and return the root once
    the run has ended."""
    real = tmp_path / "real-tmp"
    real.mkdir()
    env = hermetic_env(TMPDIR=str(real))
    for key in (suite_tmp.MARKER, suite_tmp.REAL_TMP_MARKER, "PYTEST_DEBUG_TEMPROOT"):
        env.pop(key, None)
    with subprocess.Popen(
        [sys.executable, "-c", ENTER, how],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        text=True,
    ) as proc:
        try:
            assert proc.stdout is not None
            root = Path(proc.stdout.readline().strip())
            # The subject is present: a root of the suite's own name, under the test's temp dir.
            assert root.parent == real and root.name.startswith("goh-suite-tmp."), root
            end(proc, root)
        finally:
            proc.kill()
            proc.wait(timeout=60)
    return root


def _gone(root: Path) -> bool:
    give_up = GiveUp(15)
    while root.exists():
        if give_up.passed():
            return False
        time.sleep(0.1)
    return True


@pytest.mark.parametrize("how", ["exit", "fail"])
def test_a_run_that_ends_removes_its_root(tmp_path: Path, how: str) -> None:
    root = _run(tmp_path, how, lambda proc, _root: proc.wait(timeout=60))
    assert _gone(root), f"the root outlived a run that ended ({how}): {root}"


@pytest.mark.parametrize("sig", [signal.SIGKILL, signal.SIGTERM, signal.SIGHUP])
def test_a_killed_run_leaves_no_root(tmp_path: Path, sig: signal.Signals) -> None:
    def kill(proc: subprocess.Popen, root: Path) -> None:
        assert root.is_dir()  # the control: the root exists while the run lives
        os.kill(proc.pid, sig)
        proc.wait(timeout=60)

    root = _run(tmp_path, "sleep", kill)
    assert _gone(root), f"a run killed by {sig.name} left its root: {root}"


def test_the_root_names_its_owner_for_the_backstop(tmp_path: Path) -> None:
    """While it runs, the root carries `.goh-owner` (the live pid), so the session-start backstop
    sweep (lib/prune_kept.py) never removes a long-running suite's root from under it."""

    def owner(proc: subprocess.Popen, root: Path) -> None:
        assert (root / ".goh-owner").read_text().strip() == str(proc.pid)

    assert _gone(_run(tmp_path, "sleep", owner))
