#!/usr/bin/env python3
"""Machine-wide mutual exclusion for taking over the desktop — Python half.

HOUSE LIB, and it is the SAME LOCK as desktop_lock.sh, not a parallel one: same
path, same owner-file format, same staleness rules. That is the whole point. A
bash capture run and a Python capture run contend for one physical desktop, so
if the two halves disagreed about the path or about what "stale" means they
would happily run at the same time while both believing they held the lock.
Change one half, change the other.

RECORD FORMAT CONTRACT. See the same block in desktop_lock.sh: `$LOCK_DIR/owner`
is exactly three lines -- pid, whitespace-normalised `ps lstart`, label. This
half writes and reads the SAME bytes, and every fix below was made without
touching them. A fix that needed a new field would change the format, so it
belongs on BOTH halves or in neither.

WHERE THIS FILE IS CANONICAL. It lives here, beside the bash half, because
gates_of_heck is the house lib and this is the machine-wide desktop mutex --
koffee_big/tools/smoke.sh, ZoneWM/scripts/with_desktop_lock.sh and
routines/tools/capture_ui.sh source the .sh, games/necrohand/tools/qa_lock.py
imports this. A near-identical copy once also lived in ~/Projects/scripts/lib; it
drifted (2026-10-04: three real fixes made only there), so it was deleted and its
suite retargeted here rather than maintained as a second implementation. One
implementation, one set of behaviour, one place to change it.

WHAT COUNTS AS TAKING OVER THE DESKTOP -- take the lock if you do any of:
  - drive the menu bar or Accessibility tree (osascript / System Events)
  - move or park the real cursor, or synthesize clicks and keystrokes
  - call `screencapture`, or otherwise depend on what is frontmost
  - launch a GUI app whose windows must be the ones you then act on
  - DISRUPT the desktop without ever reading it: anything that calls
    makeKeyAndOrderFront or claims a menu bar slot (it steals focus from
    whoever IS mid-capture), or `pkill`s an app by name -- you may be closing
    the lock owner's own copy out from under them

WHY IT IS A HARD LOCK. Two such runs at once do not merely produce a bad
screenshot. They interleave clicks into each other's windows, and any run that
also does the backup-mutate-restore dance on a config file will CORRUPT it: B
backs up the state A already modified, A restores the pristine copy, then B
restores A's test state over it. Both runs report success, the damage is to the
user's real saved data, and nothing downstream can detect it.

WHY `os.mkdir` AND NOT fcntl.flock. An flock dies with the file descriptor, which
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
Note that bias does NOT extend to "reclaim when we cannot check": an unreadable
start time means identity is unproven, not disproven, and the age ceiling above
is the bound that covers a `ps` that is broken for good. Stealing the desktop
from a live peer is the corrupting outcome, so it takes positive evidence.

Usage:
    from desktop_lock import desktop_lock
    with desktop_lock("necrohand hat-drop capture"):
        ...
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import time
from collections.abc import Callable

# Identical to DESKTOP_LOCK_DIR in desktop_lock.sh. /tmp and not
# tempfile.gettempdir(): the contended resource is the one physical desktop, and
# TMPDIR is per-user on macOS and per-SESSION under some agent harnesses -- which
# would hand each caller a private lock and no exclusion whatsoever.
LOCK_DIR = "/tmp/mac-desktop-ui.lock"

# Seconds. Long enough that a run queued behind a real peer waits it out rather
# than failing; short enough that a wedged harness reports instead of hanging a
# scheduled loop forever. Override with DESKTOP_LOCK_TIMEOUT / DESKTOP_LOCK_MAX_HOLD
# in the environment, exactly as the bash half did, or per call.
DEFAULT_TIMEOUT = 300
DEFAULT_MAX_HOLD = 900

# Set only once actually acquired, so release is a no-op for a process that never
# held the lock and can be called unconditionally from a `finally` or an EXIT
# trap. This is the bash half's DESKTOP_LOCK_HELD, ported here on 2026-10-04 --
# see release() for what it was fixing.
_HELD = False


class DesktopBusy(RuntimeError):
    """The desktop stayed held by a live, un-wedged peer past the timeout."""


def _env_seconds(name: str, default: float) -> float:
    """A duration from the environment, falling back to `default`.

    The bash half did `${DESKTOP_LOCK_TIMEOUT:-300}` with no validation, so a
    non-numeric value was a silent arithmetic failure there; here it falls back
    and the run proceeds on the documented number instead of raising.
    """
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _timeout() -> float:
    return _env_seconds("DESKTOP_LOCK_TIMEOUT", DEFAULT_TIMEOUT)


def _max_hold() -> float:
    return _env_seconds("DESKTOP_LOCK_MAX_HOLD", DEFAULT_MAX_HOLD)


def _start_time(pid: int) -> str:
    """A process's start time, used to tell the real owner from a recycled PID.

    Empty when the process does not exist, which callers treat as unproven.

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
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    return " ".join(out.split())


def _owner() -> tuple[str, str, str] | None:
    """(pid, start_time, label) from the owner file, or None when there is no
    usable record at all.

    Three fields, the last two optional -- the bash half's `_desktop_lock_field n`
    returns empty past the end of the file, and only field 1 was ever required.
    A truncated owner record therefore does NOT mean stale: the bash side read it
    as held, the first python version read it as stale, and "stale" means this
    process deletes a lock a live peer is sitting in, which is the one outcome the
    lock exists to prevent. Unreadable (no file, or no PID in it) still counts as
    stale, because that means a run died between the mkdir and the write and left
    a lock nobody alive could ever release.
    """
    try:
        with open(os.path.join(LOCK_DIR, "owner")) as handle:
            lines = handle.read().split("\n")
    except OSError:
        return None
    fields = [lines[i] if i < len(lines) else "" for i in range(3)]
    if not fields[0].strip():
        return None
    return fields[0].strip(), fields[1], fields[2]


def _owner_label() -> str:
    """The owner's label for a message, or 'an unknown run'.

    The bash half's `_desktop_lock_owner_label`, which fell back to that string
    when the label line was blank. Every message below used to spell the fallback
    out separately, and two of them would have printed an empty name for a record
    whose third line was missing.
    """
    owner = _owner()
    if owner is None or not owner[2].strip():
        return "an unknown run"
    return owner[2]


def _owner_alive() -> bool:
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
    # Only a start time we could READ, and that disagrees, proves this is not
    # the owner. An unreadable one leaves identity unproven rather than
    # disproven -- see the module docstring -- and the MAX HOLD ceiling bounds it.
    return not (recorded.strip() and current and recorded.strip() != current)


def _expired(max_hold: float) -> bool:
    """Has a live owner held the desktop past any plausible run length?"""
    try:
        created = os.stat(LOCK_DIR).st_mtime
    except OSError:
        return False
    return (time.time() - created) >= max_hold


def _force_remove() -> bool:
    """Clear the lock directory. True when it is gone afterwards.

    rmtree, and not "remove the owner file then rmdir": that pair only empties a
    directory holding exactly an `owner` file, which is what its author assumed
    and what the tests exercise. Anything else -- a half-written record, a
    `.DS_Store` Finder dropped in while the owner was working -- left the
    directory in place, so the reclaim did nothing, the next os.mkdir failed
    again, and acquire() spun: "stale desktop lock" printed once per iteration,
    forever, on a lock nobody could ever take. The bash half's `rm -rf` could not
    get into that state, which is why this is a port and not a new idea.
    """
    shutil.rmtree(LOCK_DIR, ignore_errors=True)
    return not os.path.exists(LOCK_DIR)


def acquire(
    label: str = "desktop run",
    timeout: float | None = None,
    max_hold: float | None = None,
    log: Callable[[str], None] = print,
) -> None:
    """Take the lock, waiting out a live peer. Raises rather than proceeding
    unlocked -- proceeding is the corrupting case this module exists to stop.

    `timeout` and `max_hold` default to the environment (DESKTOP_LOCK_TIMEOUT,
    DESKTOP_LOCK_MAX_HOLD) and then to the module constants, which is the
    precedence the bash half's `${VAR:-default}` gave at source time. Passing
    None for both explicitly asks for the environment.
    """
    global _HELD
    wait_for = _timeout() if timeout is None else timeout
    hold_ceiling = _max_hold() if max_hold is None else max_hold
    waited = 0
    announced = False
    while True:
        try:
            os.mkdir(LOCK_DIR)
            break
        except FileExistsError:
            if not _owner_alive():
                log(f"→ stale desktop lock from {_owner_label()} -- reclaiming")
            elif _expired(hold_ceiling):
                log(
                    f"→ desktop held past {hold_ceiling:g}s by "
                    f"{_owner_label()} -- wedged, reclaiming"
                )
            else:
                if not announced:
                    log(f"→ the desktop is held by {_owner_label()}; waiting up to {wait_for:g}s")
                    announced = True
                if waited >= wait_for:
                    raise DesktopBusy(f"desktop still held by {_owner_label()} after {wait_for:g}s")
                time.sleep(1)
                waited += 1
                continue
            # Reclaiming. If the clear did not actually clear, looping here is
            # the spin _force_remove's docstring describes, so fail loudly
            # instead: one unremovable directory has to be somebody's decision,
            # not this process's, for ever.
            if not _force_remove():
                raise RuntimeError(
                    f"cannot clear {LOCK_DIR} to reclaim a stale desktop lock "
                    "(something is holding it open) -- refusing to spin"
                )
            continue
    pid = os.getpid()
    with open(os.path.join(LOCK_DIR, "owner"), "w") as handle:
        handle.write(f"{pid}\n{_start_time(pid)}\n{label} (pid {pid})\n")
    _HELD = True
    if announced:
        log(f"→ desktop acquired after {waited}s")


def release() -> None:
    """Release a lock THIS process holds.

    Two guards, because they stop different accidents.

    THE IN-PROCESS FLAG is the bash half's DESKTOP_LOCK_HELD, ported here on
    2026-10-04. It is what this release did NOT have: a `finally` that ran after
    a failed acquire called _owner_alive, found the owner's file unreadable (the
    mkdir-to-write window, or a peer mid-reclaim), and deleted a lock belonging
    to somebody else. A process that never took the lock must not be able to
    free the lock it was waiting for, which is exactly what the suite's
    "non-holder's release" case asserts.

    THE OWNER CHECK is the other half, and the bash side lacks it: a holder that
    was reclaimed by a peer (wedged, past the ceiling) would otherwise delete the
    NEW owner's lock on its way out. Keep both. Neither subsumes the other, which
    is why removing the flag and keeping this one is not a simplification.
    """
    global _HELD
    if not _HELD:
        return
    _HELD = False
    owner = _owner()
    if owner is not None and owner[0] != str(os.getpid()):
        return
    _force_remove()


@contextlib.contextmanager
def desktop_lock(
    label: str = "desktop run",
    timeout: float | None = None,
    max_hold: float | None = None,
    log: Callable[[str], None] = print,
):
    """Acquire for the duration of the block, release on every exit from it."""
    acquire(label, timeout=timeout, max_hold=max_hold, log=log)
    try:
        yield
    finally:
        release()
