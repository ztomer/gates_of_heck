"""Gates SHARE the load slot (bounded); a spinner experiment still holds it alone (GOH.GATESLOT).

2026-11-11: a 0-spinner `load.py --wait 0 -- <gate>` took the slot as exclusively as a load
experiment, so every gate on the machine ran one at a time -- the queue ran five deep and stalled
~20 minutes behind one `git push` holder, and two agents hit their 90-minute stop waiting. The
ruling is docs/decisions/D-0003: a run with no spinners holds a SHARED place (at most
GOH_LOAD_GATES of them); a run with spinners holds the slot alone, and a waiting experiment is
not starved by gates that keep arriving (a gate arriving behind it waits for it).
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import load_kit
import pytest
import timing_bounds
from conftest import REPO_ROOT, hermetic_env

TOOL = REPO_ROOT / "tools" / "load.py"
pytestmark = pytest.mark.xdist_group("load_orphans")  # with tests/test_load_tool.py


def _env(**extra: str) -> dict[str, str]:
    return hermetic_env(**extra)  # no inherited GOH_LOAD_GATES


def _run(args: list[str], timeout: float = 60.0, **extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=timing_bounds.patience(timeout),
        env=_env(**extra),
        check=False,
    )  # fmt: skip


def _holding_gate(directory: Path, release: Path, tag: str, **extra: str) -> subprocess.Popen:
    """A 0-spinner run holding its place until `release` exists. The loop writes its pid to
    `<release>.pid`: a test that SIGKILLs the gate orphans the loop, and owns it (`_own_loop`)."""
    script = f"echo $$ >'{release}.pid'; until [ -e '{release}' ]; do sleep 0.05; done # {tag}"
    return subprocess.Popen(
        [sys.executable, str(TOOL), "--dir", str(directory), "0", "--", "/bin/sh", "-c", script],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=_env(**extra),
    )  # fmt: skip


def _shared(directory: Path) -> list[Path]:
    where = directory / "gates"
    return sorted(where.glob("*.json")) if where.is_dir() else []


def _until(predicate, seconds: float = 20.0) -> None:
    assert load_kit.wait_until(predicate, timing_bounds.patience(seconds)), "never happened"


def _own_loop(release: Path) -> None:
    """The suite owns the holding loop (load_kit.own_pid), so the kit reaps it at the test's end,
    PASS OR FAIL. A SIGKILLed `load.py` cannot wait for its child, the `release` touch alone raced
    pytest deleting tmp_path, and the loop spun at PID 1 on a path that no longer existed: one per
    run, 28 reaped by hand on 2026-11-12, the oldest 12 h old (GOH.COVPHASES)."""
    pidfile = Path(f"{release}.pid")
    _until(lambda: pidfile.is_file() and pidfile.read_text().strip().isdigit())
    load_kit.own_pid(int(pidfile.read_text().strip()))


def _end(proc: subprocess.Popen, release: Path) -> None:
    release.touch()
    try:
        proc.wait(timeout=timing_bounds.patience(30))
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_two_gates_run_together(tmp_path: Path) -> None:
    release = tmp_path / "release"
    first = _holding_gate(tmp_path, release, "gate-a")
    try:
        _until(lambda: len(_shared(tmp_path)) == 1)
        t0 = time.monotonic()
        second = _run(["--dir", str(tmp_path), "--wait-max", "5", "0", "--", "true"])
        took = time.monotonic() - t0
    finally:
        _end(first, release)
    assert second.returncode == 0, (second.returncode, second.stderr)
    assert "waiting for the slot" not in second.stderr + second.stdout
    assert took < timing_bounds.patience(4), took
    assert _shared(tmp_path) == [], "a finished gate left its place behind"


def test_the_gates_sharing_the_slot_are_bounded(tmp_path: Path) -> None:
    release = tmp_path / "release"
    first = _holding_gate(tmp_path, release, "gate-a", GOH_LOAD_GATES="1")
    try:
        _until(lambda: len(_shared(tmp_path)) == 1)
        late = _run(["--dir", str(tmp_path), "--wait-max", "1", "0", "--", "true"],
                    GOH_LOAD_GATES="1")  # fmt: skip
    finally:
        _end(first, release)
    assert late.returncode == 3, (late.returncode, late.stderr)
    assert "gate-a" in late.stderr and "GOH_LOAD_GATES" in late.stderr, late.stderr


def test_an_experiment_is_refused_beside_a_gate(tmp_path: Path) -> None:
    release = tmp_path / "release"
    first = _holding_gate(tmp_path, release, "gate-a")
    try:
        _until(lambda: len(_shared(tmp_path)) == 1)
        experiment = _run(["--dir", str(tmp_path), "--despite-gate", "1", "--", "true"])
    finally:
        _end(first, release)
    assert experiment.returncode == 3, (experiment.returncode, experiment.stderr)
    assert "gate-a" in experiment.stderr, experiment.stderr


def test_a_gate_waits_while_an_experiment_holds_the_slot(tmp_path: Path) -> None:
    with load_kit.owned(tmp_path, 1, ["/bin/sh", "-c", "sleep 30 # the-holder"]) as tool:
        tool.wait_for_spinners()
        late = _run(["--dir", str(tmp_path), "--wait-max", "1", "0", "--", "true"])
    assert late.returncode == 3, (late.returncode, late.stderr)
    assert "the-holder" in late.stderr, late.stderr


def test_a_waiting_experiment_is_not_starved_by_arriving_gates(tmp_path: Path) -> None:
    """The experiment waits for the gates ahead of it; a gate that arrives behind it waits for it,
    so a stream of gates can never hold the slot shared for ever."""
    release = tmp_path / "release"
    first = _holding_gate(tmp_path, release, "gate-a")
    experiment = None
    errs = ""
    try:
        _until(lambda: len(_shared(tmp_path)) == 1)
        experiment = subprocess.Popen(
            [sys.executable, str(TOOL), "--dir", str(tmp_path), "--despite-gate", "--wait",
             "--wait-max", "60", "1", "--", "/bin/sh", "-c", "exit 7"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=_env(),
        )  # fmt: skip
        _until((tmp_path / "pending.experiment").exists)
        arriving = _run(["--dir", str(tmp_path), "--wait-max", "1", "0", "--", "true"])
        assert arriving.returncode == 3, (arriving.returncode, arriving.stderr)
        assert "waiting" in arriving.stderr and "experiment" in arriving.stderr, arriving.stderr
    finally:
        _end(first, release)
        if experiment is not None:
            _, errs = experiment.communicate(timeout=timing_bounds.patience(60))
    assert experiment is not None and experiment.returncode == 7, (experiment.returncode, errs)
    assert not (tmp_path / "pending.experiment").exists(), "the experiment left its mark behind"


def test_a_dead_gates_place_is_pruned(tmp_path: Path) -> None:
    """A gate SIGKILLed before its cleanup leaves its file; the next claim does not count it."""
    release = tmp_path / "release"
    first = _holding_gate(tmp_path, release, "gate-a", GOH_LOAD_GATES="1")
    _own_loop(release)
    _until(lambda: len(_shared(tmp_path)) == 1)
    first.kill()
    first.wait()
    try:
        second = _run(["--dir", str(tmp_path), "--wait-max", "5", "0", "--", "true"],
                      GOH_LOAD_GATES="1")  # fmt: skip
    finally:
        release.touch()
    assert second.returncode == 0, (second.returncode, second.stderr)
    assert _shared(tmp_path) == []
