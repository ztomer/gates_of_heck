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

THE SECOND SHAPE (2026-10-20, later the same day). A record can VANISH: an experiment that ran in
a temporary directory leaves its registry there, and that directory is pruned -- eight helpers,
orphaned by a pytest that died, each burning a core for an hour, none of them in a registry the
backstop reads. A backstop that only knows the registry cannot see those.

So the backstop asks one more question, and it is the RECORDED EXEMPTION: is this process's
command line EXACTLY the house's spinner shape, is its parent 1, and is the owner its release pipe
names gone? A name lookup feeds that question (`pgrep`), so the house's kill-by-name rule sees it
-- the lookup and the kill sit on ONE line, so the rule cannot be stepped around by a rewrite --
and the exemption stands in `kill_by_name_allow.json` with its reason. What the lookup returns is
a CANDIDATE list: nothing is killed until the candidate's whole command line has been read back
and matched exactly, so the answer to "is this ours?" never rests on the pattern.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import load_registry as reg

GRACE_S = 2.0
POLL_S = 0.05

# The coarse finder: a release pipe's name, which every spinner of the house's shape carries.
# It is a CANDIDATE list and nothing more -- the exact shape is checked afterwards, and only then
# is anything killed.
ORPHAN_PATTERN = r"release-[0-9]+-[0-9]+\.fifo"


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


def kill(pid: int, grace: float = GRACE_S) -> bool:
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


def pgrep(pattern: str) -> list[int]:
    """The name lookup: `pgrep -f`, and only that.

    A name matches processes the caller does not own -- zinc's harness SIGKILLed another
    session's browser (2026-09-23) -- so this lookup is the RECORDED EXEMPTION: the house's
    kill-by-name rule sees it, because the lookup and the kill sit on ONE line, and the entry
    that excuses it is in kill_by_name_allow.json with its reason written down.

    What comes back is a CANDIDATE list, never a verdict: every candidate is read back and
    matched against the house's own shape, exactly, before anything is touched.
    """
    result = subprocess.run(
        ["pgrep", "-f", pattern],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=10,
        check=False,
    )
    return [int(part) for part in result.stdout.split() if part.isdigit()]


def _process(pid: int) -> tuple[int, str] | None:
    """The parent and the WHOLE command line of a pid, or None when it cannot be read."""
    result = subprocess.run(
        ["ps", "-ww", "-o", "ppid=,command=", "-p", str(pid)],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=10,
        check=False,
    )
    parts = result.stdout.strip().split(None, 1)
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), parts[1].strip()
    except ValueError:
        return None


def our_orphans(candidates: list[int]) -> list[int]:
    """The candidates that are PROVABLY the house's own orphans.

    Three things must hold, and none of them alone is enough:

      * the parent is 1: nobody owns it any more, so nobody will ever reap it;
      * the whole command line is EXACTLY the house's spinner shape -- a name that matches a
        process the caller does not own is the mistake the house forbids;
      * the owner the release pipe names is gone: the pipe names the tool that started it.
    """
    ours: list[int] = []
    for pid in candidates:
        info = _process(pid)
        if info is None:
            continue
        parent, command = info
        if parent != 1:
            continue
        shape = reg.spinner_shape(command)
        if shape is None:
            continue
        _pipe, owner = shape
        if reg.pid_alive(owner):
            continue
        ours.append(pid)
    return ours


def reap_orphans() -> Report:
    """The unregistered orphans: the house's spinner shape, orphaned only.

    This is the SECOND shape of the class of 2026-10-20: a spinner whose registry was pruned with
    the directory that held it, re-parented to launchd, and burning a core with nothing naming it.
    The registry cannot reach those, so the shape does -- exactly, and only when the parent is 1.

    The exemption this line needs is recorded in kill_by_name_allow.json: one call, one file, with
    the reason written down. The house's kill-by-name rule SEES this line -- the lookup and the
    kill are on it together -- so a rewrite that moved them apart would be seen too.
    """
    report = Report()
    outcomes = [(pid, kill(pid)) for pid in our_orphans(pgrep(ORPHAN_PATTERN))]
    for pid, gone in outcomes:
        (report.killed if gone else report.failed).append(pid)
    return report


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
            if kill(pid):
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
