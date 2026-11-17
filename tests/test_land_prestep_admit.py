"""A land's pre-step never queues for a long gate slot (GOH.PRESTEPADMIT).

tools/land.sh admitted the pre-step as a `ship` gate, so a land queued for a long slot TWICE (the
pre-step, then the gate): a lane that would go red waited up to 632 s before failing in 100-200 s,
and 'pre-land green in 787 s' hid the 632 s wait (ADVISORYLOAD, 2026-11-16). The pre-step is
admitted in the SHORT lane (the commit gate's, lib/gate_lanes.py), which no long gate takes, so it
starts while both long slots are held; and its line names the wait apart from the run.
"""

from __future__ import annotations

import fcntl
import os
import signal
import subprocess
import time
from pathlib import Path

import pytest
from conftest import git, hermetic_env
from test_affected_tests import _declare
from test_land import LAND, _main, _worktree
from timing_bounds import GiveUp, patience


@pytest.mark.load_sensitive
def test_the_pre_step_starts_while_every_long_slot_is_held(repo: Path, tmp_path: Path) -> None:
    wt = _worktree(repo, tmp_path)
    seen, slots = tmp_path / "seen", tmp_path / "slots"
    slots.mkdir()
    _declare(wt, f"GOH_LAND_GATE='echo gate >> {seen}'\nGOH_LAND_PRE='echo pre >> {seen}'\n")
    held = []
    for i in range(2):  # two long gates hold both long slots
        fd = os.open(slots / f"slot-{i}", os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        held.append(fd)
    env = hermetic_env(drop_git=True, GOH_GATE_SLOTS="2", GOH_GATE_SLOT_DIR=str(slots))
    env.pop("GATES_OF_HECK_SUITE_RUNNER", None)
    land = subprocess.Popen(["bash", str(LAND)], cwd=wt, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            stdin=subprocess.DEVNULL, start_new_session=True)  # fmt: skip
    try:
        give_up = GiveUp(10)
        while not (seen.exists() and "pre" in seen.read_text()):
            assert not give_up.passed(), "the pre-step waited for a long gate slot"
            assert land.poll() is None, land.communicate()[0]
            time.sleep(0.2)
        assert "gate" not in seen.read_text()  # the gate itself still waits for its long slot
        for fd in held:  # the long gates end: the land's gate takes a slot and lands
            os.close(fd)
        held.clear()
        out, _ = land.communicate(timeout=patience(60))
    finally:
        for fd in held:
            os.close(fd)
        if land.poll() is None:  # the land and its load.py: never left running
            os.killpg(land.pid, signal.SIGKILL)
            land.communicate()
    assert land.returncode == 0, out
    assert seen.read_text() == "pre\ngate\n" and _main(repo) == git(wt, "rev-parse", "HEAD").strip()
    line = next(x for x in out.splitlines() if "pre-land green in" in x)
    assert "waited " in line and ", ran " in line, line
