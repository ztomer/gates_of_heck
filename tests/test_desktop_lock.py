"""desktop_lock — Python half: record-format contract + deadlock defences.

The bash self-test (lib/desktop_lock/test_desktop_lock.sh) exercises the shell
half against the REAL machine-wide lock. These tests exercise the Python half
against a sandboxed LOCK_DIR, so the whole suite can run anywhere without
contending with a capture run that happens to be mid-flight on this Mac.

The three tests under "the fixes that were only ever in one copy" existed for a
reason worth keeping in the shape they have. Each one was CALIBRATED by reverting
the fix it covers and watching it go red — because the alternative is a suite
that says "acquire cannot spin" and would have passed just as happily against the
code that did. Where the pre-fix behaviour was a HANG rather than a wrong value
(the unbounded reclaim spin), the assertion counts attempts instead of trusting a
wall clock, so a regression is a fast red here and not a stuck gate on someone
else's machine.
"""

import importlib.util
import os
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
            ["ps", "-o", "lstart=", "-p", pid], capture_output=True, text=True, check=True
        ).stdout
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
    (lock_dir / "owner").write_text("999999999\nThu Jan  1 00:00:00 1970\nsomeone else entirely\n")
    code = (
        "import importlib.util,sys;"
        f"spec=importlib.util.spec_from_file_location('dl',{str(MODULE)!r});"
        "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);"
        f"m.LOCK_DIR={str(lock_dir)!r};m.release()"
    )
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)
    assert (lock_dir / "owner").exists(), "a foreign process freed our lock"


# ---- deadlock defences ------------------------------------------------------


def test_stale_lock_no_live_owner_is_reclaimed_immediately(lock):
    lock_dir = Path(lock.LOCK_DIR)
    lock_dir.mkdir()
    (lock_dir / "owner").write_text(
        "999999999\nThu Jan  1 00:00:00 1970\ndead run (pid 999999999)\n"
    )
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
        f"{mine}\nThu Jan  1 00:00:00 1970\nrecycled pid (pid {mine})\n"
    )
    lock.acquire("after reuse", timeout=5, log=lambda *_: None)


def test_live_unwedged_peer_raises_desktop_busy(lock):
    """A genuinely alive owner with matching start time must NOT be reclaimed:
    acquire raises DesktopBusy once the timeout passes."""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        lock_dir = Path(lock.LOCK_DIR)
        lock_dir.mkdir()
        start_time = lock._start_time(child.pid)
        assert start_time, "setup: could not read the child's real start time"
        (lock_dir / "owner").write_text(f"{child.pid}\n{start_time}\nlive peer (pid {child.pid})\n")
        with pytest.raises(lock.DesktopBusy):
            lock.acquire("intruder", timeout=2, log=lambda *_: None)
        # ...and the intruder must not have deleted the live peer's lock.
        assert (lock_dir / "owner").exists()
    finally:
        child.kill()
        child.wait()


def test_wedged_but_alive_owner_reclaimed_on_age_ceiling(lock):
    """Liveness says busy forever; only MAX_HOLD frees a wedged owner."""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        lock_dir = Path(lock.LOCK_DIR)
        lock_dir.mkdir()
        start_time = lock._start_time(child.pid)
        (lock_dir / "owner").write_text(f"{child.pid}\n{start_time}\nwedged (pid {child.pid})\n")
        ancient = time.time() - 10_000
        os.utime(lock_dir, (ancient, ancient))
        lock.acquire("after wedge", timeout=5, max_hold=900, log=lambda *_: None)
        assert (Path(lock.LOCK_DIR) / "owner").read_text().splitlines()[0] == str(os.getpid())
    finally:
        child.kill()
        child.wait()


# ---- the fixes that were only ever in one copy --------------------------------
#
# A near-identical copy of this module lived in ~/Projects/scripts/lib until
# 2026-10-04. Three real bugs were fixed there and not here, so the half other
# repos actually imported was the broken one. The copy is gone; these are the
# assertions that keep its fixes alive here, and each names what it pins.


def test_force_remove_clears_a_lock_holding_more_than_its_owner_record(lock):
    """BUG 1. Clearing the lock used to mean "unlink owner, then rmdir".

    That pair only empties a directory holding exactly an `owner` file -- which
    is what the author assumed and what the old tests exercised. A `.DS_Store`
    the Finder drops in, or a half-written extra file, left the directory in
    place, so the reclaim was a no-op, the next os.mkdir failed again, and
    acquire() printed "stale desktop lock" forever. The bash half's `rm -rf`
    could not get into that state.
    """
    lock_dir = Path(lock.LOCK_DIR)
    lock_dir.mkdir()
    (lock_dir / "owner").write_text("999999999\nThu Jan  1 00:00:00 1970\ndead\n")
    (lock_dir / ".DS_Store").write_bytes(b"\x00\x01\x02")
    (lock_dir / "half-written.tmp").write_text("x")

    assert lock._force_remove() is True, "reclaim reported failure but did not report it"
    assert not lock_dir.exists(), "the lock directory survived a reclaim"


def test_acquire_refuses_to_spin_when_a_reclaim_does_not_clear(lock, monkeypatch):
    """BUG 1, second half. The pre-fix loop was `reclaim; continue` with nothing
    checking whether the reclaim worked, so an unremovable directory meant a
    silent infinite spin rather than a report.

    The assertion counts mkdir attempts instead of leaning on a wall clock: a
    timeout-based test cannot tell "did not spin" from "this suite is stuck", and
    the whole point of the fix is that the caller is told instead.
    """
    lock_dir = Path(lock.LOCK_DIR)
    lock_dir.mkdir()
    (lock_dir / "owner").write_text("999999999\nThu Jan  1 00:00:00 1970\ndead\n")
    monkeypatch.setattr(lock, "_force_remove", lambda: False)

    real_mkdir = os.mkdir
    attempts: list[str] = []

    def counting_mkdir(path, *args, **kwargs):
        attempts.append(str(path))
        if len(attempts) > 3:
            raise AssertionError(
                f"acquire() retried the reclaim {len(attempts)} times -- it is spinning"
            )
        return real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(lock.os, "mkdir", counting_mkdir)
    with pytest.raises(RuntimeError, match="refusing to spin"):
        lock.acquire("spin probe", timeout=1, log=lambda *_: None)
    assert len(attempts) == 1, "a failed reclaim must not be retried"


def test_release_without_acquire_leaves_an_unreadable_owner_lock_alone(lock):
    """BUG 2. release() used to delete a lock whose owner record it could not
    read, on the reasoning that "no readable owner" meant "no owner, so removing
    it is safe". It does not: a process that never took the lock frees the one it
    was WAITING for, straight out of the mkdir-to-write window or a peer
    mid-reclaim. This is the bash half's DESKTOP_LOCK_HELD, ported."""
    lock_dir = Path(lock.LOCK_DIR)
    lock_dir.mkdir()  # deliberately no owner file: the unreadable case
    lock.release()
    assert lock_dir.exists(), "a process that never held the lock freed it"
    assert not lock._HELD


def test_truncated_owner_record_with_a_live_pid_is_not_stale(lock):
    """BUG 3. The bash half has only ever REQUIRED field 1, so a record whose
    start time or label line never got written read as HELD there. This half
    required three lines and read the same record as STALE -- which means it
    deleted a lock a live peer was sitting in, the one outcome the lock exists to
    prevent. This process is alive and owns the recorded number, so the only
    honest reading is "held", bounded by the age ceiling."""
    lock_dir = Path(lock.LOCK_DIR)
    lock_dir.mkdir()
    (lock_dir / "owner").write_text(f"{os.getpid()}\n")

    with pytest.raises(lock.DesktopBusy):
        lock.acquire("intruder", timeout=1, log=lambda *_: None)
    assert (lock_dir / "owner").read_text() == f"{os.getpid()}\n", "a live peer's lock was taken"


def test_owner_label_falls_back_when_the_label_line_is_missing(lock):
    """The other half of the same tolerance. A record with a blank third line
    used to be printed as an empty name in two of the three messages that quote
    the owner; the bash half's fallback is the string `an unknown run`."""
    lock_dir = Path(lock.LOCK_DIR)
    lock_dir.mkdir()
    (lock_dir / "owner").write_text(f"{os.getpid()}\nThu Jan  1 00:00:00 1970\n")
    assert lock._owner_label() == "an unknown run"
    (lock_dir / "owner").write_text(f"{os.getpid()}\nThu Jan  1 00:00:00 1970\na real run\n")
    assert lock._owner_label() == "a real run"


def test_timeout_and_max_hold_come_from_the_environment(lock, monkeypatch):
    """The bash half read `${DESKTOP_LOCK_TIMEOUT:-300}` at source time. This one
    took its defaults as function arguments, so the env seam did not exist here at
    all -- and the seam is what lets a test state a bound. Measured: an intruder
    handed DESKTOP_LOCK_TIMEOUT=2 waited 300s, which silently changed what "a
    second acquire is refused" meant."""
    monkeypatch.setenv("DESKTOP_LOCK_TIMEOUT", "2")
    monkeypatch.setenv("DESKTOP_LOCK_MAX_HOLD", "7")
    assert lock._timeout() == 2
    assert lock._max_hold() == 7

    # Per-call arguments still win, which is the other half of the precedence.
    monkeypatch.delenv("DESKTOP_LOCK_TIMEOUT")
    assert lock._timeout() == lock.DEFAULT_TIMEOUT
    monkeypatch.setenv("DESKTOP_LOCK_TIMEOUT", "not-a-number")
    assert lock._timeout() == lock.DEFAULT_TIMEOUT, "a bad value must fall back, not raise"


def test_the_refusal_message_quotes_the_environments_bound(lock, monkeypatch):
    """The bound in the message is what a reader acts on, so it has to be the one
    that was actually waited -- asserted here rather than only in the shell suite,
    which takes the REAL machine-wide lock and so cannot run everywhere.

    THIS PROCESS is the live peer, not a spawned child. It is alive by
    construction, its recorded start time is therefore the one `_start_time`
    really returns, and the whole case costs no subprocess and no wall clock
    beyond the 1s bound itself. A child here would be a second thing that can be
    slow or flaky in a suite whose subject is a lock.
    """
    lock_dir = Path(lock.LOCK_DIR)
    lock_dir.mkdir()
    mine = os.getpid()
    start_time = lock._start_time(mine)
    assert start_time, "setup: could not read our own start time"
    (lock_dir / "owner").write_text(f"{mine}\n{start_time}\nlive peer\n")
    monkeypatch.setenv("DESKTOP_LOCK_TIMEOUT", "1")
    with pytest.raises(lock.DesktopBusy, match=r"after 1s"):
        lock.acquire("intruder", log=lambda *_: None)
    assert (lock_dir / "owner").read_text() == f"{mine}\n{start_time}\nlive peer\n", (
        "the refused intruder deleted a live peer's lock"
    )
