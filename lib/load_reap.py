"""The backstop: kill the spinners whose owner is gone, and NOTHING else.

A trap cannot catch an orphan: it does not run when its owner is SIGKILLed. So the house runs this
every minute (the launchd job `com.ztomer.goh-load-reap`), reading the load registry the tool
wrote -- a record, not a memory -- and killing the pids in it whose parent is 1.

THE NARROWNESS IS THE POINT. Three things are never touched:

  * a spinner with a LIVE parent: that is an experiment in progress, and the owner will reap it;
  * a process no registry names: it was never ours, and this machine runs other people's work;
  * a pid whose start time no longer matches the record: the kernel reused the number, so the
    process under it is something else entirely.

Nothing is matched by NAME. A name is not an owner -- the house forbids killing by name
(`goh kill-by-name`), and the pkill that a name-based reaper needs is exactly what killed another
session's browser once (zinc, 2026-09-23). This kills by PID, from its own registry.
"""

from __future__ import annotations

import os
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path

import load_registry as reg

GRACE_S = 2.0
POLL_S = 0.05


@dataclass
class Report:
    """What one sweep did, in the words the job's log needs."""

    killed: list[int] = field(default_factory=list)
    kept: list[int] = field(default_factory=list)
    dropped: list[int] = field(default_factory=list)
    failed: list[int] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    swept: list[str] = field(default_factory=list)

    @property
    def touched(self) -> int:
        return len(self.killed) + len(self.kept) + len(self.dropped) + len(self.failed)

    def summary(self) -> str:
        return (
            f"killed {len(self.killed)}, kept {len(self.kept)}, dropped {len(self.dropped)}, "
            f"failed {len(self.failed)}, registries removed {len(self.removed)}"
        )


def _kill(pid: int, grace: float = GRACE_S) -> bool:
    """SIGTERM, then SIGKILL, and report whether the pid is gone."""
    for number in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.kill(pid, number)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            if reg.pid_gone(pid):
                return True
            time.sleep(POLL_S)
        if reg.pid_gone(pid):
            return True
    return reg.pid_gone(pid)


def reap_registry(path: Path) -> Report:
    """One registry: kill its orphans, drop what is gone, and keep the rest."""
    report = Report()
    data = reg.read(path)
    if data is None:
        return report
    entries = data.get("pids")
    if not isinstance(entries, list):
        entries = []
    survivors: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            pid = int(entry.get("pid", 0))
        except (TypeError, ValueError):
            continue
        if pid <= 0:
            continue
        if reg.pid_gone(pid):
            report.dropped.append(pid)
            continue
        recorded = entry.get("start")
        current = reg.pid_start(pid)
        if recorded and current and recorded != current:
            report.dropped.append(pid)  # the pid was reused: not the process we registered
            continue
        if reg.pid_ppid(pid) == 1:
            if _kill(pid):
                report.killed.append(pid)
            else:
                report.failed.append(pid)
                survivors.append(entry)
            continue
        report.kept.append(pid)  # a live parent: an experiment in progress
        survivors.append(entry)
    if survivors:
        data["pids"] = survivors
        reg.write(path, data)
    else:
        reg.remove(path)
        report.removed.append(str(path))
    return report


def sweep_release_pipes(directory: Path | None = None) -> list[str]:
    """The release pipes of owners that are gone: they are the tool's, and nobody else uses them.

    A spinner blocks on its release pipe until the byte arrives, so a pipe whose owner died is a
    spinner that will never be released -- and a file nobody will ever read.
    """
    where = directory or reg.load_dir()
    if not where.is_dir():
        return []
    gone: list[str] = []
    for path in sorted(where.glob("release-*.fifo")):
        parts = path.name.split("-")
        if len(parts) < 3 or not parts[1].isdigit():
            continue
        owner = int(parts[1])
        if owner == os.getpid() or reg.pid_alive(owner):
            continue
        reg.remove(path)
        gone.append(str(path))
    return gone


def reap_all(directory: Path | None = None) -> Report:
    """Every registry in the load directory, and the release pipes of owners that are gone."""
    total = Report()
    for path in reg.registry_files(directory):
        one = reap_registry(path)
        total.killed.extend(one.killed)
        total.kept.extend(one.kept)
        total.dropped.extend(one.dropped)
        total.failed.extend(one.failed)
        total.removed.extend(one.removed)
    total.swept.extend(sweep_release_pipes(directory))
    return total
