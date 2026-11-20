"""pid_owner.py -- signal a SAVED pid only while it still names the process it was saved for.

THE CLASS (GOH.STRAYSIGNAL, 2026-11-20). A pid is a number the kernel hands out again. On a loaded
machine goh's full suite allocates several hundred pids a second (430/s measured at load 17), so
the whole 99999-wide space wraps in minutes, and a pid read from a pidfile or kept in a variable
after its process died names a stranger by then. Code that signals such a pid -- a test's
`finally: os.kill(int(pidfile.read_text()), SIGKILL)` after the process was already stopped --
SIGKILLs whatever the kernel handed the number to: in GOH.LANDMISS1119's land a fake build's
`sleep 60` ended after about a second, and a shell reads a killed foreground child as a finished
command.

THE FIX. A pid is an identity only together with the process's BIRTH: the kernel's start time
(macOS `proc_pidinfo(PROC_PIDTBSDINFO)`, microseconds; Linux `/proc/PID/stat` field 22, clock
ticks). `stamp(pid)` records both while the process is known to be ours (when it was spawned, or
when its pidfile first appears); `signal(stamp, sig)` re-reads the birth and REFUSES when the
pid is gone, is now somebody else's, or cannot be read. A stamp taken at the moment of the kill
proves nothing, so take it early. `signal_group` does the same for the process group the stamped
process led: the group id is a pid, so the leader is checked when alive, and when the leader is
gone the group is still ours, because the kernel does not reuse a pid while a group of that id
has members.

Limits, stated: the check and the kill are two syscalls, so a pid that dies AND is reused between
them (a whole wrap of the pid space in microseconds) is not caught. A zombie reads as gone: it
cannot be signalled into doing anything anyway. checks/check_saved_pid_kill.py refuses a new
`os.kill`/`os.killpg` of a pid that is neither a child handle's nor stamped here; lib/pid_owner.sh
is the shell half (`ps -o lstart=`, one-second resolution). tests/test_pid_owner.py.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PROC_PIDTBSDINFO = 3
BSDINFO_SIZE = 136  # sizeof(struct proc_bsdinfo)
PGID_AT = 100  # offsetof(pbi_pgid)
START_AT = 120  # offsetof(pbi_start_tvsec); pbi_start_tvusec follows


@dataclass(frozen=True)
class Stamp:
    """A process as it was when stamped: its pid, its birth, and the process group it was in."""

    number: int
    born: tuple[int, int]
    group: int


def _darwin_birth(pid: int) -> tuple[tuple[int, int], int] | None:
    import ctypes

    libproc = _libproc()
    buf = ctypes.create_string_buffer(BSDINFO_SIZE)
    if (
        libproc.proc_pidinfo(pid, PROC_PIDTBSDINFO, ctypes.c_uint64(0), buf, BSDINFO_SIZE)
        != BSDINFO_SIZE
    ):
        return (
            None  # gone, a zombie (ESRCH), or another user's (EPERM): none of them ours to signal
        )
    raw = buf.raw
    group = int.from_bytes(raw[PGID_AT : PGID_AT + 4], sys.byteorder)
    sec = int.from_bytes(raw[START_AT : START_AT + 8], sys.byteorder)
    usec = int.from_bytes(raw[START_AT + 8 : START_AT + 16], sys.byteorder)
    return (sec, usec), group


def parse_proc_stat(text: str) -> tuple[tuple[int, int], int] | None:
    """`/proc/PID/stat`'s birth and group: fields 22 (starttime) and 5 (pgrp). The command name
    (field 2) is in parentheses and may hold spaces and parentheses, so fields count from the LAST
    `)`. A zombie (state Z) is gone."""
    _, close, rest = text.rpartition(")")
    fields = rest.split()
    if not close or len(fields) < 20 or fields[0] == "Z":
        return None
    try:
        return (int(fields[19]), 0), int(fields[2])
    except ValueError:
        return None


def _linux_birth(pid: int) -> tuple[tuple[int, int], int] | None:
    try:
        text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return parse_proc_stat(text)


_LIBPROC = None


def _libproc():
    global _LIBPROC  # noqa: PLW0603 -- one dlopen per process
    if _LIBPROC is None:
        import ctypes

        _LIBPROC = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    return _LIBPROC


def birth(pid: int, platform: str | None = None) -> tuple[tuple[int, int], int] | None:
    """(start time, process group) of a live process of ours, or None. An OS goh does not run on is
    a hard error (the platform policy), never a guess that lets every kill through."""
    if pid <= 0:
        return None
    plat = platform or sys.platform
    if plat == "darwin":
        return _darwin_birth(pid)
    if plat.startswith("linux"):
        return _linux_birth(pid)
    raise RuntimeError(f"pid_owner: no process start time on {plat!r} (macOS and Linux only)")


def stamp(pid: int) -> Stamp | None:
    """The process `pid` names NOW, or None when there is none of ours."""
    found = birth(pid)
    return None if found is None else Stamp(pid, found[0], found[1])


def lstart(pid: int) -> str | None:
    """`ps -o lstart=` of `pid`, whitespace-collapsed: the start time a SHELL can read, which is
    what a pidfile written by a shell (`goh_pid_stamp $! > FILE`) carries after the pid."""
    try:
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True,
                             text=True, stdin=subprocess.DEVNULL, timeout=30,
                             check=False).stdout  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return None
    return " ".join(out.split()) or None


def stamp_line(pid: int) -> str | None:
    """`<pid> <lstart>`, the line a pidfile carries so a later reader can prove the pid is still
    the process that wrote it (the shell half's `goh_pid_stamp` prints the same)."""
    started = lstart(pid)
    return None if started is None else f"{pid} {started}"


def read_stamp(path: str | os.PathLike[str]) -> Stamp | None:
    """Stamp the process a pidfile names, or None: missing, empty, not a pid, gone. A file that
    carries the start time after the pid (`stamp_line`, `goh_pid_stamp`) is checked against it,
    so the process is the one that was written down however long ago; a bare pid is stamped as it
    is NOW, which proves something only while the process is known to be alive."""
    try:
        words = Path(path).read_text(encoding="utf-8", errors="replace").split()
    except OSError:
        return None
    if not words or not words[0].isdigit():
        return None
    pid = int(words[0])
    if len(words) > 1 and lstart(pid) != " ".join(words[1:]):
        return None
    return stamp(pid)


def same(s: Stamp | None) -> bool:
    """Does the stamped pid still name the stamped process?"""
    if s is None:
        return False
    found = birth(s.number)
    return found is not None and found[0] == s.born


def signal(s: Stamp | None, sig: int) -> bool:
    """Send `sig` to the stamped process if it is still that process. True when sent; False when
    refused (no stamp, the process is gone, the pid is somebody else's now) or the kernel did."""
    if not same(s):
        return False
    assert s is not None
    try:
        os.kill(s.number, sig)
    except OSError:
        return False
    return True


def signal_group(s: Stamp | None, sig: int) -> bool:
    """Send `sig` to the process group the stamped process LED (`s.group == s.number`), if that
    group is still the one: the leader is alive and the same, or the leader is gone (a group id is
    not reused while the group has members). A stamp of a process that led no group is refused, so
    a group it merely belonged to is never signalled through it."""
    if s is None or s.group != s.number:
        return False
    found = birth(s.number)
    if found is not None and found[0] != s.born:
        return False  # the leader's pid is somebody else's now, and so is the group id
    if found is None:
        try:
            os.kill(s.number, 0)
        except ProcessLookupError:
            pass  # no process holds the number: whatever group carries the id is the original's
        except OSError:
            return False  # another user's process holds the number
        else:
            # A process holds the number and its birth cannot be read: a zombie, the stamped
            # leader's or a stranger's that took the number once the group had emptied. The two
            # cannot be told apart, so it is refused; a caller holding the leader's Popen handle
            # signals the group through that instead.
            return False
    try:
        os.killpg(s.group, sig)
    except OSError:
        return False
    return True


def signal_child_group(proc: subprocess.Popen, sig: int) -> bool:
    """Send `sig` to the group a child started with `start_new_session=True` leads (its id is the
    child's pid), from the child's HANDLE. While the handle has not reaped the child the id is ours
    whatever happens; once it has, the id is still ours while no process holds the number (it is
    not reused while the group has members), and a process that does hold it is a stranger that
    was handed the number after the group emptied, so the signal is refused. True when sent."""
    if proc.poll() is not None:
        try:
            os.kill(proc.pid, 0)
        except ProcessLookupError:
            pass
        except OSError:
            return False
        else:
            return False
    try:
        os.killpg(proc.pid, sig)
    except OSError:
        return False
    return True
