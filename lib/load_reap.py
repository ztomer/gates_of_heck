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

THE THIRD SHAPE (2026-10-21). The second shape knows ONE command line -- the house's own spinner,
matched whole. A hand-rolled one is a different command line, so the backstop left all
twenty-four of them alone on 2026-10-21: `/bin/zsh /private/var/.../opencode/load.sh`, four cores
each, 78 minutes of CPU apiece, four and a half hours old, parent 1, their owner long dead. The
script was a file:

    for i in $(seq 1 24); do (while :; do :; done) & done

So the backstop learned a shape it can NAME without owning it: an orphaned process that is
BURNING a core on a busy loop. Four questions, and none of them alone is enough:

  * the parent is 1: nobody owns it any more, so nobody will ever reap it;
  * it has BURNED: its CPU time is at or above the floor -- a spinner burns, a sleeper does not;
  * it is STILL burning: the CPU time grew between two samples, so a process that finished its
    work and is waiting for something is left alone;
  * the code a SHELL runs contains a busy loop: the inline `-c` argument, or the script the
    command line names, read as CODE -- a loop that is only quoted or only commented is text.

This is the RECORDED EXEMPTION too, and the second one: a name lookup feeds it (`pgrep`), the
lookup and the kill sit on ONE line, and its reason is in `kill_by_name_allow.json`. The lookup
returns CANDIDATES and nothing else; every candidate is read back, and the four questions decide.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import busyloop
import load_registry as reg

GRACE_S = 2.0
POLL_S = 0.05

# The coarse finder: a release pipe's name, which every spinner of the house's shape carries.
# It is a CANDIDATE list and nothing more -- the exact shape is checked afterwards, and only then
# is anything killed.
ORPHAN_PATTERN = r"release-[0-9]+-[0-9]+\.fifo"

# The third shape's finder: a process that NAMES a shell, or whose command line carries a busy
# loop in it. It is a CANDIDATE list and nothing more -- the four questions decide, and the
# questions are the exemption's scope.
CANDIDATE_PATTERN = (
    r"(while[[:space:]]+(:|true|false))|(^|[[:space:]/])(ba|z|k|da)?sh([[:space:]]|$)"
)

# The floor: a process that has burned this much CPU on one process has been spinning, not
# working. A minute of CPU on an empty loop is a leak; a compiler that ran for a minute is a
# build, and the build's own question is answered by the code it runs.
BURN_CPU_S = 60.0

# How long the sweep watches a candidate's CPU time grow, and how often it looks.
SAMPLE_S = 1.0
SAMPLE_STEP_S = 0.2

# The shells whose code this rule reads. A process that is not one of these is not running shell
# code, and a shell loop is the shape.
SHELLS = ("sh", "bash", "zsh", "ksh", "dash")

# How much of a script the rule reads. A loader is small; a huge file is not read to the end for
# a loop that is in its first lines.
SCRIPT_BYTES = 65536


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


# ── the third shape: an orphaned process BURNING a core on a busy loop ───────────────────────


def _cpu_seconds(text: str) -> float:
    """The CPU time `ps` reports, in seconds.

    `ps` prints it as `[[dd-]hh:]mm:ss[.ss]`; a day is 86400 seconds and an hour 3600, and a
    value this cannot read is zero -- a process whose CPU time is unknown is not burning.
    """
    text = text.strip()
    if not text:
        return 0.0
    seconds = 0.0
    if "-" in text:
        head, _, text = text.partition("-")
        try:
            seconds = float(head) * 86400
        except ValueError:
            return 0.0
    try:
        numbers = [float(part) for part in text.split(":")]
    except ValueError:
        return 0.0
    for number in numbers:
        seconds = seconds * 60 + number
    return seconds


def _table() -> dict[int, tuple[int, float, str]]:
    """pid -> (parent, CPU seconds, whole command line), from ONE `ps` call.

    One call for the whole machine: a sweep that spawned a `ps` per candidate would be slower
    than the interval it runs at, and a slow sweep is a sweep that overlaps itself.
    """
    result = subprocess.run(
        ["ps", "-ax", "-o", "pid=,ppid=,cputime=,command="],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=30,
        check=False,
    )  # fmt: skip
    out: dict[int, tuple[int, float, str]] = {}
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) < 4:
            continue
        try:
            pid = int(parts[0])
            parent = int(parts[1])
        except ValueError:
            continue
        out[pid] = (parent, _cpu_seconds(parts[2]), parts[3])
    return out


def _words(command: str) -> list[tuple[str, int]]:
    """The command's words, and where each one starts."""
    out = []
    index = 0
    length = len(command)
    while index < length:
        if command[index].isspace():
            index += 1
            continue
        start = index
        while index < length and not command[index].isspace():
            index += 1
        out.append((command[start:index], start))
    return out


def _inline_code(command: str, at: int) -> str | None:
    """The text after a `-c`, as the shell will read it.

    The quotes come off once: the argument of `-c` IS code, and a loop inside it runs. What is
    inside THAT code is judged by the mask, so a loop the code only quotes is still text.
    """
    rest = command[at + 2 :].lstrip()
    if not rest:
        return None
    if rest[0] in "'\"":
        quote = rest[0]
        end = rest.find(quote, 1)
        return rest[1:end] if end > 0 else rest[1:]
    parts = rest.split()
    return parts[0] if parts else None


def _shell_code(command: str) -> str | None:
    """The code a SHELL in this command line runs: an inline `-c` argument, or a script file.

    None when the command line runs no shell, or names no file this rule can read: a script whose
    directory was pruned is a spinner nobody can judge, and it is left alone.
    """
    words = _words(command)
    for index, (word, _at) in enumerate(words):
        if word.rsplit("/", 1)[-1] not in SHELLS:
            continue
        if index + 1 >= len(words):
            continue
        nxt, nxt_at = words[index + 1]
        if nxt == "-c":
            return _inline_code(command, nxt_at)
        try:
            return Path(nxt).read_text(encoding="utf-8", errors="replace")[:SCRIPT_BYTES]
        except OSError:
            return None
    return None


def burning_orphans(candidates: list[int], min_cpu_s: float = BURN_CPU_S) -> list[int]:
    """The candidates that are PROVABLY an orphaned process burning a core on a busy loop.

    Four questions, and none of them alone is enough:

      * the parent is 1: nobody owns it any more, so nobody will ever reap it;
      * it has BURNED: its CPU time is at or above the floor -- a spinner burns, a sleeper does not;
      * it is STILL burning: the CPU time grew between two samples, so a process that finished its
        work and is waiting for something is left alone;
      * the code a SHELL runs contains a busy loop: the inline `-c` argument, or the script the
        command line names, read as CODE -- a loop that is only quoted is text, not a spin.
    """
    table = _table()
    burning = []
    for pid in candidates:
        info = table.get(pid)
        if info is None:
            continue
        parent, cpu, command = info
        if parent != 1:
            continue
        if cpu < min_cpu_s:
            continue
        code = _shell_code(command)
        if code is None:
            continue
        if not busyloop.has_busy_loop(code):
            continue
        burning.append((pid, cpu))
    if not burning:
        return []
    time.sleep(SAMPLE_S)
    later = _table()
    out = []
    for pid, cpu in burning:
        info = later.get(pid)
        if info is None:
            continue
        if info[1] <= cpu:
            continue  # it stopped burning: it finished its work
        out.append(pid)
    return out


def reap_burning_orphans(min_cpu_s: float = BURN_CPU_S) -> Report:
    """The third shape: an orphaned process burning a core on a busy loop.

    THE CLASS (2026-10-21): twenty-four hand-rolled spinners, four cores each, four and a half
    hours old, and a backstop that knew only the house's own command line. This shape is named
    rather than owned: the questions above are what make it ours to kill, and the process they
    name is nobody's.

    The exemption this line needs is recorded in kill_by_name_allow.json: one call, one file,
    with the reason written down. The house's kill-by-name rule SEES this line -- the lookup and
    the kill are on it together -- so a rewrite that moved them apart would be seen too.
    """
    report = Report()
    outcomes = [(pid, kill(pid)) for pid in burning_orphans(pgrep(CANDIDATE_PATTERN), min_cpu_s)]
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
