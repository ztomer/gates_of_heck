"""tree_lock.sh — one gate per build tree at a time.

The incident (ZoneWM, 2026-09-21): two pre-push gates on one checkout, the
second's cold wipe landed during the first's test phase, four test bundles
vanished under dlopen, the push went red naming the tests. These tests run the
real shell library against a throwaway repo: a second acquirer BLOCKS until the
first exits, and a holder that is SIGKILLed releases the lock at once.
"""

import os
import signal
import subprocess
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB = REPO_ROOT / "lib" / "tree_lock.sh"
TUI = REPO_ROOT / "tui" / "lib.sh"

# A holder: takes the lock, reports, then sleeps until killed or done. The token
# is LOCKED, not "held": the wait message says "is held by", and a needle that
# matches the peer's announcement reads a blocked waiter as a holder.
HOLD = """
. "{tui}"; . "{lib}"
tree_lock_acquire "{repo}" "holder"
echo LOCKED; sleep {secs}; echo released
"""


def _spawn(repo: Path, secs: float) -> subprocess.Popen:
    script = HOLD.format(tui=TUI, lib=LIB, repo=repo, secs=secs)
    # Its own process group, so a test can kill the gate AND its children the
    # way an operator would (`kill -9 -<pgid>`); see the orphan test for why
    # killing the shell alone is not a release.
    return subprocess.Popen(["bash", "-c", script], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            start_new_session=True)


def _wait_line(proc: subprocess.Popen, needle: str, timeout: float) -> str:
    deadline = time.time() + timeout
    out = ""
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        out += line
        if needle in line:
            return out
    raise AssertionError(f"{needle!r} not seen within {timeout}s; got: {out!r}")


def test_second_gate_waits_for_the_first(tmp_path):
    first = _spawn(tmp_path, 2.0)
    _wait_line(first, "LOCKED", 10)
    t0 = time.time()
    second = _spawn(tmp_path, 0)
    out = _wait_line(second, "LOCKED", 15)
    assert time.time() - t0 >= 1.5, "the second gate did not wait for the first"
    assert "waiting" in out, out            # it announced the wait, naming a peer
    assert "holder (pid" in out, out        # ... by the identity the holder wrote
    first.wait(10)
    second.wait(10)
    assert (first.returncode, second.returncode) == (0, 0)


def test_a_killed_holder_releases_at_once(tmp_path):
    """SIGKILL runs no trap; the kernel drops the flock with the last fd."""
    first = _spawn(tmp_path, 60)
    _wait_line(first, "LOCKED", 10)
    second = _spawn(tmp_path, 0)
    time.sleep(1.0)                          # long enough to be blocked, not done
    assert second.poll() is None, "the second gate did not block behind a live holder"
    os.killpg(first.pid, signal.SIGKILL)     # the gate and its `sleep`
    t0 = time.time()
    _wait_line(second, "LOCKED", 10)
    assert time.time() - t0 < 5, "a SIGKILLed holder did not release the lock"
    second.wait(10)
    assert second.returncode == 0


def test_an_orphaned_child_still_using_the_tree_keeps_it(tmp_path):
    """Kill the gate's SHELL only and its child (`swift test`, here `sleep`)
    inherits fd 9 and keeps the lock until it exits. That is the property, not
    a leak: the tree is in use exactly as long as something the gate started
    is running in it, and a waiter that wiped `.build` under an orphaned
    `swift test` would reproduce the incident with one more process in the
    story. Measured 2026-09-21: `lsof` shows the orphan holding `9w`."""
    first = _spawn(tmp_path, 3)
    _wait_line(first, "LOCKED", 10)
    # LOCKED is echoed before `sleep` forks; kill only once the child exists,
    # or the test measures the fork race instead of the inherited fd.
    for _ in range(50):
        if subprocess.run(["pgrep", "-P", str(first.pid)], capture_output=True).returncode == 0:
            break
        time.sleep(0.1)
    else:
        raise AssertionError("the holder never forked its child")
    os.kill(first.pid, signal.SIGKILL)       # the shell only; `sleep 3` lives on
    t0 = time.time()
    second = _spawn(tmp_path, 0)
    _wait_line(second, "LOCKED", 15)
    assert time.time() - t0 >= 2.0, "the orphaned child's hold on the tree was not honoured"
    second.wait(10)


def test_the_lock_file_is_never_removed(tmp_path):
    """Removing the file under a holder would hand the next caller a fresh inode."""
    proc = _spawn(tmp_path, 0)
    proc.wait(10)
    assert (tmp_path / ".goh-tree.lock").exists()
    assert "holder (pid" in (tmp_path / ".goh-tree.lock").read_text()


def test_the_swift_gate_takes_it():
    """The gate that wipes the tree is the one that must hold the lock."""
    gate = (REPO_ROOT / "gates" / "swift_gate.sh").read_text()
    wipe = gate.index('rm -rf .build')
    assert 'tree_lock_acquire "$PWD"' in gate[:wipe], "the swift gate wipes .build before locking it"
