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
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

REGISTRY_NAME = "registry.json"
VERSION = 1
DEFAULT_PARTS = (".cache", "goh", "load")


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
