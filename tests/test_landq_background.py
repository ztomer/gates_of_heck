"""`goh land --enqueue --land` in the background (GOH.TOOLTIMEOUT, lib/landq_background.py).

78 Bash calls in a week sat past the tool's 120 s timeout; the enqueue's pre-step runs up to 30 min.
Without --wait or --foreground the enqueue refuses what refuses at once, then runs detached, and the
lane's `--wait ITEM` follows it -- a red pre-step included.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from conftest import git, hermetic_env
from test_landq import FAKE_LOOPCTL, _land_wt, landq, recorded
from timing_bounds import assert_sooner, patience


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    """tests/test_landq.py's queue: its own dir, a fake loopctl recording each outcome."""
    fake = tmp_path / "loopctl.py"
    fake.write_text(FAKE_LOOPCTL)
    return hermetic_env(drop_git=True, GOH_LANDQ_DIR=str(tmp_path / "landq"),
                        GOH_LOOPCTL=str(fake), FAKE_LOOPCTL_LOG=str(tmp_path / "loopctl.log"),
                        GOH_LAND_TIMEOUT="60")  # fmt: skip


def _pending(tmp_path: Path) -> list[Path]:
    pending = tmp_path / "landq" / "pending"
    return list(pending.iterdir()) if pending.exists() else []


@pytest.mark.load_sensitive
def test_enqueue_land_backgrounds_its_long_pre_step_and_the_wait_follows_it(repo, tmp_path, env):
    """GOH.TOOLTIMEOUT: the enqueue's pre-step runs up to 30 min, past the Bash tool's timeout. Not
    given --wait (nor --foreground) it says so, runs in the background and returns at once; the
    lane's `--wait ITEM` then follows the pre-step into the queue and the land."""
    wt, tip = _land_wt(repo, tmp_path)
    env = {**env, "GOH_LAND_GATE": "true", "GOH_LAND_PRE": "sleep 20"}
    t0 = time.monotonic()
    done = landq(env, "--enqueue", "--land", str(wt), "--item", "T.BG", "--no-start")
    out = done.stdout + done.stderr
    assert done.returncode == 0, out
    # the pass: checks and a spawn; the failure: an enqueue that sat through the 20 s pre-step
    assert_sooner(time.monotonic() - t0, 3, 20, "the backgrounded enqueue")
    assert "runs long" in out and "background" in out and "--wait T.BG" in out, out
    waited = landq(env, "--wait", "T.BG", "--timeout", str(patience(90)))
    assert waited.returncode == 0, waited.stdout + waited.stderr
    assert recorded(env)["T.BG"]["state"] == "landed"
    assert git(repo, "rev-parse", "HEAD").strip() == tip


@pytest.mark.load_sensitive
def test_a_backgrounded_pre_step_that_goes_red_is_recorded_red_for_the_wait(repo, tmp_path, env):
    """The red a foreground enqueue printed and exited 2 on is, backgrounded, a red outcome the
    lane's --wait reads: exit 1 with the failing test named, and nothing queued."""
    wt, _ = _land_wt(repo, tmp_path)
    pre = "echo 'FAILED tests/test_wiring.py::test_planted_red - planted'; exit 1"
    env = {**env, "GOH_LAND_GATE": "true", "GOH_LAND_PRE": pre}
    assert (
        landq(env, "--enqueue", "--land", str(wt), "--item", "T.BR", "--no-start").returncode == 0
    )
    waited = landq(env, "--wait", "T.BR", "--timeout", str(patience(90)))
    out = waited.stdout + waited.stderr
    assert waited.returncode == 1 and "test_planted_red" in out, out
    assert recorded(env)["T.BR"]["state"] == "red"
    assert _pending(tmp_path) == []


@pytest.mark.load_sensitive
def test_enqueue_land_with_wait_runs_here_and_blocks_until_it_landed(repo, tmp_path, env):
    wt, tip = _land_wt(repo, tmp_path)
    env = {**env, "GOH_LAND_GATE": "true", "GOH_LAND_PRE": "true"}
    done = landq(
        env,
        "--enqueue",
        "--land",
        str(wt),
        "--item",
        "T.WT",
        "--wait",
        "--timeout",
        str(patience(90)),
    )
    out = done.stdout + done.stderr
    assert done.returncode == 0 and "background" not in out, out
    assert "landed" in out and git(repo, "rev-parse", "HEAD").strip() == tip, out


def test_a_bare_wait_with_no_enqueue_is_refused_naming_what_it_needs(env):
    done = landq(env, "--wait")
    assert done.returncode == 2 and "ID|REQUEST" in done.stdout + done.stderr, done.stderr
