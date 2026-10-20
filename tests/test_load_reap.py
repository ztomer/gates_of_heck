"""The backstop: it kills a registered orphan, and NOTHING else.

A trap cannot catch an orphan -- it does not run when its owner is SIGKILLed -- so the machine
runs a minute job that reads the registry the load tool wrote and kills the pids in it whose
parent is 1. A backstop that kills what it cannot prove is ours is worse than no backstop: it
kills other people's work.

So the narrowness is pinned here, in both directions:

  * a registered orphan (ppid 1) is killed;
  * a registered spinner with a LIVE parent is left alone -- that is an experiment in progress;
  * a process no registry names is left alone -- it was never ours;
  * a pid whose start time no longer matches the record is dropped, not killed -- the kernel
    reused the number, so the process under it is something else.
"""

from __future__ import annotations

import ast
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))

import load_registry as reg  # noqa: E402
import load_reap  # noqa: E402

REAPER = REPO_ROOT / "tools" / "load_reap.py"
LIBRARY = REPO_ROOT / "lib" / "load_reap.py"

# Every child this module starts, so a FAILING test cannot leave the very orphans it is testing
# for. The same rule the module enforces on other code, applied to itself.
_SPAWNED: list[int] = []


@pytest.fixture(autouse=True)
def _no_strays():
    """Reap the children a failed assertion would otherwise leave behind."""
    yield
    for pid in _SPAWNED:
        try:
            os.kill(pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass


def _own(proc: subprocess.Popen) -> subprocess.Popen:
    """Register a child, so the fixture kills it if the test does not."""
    _SPAWNED.append(proc.pid)
    return proc


def _spawn(argv: list[str]) -> subprocess.Popen:
    """A child this module owns: the fixture kills it if the test does not."""
    return _own(subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL))


def _alive(pid: int) -> bool:
    result = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=10,
        check=False,
    )  # fmt: skip
    state = result.stdout.strip()
    return bool(state) and not state.startswith("Z")


def _orphan() -> int:
    """A process whose parent is gone, so the kernel re-parented it to launchd (ppid 1)."""
    helper = subprocess.run(
        [
            sys.executable,
            "-c",
            "import subprocess,sys;"
            "p=subprocess.Popen(['/bin/sh','-c','sleep 300'],"
            "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
            "print(p.pid)",
        ],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=30,
        check=True,
    )  # fmt: skip
    pid = int(helper.stdout.strip())
    _SPAWNED.append(pid)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and reg.pid_ppid(pid) != 1:
        time.sleep(0.05)
    return pid


def _register(directory: Path, entries: list[dict]) -> Path:
    path = reg.registry_path(directory)
    reg.write(
        path,
        {
            "version": reg.VERSION,
            "owner_pid": os.getpid(),
            "owner_start": reg.pid_start(os.getpid()) or "",
            "started_at": time.time(),
            "command": ["fixture"],
            "pids": entries,
        },
    )
    return path


def test_the_backstop_kills_a_registered_orphan(tmp_path: Path) -> None:
    """The leak, caught by the record: the owner is gone, the spinner is ours, it goes."""
    pid = _orphan()
    assert reg.pid_ppid(pid) == 1, "the fixture did not become an orphan"
    _register(tmp_path, [{"pid": pid, "start": reg.pid_start(pid) or ""}])
    report = load_reap.reap_all(tmp_path)
    assert pid in report.killed, report
    assert not _alive(pid), "the orphan outlived the backstop"
    assert not list(tmp_path.glob("*.json")), "the registry outlived the experiment"


def test_the_backstop_leaves_a_spinner_with_a_live_parent_alone(tmp_path: Path) -> None:
    """An experiment in progress: its owner is alive and will reap it."""
    proc = _spawn(["/bin/sh", "-c", "sleep 300"])
    assert reg.pid_ppid(proc.pid) != 1
    _register(tmp_path, [{"pid": proc.pid, "start": reg.pid_start(proc.pid) or ""}])
    report = load_reap.reap_all(tmp_path)
    assert proc.pid in report.kept, report
    assert _alive(proc.pid), "the backstop killed a spinner that still had its owner"
    assert list(tmp_path.glob("*.json")), "the registry of a live experiment was removed"


def test_the_backstop_leaves_an_unregistered_process_alone(tmp_path: Path) -> None:
    """A process no registry names was never ours to kill."""
    proc = _spawn(["/bin/sh", "-c", "sleep 300"])
    load_reap.reap_all(tmp_path)
    assert _alive(proc.pid), "the backstop killed a process it had no record of"


def test_the_backstop_drops_a_pid_the_kernel_reused(tmp_path: Path) -> None:
    """A pid is not an identity: the record's start time is what says the process is the same one."""
    proc = _spawn(["/bin/sh", "-c", "sleep 300"])
    _register(tmp_path, [{"pid": proc.pid, "start": "Tue Jan  1 00:00:00 1990"}])
    report = load_reap.reap_all(tmp_path)
    assert proc.pid in report.dropped, report
    assert _alive(proc.pid), "the backstop killed a pid the kernel had reused"


def test_the_backstop_removes_the_registry_when_every_spinner_is_gone(tmp_path: Path) -> None:
    """A record of nothing is a record that blocks the next experiment: it goes."""
    path = _register(tmp_path, [{"pid": 999999, "start": "Tue Jan  1 00:00:00 1990"}])
    report = load_reap.reap_all(tmp_path)
    assert report.removed == [str(path)], report
    assert not path.exists()


def test_the_backstop_removes_the_release_pipes_of_a_dead_owner(tmp_path: Path) -> None:
    """A spinner waits on its release pipe until the byte arrives: a pipe whose owner died is a
    spinner that will never be released, and a file nobody will ever read."""
    fifo = tmp_path / "release-999999-0.fifo"
    os.mkfifo(fifo)
    live = tmp_path / "release-1-0.fifo"  # pid 1 is alive, and never ours
    os.mkfifo(live)
    gone = load_reap.sweep_release_pipes(tmp_path)
    assert str(fifo) in gone, gone
    assert not fifo.exists()
    assert live.exists(), "a live owner's release pipe was removed"


def test_the_backstop_runs_as_the_minute_job(tmp_path: Path) -> None:
    """The job the machine runs: it kills the orphan and says so, and it exits 0."""
    pid = _orphan()
    _register(tmp_path, [{"pid": pid, "start": reg.pid_start(pid) or ""}])
    result = subprocess.run(
        [sys.executable, str(REAPER), "--dir", str(tmp_path)],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )  # fmt: skip
    assert result.returncode == 0, result.stderr
    assert str(pid) in result.stdout, result.stdout
    assert not _alive(pid)


def test_the_backstop_kills_by_pid_and_never_by_name(tmp_path: Path) -> None:
    """The house forbids killing by name: a name is not an owner. The reaper may only ever name a
    pid it took from its own registry -- so every kill it does is `os.kill` on a pid, and it
    spawns no process at all (a spawned killer would be one whose matching it cannot read)."""
    for source in (REAPER, LIBRARY):
        text = source.read_text(encoding="utf-8")
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "kill":
                    assert isinstance(node.func.value, ast.Name) and node.func.value.id == "os", (
                        f"{source.name} kills through {node.func.attr}"
                    )
        assert "subprocess" not in text, f"{source.name} spawns a process to do its killing"


def test_the_registry_is_json_a_reader_can_read(tmp_path: Path) -> None:
    """The record is the contract between the tool and the backstop: it is written whole."""
    path = _register(tmp_path, [{"pid": 1234, "start": "Tue Oct 20 12:00:00 2026"}])
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["pids"][0]["pid"] == 1234
    assert data["owner_pid"] == os.getpid()
