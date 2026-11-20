"""A fake gate never outlives the pytest that started it (GOH.HOLDERORPHAN, 2026-11-19).

tests/test_land_prewait.py's push-holder was found PPID 1 and 74 minutes old: a pytest run was
interrupted, and the fake gate it had started in its own session waited on a release file nobody
would ever write. Every fake that waits on a file now gives up when its test process is gone and
after a bounded deadline (tests/orphan_kit.py), and the suite adopts it so the test's end, the
session's end and a SIGTERM of pytest all kill its process group.

The instrument here is the one that failed: a CHILD python starts the gate and is SIGKILLed, which
is what an interrupted pytest looks like to the gate (no finalizer runs). The gate and the slot
holder it spawned must both be gone within a bound.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import load_kit
import orphan_kit
import pid_owner  # lib/, on the path since conftest imported load_kit
import pytest
from timing_bounds import GiveUp, patience

TESTS = Path(__file__).resolve().parent

# The "pytest" that dies: it starts a Gate, says which pids that made, and sleeps until killed.
PARENT = """
import os, sys
sys.path.insert(0, {tests!r})
from pathlib import Path
from test_gate_slot import Gate, _env
tmp = Path(sys.argv[1])
gate = Gate(tmp, "orphan-holder", _env(tmp, 1))
gate.wait_for("TAKEN")
print(gate.proc.pid, flush=True)
import time
time.sleep(600)
"""


def _alive(pgid: int) -> bool:
    """Is any process left in this process group? (No `ps`: signal 0 asks the kernel.)"""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # macOS: EPERM for a group of zombies not yet reaped
        return False
    return True


@pytest.mark.load_sensitive
def test_a_gate_whose_pytest_was_killed_is_gone_soon(tmp_path):
    parent = subprocess.Popen(
        [sys.executable, "-c", PARENT.format(tests=str(TESTS)), str(tmp_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        start_new_session=True,
    )
    gate_pid, gate = 0, None
    try:
        line = parent.stdout.readline()
        assert line.strip().isdigit(), f"the stand-in pytest said {line!r}"
        gate_pid = int(line)
        assert _alive(gate_pid), "the gate was not running before the kill"
        gate = pid_owner.stamp(gate_pid)  # alive: the cleanup signals only this process's group
        os.kill(parent.pid, signal.SIGKILL)  # no finalizer, no atexit: an interrupted pytest
        parent.wait(timeout=patience(30))
        give_up = GiveUp(20)
        while _alive(gate_pid) and not give_up.passed():
            time.sleep(0.1)
        assert not _alive(gate_pid), "the fake gate (or its slot holder) outlived its pytest"
    finally:
        if not pid_owner.signal_group(gate, signal.SIGKILL):
            pid_owner.signal(gate, signal.SIGKILL)
        parent.stdout.close()
        parent.wait(timeout=patience(30))


def _sh(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", script], stdin=subprocess.DEVNULL, timeout=patience(30), check=False
    )


@pytest.mark.load_sensitive
def test_the_wait_snippet_ends_on_a_missing_parent_and_on_its_deadline(tmp_path):
    release = tmp_path / "never"
    dead = subprocess.Popen(["true"], stdin=subprocess.DEVNULL)
    dead.wait(timeout=patience(30))
    gone = orphan_kit.wait_for_file(release, parent=dead.pid)
    got = _sh(gone)
    assert got.returncode == orphan_kit.EXIT_PARENT_GONE
    expired = orphan_kit.wait_for_file(release, seconds=0)
    got = _sh(expired)
    assert got.returncode == orphan_kit.EXIT_DEADLINE
    release.touch()
    ok = orphan_kit.wait_for_file(release)
    assert _sh(ok).returncode == 0


@pytest.mark.load_sensitive
def test_an_adopted_group_is_killed_when_the_test_ends(tmp_path):
    """The kit's reap takes the whole group, even after its leader is gone."""
    script = "sleep 300 & echo $! > pid; wait"
    with subprocess.Popen(
        ["bash", "-c", script],
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    ) as proc:
        orphan_kit.adopt(proc)
        assert load_kit.wait_until((tmp_path / "pid").exists)
        child = int((tmp_path / "pid").read_text())
        os.kill(proc.pid, signal.SIGKILL)  # the leader dies; the group's other member lives on
        proc.wait(timeout=patience(30))
        assert load_kit.alive(child)
        assert load_kit.reap() == []
        assert load_kit.wait_until(lambda: not load_kit.alive(child)), "the adopted group survived"


@pytest.mark.load_sensitive
def test_a_sigterm_of_pytest_reaps_the_adopted_groups(tmp_path):
    """SIGTERM skips pytest's finalizers: load_kit's handler reaps first, then lets it die."""
    code = (
        "import os, signal, subprocess, sys, time\n"
        f"sys.path.insert(0, {str(TESTS)!r})\n"
        "import load_kit, orphan_kit\n"
        "signal.signal(signal.SIGTERM, load_kit._reap_then_die)\n"
        "p = subprocess.Popen(['bash', '-c', 'sleep 300 & echo $! > ' + sys.argv[1] + '; wait'],\n"
        "                     stdin=subprocess.DEVNULL, start_new_session=True)\n"
        "orphan_kit.adopt(p)\n"
        "print('ready', flush=True)\n"
        "time.sleep(300)\n"
    )
    pidfile = tmp_path / "member"
    with subprocess.Popen(
        [sys.executable, "-c", code, str(pidfile)],
        stdout=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        start_new_session=True,
    ) as child:
        try:
            assert child.stdout.readline().strip() == "ready"
            assert load_kit.wait_until(pidfile.exists)
            member = int(pidfile.read_text())
            assert load_kit.alive(member)
            os.kill(child.pid, signal.SIGTERM)
            child.wait(timeout=patience(30))
            assert load_kit.wait_until(lambda: not load_kit.alive(member)), "the group survived"
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
            child.stdout.close()
