#!/usr/bin/env python3
"""Machine-wide mutual exclusion for taking over the desktop — Python half.

HOUSE LIB, and it is the SAME LOCK as desktop_lock.sh, not a parallel one: same
path, same owner-file format, same staleness rules. That is the whole point. A
bash capture run and a Python capture run contend for one physical desktop, so
if the two halves disagreed about the path or about what "stale" means they
would happily run at the same time while both believing they held the lock.
Change one half, change the other.

WHAT COUNTS AS TAKING OVER THE DESKTOP -- take the lock if you do any of:
  - drive the menu bar or Accessibility tree (osascript / System Events)
  - move or park the real cursor, or synthesize clicks and keystrokes
  - call `screencapture`, or otherwise depend on what is frontmost
  - launch a GUI app whose windows must be the ones you then act on

WHY IT IS A HARD LOCK. Two such runs at once do not merely produce a bad
screenshot. They interleave clicks into each other's windows, and any run that
also does the backup-mutate-restore dance on a config file will CORRUPT it: B
backs up the state A already modified, A restores the pristine copy, then B
restores A's test state over it. Both runs report success, the damage is to the
user's real saved data, and nothing downstream can detect it.

WHY `mkdir` AND NOT fcntl.flock. An flock dies with the file descriptor, which
makes "is the holder still alive" invisible to a peer that wants to reclaim a
lock rather than block forever on it. os.mkdir is atomic on every POSIX
filesystem and leaves an inspectable owner record behind, which is what makes
the deadlock defences below possible. It also matches the bash half exactly.

DEADLOCK DEFENCES, because an agent session dies in more ways than it exits.
Three independent releases:
  1. THE CONTEXT MANAGER / finally, on any clean exit or exception.
  2. OWNER LIVENESS, for SIGKILL and crashes, which run no cleanup at all.
     PID ALONE IS NOT ENOUGH -- PIDs get recycled, and a recycled PID reads as
     "alive" forever, which is a permanent deadlock wearing the costume of a
     busy peer. The owner's START TIME is recorded next to the PID and must
     match too.
  3. A MAX HOLD AGE, for an owner that is alive but WEDGED (a hung osascript, a
     modal nobody will dismiss). Liveness cannot tell that from useful work.

The bias throughout is RECLAIM RATHER THAN WAIT: a wrongly-reclaimed lock costs
one confused screenshot, a wrongly-held one costs every later run on the machine.

Usage:
    from desktop_lock import desktop_lock
    with desktop_lock("necrohand hat-drop capture"):
        ...
"""

import contextlib
import os
import subprocess
import time

# Identical to DESKTOP_LOCK_DIR in desktop_lock.sh. /tmp and not tempfile.gettempdir():
# the contended resource is the one physical desktop, and TMPDIR is per-user on
# macOS and per-SESSION under some agent harnesses -- which would hand each
# caller a private lock and no exclusion whatsoever.
LOCK_DIR = "/tmp/mac-desktop-ui.lock"
DEFAULT_TIMEOUT = 300
DEFAULT_MAX_HOLD = 900


class DesktopBusy(RuntimeError):
    """The desktop stayed held by a live, un-wedged peer past the timeout."""


def _start_time(pid):
    """A process's start time, used to tell the real owner from a recycled PID.

    Empty when the process does not exist, which callers treat as dead.

    THE NORMALISATION IS PART OF THE CROSS-LANGUAGE CONTRACT and must match
    desktop_lock.sh byte for byte, or each half reads the other's records as
    impostors and silently grants a lock the peer is holding. An untested first
    version did exactly that: it used `" ".join(out.split(" "))`, which looks
    like a whitespace squeeze and is a no-op, while the bash half really was
    squeezing. `ps` pads single-digit days ("Aug  1" vs "Aug 18"), so the two
    disagreed on two days in three. split() with no argument collapses every
    whitespace run and trims the ends; the bash half gets there via word
    splitting and "$*".
    """
    try:
        out = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    return " ".join(out.split())


def _owner():
    """(pid, start_time, label) from the owner file, or None if unreadable.

    Unreadable counts as stale: it means a run died between the mkdir and the
    write, leaving a lock that nobody alive can ever release.
    """
    try:
        with open(os.path.join(LOCK_DIR, "owner")) as handle:
            lines = handle.read().split("\n")
    except OSError:
        return None
    if len(lines) < 3 or not lines[0].strip():
        return None
    return lines[0].strip(), lines[1], lines[2]


def _owner_alive():
    owner = _owner()
    if owner is None:
        return False
    pid, recorded, _ = owner
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        return False
    # PID is live -- but is it the SAME process, or a recycled number?
    current = _start_time(pid)
    if recorded and current and recorded.strip() != current.strip():
        return False
    return True


def _expired(max_hold):
    """Has a live owner held the desktop past any plausible run length?"""
    try:
        created = os.stat(LOCK_DIR).st_mtime
    except OSError:
        return False
    return (time.time() - created) >= max_hold


def acquire(label="desktop run", timeout=DEFAULT_TIMEOUT,
            max_hold=DEFAULT_MAX_HOLD, log=print):
    """Take the lock, waiting out a live peer. Raises rather than proceeding
    unlocked -- proceeding is the corrupting case this module exists to stop."""
    waited = 0
    announced = False
    while True:
        try:
            os.mkdir(LOCK_DIR)
            break
        except FileExistsError:
            if not _owner_alive():
                owner = _owner()
                log(f"→ stale desktop lock from "
                    f"{owner[2] if owner else 'an unknown run'} -- reclaiming")
                _force_remove()
                continue
            if _expired(max_hold):
                owner = _owner()
                log(f"→ desktop held past {max_hold}s by "
                    f"{owner[2] if owner else 'an unknown run'} -- wedged, reclaiming")
                _force_remove()
                continue
            if not announced:
                owner = _owner()
                log(f"→ the desktop is held by "
                    f"{owner[2] if owner else 'an unknown run'}; waiting up to {timeout}s")
                announced = True
            if waited >= timeout:
                owner = _owner()
                raise DesktopBusy(
                    f"desktop still held by "
                    f"{owner[2] if owner else 'an unknown run'} after {timeout}s")
            time.sleep(1)
            waited += 1
    pid = os.getpid()
    with open(os.path.join(LOCK_DIR, "owner"), "w") as handle:
        handle.write(f"{pid}\n{_start_time(pid)}\n{label} (pid {pid})\n")
    if announced:
        log(f"→ desktop acquired after {waited}s")


def _force_remove():
    for name in ("owner",):
        with contextlib.suppress(OSError):
            os.remove(os.path.join(LOCK_DIR, name))
    with contextlib.suppress(OSError):
        os.rmdir(LOCK_DIR)


def release():
    """Release a lock THIS process holds. Checking ownership matters: without
    it, a run that gave up waiting would delete the lock of the peer it waited
    for."""
    owner = _owner()
    if owner is not None and owner[0] != str(os.getpid()):
        return
    _force_remove()


@contextlib.contextmanager
def desktop_lock(label="desktop run", timeout=DEFAULT_TIMEOUT,
                 max_hold=DEFAULT_MAX_HOLD, log=print):
    acquire(label, timeout=timeout, max_hold=max_hold, log=log)
    try:
        yield
    finally:
        release()
