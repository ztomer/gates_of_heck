"""A suite run by hand is admitted where every gate is (GOH.ADMITSUITE, after GOH.ONEADMISSION).

tools/pytest.sh outside a gate asked only lib/mem_admit.py: a hand-run suite (up to 12 xdist
workers) started beside two push gates holding both gate slots, so the slots counted gates while
the CPU they guard went to whoever did not ask. It now comes in by the one entry (lib/admission.py
`admit` through lib/gate_slot.sh: memory, then a slot), BOUNDED (GOH_SUITE_WAIT_MAX, default 900 s,
the memory wait counted inside it) and refused with exit 75 naming the holders when the bound
passes, so a wait never looks like a hang. A whole run takes the long lane (`work`, behind every
push and land); a narrowed run (a path argument) takes the short lane, the commit gate's, so a
red/green loop on one file waits behind commit gates only. The stub interpreter runs the REAL
admission (`python -S`/`-c`: the slot helper, the lock order, the share) and records pytest's run.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import gate_slot  # lib/: the semaphore a push gate takes
import pytest
from conftest import REPO_ROOT
from timing_bounds import GiveUp, patience

RUNNER = REPO_ROOT / "tools" / "pytest.sh"


def _stub(tmp_path: Path) -> tuple[Path, Path, Path]:
    """The interpreter seam: the admission's helpers run for real; pytest's run is recorded, and
    blocks while `<tmp>/block` exists (so a test can hold an admitted suite open)."""
    seen, block = tmp_path / "seen", tmp_path / "block"
    stub = tmp_path / "python3"
    stub.write_text(
        "#!/bin/bash\n"
        f'case "$1" in -S | -c) exec "{sys.executable}" "$@" ;; esac\n'
        'if [ "$1 $2" != "-m pytest" ] || [ "$3" = --version ]; then exit 0; fi\n'
        f'echo ran >> "{seen}"\n'
        f'while [ -e "{block}" ]; do sleep 0.05; done\n'
    )
    stub.chmod(0o755)
    return stub, seen, block


def _env(tmp_path: Path, stub: Path, **extra: str) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("GATES_OF_HECK_SUITE_RUNNER", "GATES_OF_HECK_SUITE_NESTED_OK", "GOH_GATE_SLOT")
        and not k.startswith("GOH_HELD")
    }
    env.update(
        GOH_PY=str(stub),
        GOH_PY_OK=str(stub),
        GATES_OF_HECK_SUITE_LOCK=str(tmp_path / "goh-suite.lock"),
        GOH_SUITE_CORES="2",
        GOH_GATE_SLOTS="1",
        GOH_GATE_SLOT_DIR=str(tmp_path / "slots"),
        **extra,
    )
    return env


@contextlib.contextmanager
def _push_gate(tmp_path: Path, env: dict[str, str]) -> Iterator[subprocess.Popen]:
    """A push gate holding the one long slot, through the shell's own acquire (class ship); ended
    (its group TERMed, then reaped) when the block exits, whatever the test did."""
    ready = tmp_path / "gate-ready"
    script = (
        f'. "{REPO_ROOT}/tui/lib.sh"; . "{REPO_ROOT}/lib/gate_slot.sh"; '
        f'gate_slot_acquire push-gate ship; touch "{ready}"; sleep 120'
    )
    genv = dict(env, GOH_PY=sys.executable, GOH_PY_OK=sys.executable)
    with subprocess.Popen(["bash", "-c", script], env=genv, stdin=subprocess.DEVNULL,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          start_new_session=True) as gate:  # fmt: skip
        try:
            give_up = GiveUp(20)
            while not ready.exists():
                assert gate.poll() is None, "the push gate died before it held its slot"
                assert not give_up.passed(), "the push gate never took its slot"
                time.sleep(0.05)
            yield gate
        finally:
            _end(gate)


def _end(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=patience(20))


@contextlib.contextmanager
def _suite(env: dict[str, str], *args: str) -> Iterator[subprocess.Popen]:
    """tools/pytest.sh run by hand; its group ended and reaped when the block exits."""
    with subprocess.Popen(["bash", str(RUNNER), *args], env=env, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          start_new_session=True) as suite:  # fmt: skip
        try:
            yield suite
        finally:
            _end(suite)


@pytest.mark.load_sensitive
def test_a_hand_run_suite_waits_for_the_push_gate_and_is_refused_at_its_bound(
    tmp_path: Path,
) -> None:
    stub, seen, _ = _stub(tmp_path)
    env = _env(tmp_path, stub, GOH_SUITE_WAIT_MAX="1")
    with _push_gate(tmp_path, env), _suite(env) as done:
        out, errs = done.communicate(timeout=patience(60))
    assert not seen.exists(), "the suite ran beside the push gate holding the only slot"
    assert done.returncode == 75, out + errs
    assert "push-gate" in errs and "GOH_SUITE_WAIT_MAX" in errs, errs


@pytest.mark.load_sensitive
def test_the_suite_starts_when_the_gate_ends_and_holds_the_slot_against_the_next(
    tmp_path: Path,
) -> None:
    stub, seen, block = _stub(tmp_path)
    env = _env(tmp_path, stub, GOH_SUITE_WAIT_MAX="120")
    block.touch()
    with contextlib.ExitStack() as stack:
        stack.callback(block.unlink, missing_ok=True)  # never a stub left blocking
        gate = stack.enter_context(_push_gate(tmp_path, env))
        suite = stack.enter_context(_suite(env))
        # The suite is at its slot wait once its ticket is in the long lane's queue (gate_lanes).
        waiting, give_up = tmp_path / "slots" / "waiting", GiveUp(30)
        while not (waiting.is_dir() and any(p.name.startswith("long-") for p in waiting.iterdir())):
            assert suite.poll() is None, suite.communicate()
            assert not give_up.passed(), "the suite never queued for the push gate's slot"
            time.sleep(0.05)
        assert not seen.exists(), "the suite did not wait for the push gate's slot"
        _end(gate)  # the push gate ends: the suite takes its slot
        give_up = GiveUp(30)
        while not seen.exists():
            assert suite.poll() is None, suite.communicate()
            assert not give_up.passed(), "the suite never started after the slot was freed"
            time.sleep(0.05)
        # ...and the next push gate now waits for the SUITE (the same one admission).
        slots = str(tmp_path / "slots")
        assert gate_slot.acquire(slots, 1, 0.05, os.getpid(), max_wait=0.5, rank=0) is None
        block.unlink()
        out, errs = suite.communicate(timeout=patience(60))
        assert suite.returncode == 0, out + errs
        got = gate_slot.acquire(slots, 1, 0.05, os.getpid(), max_wait=patience(10), rank=0)
        assert got is not None, "the suite's slot was not freed when it ended"
        os.close(got[1])


@pytest.mark.load_sensitive
def test_a_narrowed_run_takes_the_short_lane_not_the_push_gates(tmp_path: Path) -> None:
    stub, seen, _ = _stub(tmp_path)
    env = _env(tmp_path, stub, GOH_SUITE_WAIT_MAX="1")
    with _push_gate(tmp_path, env):
        with _suite(env, "tests/test_suite_lock.py") as done:
            out, errs = done.communicate(timeout=patience(60))
        assert done.returncode == 0 and seen.exists(), out + errs
        # A commit gate holding the short slot: the narrowed run waits for IT, bounded.
        short = gate_slot.acquire(str(tmp_path / "slots"), 1, 0.05, os.getpid(), lane="short")
        assert short is not None
        try:
            seen.unlink()
            with _suite(env, "tests/test_suite_lock.py") as done:
                out, errs = done.communicate(timeout=patience(60))
        finally:
            os.close(short[1])
        assert done.returncode == 75 and not seen.exists(), out + errs
