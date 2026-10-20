"""The gate slot holder's OWNER WATCH -- the branch that had never run (v0.39.0, 2026-10-20).

`lib/gate_slot.py`'s holder watches the process it holds a slot for: a consumer's `tools/gate.sh`
is not the holder's child, so its end is a kernel event -- kqueue NOTE_EXIT on macOS, a pidfd on
Linux. The pidfd branch shipped in v0.39.0 and had NEVER RUN: this repo's suite is macOS, and
nothing in the estate ran on Linux. A branch that has never run is the one that is wrong, so this
file proves it on Linux -- in a container, on the branch the code takes -- and pins the two ways
it could be wrong that a macOS run cannot see:

  * a kernel WITHOUT pidfd (Linux before 5.3) answers ENOSYS, an OSError, not the
    ProcessLookupError that means "the owner is already gone". Unhandled, it killed the holder --
    and a holder that dies FREES the slot, so the machine-wide limit silently stopped limiting
    while every gate still believed it was queued;
  * select(2) refuses a file descriptor at or above FD_SETSIZE (1024), and a pidfd is a plain
    descriptor: the watch died with a ValueError where the fallback was never reached.

The Linux-only cases run here (macOS: skipped) and in the container the proof was made in:
`docker run --rm -v <repo>:/goh -w /goh <image> python3 -m pytest tests/test_gate_slot_linux.py`.
"""

from __future__ import annotations

import contextlib
import os
import resource
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HELPER = REPO_ROOT / "lib" / "gate_slot.py"

sys.path.insert(0, str(REPO_ROOT / "lib"))
import gate_slot  # noqa: E402

linux_only = pytest.mark.skipif(
    sys.platform != "linux", reason="the pidfd branch is Linux's; macOS takes the kqueue branch"
)


def _mechanism_of_a_live_watch() -> str:
    return gate_slot.OwnerWatch(os.getpid()).mechanism


def test_the_ancestry_walk_reaches_the_top_of_the_tree():
    """PID 1 is IN the ancestry, and it was not: the walk stopped before it.

    In a container the `tools/gate.sh` that holds the slot IS pid 1 (bash execs its last
    command), and the holder asks "is the owner still my ancestor?". With pid 1 excluded the
    answer was no, the holder exited 3, the shell said "the slot holder exited without a slot",
    and the consumer's own gate.sh FAILED -- in every container, for every consumer.
    """
    assert 1 in gate_slot.ancestors()


@linux_only
def test_the_container_s_init_process_is_accepted_as_the_owner(tmp_path):
    """The consequence, on the platform where it happens: the holder takes the watch when the
    owner is pid 1."""
    assert 1 in gate_slot.ancestors()
    slots = tmp_path / "slots"
    slots.mkdir()
    handshake = tmp_path / "handshake"
    os.mkfifo(handshake)
    holder = subprocess.Popen(
        [
            sys.executable,
            str(HELPER),
            "hold",
            "--dir",
            str(slots),
            "--slots",
            "1",
            "--owner",
            "1",
            "--repo",
            "container-init",
            "--handshake",
            str(handshake),
            "--for-caller",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        with open(handshake) as fh:
            line = fh.readline().strip()
        fields = line.split()
        assert len(fields) == 5, line
        assert fields[4] == "pidfd", line
    finally:
        holder.terminate()
        holder.wait(timeout=30)


@linux_only
def test_the_pidfd_branch_is_the_one_that_runs_on_linux():
    """Not the capability, the BRANCH: what `OwnerWatch` takes on this kernel."""
    assert gate_slot.watch_mechanism() == "pidfd"
    assert _mechanism_of_a_live_watch() == "pidfd"


@linux_only
def test_the_holder_reports_the_pidfd_branch_and_not_the_poll(tmp_path):
    """The holder's handshake carries the mechanism, so a run PROVES which watch took the slot."""
    slots = tmp_path / "slots"
    slots.mkdir()
    handshake = tmp_path / "handshake"
    os.mkfifo(handshake)
    # The owner is this test process: the holder watches it, and the owner is an ancestor.
    holder = subprocess.Popen(
        [
            sys.executable,
            str(HELPER),
            "hold",
            "--dir",
            str(slots),
            "--slots",
            "1",
            "--owner",
            str(os.getpid()),
            "--repo",
            "linux-proof",
            "--handshake",
            str(handshake),
            "--for-caller",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        with open(handshake) as fh:
            line = fh.readline().strip()
        fields = line.split()
        assert len(fields) == 5, line
        assert fields[4] == "pidfd", line
    finally:
        holder.terminate()
        holder.wait(timeout=30)


def _no_pidfd(_pid: int) -> int:
    """A kernel without pidfd: ENOSYS, an OSError -- not the ProcessLookupError of a dead owner."""
    raise OSError(38, "Function not implemented")


class _SelectWithoutKqueue:
    """The stdlib select, with kqueue hidden -- a kernel that has no kqueue.

    The real module is untouched: the stdlib's subprocess uses select.kqueue on macOS, and
    deleting the attribute from it breaks every child this test spawns.
    """

    def __init__(self, real) -> None:
        self._real = real

    def __getattr__(self, name):
        if name == "kqueue":
            raise AttributeError(name)
        return getattr(self._real, name)


@contextlib.contextmanager
def _a_kernel_with_no_event(monkeypatch):
    """This host as a kernel with NEITHER kqueue nor pidfd: the poll fallback, and only it."""
    monkeypatch.setattr(gate_slot, "select", _SelectWithoutKqueue(select))
    monkeypatch.setattr(gate_slot.os, "pidfd_open", _no_pidfd, raising=False)
    yield


def test_a_kernel_without_pidfd_falls_back_to_the_poll(monkeypatch):
    """The fallback is REACHED, not merely written: the old code raised the OSError out of the
    constructor, and the holder died with it -- freeing the slot it was supposed to hold."""
    with _a_kernel_with_no_event(monkeypatch):
        watch = gate_slot.OwnerWatch(os.getpid())
        assert watch.mechanism == "poll"
        assert watch.alive(wait=0.0) is True


def _fd_at_or_above(fd: int, floor: int = 1024) -> int:
    """`fd` duplicated onto a descriptor number select(2) refuses."""
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if hard < floor + 16:
        pytest.skip("this host's descriptor hard limit is below FD_SETSIZE")
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (max(soft, floor + 16), hard))
    except (ValueError, OSError):
        pytest.skip("this host will not raise its descriptor limit")
    for candidate in range(floor, floor + 16):
        try:
            os.dup2(fd, candidate, inheritable=False)
        except OSError:
            continue
        return candidate
    pytest.skip("no free descriptor at or above FD_SETSIZE")


def test_the_pidfd_watch_survives_a_descriptor_select_would_refuse():
    """The defect, proved: select(2) refuses the descriptor, poll(2) does not.

    A pidfd is a plain descriptor, and the holder's number depends on what the process opened
    before it -- so the watch has to be poll(2), which has no FD_SETSIZE.
    """
    read_fd, write_fd = os.pipe()
    try:
        high = _fd_at_or_above(read_fd)
        with pytest.raises(ValueError):
            select.select([high], [], [], 0)
        watch = gate_slot.OwnerWatch(os.getpid())
        watch._pidfd = high
        assert watch._pidfd_readable(0.05) is False  # nothing written: not readable, no exception
        os.write(write_fd, b"x")
        assert watch._pidfd_readable(0.05) is True
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_the_poll_fallback_still_notices_the_owner_going_away(monkeypatch):
    """The fallback is not only reached: it works. A watch that cannot see the owner go would
    hold the slot until the machine was rebooted."""
    with _a_kernel_with_no_event(monkeypatch):
        with subprocess.Popen(["sleep", "0.2"], stdin=subprocess.DEVNULL) as child:
            watch = gate_slot.OwnerWatch(child.pid)
            assert watch.mechanism == "poll"
            time.sleep(0.5)
            child.wait(timeout=10)
            assert watch.alive(wait=0.1) is False
