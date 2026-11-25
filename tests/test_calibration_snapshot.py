"""A sibling worker writing into the tree cannot turn the self-proof sweep red (GOH.CALIBXDIST).

The sweep (checks/check_probes_pass.py) stamps the tree it is given around its probes, so the
calibration test sweeps a snapshot of this repo (tests/calib_snapshot.py), never the live checkout.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest
import timing_bounds
from calib_snapshot import ROOT, snapshot, this_snapshot
from conftest import git
from test_gate_calibration import estate, run_gate


def _slow_probe(started: Path) -> str:
    """A self-proof that says it has begun (a file OUTSIDE the tree), then outlasts the sibling."""
    return (
        "import sys, time\n"
        'if "--probe" in sys.argv:\n'
        f"    open({str(started)!r}, 'w').close()\n"
        "    time.sleep(2)\n"
        '    print("clean")\n'
        "sys.exit(0)\n"
    )


def _sibling_writes_while(root: Path, started: Path, run):
    """What a sibling worker does to the tree mid-sweep: plant an untracked file in it, once the
    sweep's probes are running (so the file is not in the stamp taken before them)."""

    def write():
        wait = timing_bounds.GiveUp(30)
        while not started.exists() and not wait.passed():
            time.sleep(0.02)
        (root / "sibling-artifact.txt").write_text("written by a sibling\n")

    thread = threading.Thread(target=write)
    thread.start()
    try:
        return run()
    finally:
        thread.join()


@pytest.mark.load_sensitive
def test_a_sibling_write_into_the_tree_turns_the_sweep_red_on_that_tree(tmp_path):
    """The instrument: handed a tree a sibling writes into, the sweep is red (it must be)."""
    started = tmp_path / "started"
    live = estate(
        tmp_path,
        {"slow": _slow_probe(started)},
        {"proven_by": {"slow": "checks/check_slow.py --probe: red on a violation"}},
    )
    got = _sibling_writes_while(live, started, lambda: run_gate(live))
    assert got.returncode == 1 and "sibling-artifact.txt" in got.stderr, got.stdout + got.stderr


@pytest.mark.load_sensitive
def test_a_sibling_write_into_the_live_tree_does_not_reach_the_sweep_of_its_snapshot(tmp_path):
    """The fix: the same sibling write, into the tree the snapshot was taken from, is not seen."""
    started = tmp_path / "started"
    live = estate(
        tmp_path,
        {"slow": _slow_probe(started)},
        {"proven_by": {"slow": "checks/check_slow.py --probe: red on a violation"}},
    )
    copy = snapshot(live, tmp_path / "copy")
    got = _sibling_writes_while(live, started, lambda: run_gate(copy))
    assert got.returncode == 0, got.stdout + got.stderr
    assert not (copy / "sibling-artifact.txt").exists()


def test_this_checkouts_snapshot_holds_what_a_copy_does_committed(tmp_path):
    copy = this_snapshot(tmp_path / "copy")
    sys.path.insert(0, str(ROOT / "checks"))
    from _gitutil import tree_files

    gone = git(ROOT, "ls-files", "--deleted").splitlines()
    assert set(tree_files(str(copy))) == set(tree_files(str(ROOT))) - set(gone)
    changed = git(copy, "status", "--porcelain")
    assert changed == "", f"the sweep's tree starts with changes: {changed}"
    assert (copy / "tests" / "calib_snapshot.py").read_bytes() == (
        ROOT / "tests" / "calib_snapshot.py"
    ).read_bytes()
