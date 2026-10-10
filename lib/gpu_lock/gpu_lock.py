"""Machine-wide mutual exclusion for the GPU -- Python half.

THE SAME LOCK as gpu_lock.sh beside it and as ztools' Rust client
(`rust/src/ztools/eval/gpu_lock.rs`): same directory, same three-line owner
record, same staleness rules, same environment names. A bash eval, a Rust eval
and a Python measurement contend for one GPU, so if the halves disagreed about
the path or about what "stale" means they would run at once while each believed
it held the lock. Change one half, change the others.

WHY IT IS A SEPARATE LOCK FROM THE DESKTOP'S. The desktop and the GPU are
different resources: a screenshot and a model run have no reason to exclude
each other. Sharing a name would turn every capture into a false "GPU busy" and
every eval into a false "desktop busy". Same design, own path.

WHO TAKES IT. Anything that loads model weights or measures a model (ztools'
evals), and anything whose MEASUREMENT a model run would corrupt: a run that
loads the machine for hours shifts every time column a census reads, and a
census that composites wallpaper on the GPU slows a decode-rate reading. A
holder that only needs to know (not exclude) asks `holder()`.

THE RECORD FORMAT CONTRACT. `$LOCK_DIR/owner` is exactly three lines: pid, the
whitespace-normalised `ps -o lstart=` of that pid, and a label. The start time
tells the real owner from a recycled pid; its normalisation must be byte-for-
byte the bash half's (word-split and rejoined), or each half reads the other's
records as impostors -- which an untested first desktop lock did.

THE CEILING MEASURES PROGRESS, NOT DURATION. An honest eval runs for hours, so a
wall-clock ceiling short enough to catch a wedge would reclaim a healthy run and
hand the GPU to a peer that restarts the server under it. A holder HEARTBEATS
(`heartbeat()`, the directory's mtime) after each unit of work, and the ceiling
is measured from the last beat.

THE BIAS IS TO REFUSE, NOT TO STEAL. Reclaiming wrongly corrupts a measurement
nobody will notice is wrong, so a waiter that cannot get the lock raises and
names who holds it rather than waiting long enough to be tempted. A dead owner
(or a recycled pid) and an owner past the idle ceiling are reclaimed; a live,
beating one never is.

INHERITANCE. `acquire()` exports ZTOOLS_GPU_LOCK_OWNER, so a child that asks
for the lock its ancestor holds adopts it rather than blocking on its own
parent forever, and never releases it.

Usage:
    from gpu_lock import gpu_lock
    with gpu_lock("census S6"):
        ...
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import time
from collections.abc import Callable

# Identical to GPU_LOCK_DIR in gpu_lock.sh and DEFAULT_DIR in ztools' Rust client.
# /tmp and not tempfile.gettempdir(): TMPDIR is per-user on macOS and per-session
# under some agent harnesses, which would hand each caller a private lock.
DEFAULT_DIR = "/tmp/mac-osaurus-gpu.lock"
# The seams. Production never sets DIR_ENV; a test points it at a scratch path so
# exclusion and reclamation are provable without touching the real lock.
DIR_ENV = "ZTOOLS_GPU_LOCK_DIR"
OWNER_ENV = "ZTOOLS_GPU_LOCK_OWNER"
# Seconds. Short on purpose: a peer holding the GPU is usually mid-eval and holds
# it for hours, so the useful answer is who, not a long wait.
DEFAULT_TIMEOUT = 60
# Seconds since the last heartbeat (the bash half's GPU_LOCK_MAX_IDLE).
DEFAULT_MAX_IDLE = 14400
UNKNOWN = "an unknown run"

_held = False
_inherited = False


class GpuBusy(RuntimeError):
    """The GPU stayed held by a live, beating peer past the timeout."""


def lock_dir() -> str:
    return os.environ.get(DIR_ENV) or DEFAULT_DIR


def _env_seconds(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


def start_time(pid: int | str) -> str:
    """`ps -o lstart=` of `pid`, whitespace-collapsed exactly as the bash half's
    word-split-and-rejoin; empty when the process does not exist."""
    try:
        out = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    return " ".join(out.split())


def _owner() -> tuple[str, str, str] | None:
    """(pid, start time, label), or None when no record names a pid: a run that
    died between the mkdir and the write left a lock nobody can release."""
    try:
        with open(os.path.join(lock_dir(), "owner")) as handle:
            lines = handle.read().split("\n")
    except OSError:
        return None
    fields = [lines[i] if i < len(lines) else "" for i in range(3)]
    if not fields[0].strip():
        return None
    return fields[0].strip(), fields[1], fields[2]


def _label() -> str:
    owner = _owner()
    return owner[2] if owner and owner[2].strip() else UNKNOWN


def _owner_alive() -> bool:
    owner = _owner()
    if owner is None:
        return False
    pid, recorded, _ = owner
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        return False
    # Live -- but the same process, or a recycled number? As the bash half: a
    # recorded start time that disagrees with the live one is not the owner.
    return not (recorded.strip() and recorded.strip() != start_time(pid))


def _expired(max_idle: float) -> bool:
    """Whether the holder has not beaten for `max_idle` seconds."""
    try:
        beat = os.stat(lock_dir()).st_mtime
    except OSError:
        return False
    return time.time() - beat >= max_idle


def holder() -> str:
    """The label of whoever holds the GPU, ours included; '' when it is free."""
    return _label() if _owner_alive() else ""


def foreign_holder() -> str:
    """The label of a live holder that is neither this process nor the ancestor
    that took the lock for it; '' when the GPU is free or ours."""
    if not _owner_alive():
        return ""
    pid = _owner()[0]
    if pid == str(os.getpid()) or pid == os.environ.get(OWNER_ENV):
        return ""
    return _label()


def acquire(
    label: str = "gpu run",
    timeout: float | None = None,
    max_idle: float | None = None,
    log: Callable[[str], None] = print,
    nap: Callable[[float], None] = time.sleep,
) -> None:
    """Take the lock, or raise GpuBusy naming the holder. Never proceeds unlocked."""
    global _held, _inherited
    path = lock_dir()
    inherited = os.environ.get(OWNER_ENV)
    if inherited and _owner_alive() and _owner()[0] == inherited:
        _inherited = True
        log(f"→ gpu already held by {_label()} (inherited)")
        return
    wait_for = _env_seconds("GPU_LOCK_TIMEOUT", DEFAULT_TIMEOUT) if timeout is None else timeout
    ceiling = _env_seconds("GPU_LOCK_MAX_IDLE", DEFAULT_MAX_IDLE) if max_idle is None else max_idle
    waited, announced = 0, False
    while True:
        try:
            os.mkdir(path)
            break
        except FileExistsError:
            pass
        if not _owner_alive():
            log(f"→ stale gpu lock from {_label()} -- reclaiming")
        elif _expired(ceiling):
            log(f"→ gpu held with no progress for {ceiling:g}s by {_label()} -- wedged, reclaiming")
        else:
            if not announced:
                log(f"→ the gpu is held by {_label()}")
                announced = True
            if waited >= wait_for:
                raise GpuBusy(f"gpu still held by {_label()} after {wait_for:g}s")
            nap(1)
            waited += 1
            continue
        shutil.rmtree(path, ignore_errors=True)
        if os.path.exists(path):
            raise RuntimeError(
                f"cannot clear {path} to reclaim a stale gpu lock -- refusing to spin"
            )
    pid = os.getpid()
    with open(os.path.join(path, "owner"), "w") as handle:
        handle.write(f"{pid}\n{start_time(pid)}\n{label} (pid {pid})\n")
    _held = True
    os.environ[OWNER_ENV] = str(pid)
    if announced:
        log(f"→ gpu acquired after {waited}s")


def heartbeat() -> None:
    """Say "still working", so the idle ceiling never reclaims a healthy long run.
    A no-op unless this process holds the lock: an unheld caller must not keep a
    peer's expired lock alive."""
    if _held:
        with contextlib.suppress(OSError):
            os.utime(lock_dir())


def release() -> None:
    """Release only a lock this process took (not one it inherited), and only
    while the record is still ours: a holder reclaimed as wedged must not delete
    the new owner's lock on its way out."""
    global _held, _inherited
    if _inherited:
        _inherited = False
        return
    if not _held:
        return
    _held = False
    if os.environ.get(OWNER_ENV) == str(os.getpid()):
        del os.environ[OWNER_ENV]
    owner = _owner()
    if owner is not None and owner[0] != str(os.getpid()):
        return
    shutil.rmtree(lock_dir(), ignore_errors=True)


@contextlib.contextmanager
def gpu_lock(label: str = "gpu run", timeout: float | None = None, **kw):
    """Hold the GPU for the block; release on every exit from it."""
    acquire(label, timeout=timeout, **kw)
    try:
        yield
    finally:
        release()
