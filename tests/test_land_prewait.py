"""A slot wait that expires is no verdict, never red (GOH.PREWAITVERDICT).

PRESTEPHEAD's enqueue (2026-11-17 23:32) waited the full --wait-max (900 s) for a gate slot, ran
nothing, and printed 'the pre-land tests failed': load.py's EXIT_BUSY (3) read as a red set. The
pre-step and the land's gate both queue for a slot under tools/load.py; neither may say its tests
failed when they never ran. The slot is held by the test itself (`_Occupant`, an flock only the
test can drop) and the wait is GOH_LAND_PRE_WAIT / GOH_LAND_GATE_WAIT, tiny.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import gate_lanes
import gate_slot
from conftest import REPO_ROOT, git, hermetic_env
from orphan_kit import adopt
from test_affected_tests import _declare
from test_land import LAND, _main, _worktree
from test_landq import _land_wt
from test_one_admission import _env
from timing_bounds import patience


class _Occupant:
    """The ONE slot of a lane, held by this very process: an exclusive flock on the slot file and a
    holder's record in it, nothing between the test and the lock (GOH.PREWAITRACE).

    The slot was held by a fake push gate -- a shell, its slot holder and a wait on a file -- and
    the lander's pre-land re-run (2026-11-21 19:55, 16 workers, load 25) saw it freed inside the
    pre-step's 1 s wait: the pre-step ran, and the test that says it never got a slot went red; it
    passed 3 of 3 alone. A wait that is MEANT to expire needs a lock only the test can drop, so
    this takes the flock itself. The record names a live process that is no ancestor of the land
    (an ancestor's slot reads as nested, lib/gate_slot.py `nested`: the gate would not wait)."""

    def __init__(self, tmp_path: Path, gate_class: str) -> None:
        slots = tmp_path / "slots"
        slots.mkdir(exist_ok=True)
        lane, _ = gate_lanes.lane_of(gate_class)
        self.path = gate_slot.slot_path(str(slots), 0, lane)
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # BlockingIOError: not this test's
        self.sleeper = subprocess.Popen(
            ["sleep", "600"], start_new_session=True, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
        )  # fmt: skip
        adopt(self.sleeper)
        record = f"{self.sleeper.pid}\tpush-holder\t{time.time():.0f}\t\n"
        os.write(self.fd, record.encode())

    def held(self) -> bool:
        """Whether anyone but this occupant could not take the slot now (asked on a second open)."""
        return gate_slot.held(self.path)

    def release(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1
        if self.sleeper.poll() is None:
            self.sleeper.kill()
        self.sleeper.wait()


def _holder(tmp_path: Path, gate_class: str) -> _Occupant:
    """The slot of `gate_class`'s lane (short: the pre-step's own; work: the gate's), held."""
    occupant = _Occupant(tmp_path, gate_class)
    assert occupant.held(), "the occupant holds the slot"
    return occupant


def _land(repo: Path, tmp_path: Path, **env: str) -> tuple[subprocess.CompletedProcess[str], Path]:
    wt = _worktree(repo, tmp_path)
    seen = tmp_path / "seen"
    _declare(wt, f"GOH_LAND_GATE='echo gate >> {seen}'\nGOH_LAND_PRE='echo pre >> {seen}'\n")
    full = hermetic_env(drop_git=True, **{**_env(tmp_path, GOH_GATE_SLOTS="1"), **env})
    full.pop("GATES_OF_HECK_SUITE_RUNNER", None)
    r = subprocess.run(["bash", str(LAND)], cwd=wt, env=full, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=patience(120), check=False)  # fmt: skip
    return r, wt


def test_a_pre_step_that_never_got_a_slot_is_no_verdict_not_red(repo: Path, tmp_path: Path) -> None:
    holder = _holder(tmp_path, "short")
    try:
        r, wt = _land(repo, tmp_path, GOH_LAND_PRE_WAIT="1s")
    finally:
        holder.release()
    out = r.stdout + r.stderr
    assert "the pre-land tests failed" not in out, out
    assert "pre-land: no verdict" in out and "waited" in out and "gate slot" in out, out
    assert "ran nothing" in out, out
    assert (tmp_path / "seen").read_text() == "gate\n", "the pre-step ran, or the gate did not"
    assert r.returncode == 0 and _main(repo) == git(wt, "rev-parse", "HEAD").strip(), out


def test_a_gate_that_never_got_a_slot_says_it_never_ran(repo: Path, tmp_path: Path) -> None:
    holder = _holder(tmp_path, "work")
    try:
        r, wt = _land(repo, tmp_path, GOH_LAND_GATE_WAIT="1s")
    finally:
        holder.release()
    out = r.stdout + r.stderr
    assert r.returncode == 75, out  # load.py's EXIT_GATE: try again, no verdict
    assert "the gate failed" not in out, out
    assert "the gate never ran" in out and "waited" in out and "gate slot" in out, out
    assert (tmp_path / "seen").read_text() == "pre\n", "the gate ran"
    assert _main(repo) != git(wt, "rev-parse", "HEAD").strip()


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
        holder.release()
    out = done.stdout + done.stderr
    assert done.returncode == 0 and "the pre-land tests failed" not in out, out
    (path,) = list((tmp_path / "landq" / "pending").iterdir())
    verdict = json.loads(path.read_text())["pre_verdict"]
    assert verdict["state"] == "none" and verdict["kind"] == "slot-wait", verdict
    assert "gate slot" in verdict["why"] and "ran nothing" in verdict["why"], verdict
