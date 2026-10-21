"""The ONE way to make load: it reaps itself, and a second experiment cannot collide.

THE CLASS (2026-10-20). An agent that had to reproduce a timing defect spawned its own busy loops
(`for j in $(seq 1 12); do (while :; do :; done) & done`), and the shell that owned them was killed
before it reached its own cleanup. The spinners were re-parented to launchd (ppid 1) and kept
burning a core each for hours -- 24 for 3h12m (load average 206), then 12 for 26m, then 8 for 28m
-- and the load was blamed on other agents. Three times in one day.

`tools/load.py` is the way that cannot leak: it registers the pids it starts, kills exactly those
on the way out, refuses a second experiment while one holds the slot, and -- the case a trap
cannot cover -- it notices that its OWN owner died and cleans up after it.

THE OWNER IS THE SUITE (2026-10-20, later the same day). The tool was right about itself and
these tests were not: they killed the TOOL with SIGKILL, so its cleanup never ran, and its
spinners were left burning a core each -- eight of them, about an hour of CPU apiece. A test that
starts a spinner OWNS it, and a test that FAILS owns it too, because the failure path is the one
that never reaches the cleanup. So every tool here runs inside `load_kit.owned`, whose `finally`
reaps it and its spinners whether the test passes or fails, and `load_kit` proves at the end of
the session that nothing the suite started outlived it.

These tests pin that behaviour, and they are red-first: each names the failure it forbids.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import timing_bounds

import load_kit
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))

import load_registry as reg  # noqa: E402

TOOL = REPO_ROOT / "tools" / "load.py"
REAPER = REPO_ROOT / "tools" / "load_reap.py"
SPINNERS = re.compile(r"spinner\(s\) started: ((?:\d+[ ]?)+)")

# ONE WORKER (with tests/test_load_reap.py): the backstop's sweep is global, so a deliberate
# orphan made by one test would be found -- and killed -- by a sweep another test runs at the
# same time. A shared group keeps them on one worker, and a worker runs its tests one by one.
pytestmark = pytest.mark.xdist_group("load_orphans")


def _spinner_pids(output: str) -> list[int]:
    match = SPINNERS.search(output)
    assert match, f"the tool did not name its spinners: {output!r}"
    return [int(part) for part in match.group(1).split()]


def _run(args: list[str], timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    """The tool, run to its end: its own exit status, and what it said.

    A deadline stretched by the box's load, and a refusal on expiry: a tool that hangs is a
    defect, not a slow pass.
    """
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=timing_bounds.patience(timeout),
        check=False,
    )  # fmt: skip


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
        assert not load_kit.alive(pid), f"spinner {pid} is still running"


def test_the_tool_reports_the_commands_own_status(tmp_path: Path) -> None:
    """A load experiment's verdict is the command's, not the tool's."""
    result = _run(["--dir", str(tmp_path), "2", "--", "/bin/sh", "-c", "exit 7"])
    assert result.returncode == 7, (result.returncode, result.stderr)


def test_a_second_experiment_is_refused_while_the_slot_is_held(tmp_path: Path) -> None:
    """Two load experiments at once measure a machine twice as busy, and neither can be told
    from the other afterwards: the slot is one, and the second is told why."""
    with load_kit.owned(tmp_path, 2, ["/bin/sh", "-c", "sleep 30"]) as tool:
        tool.wait_for_spinners()
        second = _run(["--dir", str(tmp_path), "2", "--", "/bin/sh", "-c", "sleep 1"])
        assert second.returncode == 3, (second.returncode, second.stdout, second.stderr)
        assert "already holds the slot" in second.stderr, second.stderr
        assert "tools/load.py --reap" in second.stderr, second.stderr


def test_a_killed_owner_leaves_no_spinner_alive(tmp_path: Path) -> None:
    """The leak of 2026-10-20: the shell that owned the spinners was killed before it could clean
    up. The tool watches for that (its own parent is gone) and cleans up itself."""
    spoken = tmp_path / "spoken.txt"
    argv = [
        "/bin/sh",
        "-c",
        f'{sys.executable} "{TOOL}" --dir "{tmp_path}" 3 -- /bin/sh -c "sleep 30"; exit 0',
    ]
    with load_kit.owning(argv, log=spoken) as owner:
        pids = owner.wait_for_spinners()
        assert len(pids) == 3
        owner.proc.kill()  # the owner is killed: the tool is re-parented to launchd
        owner.proc.wait(timeout=timing_bounds.patience(30))
        assert load_kit.wait_until(lambda: not any(load_kit.alive(pid) for pid in pids)), (
            f"the spinners outlived their owner: {pids}"
        )
        assert load_kit.wait_until(lambda: not list(tmp_path.glob("*.json"))), (
            "the registry outlived its owner"
        )


def test_a_sigkilled_tool_leaves_orphans_the_backstop_kills(tmp_path: Path) -> None:
    """The case a trap cannot cover: the tool itself is SIGKILLed, so nothing in it ran. The
    spinners are re-parented to launchd, and the registry is what the backstop reads."""
    with load_kit.owned(tmp_path, 3, ["/bin/sh", "-c", "sleep 30"]) as tool:
        pids = tool.wait_for_spinners()
        tool.proc.kill()  # the tool dies without running its cleanup: the trap never ran
        tool.proc.wait(timeout=timing_bounds.patience(30))
        assert load_kit.wait_until(lambda: all(reg.pid_ppid(pid) == 1 for pid in pids)), (
            "the spinners were not re-parented to launchd"
        )
        assert load_kit.alive(pids[0]), "the spinners did not outlive the tool"
        reaped = subprocess.run(
            [sys.executable, str(REAPER), "--dir", str(tmp_path)],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=timing_bounds.patience(60),
            check=False,
        )  # fmt: skip
        assert reaped.returncode == 0, reaped.stderr
        assert load_kit.wait_until(lambda: not any(load_kit.alive(pid) for pid in pids)), (
            "the backstop did not kill the orphans"
        )
        assert not list(tmp_path.glob("*.json")), "the registry outlived the experiment"


def test_the_leak_detector_finds_a_spinner_nobody_owns(tmp_path: Path) -> None:
    """THE INSTRUMENT BITES. A check that cannot see a leak cannot rule one out, so the
    detector is shown a real one: a spinner of the house's shape, left in a directory this suite
    used, is FOUND. (This is the red-first half of the leak check, in the suite itself: the
    whole session's check is the same detector, and it is what went red on the suite of
    2026-10-20.)"""
    fifo = tmp_path / "release-999999-0.fifo"
    os.mkfifo(fifo)
    with load_kit.owning(["/bin/sh", "-c", reg.spinner_command(fifo)]) as child:
        found = load_kit.leaked_spinners([str(tmp_path)])
        assert [pid for pid, _command in found] == [child.proc.pid], found


def test_a_failing_test_inside_the_owner_leaves_nothing_behind(tmp_path: Path) -> None:
    """THE FAILURE PATH (2026-10-20). A test that dies in the middle never reaches its own
    cleanup, so the OWNER has to do it: the eight orphans of that day were a pytest that died
    between starting the tool and recording what it had started."""
    with pytest.raises(RuntimeError):
        with load_kit.owned(tmp_path, 2, ["/bin/sh", "-c", "sleep 30"]) as tool:
            tool.wait_for_spinners()
            raise RuntimeError("the test failed")
    assert not load_kit.leaked_spinners(), "the failed test left spinners running"


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
