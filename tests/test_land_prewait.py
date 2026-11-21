"""A slot wait that expires is no verdict, never red (GOH.PREWAITVERDICT).

PRESTEPHEAD's enqueue (2026-11-17 23:32) waited the full --wait-max (900 s) for a gate slot, ran
nothing, and printed 'the pre-land tests failed': load.py's EXIT_BUSY (3) read as a red set. The
pre-step and the land's gate both queue for a slot under tools/load.py; neither may say its tests
failed when they never ran. The slot is held by the test (the push gate of tests/test_gate_slot.py)
and the wait is GOH_LAND_PRE_WAIT / GOH_LAND_GATE_WAIT, tiny.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, git, hermetic_env
from test_affected_tests import _declare
from test_gate_slot import Gate
from test_land import LAND, _main, _worktree
from test_landq import _land_wt
from test_one_admission import _end, _env, _until
from timing_bounds import patience


def _holder(tmp_path: Path, lane: str) -> Gate:
    """A gate holding the ONE slot of `lane` (short: the pre-step's own lane; work: the gate's)."""
    gate = Gate(tmp_path, "push-holder", _env(tmp_path, GOH_GATE_SLOTS="1", GOH_GATE_CLASS=lane))
    _until(lambda: "TAKEN" in gate.out() or gate.proc.poll() is not None)
    assert "TAKEN" in gate.out(), gate.out()
    return gate


def _land(repo: Path, tmp_path: Path, **env: str) -> tuple[subprocess.CompletedProcess[str], Path]:
    wt = _worktree(repo, tmp_path)
    seen = tmp_path / "seen"
    _declare(wt, f"GOH_LAND_GATE='echo gate >> {seen}'\nGOH_LAND_PRE='echo pre >> {seen}'\n")
    full = hermetic_env(drop_git=True, **{**_env(tmp_path, GOH_GATE_SLOTS="1"), **env})
    full.pop("GATES_OF_HECK_SUITE_RUNNER", None)
    r = subprocess.run(["bash", str(LAND)], cwd=wt, env=full, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=patience(120), check=False)  # fmt: skip
    return r, wt


@pytest.mark.load_sensitive  # a bounded slot wait
def test_a_pre_step_that_never_got_a_slot_is_no_verdict_not_red(repo: Path, tmp_path: Path) -> None:
    holder = _holder(tmp_path, "short")
    try:
        r, wt = _land(repo, tmp_path, GOH_LAND_PRE_WAIT="1s")
    finally:
        _end(holder)
    out = r.stdout + r.stderr
    assert "the pre-land tests failed" not in out, out
    assert "pre-land: no verdict" in out and "waited" in out and "gate slot" in out, out
    assert "ran nothing" in out, out
    assert (tmp_path / "seen").read_text() == "gate\n", "the pre-step ran, or the gate did not"
    assert r.returncode == 0 and _main(repo) == git(wt, "rev-parse", "HEAD").strip(), out


@pytest.mark.load_sensitive  # a bounded slot wait
def test_a_gate_that_never_got_a_slot_says_it_never_ran(repo: Path, tmp_path: Path) -> None:
    holder = _holder(tmp_path, "work")
    try:
        r, wt = _land(repo, tmp_path, GOH_LAND_GATE_WAIT="1s")
    finally:
        _end(holder)
    out = r.stdout + r.stderr
    assert r.returncode == 75, out  # load.py's EXIT_GATE: try again, no verdict
    assert "the gate failed" not in out, out
    assert "the gate never ran" in out and "waited" in out and "gate slot" in out, out
    assert (tmp_path / "seen").read_text() == "pre\n", "the gate ran"
    assert _main(repo) != git(wt, "rev-parse", "HEAD").strip()


@pytest.mark.load_sensitive  # a bounded slot wait
def test_the_request_records_that_the_pre_step_had_no_verdict_and_why(
    repo: Path, tmp_path: Path
) -> None:
    """The selection-miss ledger and GOH.PRESTEPRECORD read `pre_verdict` off the land request."""
    wt, _ = _land_wt(repo, tmp_path)
    _declare(wt, "GOH_LAND_GATE=true\nGOH_LAND_PRE='true'\n")
    holder = _holder(tmp_path, "short")
    env = hermetic_env(drop_git=True, **{**_env(tmp_path, GOH_GATE_SLOTS="1"),
                                         "GOH_LAND_PRE_WAIT": "1s",
                                         "GOH_LANDQ_DIR": str(tmp_path / "landq")})  # fmt: skip
    env.pop("GATES_OF_HECK_SUITE_RUNNER", None)
    try:
        done = subprocess.run(
            [sys.executable, str(REPO_ROOT / "tools" / "landq.py"), "--enqueue", "--land", str(wt),
             "--item", "T.W", "--no-start", "--foreground"], env=env, capture_output=True,
            text=True, stdin=subprocess.DEVNULL, timeout=patience(120), check=False)  # fmt: skip
    finally:
        _end(holder)
    out = done.stdout + done.stderr
    assert done.returncode == 0 and "the pre-land tests failed" not in out, out
    (path,) = list((tmp_path / "landq" / "pending").iterdir())
    verdict = json.loads(path.read_text())["pre_verdict"]
    assert verdict["state"] == "none" and verdict["kind"] == "slot-wait", verdict
    assert "gate slot" in verdict["why"] and "ran nothing" in verdict["why"], verdict
