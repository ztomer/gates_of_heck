"""ONE admission for every gate on the machine (GOH.ONEADMISSION, docs/decisions/D-0008).

2026-11-16: a land waited 900 s on a load place (`tools/load.py 0 --`, D-0003's three) held by a
post-land run while gate slots sat partly free: a land's gate and a push gate were counted by two
controllers that did not know each other. Now `load.py 0` is admitted where every gate is, a gate
slot (lib/admission.py `admit`: memory, then a slot):

  * a `load.py 0` run waits for the slot a push gate (lib/gate_slot.sh) holds, and starts when it
    is freed -- the two callers that used different controllers contend for the one;
  * the gate it runs is inside its slot, so it takes no second one;
  * its wait is bounded by --wait-max and the expiry names the holder;
  * an experiment (spinners) holds off on it as on any gate in flight; a gate does not wait for an
    experiment (land and push over experiment).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import load_kit
import pytest
import timing_bounds
from conftest import REPO_ROOT, hermetic_env
from test_gate_slot import Gate

LOAD = REPO_ROOT / "tools" / "load.py"
pytestmark = pytest.mark.xdist_group("load_orphans")  # with tests/test_load_tool.py


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    """The test's own slots, load directory and no memory wait (a fixture gate's suite admitted)."""
    return hermetic_env(
        NO_COLOR="1", GOH_DIR=str(REPO_ROOT), GOH_MEM_ADMIT="0",
        GOH_GATE_SLOT_DIR=str(tmp_path / "slots"), GOH_LOAD_DIR=str(tmp_path / "load"), **extra,
    )  # fmt: skip


def _load(tmp_path: Path, args: list[str], **extra: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, str(LOAD), "--background-ok", "test", *args],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env=_env(tmp_path, **extra),
    )  # fmt: skip


def _until(predicate, seconds: float = 30.0) -> None:
    assert load_kit.wait_until(predicate, timing_bounds.patience(seconds)), "never happened"


def _push_gate(tmp_path: Path, name: str) -> Gate:
    gate = Gate(tmp_path, name, _env(tmp_path, GOH_GATE_SLOTS="1"))
    _until(lambda: "TAKEN" in gate.out() or gate.proc.poll() is not None)
    assert "TAKEN" in gate.out(), gate.out()
    return gate


def _end(gate: Gate) -> None:
    gate.release.touch()
    try:
        gate.proc.wait(timeout=timing_bounds.patience(30))
    finally:
        if gate.proc.poll() is None:
            gate.proc.kill()
            gate.proc.wait()


def test_a_hermetic_child_keeps_the_suites_memory_admission_off() -> None:
    """Every fixture land is admitted now, memory first: a scrub that dropped host_locks.py's
    GOH_MEM_ADMIT=0 sent it to the host's reading (a wait of up to 600 s while swapping)."""
    assert hermetic_env().get("GOH_MEM_ADMIT") == "0"


@pytest.mark.load_sensitive
def test_a_load_gate_waits_for_the_slot_a_push_gate_holds(tmp_path: Path) -> None:
    """One slot, held by a push gate: `load.py 0` does not run its command until it is freed."""
    ran = tmp_path / "ran"
    gate = _push_gate(tmp_path, "push-a")
    run = None
    try:
        run = _load(
            tmp_path, ["--wait-max", "60s", "0", "--", "touch", str(ran)], GOH_GATE_SLOTS="1"
        )
        _until((tmp_path / "slots").is_dir, 10)
        assert not load_kit.wait_until(ran.exists, 2.0), "ran beside the push gate's slot"
        assert run.poll() is None, run.communicate()[0]
    finally:
        _end(gate)
    out, _ = run.communicate(timeout=timing_bounds.patience(60))
    assert run.returncode == 0, out
    assert ran.exists(), out
    assert "gate slot 1/1 taken" in out, out


@pytest.mark.load_sensitive
def test_the_gate_a_load_run_starts_takes_no_second_slot(tmp_path: Path) -> None:
    inner = f'. "{REPO_ROOT}/tui/lib.sh"; . "{REPO_ROOT}/lib/gate_slot.sh"; gate_slot_acquire inner'
    run = _load(tmp_path, ["--wait-max", "20s", "0", "--", "bash", "-c", inner], GOH_GATE_SLOTS="1")
    out, _ = run.communicate(timeout=timing_bounds.patience(60))
    assert run.returncode == 0, out
    assert "inside a gate that holds slot-0" in out, out


@pytest.mark.load_sensitive
def test_a_load_gates_wait_is_bounded_and_names_the_holder(tmp_path: Path) -> None:
    gate = _push_gate(tmp_path, "push-holder")
    try:
        run = _load(tmp_path, ["--wait-max", "1s", "0", "--", "true"], GOH_GATE_SLOTS="1")
        out, _ = run.communicate(timeout=timing_bounds.patience(60))
    finally:
        _end(gate)
    assert run.returncode == 3, out
    assert "push-holder" in out and "--wait-max" in out, out


@pytest.mark.load_sensitive
def test_an_experiment_holds_off_beside_a_load_gate(tmp_path: Path) -> None:
    """A run with no spinners joins no bench lock; its held slot is what the experiment sees."""
    release, started = tmp_path / "release", tmp_path / "started"
    hold = f"touch '{started}'; until [ -e '{release}' ]; do sleep 0.05; done"
    holder = _load(tmp_path, ["0", "--", "/bin/sh", "-c", hold])
    try:
        _until(started.exists)  # its command runs: it was admitted
        experiment = _load(tmp_path, ["1", "--", "true"])
        out, _ = experiment.communicate(timeout=timing_bounds.patience(60))
    finally:
        release.touch()
        holder.communicate(timeout=timing_bounds.patience(30))
    assert experiment.returncode == 75, out
    assert "a gate slot" in out, out


@pytest.mark.load_sensitive
def test_a_gate_does_not_wait_for_an_experiment(tmp_path: Path) -> None:
    """Land and push over experiment: the experiment's slot is no gate's admission."""
    load = tmp_path / "load"
    load.mkdir()
    with load_kit.owned(load, 1, ["/bin/sh", "-c", "sleep 30 # the-holder"]) as tool:
        tool.wait_for_spinners()
        late = _load(tmp_path, ["--dir", str(load), "--wait-max", "1s", "0", "--", "true"])
        out, _ = late.communicate(timeout=timing_bounds.patience(60))
    assert late.returncode == 0, out
