"""lib/pid_owner.py and lib/pid_owner.sh: a saved pid is signalled only while it names its process.

THE HAZARD, shown once (GOH.STRAYSIGNAL). A pidfile names a process; the process ends; the kernel
hands the number to another. The suite cannot make the kernel reuse a number on demand (macOS
allocates pids in order, so a reuse takes a wrap of the whole space), so the recycle is FAKED with
a stand-in: a second process of the test's own, sitting at the pid the file names, while the stamp
holds the birth the first process had. The bare kill the suite used to run -- `os.kill(int(
pidfile.read_text()), SIG)` -- signals the stand-in; the helper refuses and the stand-in lives.
Every process here is one the test started.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import REPO_ROOT
from orphan_kit import adopt
from timing_bounds import GiveUp, patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import pid_owner

SHELL_LIB = REPO_ROOT / "lib" / "pid_owner.sh"


def _sleeper(**kwargs) -> subprocess.Popen:
    return subprocess.Popen(["/bin/sleep", "60"], stdin=subprocess.DEVNULL, **kwargs)


def _ended(proc: subprocess.Popen) -> int | None:
    try:
        return proc.wait(timeout=patience(10))
    except subprocess.TimeoutExpired:
        return None


def _gone(s: pid_owner.Stamp) -> bool:
    give_up = GiveUp(10)
    while not give_up.passed():
        if not pid_owner.alive(s):
            return True
        time.sleep(0.02)
    return False


def _recycled(saved: pid_owner.Stamp, holder: subprocess.Popen) -> pid_owner.Stamp:
    """The stamp as it would read once `holder` had been handed `saved`'s number: the number is
    the holder's, the birth is still the saved process's."""
    return pid_owner.Stamp(holder.pid, saved.born, holder.pid)


@pytest.mark.load_sensitive(reason="spawns and signals its own sleepers")
def test_a_bare_kill_of_a_recycled_pid_signals_the_stand_in(tmp_path: Path) -> None:
    first = _sleeper()
    saved = pid_owner.stamp(first.pid)
    assert saved is not None
    first.kill()
    first.wait()
    stand_in = _sleeper()
    try:
        pidfile = tmp_path / "step.pid"
        pidfile.write_text(f"{stand_in.pid}\n")  # the number, now somebody else's
        os.kill(int(pidfile.read_text()), signal.SIGTERM)  # the bare shape this item removed
        assert _ended(stand_in) == -signal.SIGTERM, "the bare kill missed the process at the pid"
    finally:
        stand_in.kill()
        stand_in.wait()


@pytest.mark.load_sensitive(reason="spawns and signals its own sleepers")
def test_a_liveness_probe_reads_a_recycled_pid_as_gone() -> None:
    """GOH.STRAYSIGNAL2: `os.kill(pid, 0)` answers yes for the stand-in at the number; `alive`
    and the shell's `goh_pid_alive` answer no, and yes for the stamped process itself."""
    first = _sleeper()
    saved = pid_owner.stamp(first.pid)
    assert saved is not None and pid_owner.alive(saved)
    assert pid_owner.alive_line(pid_owner.stamp_line(first.pid) or "")
    first.kill()
    first.wait()
    stand_in = _sleeper()
    try:
        recycled = _recycled(saved, stand_in)
        os.kill(recycled.number, 0)  # the bare probe: no error, the stand-in reads as alive
        assert not pid_owner.alive(recycled)
        assert not pid_owner.alive_line(f"{stand_in.pid} Thu Jan  1 00:00:00 1970")
        assert not pid_owner.alive(saved) and not pid_owner.alive(None)
        done = _sh(f'goh_pid_alive "{stand_in.pid} Thu Jan  1 00:00:00 1970" && exit 3\n'
                   f'goh_pid_alive "$(goh_pid_stamp {stand_in.pid})" || exit 4\n'
                   'goh_pid_alive "" && exit 5\necho fine\n')  # fmt: skip
        assert done.stdout.strip() == "fine", (done.returncode, done.stderr)
    finally:
        stand_in.kill()
        stand_in.wait()


@pytest.mark.load_sensitive(reason="spawns and signals its own sleepers")
def test_the_helper_refuses_a_recycled_pid_and_the_stand_in_lives() -> None:
    first = _sleeper()
    saved = pid_owner.stamp(first.pid)
    assert saved is not None
    first.kill()
    first.wait()
    stand_in = _sleeper()
    try:
        recycled = _recycled(saved, stand_in)
        assert not pid_owner.same(recycled)
        assert pid_owner.signal(recycled, signal.SIGTERM) is False
        assert pid_owner.signal_group(recycled, signal.SIGTERM) is False
        with pytest.raises(subprocess.TimeoutExpired):  # alive: nothing was sent to it
            stand_in.wait(timeout=patience(0.3))
    finally:
        stand_in.kill()
        stand_in.wait()


@pytest.mark.load_sensitive(reason="spawns and signals its own sleeper")
def test_the_stamped_process_itself_is_signalled() -> None:
    proc = _sleeper()
    try:
        s = pid_owner.stamp(proc.pid)
        assert s is not None and s.number == proc.pid and pid_owner.same(s)
        assert pid_owner.signal(s, signal.SIGTERM) is True
        assert _ended(proc) == -signal.SIGTERM
        assert pid_owner.signal(s, signal.SIGTERM) is False, "a reaped pid is refused"
        assert pid_owner.stamp(proc.pid) is None
    finally:
        proc.kill()
        proc.wait()


@pytest.mark.load_sensitive(reason="spawns a child and leaves it a zombie for a moment")
def test_a_zombie_is_gone_and_a_pidfile_is_stamped_when_its_process_lives(tmp_path: Path) -> None:
    with subprocess.Popen(["/usr/bin/true"], stdin=subprocess.DEVNULL) as proc:
        give_up = GiveUp(10)
        while pid_owner.stamp(proc.pid) is not None and not give_up.passed():
            time.sleep(0.02)  # unreaped, it ends as a zombie: no birth to read, nothing to signal
        zombie = pid_owner.stamp(proc.pid)
    assert zombie is None
    live = _sleeper()
    try:
        pidfile = tmp_path / "pid"
        pidfile.write_text(f"{live.pid}\n")
        assert pid_owner.read_stamp(pidfile) == pid_owner.stamp(live.pid)
        line = pid_owner.stamp_line(live.pid)
        assert line is not None
        pidfile.write_text(line + "\n")  # written with its start time: proved against it
        assert pid_owner.read_stamp(pidfile) == pid_owner.stamp(live.pid)
        pidfile.write_text(f"{live.pid} Thu Jan  1 00:00:00 1970\n")  # the number, another birth
        assert pid_owner.read_stamp(pidfile) is None
        for text in ("", "nope\n", "-3\n"):
            pidfile.write_text(text)
            assert pid_owner.read_stamp(pidfile) is None, text
        assert pid_owner.read_stamp(tmp_path / "missing") is None
    finally:
        live.kill()
        live.wait()


@pytest.mark.load_sensitive(reason="spawns a session leader and its leaked child")
def test_a_group_is_signalled_while_its_leader_is_the_same_or_gone(tmp_path: Path) -> None:
    pidfile = tmp_path / "leak.pid"
    argv = ["/bin/sh", "-c", f"/bin/sleep 60 & echo $! > {pidfile}; exec /bin/sleep 60"]
    with subprocess.Popen(argv, stdin=subprocess.DEVNULL, start_new_session=True) as leader:
        adopt(leader)  # the suite owns the group: a failure here still reaps the leak
        _signal_a_group(leader, pidfile)


def _signal_a_group(leader: subprocess.Popen, pidfile: Path) -> None:
    s = pid_owner.stamp(leader.pid)
    try:
        assert s is not None and s.group == leader.pid
        give_up = GiveUp(10)
        while not (pidfile.exists() and pidfile.read_text().strip()) and not give_up.passed():
            time.sleep(0.02)
        leak = pid_owner.read_stamp(pidfile)
        assert leak is not None and leak.group == leader.pid
        assert pid_owner.signal_group(leak, signal.SIGTERM) is False, "a member is not a leader"
        leader.kill()
        leader.wait()  # the leader is gone; its group lives on in the leak
        assert pid_owner.signal_group(s, signal.SIGTERM) is True
        assert _gone(leak), "the group's surviving member was not signalled"
    finally:
        pid_owner.signal_group(s, signal.SIGKILL)  # whatever of the group is left
        leader.kill()
        leader.wait()


def test_proc_stat_is_parsed_from_the_last_parenthesis() -> None:
    tail = " ".join(["S", "1", "4242"] + ["0"] * 16 + ["987654"] + ["0"] * 10)
    assert pid_owner.parse_proc_stat(f"4243 (we) (ird) {tail}") == ((987654, 0), 4242)
    assert pid_owner.parse_proc_stat(f"4243 (z) Z{tail[1:]}") is None
    assert pid_owner.parse_proc_stat("4243 (short) S 1") is None
    assert pid_owner.parse_proc_stat("garbage") is None


def test_an_unsupported_os_is_a_hard_error_not_a_pass() -> None:
    with pytest.raises(RuntimeError, match="macOS and Linux only"):
        pid_owner.birth(os.getpid(), platform="sunos5")
    assert pid_owner.birth(0) is None and pid_owner.birth(-1) is None


def _sh(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", f'. "{SHELL_LIB}"\n{script}'], capture_output=True,
                          text=True, stdin=subprocess.DEVNULL, timeout=patience(30),
                          check=False)  # fmt: skip


@pytest.mark.load_sensitive(reason="its shell spawns and signals its own sleepers")
def test_the_shell_half_kills_its_stamp_and_refuses_a_recycled_one() -> None:
    done = _sh(
        "sleep 60 & a=$!; sa=$(goh_pid_stamp $a) || exit 3\n"
        # the recycle, faked: the same number with another birth
        'goh_pid_kill TERM "$a Thu Jan  1 00:00:00 1970" && exit 4\n'
        'kill -0 "$a" || exit 5\n'
        'goh_pid_kill TERM "$sa" || exit 6\n'
        'wait "$a"; rc=$?; [ "$rc" = 143 ] || exit 7\n'
        'goh_pid_kill TERM "$sa" && exit 8\n'
        "goh_pid_stamp $a && exit 9\n"
        'goh_pid_stamp "" && exit 10\n'
        'goh_pid_kill TERM "" && exit 11\n'
        "echo fine\n"
    )
    assert done.returncode == 0 and done.stdout.strip() == "fine", (done.returncode, done.stderr)
