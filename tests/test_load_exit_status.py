"""load.py exits with the wrapped command's status, as a shell does (GOH.LOADRC, 2026-11-13).

THE CLASS: a load wrapper that swallows its child's exit status turns every refused push and red
gate run through it into a pass (the failure-path no-op, skill `failure-path-no-op`, mechanism 3).
Python's `Popen.wait()` reports a signal death as a NEGATIVE number, and `sys.exit(-15)` is the
byte 241, which no shell reads as "killed by SIGTERM" (143). And when the tool's OWNER died, the
watchdog killed the command and then exited 0: a killed, never-finished command reported success.

Each test names the status it forbids.
"""

from __future__ import annotations

import importlib.util
import signal
import subprocess
import sys
from pathlib import Path

import pytest
import timing_bounds
from conftest import REPO_ROOT

TOOL = REPO_ROOT / "tools" / "load.py"

pytestmark = pytest.mark.xdist_group("load_orphans")


def _run(directory: Path, spinners: str, *command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL), "--dir", str(directory), spinners, "--", *command],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=timing_bounds.patience(60.0),
        check=False,
    )  # fmt: skip


@pytest.mark.parametrize("spinners", ["0", "2"])
@pytest.mark.parametrize("status", [1, 3, 7, 126, 255])
def test_a_failing_command_fails_the_tool_with_its_own_status(
    tmp_path: Path, spinners: str, status: int
) -> None:
    result = _run(tmp_path, spinners, "/bin/sh", "-c", f"exit {status}")
    assert result.returncode == status, (result.returncode, result.stderr)


@pytest.mark.parametrize("spinners", ["0", "2"])
@pytest.mark.parametrize("number", [signal.SIGTERM, signal.SIGKILL, signal.SIGINT, signal.SIGSEGV])
def test_a_command_killed_by_a_signal_exits_128_plus_it_as_a_shell_does(
    tmp_path: Path, spinners: str, number: signal.Signals
) -> None:
    """Was 256-N (241 for SIGTERM): a negative Popen status handed to sys.exit."""
    result = _run(tmp_path, spinners, "/bin/sh", "-c", f"kill -{int(number)} $$")
    assert result.returncode == 128 + int(number), (result.returncode, result.stderr)


def test_a_succeeding_command_still_exits_zero(tmp_path: Path) -> None:
    result = _run(tmp_path, "0", "/bin/sh", "-c", "exit 0")
    assert result.returncode == 0, (result.returncode, result.stderr)


def _load_module():
    spec = importlib.util.spec_from_file_location("goh_load_tool_under_test", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_an_owner_that_died_is_never_reported_as_success(monkeypatch) -> None:
    """The watchdog kills the command when the owner is gone; the command did not finish, so the
    exit is not 0. Was `os._exit(0)`."""
    module = _load_module()
    exits: list[int] = []

    class Stop(Exception):
        pass

    def fake_exit(code: int) -> None:
        exits.append(code)
        raise Stop

    class FakeExperiment:
        def abort(self) -> list[int]:
            return []

    monkeypatch.setattr(module.os, "getppid", lambda: 1)
    monkeypatch.setattr(module.os, "_exit", fake_exit)
    monkeypatch.setattr(module.time, "sleep", lambda _s: None)
    with pytest.raises(Stop):
        module._watch_parent(FakeExperiment(), 4242)
    assert exits and exits[0] != 0, exits
    assert exits[0] == 128 + int(signal.SIGHUP), exits
