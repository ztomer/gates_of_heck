"""desktop_lock — Python half: record-format contract + deadlock defences.

The bash self-test (lib/desktop_lock/test_desktop_lock.sh) exercises the shell
half against the REAL machine-wide lock. These tests exercise the Python half
against a sandboxed LOCK_DIR, so the whole suite can run anywhere without
contending with a capture run that happens to be mid-flight on this Mac.
"""

import importlib.util
import subprocess
import sys
import time
from pathlib import Path

import pytest

# Pinned to one xdist worker: these tests take the REAL machine-wide desktop
# mutex (with timeouts), so splitting them across workers would make them
# contend with themselves and flake. Own group, so they still parallelize
# against everything else.
pytestmark = pytest.mark.xdist_group("desk")

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE = REPO_ROOT / "lib" / "desktop_lock" / "desktop_lock.py"


@pytest.fixture
def lock(tmp_path):
    """The module with LOCK_DIR pointed at a throwaway directory."""
    spec = importlib.util.spec_from_file_location("desktop_lock", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.LOCK_DIR = str(tmp_path / "mac-desktop-ui.lock")
    return mod


def _owner_file(lock) -> list[str]:
    return (Path(lock.LOCK_DIR) / "owner").read_text().splitlines()


# ---- record format contract -------------------------------------------------


def test_owner_file_is_three_lines_pid_start_label(lock):
    with lock.desktop_lock("contract probe", log=lambda *_: None):
        lines = _owner_file(lock)
        assert len(lines) == 3
        pid = str(__import__("os").getpid())
        assert lines[0] == pid
        # The start time must be the whitespace-normalised ps lstart string --
        # byte-identical to what the bash half records for the same process.
        raw = subprocess.run(
            ["ps", "-o", "lstart=", "-p", pid],
            capture_output=True, text=True, check=True).stdout
        assert lines[1] == " ".join(raw.split()) != ""
        assert lines[2] == f"contract probe (pid {pid})"


# ---- release semantics ------------------------------------------------------


def test_release_removes_the_lock(lock):
    with lock.desktop_lock("mine", log=lambda *_: None):
        assert Path(lock.LOCK_DIR).is_dir()
    assert not Path(lock.LOCK_DIR).exists()


def test_forged_peer_lock_survives_foreign_release(tmp_path):
    """release() must check ownership: a run that gave up WAITING must not
    delete the lock of the peer it waited for. Simulate by running release()
    in a child process whose PID cannot match the forged owner."""
    spec = importlib.util.spec_from_file_location("desktop_lock", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.LOCK_DIR = str(tmp_path / "mac-desktop-ui.lock")
    lock_dir = Path(mod.LOCK_DIR)
    lock_dir.mkdir()
    (lock_dir / "owner").write_text(
        "999999999\nThu Jan  1 00:00:00 1970\nsomeone else entirely\n")
    code = (
        "import importlib.util,sys;"
        f"spec=importlib.util.spec_from_file_location('dl',{str(MODULE)!r});"
        "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);"
        f"m.LOCK_DIR={str(lock_dir)!r};m.release()"
    )
    subprocess.run([sys.executable, "-c", code], check=True,
                   capture_output=True, text=True)
    assert (lock_dir / "owner").exists(), "a foreign process freed our lock"


# ---- deadlock defences ------------------------------------------------------


def test_stale_lock_no_live_owner_is_reclaimed_immediately(lock):
    lock_dir = Path(lock.LOCK_DIR)
    lock_dir.mkdir()
    (lock_dir / "owner").write_text(
        "999999999\nThu Jan  1 00:00:00 1970\ndead run (pid 999999999)\n")
    start = time.monotonic()
    lock.acquire("after stale", timeout=5, log=lambda *_: None)
    assert time.monotonic() - start < 2, "stale reclaim should not wait out the timeout"


def test_recycled_pid_does_not_impersonate_the_owner(lock):
    """A LIVE process wearing the recorded PID but with a different start time
    is an impostor; its lock must be reclaimed, not waited on."""
    lock_dir = Path(lock.LOCK_DIR)
    lock_dir.mkdir()
    mine = str(__import__("os").getpid())
    (lock_dir / "owner").write_text(
        f"{mine}\nThu Jan  1 00:00:00 1970\nrecycled pid (pid {mine})\n")
    lock.acquire("after reuse", timeout=5, log=lambda *_: None)


def test_live_unwedged_peer_raises_desktop_busy(lock):
    """A genuinely alive owner with matching start time must NOT be reclaimed:
    acquire raises DesktopBusy once the timeout passes."""
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        lock_dir = Path(lock.LOCK_DIR)
        lock_dir.mkdir()
        start_time = lock._start_time(child.pid)
        assert start_time, "setup: could not read the child's real start time"
        (lock_dir / "owner").write_text(
            f"{child.pid}\n{start_time}\nlive peer (pid {child.pid})\n")
        with pytest.raises(lock.DesktopBusy):
            lock.acquire("intruder", timeout=2, log=lambda *_: None)
        # ...and the intruder must not have deleted the live peer's lock.
        assert (lock_dir / "owner").exists()
    finally:
        child.kill()
        child.wait()


def test_wedged_but_alive_owner_reclaimed_on_age_ceiling(lock):
    """Liveness says busy forever; only MAX_HOLD frees a wedged owner."""
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        lock_dir = Path(lock.LOCK_DIR)
        lock_dir.mkdir()
        start_time = lock._start_time(child.pid)
        (lock_dir / "owner").write_text(
            f"{child.pid}\n{start_time}\nwedged (pid {child.pid})\n")
        ancient = time.time() - 10_000
        import os
        os.utime(lock_dir, (ancient, ancient))
        lock.acquire("after wedge", timeout=5, max_hold=900,
                     log=lambda *_: None)
        assert (Path(lock.LOCK_DIR) / "owner").read_text().splitlines()[0] \
            == str(__import__("os").getpid())
    finally:
        child.kill()
        child.wait()
