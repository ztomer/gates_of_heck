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
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

REGISTRY_NAME = "registry.json"
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
    """Rewrite the registry whole: a reader never sees half a record."""
    path.parent.mkdir(parents=True, exist_ok=True)
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
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".registry-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(_dump(data))
        os.link(tmp, path)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)


def remove(path: Path) -> None:
    """Drop the registry: the experiment is over and nothing is left behind."""
    with contextlib.suppress(OSError):
        path.unlink()


def _ps(pid: int, spec: str) -> str:
    result = subprocess.run(
        ["ps", "-o", spec, "-p", str(pid)],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=10,
        check=False,
    )  # fmt: skip
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
