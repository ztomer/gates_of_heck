"""The load registry: the record of who owns which spinner, and the facts a reaper needs.

THE CLASS (2026-10-20). A load experiment started its own busy loops --
`for j in $(seq 1 12); do (while :; do :; done) & done` -- and the shell that owned them was killed
before it reached its own cleanup. The spinners were re-parented to launchd (ppid 1) and kept
burning a core each, for hours: 24 of them for 3h12m (load average 206), then 12 for 26m, then 8
for 28m inside gates_of_heck itself. Each one was blamed on "other agents". The fix is not a habit
("remember to kill them"); it is a RECORD, so a backstop that needs no memory can find the orphan.

THE RECORD. One JSON file per experiment, in the load directory (`GOH_LOAD_DIR`, default
`~/.cache/goh/load`):

    {"version": 1,
     "owner_pid": 4242, "owner_start": "Tue Oct 20 12:00:00 2026",
     "started_at": 1760000000.0, "command": ["./test.sh"],
     "pids": [{"pid": 4300, "start": "Tue Oct 20 12:00:01 2026"}]}

A pid is not an identity: the kernel reuses it. So every pid is recorded with the START TIME of
the process it named, and a reaper that finds a pid whose start time has changed does not kill it
-- it drops the entry, because the process it named is gone and something else owns the number now.

WHAT MAY BE TOUCHED. Exactly the pids this file names, and only those whose parent is 1: a
spinner with a LIVE parent is an experiment in progress, and a spinner nobody registered was never
ours to kill. Nothing is ever matched BY NAME -- a name is not an owner, and the house forbids
killing by name (`goh kill-by-name`).

THE SPINNER'S SHAPE. A spinner is started as `/bin/sh -c SPINNER_COMMAND`, and that command is
ONE definition here, because the backstop has to recognise an orphan whose registry is gone (the
registry of an experiment that ran in a temporary directory is pruned with that directory, and the
orphan is left burning a core with nothing naming it). Recognition is EXACT: the whole command
line has to equal the shape, character for character, and the parent has to be 1. A name that
matches a process the caller does not own is the mistake the house forbids, so the shape is
pinned, not a pattern.

ONE LOCK (2026-10-25). The registry has two writers -- the tool rewrites it once per spinner and
removes it when it is done; the backstop reads, prunes and writes it back -- and neither held a
lock. A backstop pass that read mid-start and wrote afterwards left a registry naming fewer
spinners than were burning (8 of 16, then 2 of 16, each time the owner's deadline hit), and one
that read before the tool removed the file brought a dead experiment's slot back. So every
mutation of the load directory -- `write`, `create`, `remove` -- takes the directory's lock
itself, and a read-modify-write holds `locked()` across its re-read and its write. The lock is
the default, not a habit: a writer cannot mutate the registry without it.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

REGISTRY_NAME = "registry.json"
# The lock every mutation of the load directory takes. Not `*.json`, so `registry_files` never
# mistakes it for a registry, and never removed: unlinking a lock file another process has open is
# how two holders end up locking two different files.
LOCK_NAME = ".registry.lock"
# How long a writer waits for the lock. A hold is a re-read and a write -- milliseconds -- so a
# minute is a holder that is wedged, and that is an error to name, not a wait to sit in forever.
LOCK_WAIT_S = 60.0
LOCK_POLL_S = 0.01
VERSION = 1
DEFAULT_PARTS = (".cache", "goh", "load")

# The command a spinner runs: it waits for its release byte on the pipe, and burns a core only
# after the byte arrives. `{fifo}` is the release pipe.
SPINNER_COMMAND = 'read _ < "{fifo}" || exit 0; while :; do :; done'

# The name of a release pipe: the pid of the tool that made it, and the spinner's index. The
# owner is in the NAME, so a pipe names the process that has to release it.
RELEASE_NAME = "release-{owner}-{index}.fifo"


def release_name(owner: int, index: int) -> str:
    """The name of a release pipe, for the tool that owns it and the spinner it is for."""
    return RELEASE_NAME.format(owner=owner, index=index)


def _release_pattern() -> str:
    """The release-pipe name as a pattern: the numbers are digits, everything else is literal.

    A POSIX ERE, not a Python regex: `pgrep -f` compiles it with regcomp, which knows no `\\d`
    and reads `\\-` as an escaped hyphen it cannot place. So the digits are `[0-9]+` and the
    hyphen is left bare.
    """
    parts = []
    for chunk in re.split(r"(\{owner\}|\{index\})", RELEASE_NAME):
        if chunk in ("{owner}", "{index}"):
            parts.append("[0-9]+")
        else:
            parts.append(chunk.replace(".", r"\."))
    return "".join(parts)


# The pattern a FINDER looks for. It is a finder, not a filter: it says which command lines are
# worth reading, and every one of them is read back and matched exactly before it is touched.
RELEASE_PATTERN = _release_pattern()


def spinner_command(fifo: Path | str) -> str:
    """The command a spinner runs, for the pipe that releases it."""
    return SPINNER_COMMAND.format(fifo=fifo)


def _shape_pattern() -> re.Pattern[str]:
    """The house's spinner shape as a pattern, derived from SPINNER_COMMAND (one definition).

    The WHOLE command line, anchored at both ends: a shape that matched a PREFIX would match a
    process that merely starts the same way, and one that matched a SUBSTRING would match a
    process that merely mentions it. Both are somebody else's process.
    """
    head, _, tail = SPINNER_COMMAND.partition("{fifo}")
    return re.compile(
        r"^/bin/sh -c " + re.escape(head) + r"(?P<fifo>[^\"]+)" + re.escape(tail) + r"$"
    )


SHAPE = _shape_pattern()

# The tail of a release pipe's path: the numbers are the owner's pid and the spinner's index.
PIPE_TAIL = re.compile(r"release-(?P<owner>[0-9]+)-(?P<index>[0-9]+)\.fifo$")


def spinner_shape(command: str) -> tuple[str, int] | None:
    """The release pipe and the owner it names, when a command line IS the house's spinner.

    None for anything else: a different loop, a different pipe, a different word. The match is
    exact, so nothing that is not ours can be recognised as ours.
    """
    match = SHAPE.match(command)
    if match is None:
        return None
    pipe = match.group("fifo")
    tail = PIPE_TAIL.search(pipe)
    if tail is None:
        return None
    return pipe, int(tail.group("owner"))


def load_dir() -> Path:
    """The directory the registries live in: `GOH_LOAD_DIR`, else `~/.cache/goh/load`."""
    raw = os.environ.get("GOH_LOAD_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    home = os.environ.get("HOME", "").strip()
    if not home:
        raise RuntimeError("no HOME: GOH_LOAD_DIR must name the load directory")
    return Path(home).joinpath(*DEFAULT_PARTS)


def registry_path(directory: Path | None = None) -> Path:
    """The registry a load experiment holds: one slot, so a second experiment cannot collide."""
    return (directory or load_dir()) / REGISTRY_NAME


def registry_files(directory: Path | None = None) -> list[Path]:
    """Every registry in the directory -- one per experiment, the tool's own among them."""
    where = directory or load_dir()
    if not where.is_dir():
        return []
    return sorted(p for p in where.glob("*.json") if p.is_file())


class _Held:
    """One process's hold on one directory's lock: re-entrant across the frames of one thread."""

    def __init__(self) -> None:
        self.guard = threading.RLock()
        self.fd = -1


_HELD: dict[str, _Held] = {}
_HELD_GUARD = threading.Lock()


def _flock(fd: int, where: Path) -> None:
    give_up = time.monotonic() + LOCK_WAIT_S
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() > give_up:
                raise TimeoutError(
                    f"the load registry lock {where} was held for {LOCK_WAIT_S:g}s: its holder is wedged"
                ) from None
            time.sleep(LOCK_POLL_S)


@contextlib.contextmanager
def locked(directory: Path | None = None) -> Iterator[None]:
    """Hold the load directory's lock: every mutation of a registry in it runs inside.

    Between processes it is an flock; within one it is RE-ENTRANT, because the tool's cleanup runs
    from a signal handler that can arrive while the same thread is inside a write -- and a second
    flock on a second descriptor would wait on its own process forever. The descriptor is recorded
    BEFORE it is locked and forgotten AFTER it is closed, so a handler that interrupts the lock's
    own bookkeeping proceeds without the lock rather than deadlocking on it: a hang is worse than
    the race.
    """
    where = (directory or load_dir()) / LOCK_NAME
    where.parent.mkdir(parents=True, exist_ok=True)
    with _HELD_GUARD:
        held = _HELD.setdefault(str(where), _Held())
    with held.guard:
        if held.fd >= 0:
            yield
            return
        held.fd = os.open(where, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        try:
            _flock(held.fd, where)
            yield
        finally:
            os.close(held.fd)  # closing the descriptor releases the lock
            held.fd = -1


def read(path: Path) -> dict | None:
    """The registry's contents, or None when it is unreadable (never half a record)."""
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _dump(data: dict) -> str:
    return json.dumps(data, indent=1, sort_keys=True) + "\n"


def write(path: Path, data: dict) -> None:
    """Rewrite the registry whole, under the lock: a reader never sees half a record."""
    with locked(path.parent):
        _write(path, data)


def _write(path: Path, data: dict) -> None:
    handle, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".registry-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(_dump(data))
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def create(path: Path, data: dict) -> None:
    """Take the slot, refusing one that is taken: two experiments never share a registry.

    The content is written FIRST and linked into place, so the file is never seen empty and the
    refusal is the link's own (EEXIST), not a check that another process can race.
    """
    with locked(path.parent):
        _create(path, data)


def _create(path: Path, data: dict) -> None:
    handle, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".registry-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(_dump(data))
        os.link(tmp, path)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)


def remove(path: Path) -> None:
    """Drop a file of the load directory, under the lock: the experiment is over."""
    if not path.parent.is_dir():
        return
    with locked(path.parent), contextlib.suppress(OSError):
        path.unlink()


def entry_pid(entry: object) -> int | None:
    """The pid a registry entry names, or None for an entry that names nothing usable."""
    if not isinstance(entry, dict):
        return None
    try:
        pid = int(entry.get("pid", 0))
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def owner_gone(data: dict) -> bool:
    """Is the experiment's owner gone? Its pid is dead, or now names a process started later."""
    try:
        owner = int(data.get("owner_pid", 0))
    except (TypeError, ValueError):
        return True
    if owner <= 0 or pid_gone(owner):
        return True
    recorded = data.get("owner_start")
    current = pid_start(owner)  # None is unknown, not gone: the pid is alive
    return bool(recorded) and current is not None and current != recorded


def settle(
    path: Path, gone: set[tuple[int, str]], before_write: Callable[[], None] | None = None
) -> bool:
    """Apply a prune to what the registry holds NOW, and report whether it was removed.

    The backstop decides what is gone from a read a kill ago (seconds), so it does not write that
    read back: it re-reads under the lock and drops exactly the entries it judged -- `(pid,
    start)`, as recorded -- keeping every one the tool registered since. A registry that vanished
    in the meantime stays gone (the tool finished), and one that names no spinner is removed only
    when its owner is gone too: a live owner between `claim` and its first spinner still holds
    the slot. `before_write` is the seam that pauses between the re-read and the write.
    """
    with locked(path.parent):
        data = read(path)
        if data is None:
            return False
        entries = data.get("pids") if isinstance(data.get("pids"), list) else []
        kept = [
            entry
            for entry in entries
            if entry_pid(entry) is not None
            and (entry_pid(entry), entry.get("start", "")) not in gone
        ]
        if before_write is not None:
            before_write()
        if not kept and owner_gone(data):
            with contextlib.suppress(OSError):
                path.unlink()
            return True
        if kept != entries:
            data["pids"] = kept
            _write(path, data)
        return False


def _ps(pid: int, spec: str) -> str:
    """One field of one process, or "" when it cannot be read -- gone, or `ps` too slow to answer.

    "" is UNKNOWN, and every caller reads it that way: an unknown is never a reason to kill, to
    drop an entry, or to call an owner gone. A loaded machine makes `ps` slow, and the backstop is
    needed most on a loaded machine (2026-10-25: eleven passes died on a lookup's timeout).
    """
    try:
        result = subprocess.run(
            ["ps", "-o", spec, "-p", str(pid)],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def pid_alive(pid: int) -> bool:
    """Is there a process with this pid? A zombie still has one."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def pid_gone(pid: int) -> bool:
    """Is the process gone for good? A zombie is gone: it holds the pid, not the CPU."""
    if not pid_alive(pid):
        return True
    return _ps(pid, "stat=").startswith("Z")


def pid_ppid(pid: int) -> int | None:
    """The process's parent, or None when it cannot be read.

    This is the ORPHAN signal: a process whose parent is 1 was re-parented to launchd, which
    means the shell that owned it is gone and nothing will ever reap it.
    """
    out = _ps(pid, "ppid=")
    try:
        return int(out.split()[0])
    except (IndexError, ValueError):
        return None


def pid_start(pid: int) -> str | None:
    """The process's start time, the half of an identity a pid alone is not."""
    return _ps(pid, "lstart=") or None
