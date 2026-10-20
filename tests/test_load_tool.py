"""The ONE way to make load: it reaps itself, and a second experiment cannot collide.

THE CLASS (2026-10-20). An agent that had to reproduce a timing defect spawned its own busy loops
(`for j in $(seq 1 12); do (while :; do :; done) & done`), and the shell that owned them was killed
before it reached its own cleanup. The spinners were re-parented to launchd (ppid 1) and kept
burning a core each for hours -- 24 for 3h12m (load average 206), then 12 for 26m, then 8 for 28m
-- and the load was blamed on other agents. Three times in one day.

`tools/load.py` is the way that cannot leak: it registers the pids it starts, kills exactly those
on the way out, refuses a second experiment while one holds the slot, and -- the case a trap
cannot cover -- it notices that its OWN owner died and cleans up after it.

These tests pin that behaviour, and they are red-first: each names the failure it forbids.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))

import load_registry as reg  # noqa: E402

TOOL = REPO_ROOT / "tools" / "load.py"
REAPER = REPO_ROOT / "tools" / "load_reap.py"
SPINNERS = re.compile(r"spinner\(s\) started: ((?:\d+[ ]?)+)")

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


def _spawn(argv: list[str], **kwargs) -> subprocess.Popen:
    """A child this module owns: the fixture kills it if the test does not."""
    return _own(subprocess.Popen(argv, stdin=subprocess.DEVNULL, **kwargs))


def reg_pid_ppid(pid: int) -> int | None:
    """The process's parent: 1 means the kernel re-parented it to launchd."""
    return reg.pid_ppid(pid)


def _alive(pid: int) -> bool:
    """Is there a live process with this pid? A zombie holds the pid, not the CPU."""
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


def _spinner_pids(output: str) -> list[int]:
    match = SPINNERS.search(output)
    assert match, f"the tool did not name its spinners: {output!r}"
    return [int(part) for part in match.group(1).split()]


def _run(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=timeout,
        check=False,
    )  # fmt: skip


def _wait_for(predicate, seconds: float = 10.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_the_default_load_directory_is_the_house_cache(tmp_path: Path, monkeypatch) -> None:
    """Where the registry lives without being told: the house cache, not a directory beside HOME."""
    monkeypatch.delenv("GOH_LOAD_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert reg.load_dir() == tmp_path / ".cache" / "goh" / "load"


def test_the_load_directory_can_be_named(monkeypatch, tmp_path: Path) -> None:
    """GOH_LOAD_DIR names it, so a test (and a second machine) can keep its own."""
    monkeypatch.setenv("GOH_LOAD_DIR", str(tmp_path / "elsewhere"))
    assert reg.load_dir() == tmp_path / "elsewhere"


def test_the_tool_leaves_no_spinner_and_no_registry(tmp_path: Path) -> None:
    """The whole point: an experiment that finishes leaves nothing behind."""
    result = _run(["--dir", str(tmp_path), "3", "--", "/bin/sh", "-c", "sleep 0.3"])
    assert result.returncode == 0, result.stderr
    pids = _spinner_pids(result.stdout)
    assert len(pids) == 3, result.stdout
    assert not list(tmp_path.glob("*.json")), "the registry outlived the experiment"
    assert not list(tmp_path.glob("*.fifo")), "a release pipe outlived the experiment"
    for pid in pids:
        assert not _alive(pid), f"spinner {pid} is still running"


def test_the_tool_reports_the_commands_own_status(tmp_path: Path) -> None:
    """A load experiment's verdict is the command's, not the tool's."""
    result = _run(["--dir", str(tmp_path), "2", "--", "/bin/sh", "-c", "exit 7"])
    assert result.returncode == 7, (result.returncode, result.stderr)


def test_a_second_experiment_is_refused_while_the_slot_is_held(tmp_path: Path) -> None:
    """Two load experiments at once measure a machine twice as busy, and neither can be told
    from the other afterwards: the slot is one, and the second is told why."""
    _spawn(
        [sys.executable, str(TOOL), "--dir", str(tmp_path), "2", "--", "/bin/sh", "-c", "sleep 30"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert _wait_for(lambda: list(tmp_path.glob("*.json"))), (
        "the first experiment never took the slot"
    )
    second = _run(["--dir", str(tmp_path), "2", "--", "/bin/sh", "-c", "sleep 1"])
    assert second.returncode == 3, (second.returncode, second.stdout, second.stderr)
    assert "already holds the slot" in second.stderr, second.stderr
    assert "tools/load.py --reap" in second.stderr, second.stderr


def test_a_killed_owner_leaves_no_spinner_alive(tmp_path: Path) -> None:
    """The leak of 2026-10-20: the shell that owned the spinners was killed before it could clean
    up. The tool watches for that (its own parent is gone) and cleans up itself."""
    spoken = tmp_path / "spoken.txt"
    with spoken.open("w", encoding="utf-8") as handle:
        owner = _spawn(
            [
                "/bin/sh",
                "-c",
                f'{sys.executable} "{TOOL}" --dir "{tmp_path}" 3 -- /bin/sh -c "sleep 30"; exit 0',
            ],
            stdout=handle,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    assert _wait_for(lambda: SPINNERS.search(spoken.read_text())), "the tool never started"
    pids = _spinner_pids(spoken.read_text())
    _SPAWNED.extend(pids)
    assert len(pids) == 3
    time.sleep(0.5)  # the release byte has been read: they are spinning
    assert all(_alive(pid) for pid in pids), "the spinners never started spinning"
    owner.kill()  # the owner is killed: the tool is re-parented to launchd
    owner.wait(timeout=30)
    assert _wait_for(lambda: not any(_alive(pid) for pid in pids), 20.0), (
        f"the spinners outlived their owner: {pids}"
    )
    assert _wait_for(lambda: not list(tmp_path.glob("*.json")), 20.0), (
        "the registry outlived its owner"
    )


def test_a_sigkilled_tool_leaves_orphans_the_backstop_kills(tmp_path: Path) -> None:
    """The case a trap cannot cover: the tool itself is SIGKILLed, so nothing in it ran. The
    spinners are re-parented to launchd, and the registry is what the backstop reads."""
    spoken = tmp_path / "spoken.txt"
    with spoken.open("w", encoding="utf-8") as handle:
        tool = _spawn(
            [
                sys.executable,
                str(TOOL),
                "--dir",
                str(tmp_path),
                "3",
                "--",
                "/bin/sh",
                "-c",
                "sleep 30",
            ],
            stdout=handle,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    assert _wait_for(lambda: SPINNERS.search(spoken.read_text())), "the tool never started"
    pids = _spinner_pids(spoken.read_text())
    _SPAWNED.extend(pids)
    time.sleep(0.5)
    assert all(_alive(pid) for pid in pids), "the spinners never started spinning"
    tool.kill()  # the tool dies without running its cleanup: the trap never ran
    tool.wait(timeout=30)
    assert _wait_for(lambda: all(reg_pid_ppid(pid) == 1 for pid in pids), 20.0), (
        "the spinners were not re-parented to launchd"
    )
    assert _alive(pids[0]), "the spinners did not outlive the tool"
    reaped = subprocess.run(
        [sys.executable, str(REAPER), "--dir", str(tmp_path)],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )  # fmt: skip
    assert reaped.returncode == 0, reaped.stderr
    assert _wait_for(lambda: not any(_alive(pid) for pid in pids), 20.0), (
        "the backstop did not kill the orphans"
    )
    assert not list(tmp_path.glob("*.json")), "the registry outlived the experiment"


def test_the_tool_refuses_a_raw_backgrounded_busy_loop_at_the_tool_boundary(tmp_path: Path) -> None:
    """The raw form cannot be typed by accident: the Claude Code guard refuses it, and names the
    house tool that does it instead."""
    guard = REPO_ROOT / "hooks" / "claude" / "discard_guard.py"
    command = "for j in $(seq 1 12); do (while :; do :; done) & done"
    result = subprocess.run(
        [sys.executable, str(guard)],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(tmp_path)}),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )  # fmt: skip
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny", out
    reason = out["permissionDecisionReason"]
    assert "tools/load.py" in reason, reason
    assert "load-ok" in reason, reason
