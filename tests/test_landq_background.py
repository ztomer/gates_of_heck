"""`goh land --enqueue --land` in the background (GOH.TOOLTIMEOUT, lib/landq_background.py).

78 Bash calls in a week sat past the tool's 120 s timeout; the enqueue's pre-step runs up to 30 min.
Without --wait or --foreground the enqueue refuses what refuses at once, then runs detached, and the
lane's `--wait ITEM` follows it -- a red pre-step included.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import git, hermetic_env
from orphan_kit import wait_for_file
from test_landq import FAKE_LOOPCTL, LANDQ, _land_wt, landq, recorded
from timing_bounds import GiveUp, assert_sooner, patience

sys.path.insert(0, str(LANDQ.parent.parent / "lib"))
import land_records  # lib/, on the path above
import land_reds
import landq_state


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    """tests/test_landq.py's queue: its own dir, a fake loopctl recording each outcome."""
    fake = tmp_path / "loopctl.py"
    fake.write_text(FAKE_LOOPCTL)
    return hermetic_env(drop_git=True, GOH_LANDQ_DIR=str(tmp_path / "landq"),
                        GOH_LOOPCTL=str(fake), FAKE_LOOPCTL_LOG=str(tmp_path / "loopctl.log"),
                        GOH_LAND_TIMEOUT="60")  # fmt: skip


def _pending(tmp_path: Path) -> list[Path]:
    return landq_state.listing(tmp_path / "landq", "pending")  # goh.db rows (GOH.LANDQDB)


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


@pytest.mark.load_sensitive  # its waits are bounded by patience
def test_a_wait_beside_an_enqueue_says_pre_step_running_and_never_the_last_red(repo, tmp_path, env):
    """GOH.WAITSTALE2 (8 lanes on 2026-11-22): the in-flight marker went up only when the pre-step
    started, after the slot and the enqueue's guards, so a `--wait ITEM` in that window returned
    the item's PREVIOUS red; and a wait past --timeout in the pre-step said "filed nothing". The
    enqueue is held in its guards, then in its pre-step, by release files: events, not clocks."""
    wt, _ = _land_wt(repo, tmp_path)
    lq = tmp_path / "landq"
    old = {"item": "T.WS", "state": "red", "sha": "b" * 40, "mode": "land", "why": "try 1"}
    land_records.write(lq, "1000000001-T.WS", old)
    at = {k: tmp_path / k for k in ("in-check", "go-check", "in-pre", "go-pre")}
    hold = {k: f"touch {at['in-' + k]}; {wait_for_file(at['go-' + k])}" for k in ("check", "pre")}
    env = {**env, "GOH_LAND_CHECK_STEPS": hold["check"], "GOH_LAND_PRE": hold["pre"]}
    argv = [sys.executable, str(LANDQ), "--enqueue", "--foreground", "--land", str(wt),
            "--item", "T.WS", "--no-start"]  # fmt: skip
    enq = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True)  # fmt: skip
    try:
        for phase in ("check", "pre"):
            give_up = GiveUp(60)
            while not at[f"in-{phase}"].exists() and enq.poll() is None:
                assert not give_up.passed(), f"the enqueue never reached its {phase}"
                time.sleep(0.05)
            waited = landq(env, "--wait", "T.WS", "--timeout", "1")
            out = waited.stdout + waited.stderr
            assert waited.returncode == 3 and "pre-step running" in out, (phase, out)
            assert "try 1" not in out, (phase, out)
            at[f"go-{phase}"].touch()
        assert enq.wait(patience(60)) == 0, enq.stdout.read() if enq.stdout else ""
    finally:
        for f in at.values():
            f.touch()
        enq.wait(patience(60))
        if enq.stdout:
            enq.stdout.close()
    assert len(_pending(tmp_path)) == 1


@pytest.mark.load_sensitive  # --wait is bounded
def test_a_pre_step_red_record_keeps_each_failing_test_and_a_bounded_traceback(repo, tmp_path, env):
    """GOH.PRESTEPLOG: a pre-step red's record kept only its why line, so `goh land-reds` had no
    E line to read and classed by name and source alone. The log keeps each failing test's
    section, its last lines only, and the FAILED lines: the cause is read without a re-run."""
    wt, _ = _land_wt(repo, tmp_path)
    frames = "".join(f"    frame {n}\n" for n in range(60))
    pytest_out = (
        f"{'_' * 20} test_planted_red {'_' * 20}\n{frames}"
        "E       AssertionError: lib/x.py has no row in docs/map.md\n"
        "tests/test_wiring.py:12: AssertionError\n"
        f"{'=' * 20} short test summary info {'=' * 20}\n"
        "FAILED tests/test_wiring.py::test_planted_red - AssertionError\n"
    )
    out_file = tmp_path / "pytest.out"
    out_file.write_text(pytest_out)
    env = {**env, "GOH_LAND_GATE": "true", "GOH_LAND_PRE": f"cat {out_file}; exit 1"}
    assert (
        landq(env, "--enqueue", "--land", str(wt), "--item", "T.PL", "--no-start").returncode == 0
    )
    waited = landq(env, "--wait", "T.PL", "--timeout", str(patience(90)))
    assert waited.returncode == 1, waited.stdout + waited.stderr
    rec = recorded(env)["T.PL"]
    log = Path(rec["log"]).read_text()
    assert "E       AssertionError: lib/x.py has no row in docs/map.md" in log, log
    assert "FAILED tests/test_wiring.py::test_planted_red" in log, log
    assert "frame 59" in log and "frame 0\n" not in log, log  # the last lines, bounded
    assert land_reds.judge({**rec, "why": rec.get("why", "")})["cause"] == "consistency-table"
