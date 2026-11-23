"""The enqueue pre-step is the queue's work, never the enqueuer's (GOH.PRESTEPDETACH).

THE FAULT (2026-11-23). GOH.NITS3 and GOH.ROADMAPIDS (twice) came back red "the pre-land tests
failed ... (exit -15)", read as their lane agents' stop SIGTERMing the pre-step. The records say
otherwise: each red came 1800.1-1800.7 s after its pre-step started, which is lib/land_prestep.py's
own 30 min TIMEOUT killing the pre-step's group. The pre-step's slot wait (up to load.py's 15 min)
plus its deadline (up to GOH_LAND_PRE_CEIL, 25 min) ran past it, and the enqueue's own clock read
its kill as a test failure. So:

    1. a SIGTERM to the enqueuing shell's group leaves the pre-step running, and its verdict is
       recorded for `goh land --wait` (the background enqueue already runs it in its own session:
       pinned here, so it stays so);
    2. the enqueue's bound covers the slot wait AND the ceiling, so it never fires first;
    3. the enqueue's bound, should it fire, is no verdict -- the tip queues and the gate decides --
       never a red naming tests that never failed.
"""

from __future__ import annotations

import importlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env
from test_landq import FAKE_LOOPCTL, LANDQ, _land_wt, enq, landq, recorded
from timing_bounds import GiveUp, patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
gates_in_flight = importlib.import_module("gates_in_flight")
land_prestep = importlib.import_module("land_prestep")
landq_inflight = importlib.import_module("landq_inflight")
pre_deadline = importlib.import_module("pre_deadline")


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    fake = tmp_path / "loopctl.py"
    fake.write_text(FAKE_LOOPCTL)
    return hermetic_env(drop_git=True, GOH_LANDQ_DIR=str(tmp_path / "landq"),
                        GOH_LOOPCTL=str(fake), FAKE_LOOPCTL_LOG=str(tmp_path / "loopctl.log"),
                        GOH_LAND_TIMEOUT="60")  # fmt: skip


def _pending(tmp_path: Path) -> list[Path]:
    pending = tmp_path / "landq" / "pending"
    return list(pending.iterdir()) if pending.exists() else []


@pytest.mark.load_sensitive  # the pre-step's few seconds and the wait are bounded by patience
def test_an_enqueuing_shell_killed_mid_pre_step_leaves_it_running_and_recorded(repo, tmp_path, env):
    """The lane's shell (its own group here, as the agent's is) enqueues, then is SIGTERMed whole
    while the pre-step runs: the pre-step finishes and its red is recorded for `--wait`."""
    wt, _ = _land_wt(repo, tmp_path)
    started = tmp_path / "pre-started"
    pre = (f"touch {started}; sleep 4; "
           "echo 'FAILED tests/test_wiring.py::test_planted_red - planted'; exit 1")  # fmt: skip
    env = {**env, "GOH_LAND_GATE": "true", "GOH_LAND_PRE": pre}
    lane = subprocess.Popen(
        ["bash", "-c", '"$@"; sleep 300', "lane", sys.executable, str(LANDQ), "--enqueue",
         "--land", str(wt), "--item", "T.KILL", "--no-start"],
        env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )  # fmt: skip
    try:
        give_up = GiveUp(60)
        while not started.exists() and not give_up.passed():
            time.sleep(0.1)
        assert started.exists(), "the pre-step never started"
        os.killpg(lane.pid, signal.SIGTERM)  # the agent's stop: its whole group
        lane.wait(timeout=patience(10))
    finally:
        if lane.poll() is None:
            os.killpg(lane.pid, signal.SIGKILL)
            lane.wait()
    waited = landq(env, "--wait", "T.KILL", "--timeout", str(patience(90)))
    out = waited.stdout + waited.stderr
    assert waited.returncode == 1 and "test_planted_red" in out, out
    assert "exit -15" not in out, out
    assert recorded(env)["T.KILL"]["state"] == "red"


def test_the_enqueues_bound_covers_the_slot_wait_and_the_ceiling() -> None:
    """The outer clock starts before land.sh's slot wait (load.py's default bound) and its
    deadline (at most the ceiling): with less than both, it fires first on a busy box."""
    bound = land_prestep.TIMEOUT
    inner = gates_in_flight.DEFAULT_WAIT_S + pre_deadline.CEILING_S
    assert bound >= inner + land_prestep.TIMEOUT_MARGIN_S, (bound, inner)
    assert landq_inflight.PRE_TIMEOUT_S == land_prestep.TIMEOUT  # the marker's bound is the same


@pytest.mark.load_sensitive  # a 3 s bound on a 60 s pre-step: it fires, whatever the load
def test_a_pre_step_stopped_by_the_enqueues_own_bound_is_no_verdict_not_red(repo, tmp_path, env):
    """The enqueue's own clock firing says nothing about the tests: the tip queues, no verdict."""
    wt, _ = _land_wt(repo, tmp_path)
    env = {**env, "GOH_LAND_GATE": "true", "GOH_LAND_PRE": "sleep 60",
           "GOH_LAND_PRE_MAX": "120s", "GOH_LAND_PRE_CEIL": "120s",
           "GOH_ENQUEUE_PRE_TIMEOUT": "3"}  # fmt: skip
    done = enq(env, "--land", str(wt), "--item", "T.BOUND", "--no-start")
    out = done.stdout + done.stderr
    assert done.returncode == 0, out
    assert "exit -15" not in out and "failed" not in out, out
    assert "no verdict" in out and "the enqueue's own bound" in out, out
    assert len(_pending(tmp_path)) == 1
