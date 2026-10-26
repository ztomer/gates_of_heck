"""A load experiment refuses a command it would starve by construction (G1f, 2026-10-25).

THE CLASS. `load.py 32 -- taskpolicy -b loop.sh` ran 10h14m at load average ~154, and
`load.py 16 -- taskpolicy -b python3 -m pytest` was killed by its 20-minute deadline the same day.
A command clamped to background priority under N spinners at normal priority gets a core only
when one is idle, and the spinners exist so that none is: the experiment cannot measure anything,
it can only hang. The owner's words: "there are multiple spinners in the background. are they
required for anything?"

So `tools/load.py` refuses a wrapped command whose argv lowers its own CPU priority (taskpolicy
-b / -c background|utility|maintenance, nice >= 10, renice >= 10, chrt --idle, after env/sudo-style
prefixes and inside `sh -c`), names why and the escape (`--background-ok <reason>`), and the
Claude Code Bash guard refuses the same command line at the tool boundary. ONE definition of
"background priority" (`lib/load_priority.py`) serves both.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import timing_bounds

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))

import load_priority  # noqa: E402

TOOL = REPO_ROOT / "tools" / "load.py"
GUARD = REPO_ROOT / "hooks" / "claude" / "discard_guard.py"

pytestmark = pytest.mark.xdist_group("load_orphans")

STARVED = [
    ["taskpolicy", "-b", "./loop.sh"],
    ["/usr/sbin/taskpolicy", "-b", "python3", "-m", "pytest"],
    ["taskpolicy", "-c", "background", "./x"],
    ["taskpolicy", "-c", "utility", "./x"],
    ["taskpolicy", "-c", "maintenance", "./x"],
    ["nice", "-n", "19", "./x"],
    ["nice", "-n19", "./x"],
    ["nice", "-19", "./x"],
    ["nice", "--adjustment=15", "./x"],
    ["nice", "./x"],
    ["renice", "-n", "19", "-p", "123"],
    ["chrt", "-i", "0", "./x"],
    ["chrt", "--idle", "0", "./x"],
    ["env", "-i", "FOO=1", "taskpolicy", "-b", "./x"],
    ["sudo", "-u", "me", "nice", "-n", "19", "./x"],
    ["nohup", "timeout", "60", "taskpolicy", "-b", "./x"],
    ["FOO=1", "taskpolicy", "-b", "./x"],
    ["/bin/sh", "-c", "cd /tmp && taskpolicy -b swift test"],
    ["bash", "-lc", "nice -n 19 ./x"],
]

NORMAL = [
    ["./loop.sh"],
    ["python3", "-m", "pytest"],
    ["taskpolicy", "-c", "user-interactive", "./x"],
    ["nice", "-n", "0", "./x"],
    ["nice", "-n", "-5", "./x"],
    ["env", "FOO=1", "python3", "x.py"],
    ["/bin/sh", "-c", "echo taskpolicy -b; true"],
    ["sudo", "./x"],
]


@pytest.mark.parametrize("argv", STARVED, ids=lambda argv: " ".join(argv))
def test_a_background_priority_command_is_named(argv: list[str]) -> None:
    assert load_priority.background_wrapper(argv), argv


@pytest.mark.parametrize("argv", NORMAL, ids=lambda argv: " ".join(argv))
def test_a_normal_priority_command_is_not(argv: list[str]) -> None:
    assert load_priority.background_wrapper(argv) is None, argv


def _tool(args: list[str], tmp_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL), "--dir", str(tmp_path), *args],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=timing_bounds.patience(60),
        check=False,
        env={**os.environ, "GOH_LOAD_NCPU": "64"},
    )  # fmt: skip


@pytest.mark.parametrize(
    "command",
    [
        ["taskpolicy", "-b", "/bin/sh", "-c", "true"],
        ["nice", "-n", "19", "/bin/sh", "-c", "true"],
        ["env", "A=1", "/bin/sh", "-c", "nice -n 19 true"],
    ],
    ids=lambda argv: " ".join(argv),
)
def test_the_tool_refuses_a_starved_command_before_any_spinner(
    tmp_path: Path, command: list[str]
) -> None:
    """The 10h14m run and the deadline-killed pytest: refused, with why and the way out, and
    not one spinner started."""
    result = _tool(["2", "--", *command], tmp_path)
    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)
    assert "background priority" in result.stderr, result.stderr
    assert "starved" in result.stderr and "--background-ok" in result.stderr, result.stderr
    assert "spinner(s) started" not in result.stdout, result.stdout
    assert not list(tmp_path.glob("*.json")), "a refused experiment took the slot"


def test_the_escape_takes_a_reason_and_then_runs(tmp_path: Path) -> None:
    """Starvation measured on purpose: the escape names its reason, and the experiment runs."""
    command = ["nice", "-n", "19", "/bin/sh", "-c", "true"]
    result = _tool(["--background-ok", "measuring the QoS clamp", "1", "--", *command], tmp_path)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert "spinner(s) started" in result.stdout, result.stdout


@pytest.mark.parametrize("reason", ["", "   "])
def test_an_escape_with_no_reason_excuses_nothing(tmp_path: Path, reason: str) -> None:
    result = _tool(["--background-ok", reason, "1", "--", "nice", "-n", "19", "true"], tmp_path)
    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)
    assert "--background-ok" in result.stderr, result.stderr
    assert "spinner(s) started" not in result.stdout, result.stdout


def test_no_spinners_starve_nothing(tmp_path: Path) -> None:
    """N = 0 makes no load, so there is nothing to starve the command."""
    result = _tool(["0", "--", "nice", "-n", "19", "/bin/sh", "-c", "true"], tmp_path)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)


def _guard(command: str, cwd: Path) -> str | None:
    event = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}
    result = subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )  # fmt: skip
    assert result.returncode == 0, result.stderr
    if not result.stdout.strip():
        return None
    return json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.parametrize(
    "command",
    [
        'python3 "$GOH_DIR/tools/load.py" --oversubscribe 32 -- taskpolicy -b ./loop.sh',
        (
            "cd x && python3 ~/Projects/gates_of_heck/tools/load.py --max 2h 16 -- "
            "taskpolicy -b python3 -m pytest"
        ),
        "tools/load.py 8 -- nice -n 19 swift test 2>&1 | tail",
    ],
)
def test_the_guard_refuses_the_command_line(tmp_path: Path, command: str) -> None:
    reason = _guard(command, tmp_path)
    assert reason is not None, f"the guard allowed: {command}"
    assert "background priority" in reason and "--background-ok" in reason, reason


@pytest.mark.parametrize(
    "command",
    [
        'python3 "$GOH_DIR/tools/load.py" 8 -- ./my_test.sh',
        "tools/load.py --background-ok 'measuring the clamp' 8 -- taskpolicy -b ./x",
        "taskpolicy -b ./x",
        "echo 'tools/load.py 8 -- taskpolicy -b ./x'",
    ],
)
def test_the_guard_allows_the_rest(tmp_path: Path, command: str) -> None:
    assert _guard(command, tmp_path) is None, command
