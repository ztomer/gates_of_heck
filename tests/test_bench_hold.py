"""lib/bench_hold.py: a Python block holds the host's bench lock, and lets it go however it ends.

A census longer than the bench lock's 900 s cap holds the host a piece at a time (ZoneWM's set 16
time cells aside on 2026-10-11 under other sessions' gates, holding nothing). Each test runs
against a private lock dir and real processes: a gate that starts during the block waits for it,
and a caller that is SIGKILLed mid-block releases.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import REPO_ROOT
from timing_bounds import GiveUp

sys.path.insert(0, str(REPO_ROOT / "lib"))
import bench_hold  # noqa: E402

LIB = REPO_ROOT / "lib" / "bench_lock.sh"


@pytest.fixture
def lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "lock"
    for k in [k for k in os.environ if k.startswith("GOH_BENCH_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("GOH_BENCH_LOCK_DIR", str(path))
    return path


def _until(cond, seconds: float = 10) -> None:
    give_up = GiveUp(seconds)
    while not cond():
        assert not give_up.passed(), "never happened"
        time.sleep(0.05)


def test_the_block_holds_the_host_under_its_label_and_lets_go_after(lock: Path) -> None:
    with bench_hold.held("census: window drag", hold_s=60) as hold:
        owner = (lock / "exclusive" / "owner").read_text()
        assert "census: window drag" in owner, owner
    assert not (lock / "exclusive").exists()
    assert hold.void is False


def test_a_gate_started_during_the_block_waits_for_it_and_names_it(
    lock: Path, tmp_path: Path
) -> None:
    ran, go = tmp_path / "ran", tmp_path / "go"  # the gate starts once the block holds the host
    with subprocess.Popen(
        ["bash", "-c", f'. "{LIB}"; until [ -e "{go}" ]; do sleep 0.05; done; bench_lock_join "py gate"; : > "{ran}"'],
        stderr=subprocess.PIPE, text=True,
    ) as gate:  # fmt: skip
        with bench_hold.held("census: theme", hold_s=60):
            go.touch()
            time.sleep(2.5)
            inside = ran.exists()
        gate.wait(timeout=20)
        said = gate.stderr.read() if gate.stderr else ""
    assert not inside, "a gate ran inside the block"
    assert ran.exists()
    assert "census: theme" in said, said


def test_a_held_host_is_refused_by_name_before_the_block_runs(lock: Path, tmp_path: Path) -> None:
    ready = tmp_path / "ready"
    peer = subprocess.Popen(
        ["bash", "-c", f'. "{LIB}"; bench_lock_exclusive "a peer measurement" && : > "{ready}" && exec sleep 60'],
    )  # fmt: skip
    try:
        _until(ready.exists)
        entered = False
        with pytest.raises(bench_hold.BenchBusy, match="a peer measurement"):
            with bench_hold.held("census", hold_s=60, claim_s=1, drain_s=1):
                entered = True
        assert not entered
        assert "a peer measurement" in (lock / "exclusive" / "owner").read_text()
    finally:
        peer.kill()
        peer.wait()


def test_a_hold_past_the_cap_is_refused_and_leaves_no_claim(
    lock: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GOH_BENCH_MAX_HOLD", "30")
    with pytest.raises(bench_hold.BenchBusy, match="past the cap"):
        with bench_hold.held("census", hold_s=60):
            pass
    assert not (lock / "exclusive").exists()


def test_a_caller_killed_mid_block_releases_the_host(lock: Path, tmp_path: Path) -> None:
    inside = tmp_path / "inside"
    script = (
        f"import sys, time; sys.path.insert(0, {str(REPO_ROOT / 'lib')!r}); import bench_hold\n"
        f"with bench_hold.held('doomed', hold_s=60):\n"
        f"    open({str(inside)!r}, 'w').close(); time.sleep(60)\n"
    )
    with subprocess.Popen([sys.executable, "-c", script]) as caller:
        try:
            _until(inside.exists)
            held_then = (lock / "exclusive").exists()
        finally:
            caller.kill()
            caller.wait()
    assert held_then
    _until(lambda: not (lock / "exclusive").exists())


def test_a_claim_voided_inside_the_block_is_reported_and_not_released_twice(lock: Path) -> None:
    with bench_hold.held("census", hold_s=60) as hold:
        # what a waiter does to an overdue hold: void it, and take the host itself
        subprocess.run(["rm", "-rf", str(lock / "exclusive")], check=True)
        (lock / "exclusive").mkdir()
        (lock / "exclusive" / "owner").write_text("1\nx\n0\n900\nthe next measurement\n")
    assert hold.void is True
    assert (lock / "exclusive" / "owner").read_text().endswith("the next measurement\n")
