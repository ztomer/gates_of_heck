"""The suite's owner of the load tool's children: every spinner it starts is reaped by it.

THE CLASS (2026-10-20, the second shape). The load tool was right about itself and the suite was
not: `tools/load.py` registers every spinner it starts and kills exactly those on the way out, but
the TEST that started it killed the TOOL with SIGKILL -- so the tool's own cleanup never ran, and
its spinners were re-parented to launchd and left burning a core each. Eight of them were found
alive at 17:40 that day, about sixty minutes of CPU apiece, none of them in a registry the
backstop reads (the registry of an experiment that ran in a temporary directory is pruned with that
directory).

THE RULE. The OWNER of a process is responsible for it. A test that starts a spinner owns it, and
a test that FAILS owns it too: the failure path is the one that leaks, because the cleanup never
runs. So this kit is the owner:

  * `owned` starts the tool and hands back a handle; the handle is reaped when the block ends,
    PASS OR FAIL, because the `finally` runs either way;
  * `reap` asks the tool to stop FIRST (SIGTERM runs its own cleanup, which kills its spinners
    and drops its registry) and only then reads the registry for the spinners a SIGKILLed tool
    left behind -- a tool that was killed cannot clean up after itself, so its owner does;
  * `leaked_spinners` is the suite's own detector: it reads the live process table for the house's
    spinner shape whose release pipe is in a directory THIS SUITE used, and nothing else (a spinner
    of the same shape started by somebody else is an experiment in progress, not the suite's
    business);
  * `suite_leak_check`, the session's last act, proves that nothing the suite started outlived
    it -- and the per-test fixture proves it at the test that leaked, where the name is useful.

THE HOUSE'S OWN RULE IS IN FORCE HERE TOO. `goh unreaped-spawn` judges the test scope: a spawn
no guard reaps is a finding. This kit is that guard, so its own spawns are shaped the way the
house's other test kits shape theirs: the child is bound to a handle the owner holds, and the
owner is what guarantees the handle is acted on.
"""

from __future__ import annotations

import contextlib
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import timing_bounds

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import load_registry as reg

TOOL = ROOT / "tools" / "load.py"
GRACE_S = 5.0

# What the tool says when its spinners are up: "load: 3 spinner(s) started: 101 102 103".
SPINNERS = re.compile(r"spinner\(s\) started: ((?:\d+[ ]?)+)")

# Every child this suite owns, every pid it owns (with the process's START TIME, because a pid
# is not an identity), and every directory it ran one in: the suite owns them all, and the
# session's last act is to prove that none of them outlived the suite.
_OWNED: list[Owned] = []
_OWNED_PIDS: list[tuple[int, str]] = []
_DIRECTORIES: list[Path] = []


def alive(pid: int) -> bool:
    """Is there a live process with this pid? A zombie holds the pid, not the CPU."""
    state = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=10,
        check=False,
    )  # fmt: skip
    text = state.stdout.strip()
    return bool(text) and not text.startswith("Z")


def live_processes() -> list[tuple[int, int, str]]:
    """Every live process: its pid, its parent, and its whole command line."""
    result = subprocess.run(
        ["ps", "-ww", "-ax", "-o", "pid=,ppid=,command="],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=30,
        check=False,
    )  # fmt: skip
    out: list[tuple[int, int, str]] = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) != 3:
            continue
        try:
            out.append((int(parts[0]), int(parts[1]), parts[2]))
        except ValueError:
            continue
    return out


def leaked_spinners(roots: list[str] | None = None) -> list[tuple[int, str]]:
    """Live spinners of the house's shape whose release pipe is in a directory this suite used.

    The pipe's directory is the scope: a spinner of the same shape started by somebody ELSE (an
    experiment in progress, in the house's own load directory) is not the suite's business. A
    pipe whose directory was PRUNED still counts -- the path is in the command line either way,
    which is how the eight orphans of 2026-10-20 were found.

    `roots` widens the scope to the run's own temporary root: a test that started a spinner
    WITHOUT this kit still put it in a directory under the run's temp root, and the leak is the
    leak whatever the test did.
    """
    scope = [f"{directory}/" for directory in _DIRECTORIES]
    scope.extend(f"{root}/" for root in roots or [])
    out: list[tuple[int, str]] = []
    for pid, _parent, command in live_processes():
        shape = reg.spinner_shape(command)
        if shape is None:
            continue
        pipe, _owner = shape
        if not any(pipe.startswith(prefix) for prefix in scope):
            continue
        out.append((pid, command))
    return out


def registered(directory: Path) -> list[int]:
    """The pids the tool registered: the spinners it started, which the suite owns too."""
    data = reg.read(reg.registry_path(directory)) or {}
    entries = data.get("pids")
    pids: list[int] = []
    for entry in entries if isinstance(entries, list) else []:
        try:
            pids.append(int(entry["pid"]))
        except (KeyError, TypeError, ValueError):
            continue
    return [pid for pid in pids if pid > 0]


def own_pid(pid: int) -> int:
    """Take ownership of a pid the suite started -- a process a helper spawned, not a Popen here.

    The suite owns every process it starts, however it started it: a helper that spawns a child
    and exits leaves that child orphaned, and nobody else will ever reap it.

    The START TIME is recorded with it, because a pid is not an identity: the kernel reuses the
    number, and a pid this suite reaped an hour ago may name somebody else's process now. The
    house's own registry records it for the same reason, and the reaper drops a pid whose start
    time changed rather than killing it.
    """
    if pid > 0 and not any(owned == pid for owned, _start in _OWNED_PIDS):
        _OWNED_PIDS.append((pid, reg.pid_start(pid) or ""))
    return pid


def kill_pid(pid: int, start: str = "") -> bool:
    """Stop a pid the suite owns, and say whether it is gone.

    The start time is the guard: a pid whose process is not the one the suite started is NOT
    killed, because that process belongs to somebody else now.

    And the answer is WAITED for: a SIGKILLed process is still there for a moment, and a check
    that reads the table once would call a killed process a survivor. The wait is bounded by a
    give-up deadline, so a process that will not die is named rather than waited on forever.
    """
    if not alive(pid):
        return True
    if start and reg.pid_start(pid) != start:
        return True  # the number was reused: that process is not ours
    with contextlib.suppress(ProcessLookupError, OSError):
        os.kill(pid, signal.SIGKILL)
    give_up = timing_bounds.GiveUp(5)
    while not give_up.passed():
        if not alive(pid):
            return True
        time.sleep(0.02)
    return not alive(pid)


def orphan(command: list[str] | None = None) -> int:
    """A process whose parent is gone, so the kernel re-parented it to launchd (ppid 1).

    The command line is the one asked for, so the SHAPE of a process can be chosen: the house's
    own spinner shape, a hand-rolled one, or something that only looks like it. The helper that
    starts it EXITS, so the child is orphaned -- which is the state the backstop has to judge.

    The suite owns the pid it returns (`own_pid`), so a test that fails still reaps it.
    """
    argv = command if command is not None else ["/bin/sh", "-c", "sleep 300"]
    helper = subprocess.run(
        [
            sys.executable,
            "-c",
            "import subprocess,sys;"
            "p=subprocess.Popen(sys.argv[1:], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,"
            "stderr=subprocess.DEVNULL, start_new_session=True);"
            "print(p.pid)",
            *argv,
        ],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=30,
        check=True,
    )  # fmt: skip
    pid = int(helper.stdout.strip())
    own_pid(pid)
    assert wait_until(lambda: reg.pid_ppid(pid) == 1), "the fixture did not become an orphan"
    return pid


def wait_until(predicate, seconds: float = 20.0) -> bool:
    """Wait for the predicate, giving up on the box's own patience (the house's deadline).

    A give-up deadline, not a literal wait: the load is re-read at every check, so a wait that
    begins on a quiet box and runs into a busy one still stretches (`timing_bounds`).
    """
    give_up = timing_bounds.GiveUp(seconds)
    while not give_up.passed():
        if predicate():
            return True
        time.sleep(0.05)
    return False


class Owned:
    """A process the suite owns: it is stopped, and reaped, when the suite is done with it.

    The child is bound to a handle the owner holds, which is the shape the house's other test kits
    use (`tests/test_gate_slot.py`): the handle is what `stop` acts on, and the owner is what
    guarantees it is acted on.
    """

    def __init__(self, argv: list[str], log: Path | None = None, **kwargs) -> None:
        self.log = log
        self.directory: Path | None = None
        handle = log.open("w", encoding="utf-8") if log is not None else subprocess.DEVNULL
        self.proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            **kwargs,
        )
        if log is not None:
            handle.close()
        _OWNED.append(self)

    def spoken(self) -> str:
        """What the child has said so far."""
        if self.log is None:
            return ""
        try:
            return self.log.read_text(encoding="utf-8")
        except OSError:
            return ""

    def spinner_pids(self) -> list[int]:
        """The pids the child reported: the spinners it started.

        Read from the child's own log, so a tool started THROUGH another shell (the owner-killed
        case) is covered too: the log is where the tool says what it started.
        """
        match = SPINNERS.search(self.spoken())
        if match is None:
            return []
        return [int(part) for part in match.group(1).split()]

    def wait_for_spinners(self, seconds: float = 30.0) -> list[int]:
        """Wait until the tool has started its spinners, and take ownership of those pids.

        A give-up deadline, not a literal wait: the box's own load stretches it (`timing_bounds`).
        """
        give_up = timing_bounds.GiveUp(seconds)
        while not give_up.passed():
            pids = self.spinner_pids()
            if pids:
                for pid in pids:
                    own_pid(pid)
                return pids
            time.sleep(0.05)
        raise AssertionError(f"the tool never started its spinners: {self.spoken()!r}")

    def stop(self) -> None:
        """Ask the child to stop, then insist.

        SIGTERM FIRST, because the load tool's own handlers run its cleanup when it gets one: that
        cleanup kills the spinners and drops the registry, and a tool that was asked to stop does
        not need its owner to guess what it left behind.
        """
        if self.proc.poll() is not None:
            return
        with contextlib.suppress(ProcessLookupError, OSError):
            self.proc.terminate()
        try:
            self.proc.wait(timeout=GRACE_S)
            return
        except subprocess.TimeoutExpired:
            pass
        # It ignored the polite word. The child leads its own process group (start_new_session),
        # so the group takes the command it started with it -- and nothing else: the spinners have
        # sessions of their own and are reaped by pid, one by one, below.
        with contextlib.suppress(ProcessLookupError, OSError):
            os.killpg(self.proc.pid, signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            self.proc.wait(timeout=GRACE_S)


class Tool(Owned):
    """The load tool, and the spinners it starts: both of them are the suite's to reap."""

    def __init__(self, directory: Path, count: int, command: list[str], **kwargs) -> None:
        super().__init__(
            [sys.executable, str(TOOL), "--dir", str(directory), str(count), "--", *command],
            log=directory / "tool.out",
            **kwargs,
        )
        self.directory = directory
        _DIRECTORIES.append(directory)


@contextlib.contextmanager
def owning(argv: list[str], log: Path | None = None, **kwargs):
    """A child the suite owns: a test that FAILS inside here still leaves nothing behind."""
    child = Owned(argv, log=log, **kwargs)
    try:
        yield child
    finally:
        reap()


@contextlib.contextmanager
def owned(directory: Path, count: int, command: list[str], **kwargs):
    """The load tool, and the reaping of it: a test that FAILS inside here still leaves nothing."""
    tool = Tool(directory, count, command, **kwargs)
    try:
        yield tool
    finally:
        reap()


def reap() -> list[int]:
    """Stop every process the suite started, and every spinner it left, and report the survivors.

    The order is the point. The tool is asked to stop FIRST -- SIGTERM runs its own cleanup,
    which kills its spinners and drops its registry -- and only then is the registry read for the
    spinners a SIGKILLed tool left behind. A test that failed before it could clean up still
    leaves nothing behind, because the owner is responsible for what it started.
    """
    survivors: list[int] = []
    for child in _OWNED:
        child.stop()
        if child.proc.poll() is None:
            survivors.append(child.proc.pid)
    for child in _OWNED:
        if child.directory is not None:
            for pid in registered(child.directory):
                own_pid(pid)
        for pid in child.spinner_pids():
            own_pid(pid)
    for pid, start in _OWNED_PIDS:
        if not alive(pid):
            continue
        if not kill_pid(pid, start):
            survivors.append(pid)
    for child in _OWNED:
        if child.directory is not None:
            reg.remove(reg.registry_path(child.directory))
    return survivors


def command_of(pid: int) -> str:
    """The whole command line of a pid, or "" when it cannot be read."""
    result = subprocess.run(
        ["ps", "-ww", "-o", "command=", "-p", str(pid)],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=10,
        check=False,
    )  # fmt: skip
    return result.stdout.strip()


def _clean(leaked: list[tuple[int, str]]) -> None:
    """Stop the spinners a leak left behind: a reported leak that keeps burning a core is only
    half reported.

    The command line is the guard, the way the start time is elsewhere: the process under the pid
    is the one that was found, or it is somebody else's now.
    """
    for pid, command in leaked:
        if command_of(pid) != command:
            continue
        kill_pid(pid)


@pytest.fixture(autouse=True)
def load_children(tmp_path_factory):
    """Every load child this test started is reaped when the test ends -- PASS OR FAIL.

    The failure path is the one that leaks: the cleanup never runs when a test dies in the middle.
    So the reaping is the fixture's, not the test's.

    A test that started a child is also CHECKED for a leak the kit could not account for -- one
    started without the kit, say -- and the check names the directory the spinner was left in,
    which is the test's own directory, so it says which test leaked.
    """
    started = len(_OWNED)
    yield
    survivors = reap()
    assert not survivors, f"the test left load processes running: {survivors}"
    if len(_OWNED) > started:
        leaked = leaked_spinners([str(tmp_path_factory.getbasetemp())])
        _clean(leaked)
        assert not leaked, f"the test left spinners running: {leaked}"


@pytest.fixture(scope="session", autouse=True)
def suite_leak_check(tmp_path_factory):
    """The suite's last act: nothing it started outlived it.

    The leak of 2026-10-20 was eight helpers, orphaned by a pytest that died, each burning a
    core for an hour. The test that started them was the OWNER, and the owner is responsible for
    them -- so this is that ownership, proved at the end of the run rather than hoped for.

    The scope is the run's own temporary root as well as the kit's directories: a spinner left
    under either one outlived the suite, and the suite is what has to answer for it.
    """
    roots = [str(tmp_path_factory.getbasetemp())]
    yield
    survivors = reap()
    leaked = leaked_spinners(roots)
    _clean(leaked)
    assert not survivors, f"the suite left processes running: {survivors}"
    assert not leaked, f"the suite left spinners running: {leaked}"
