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

THE SECOND SHAPE (2026-10-20, later the same day). A record can VANISH: an experiment that ran in
a temporary directory leaves its registry there, and that directory is pruned -- eight helpers,
orphaned by a pytest that died, each burning a core for an hour, none of them in a registry the
backstop reads. So the backstop asks one more question: is this process's command line EXACTLY
the house's spinner shape, is its parent 1, and is the owner its release pipe names gone?

That question needs a name lookup, and a name lookup is what the house forbids
(`goh kill-by-name`): it is the RECORDED EXEMPTION, and its narrowness is pinned here in the
negative controls -- a live parent, a live owner, a command line that only LOOKS like the shape,
and a process of another shape are all left alone.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import timing_bounds

import load_kit
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))

import load_registry as reg  # noqa: E402
import load_reap  # noqa: E402

REAPER = REPO_ROOT / "tools" / "load_reap.py"
LIBRARY = REPO_ROOT / "lib" / "load_reap.py"

# ONE WORKER. The backstop's sweep is global: it looks for the house's spinner shape anywhere on
# the machine, so a deliberate orphan made by one test would be found -- and killed -- by a sweep
# another test runs at the same time. These tests share a group so they share a worker, and a
# worker runs its tests one after another.
pytestmark = pytest.mark.xdist_group("load_orphans")


def _orphan(command: list[str] | None = None) -> int:
    """A process whose parent is gone, so the kernel re-parented it to launchd (ppid 1).

    The command line is the one asked for, so the SHAPE of a process can be chosen: the house's
    own spinner shape, or something that only looks like it.
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
    load_kit.own_pid(pid)
    assert load_kit.wait_until(lambda: reg.pid_ppid(pid) == 1), (
        "the fixture did not become an orphan"
    )
    return pid


def _shape_command(fifo: Path) -> list[str]:
    """The house's spinner shape, exactly: the command the load tool spawns."""
    return ["/bin/sh", "-c", reg.spinner_command(fifo)]


def _release(fifo: Path) -> None:
    """Hand a spinner its byte: the pipe's writer end, once the reader is there.

    The spinner waits on the pipe before it burns, so a shape that is never released is a shape
    that exits -- and a test that wants a LIVE spinner has to release it, exactly as the load
    tool does. A command line that never opens the pipe needs no release, and waiting for one
    would wait for a reader that is never coming.
    """
    give_up = timing_bounds.GiveUp(5)
    while True:
        try:
            handle = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
            break
        except OSError:
            if give_up.passed():
                return
            time.sleep(0.01)
    try:
        os.write(handle, b"\n")
    finally:
        os.close(handle)


def _spinner(fifo: Path, command: list[str] | None = None, release: bool = True) -> int:
    """A live process of the house's spinner shape: it waits for its byte, then it spins.

    It has no parent: the kernel re-parents it to launchd (ppid 1), which is the state the
    backstop has to judge.
    """
    os.mkfifo(fifo)
    argv = command if command is not None else _shape_command(fifo)
    pid = _orphan(argv)
    if release:
        _release(fifo)
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
    assert not load_kit.alive(pid), "the orphan outlived the backstop"
    assert not list(tmp_path.glob("*.json")), "the registry outlived the experiment"


def test_the_backstop_leaves_a_spinner_with_a_live_parent_alone(tmp_path: Path) -> None:
    """An experiment in progress: its owner is alive and will reap it."""
    with load_kit.owning(["/bin/sh", "-c", "sleep 300"]) as child:
        proc = child.proc
        assert reg.pid_ppid(proc.pid) != 1
        _register(tmp_path, [{"pid": proc.pid, "start": reg.pid_start(proc.pid) or ""}])
        report = load_reap.reap_all(tmp_path)
        assert proc.pid in report.kept, report
        assert load_kit.alive(proc.pid), "the backstop killed a spinner that still had its owner"
        assert list(tmp_path.glob("*.json")), "the registry of a live experiment was removed"


def test_the_backstop_leaves_an_unregistered_process_alone(tmp_path: Path) -> None:
    """A process no registry names was never ours to kill."""
    with load_kit.owning(["/bin/sh", "-c", "sleep 300"]) as child:
        load_reap.reap_all(tmp_path)
        assert load_kit.alive(child.proc.pid), "the backstop killed a process it had no record of"


def test_the_backstop_drops_a_pid_the_kernel_reused(tmp_path: Path) -> None:
    """A pid is not an identity: the record's start time is what says the process is the same one."""
    with load_kit.owning(["/bin/sh", "-c", "sleep 300"]) as child:
        proc = child.proc
        _register(tmp_path, [{"pid": proc.pid, "start": "Tue Jan  1 00:00:00 1990"}])
        report = load_reap.reap_all(tmp_path)
        assert proc.pid in report.dropped, report
        assert load_kit.alive(proc.pid), "the backstop killed a pid the kernel had reused"


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
        timeout=timing_bounds.patience(60),
        check=False,
    )  # fmt: skip
    assert result.returncode == 0, result.stderr
    assert str(pid) in result.stdout, result.stdout
    assert not load_kit.alive(pid)


def test_the_backstop_kills_by_pid_and_never_by_name(tmp_path: Path) -> None:
    """The house forbids killing by name: a name is not an owner. Every kill the reaper does is
    `os.kill` on a pid, and the ONE name lookup it does is the recorded exemption -- it is
    checked against the house's own shape before anything is touched, and it lives in
    `kill_by_name_allow.json` with its reason written down.

    The lookup and the kill sit on ONE line, so the house's rule sees them together: a rewrite
    that moved them apart would be seen by the rule, and one that widened the line would not
    match the allowlist entry any more.
    """
    for source in (REAPER, LIBRARY):
        text = source.read_text(encoding="utf-8")
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "kill":
                    assert isinstance(node.func.value, ast.Name) and node.func.value.id == "os", (
                        f"{source.name} kills through {node.func.attr}"
                    )
    tree = ast.parse(LIBRARY.read_text(encoding="utf-8"))
    lookups = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "pgrep"
    ]
    assert len(lookups) == 1, "the backstop does one name lookup, and it is the recorded one"
    filters = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "our_orphans"
    ]
    assert len(filters) == 1, "the lookup is filtered by the house's own shape, exactly once"
    kills = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "kill"
    ]
    assert len(kills) >= 1, kills
    assert all(len(kill.args) == 1 for kill in kills), "every kill is ONE pid, never a name"
    assert lookups[0].lineno == filters[0].lineno, (
        "the lookup and the filter are on ONE line, so the rule sees them together"
    )
    assert any(kill.lineno == lookups[0].lineno for kill in kills), (
        "the lookup and the kill are on ONE line, so the rule sees them together"
    )


# ── the second shape: the orphan no registry names ───────────────────────────


def test_the_backstop_kills_an_orphan_no_registry_names(tmp_path: Path) -> None:
    """THE CLASS (2026-10-20). The tool was SIGKILLed, so its registry went with the temporary
    directory that held it, and its spinners were left burning a core each with nothing naming
    them. The backstop finds them by the house's own shape -- exactly that shape, orphaned only."""
    fifo = tmp_path / "release-999999-0.fifo"
    pid = _spinner(fifo)
    assert reg.pid_ppid(pid) == 1, "the fixture did not become an orphan"
    report = load_reap.reap_orphans()
    assert pid in report.killed, report
    assert not load_kit.alive(pid), "the orphan outlived the backstop"


def test_the_backstop_leaves_a_live_parent_alone(tmp_path: Path) -> None:
    """NEGATIVE CONTROL. An experiment in progress: the spinner has a parent, so the parent will
    reap it. The shape is the house's own, and the owner is dead -- only the parent saves it."""
    fifo = tmp_path / "release-999999-0.fifo"
    os.mkfifo(fifo)
    with load_kit.owning(_shape_command(fifo)) as child:
        pid = child.proc.pid
        _release(fifo)
        assert reg.pid_ppid(pid) != 1, "the fixture did not keep its parent"
        report = load_reap.reap_orphans()
        assert pid not in report.killed, report
        assert load_kit.alive(pid), "the backstop killed a spinner whose parent was alive"


def test_the_backstop_leaves_a_spinner_whose_owner_is_alive_alone(tmp_path: Path) -> None:
    """NEGATIVE CONTROL. The pipe names the owner, and the owner is alive: nobody may touch it,
    orphan or not. A pid is not an owner, and neither is a name in a file name."""
    with load_kit.owning(["/bin/sh", "-c", "sleep 300"]) as owner:
        fifo = tmp_path / f"release-{owner.proc.pid}-0.fifo"
        pid = _spinner(fifo)
        assert reg.pid_ppid(pid) == 1, "the fixture did not become an orphan"
        report = load_reap.reap_orphans()
        assert pid not in report.killed, report
        assert load_kit.alive(pid), "the backstop killed a spinner whose owner was alive"


def test_the_backstop_leaves_a_process_of_another_shape_alone(tmp_path: Path) -> None:
    """NEGATIVE CONTROL. Another program's process, orphaned, with the house's words in it: it is
    not the house's spinner, so it is not the house's to kill."""
    pid = _orphan(["/bin/sh", "-c", "sleep 300"])
    report = load_reap.reap_orphans()
    assert pid not in report.killed, report
    assert load_kit.alive(pid), "the backstop killed a process that is not the house's shape"


@pytest.mark.parametrize(
    "command,reads",
    [
        # A PREFIX of the shape, with more after it. It reads the pipe, so it is released.
        ('read _ < "{fifo}" || exit 0; while :; do :; done; echo and more', True),
        # The shape MENTIONED, not run. It never opens the pipe, so nothing releases it.
        ('while :; do :; done; echo "read _ < {fifo} || exit 0"', False),
        # The shape without its quotes. It reads the pipe, so it is released.
        ("read _ < {fifo} || exit 0; while :; do :; done", True),
    ],
)
def test_the_backstop_leaves_a_command_line_that_only_looks_like_the_shape_alone(
    tmp_path: Path, command: str, reads: bool
) -> None:
    """NEGATIVE CONTROL. The shape is matched WHOLE: a command line that starts the same way, or
    mentions the same words, is somebody else's process. A prefix match would kill it."""
    fifo = tmp_path / "release-999999-0.fifo"
    pid = _spinner(fifo, ["/bin/sh", "-c", command.format(fifo=fifo)], release=reads)
    assert reg.pid_ppid(pid) == 1, "the fixture did not become an orphan"
    report = load_reap.reap_orphans()
    assert pid not in report.killed, report
    assert load_kit.alive(pid), "the backstop killed a process that only looks like the shape"


def test_the_minute_job_cleans_a_leak_the_registry_cannot_name(tmp_path: Path) -> None:
    """THE WHOLE CHAIN (2026-10-20). A load experiment is SIGKILLed, so its registry is pruned
    with the temporary directory that held it, and its spinners are left burning a core each with
    nothing naming them. The minute job -- the command the launchd job runs every 60 seconds --
    finds them by the house's own shape and stops them, well inside its interval."""
    with load_kit.owned(tmp_path, 2, ["/bin/sh", "-c", "sleep 30"]) as tool:
        pids = tool.wait_for_spinners()
        tool.proc.kill()  # the tool dies without running its cleanup
        tool.proc.wait(timeout=timing_bounds.patience(30))
        assert load_kit.wait_until(lambda: all(reg.pid_ppid(pid) == 1 for pid in pids)), (
            "the spinners were not re-parented to launchd"
        )
        assert load_kit.alive(pids[0]), "the spinners did not outlive the tool"
        reg.remove(reg.registry_path(tmp_path))  # the record goes with the directory
        started = time.monotonic()
        result = subprocess.run(
            [sys.executable, str(REAPER), "--dir", str(tmp_path)],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=timing_bounds.patience(60),
            check=False,
        )  # fmt: skip
        assert result.returncode == 0, result.stderr
        assert load_kit.wait_until(lambda: not any(load_kit.alive(pid) for pid in pids)), (
            "the minute job did not clean the leak"
        )
        elapsed = time.monotonic() - started
    # The job's interval is 60 s (the launchd plist); the sweep that cleans the leak is seconds.
    timing_bounds.assert_sooner(
        elapsed, expected_s=5.0, failure_s=60.0, what="the minute job cleaned the leak"
    )


def test_the_registry_is_json_a_reader_can_read(tmp_path: Path) -> None:
    """The record is the contract between the tool and the backstop: it is written whole."""
    path = _register(tmp_path, [{"pid": 1234, "start": "Tue Oct 20 12:00:00 2026"}])
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["pids"][0]["pid"] == 1234
    assert data["owner_pid"] == os.getpid()
