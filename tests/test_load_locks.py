"""A load experiment refuses a build tool that takes a MACHINE-WIDE lock (GOH.LOADLOCK, 2026-11-08).

THE CLASS. An agent ran `tools/load.py --max 25m --oversubscribe 32 -- taskpolicy -b cargo test -q
-p taxes-cli --test app_check`. The starved cargo got 0.36 s of CPU in 21 minutes, but had first
taken `~/.cargo/.package-cache`, so nine other agents' cargo processes across the machine blocked on
that lock for 15-20 minutes (sampled: the holder in read_manifest, the waiters in
CacheLocker::lock_exclusive). A load experiment whose command takes a machine-wide lock under
starvation becomes a fleet-wide stall.

So `tools/load.py` refuses, before any spinner starts, a command that is a build tool acquiring
shared locks (cargo, xcodebuild, swift, gradle, go, npm/pnpm/yarn), looked for through the wrappers
(taskpolicy, nice, timeout, env, caffeinate, sh -c). It names the way forward -- build first, then
put only the prebuilt binary under load -- and takes ONE escape, `--shared-lock-ok <reason>`.
The Claude Code Bash guard refuses the same command line at the tool boundary. ONE definition
(`lib/load_locks.py`) serves both.
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

import load_locks

TOOL = REPO_ROOT / "tools" / "load.py"
GUARD = REPO_ROOT / "hooks" / "claude" / "discard_guard.py"

pytestmark = pytest.mark.xdist_group("load_orphans")

LOCKING = [
    ["cargo", "test", "-q", "-p", "taxes-cli", "--test", "app_check"],
    ["cargo", "build"],
    ["cargo", "run", "--release"],
    ["cargo", "check"],
    ["cargo", "clippy", "--all-targets"],
    ["cargo", "metadata", "--format-version", "1"],
    ["cargo", "bench"],
    ["cargo", "nextest", "run"],
    ["cargo", "+stable", "test"],
    ["cargo", "--locked", "-q", "test"],
    ["cargo", "-Z", "unstable-options", "build"],
    ["/Users/me/.cargo/bin/cargo", "t"],
    ["xcodebuild", "-scheme", "App", "test"],
    ["swift", "build"],
    ["swift", "test", "--parallel"],
    ["swift", "run", "tool"],
    ["gradle", "build"],
    ["./gradlew", "test"],
    ["go", "build", "./..."],
    ["go", "test", "./..."],
    ["npm", "install"],
    ["npm", "ci"],
    ["pnpm", "install"],
    ["pnpm", "i"],
    # through the wrappers
    ["taskpolicy", "-b", "cargo", "test"],
    ["nice", "-n", "19", "cargo", "test"],
    ["timeout", "60", "cargo", "test"],
    ["env", "FOO=1", "cargo", "test"],
    ["FOO=1", "cargo", "test"],
    ["caffeinate", "-i", "cargo", "test"],
    ["xcrun", "xcodebuild", "build"],
    ["nohup", "timeout", "60", "env", "A=1", "taskpolicy", "-b", "cargo", "test"],
    ["/bin/sh", "-c", "cd /tmp && cargo test"],
    ["bash", "-lc", "echo hi; taskpolicy -b swift build"],
]

FREE = [
    ["./target/debug/deps/app_check-0a1b2c3d4e5f6071"],
    ["./loop.sh"],
    ["python3", "-m", "pytest"],
    ["cargo", "--version"],
    ["cargo", "--help"],
    ["cargo", "fmt", "--check"],
    ["go", "version"],
    ["swift", "--version"],
    ["npm", "--version"],
    ["npm", "run", "x"],
    ["taskpolicy", "-c", "user-interactive", "./prebuilt"],
    ["env", "FOO=1", "./prebuilt", "cargo", "test"],  # `cargo test` is an ARGUMENT here
    ["/bin/sh", "-c", "echo cargo test; true"],
    ["echo", "cargo", "test"],
]


@pytest.mark.parametrize("argv", LOCKING, ids=lambda argv: " ".join(argv))
def test_a_lock_taking_build_tool_is_named(argv: list[str]) -> None:
    assert load_locks.locking_command(argv), argv


@pytest.mark.parametrize("argv", FREE, ids=lambda argv: " ".join(argv))
def test_a_command_that_takes_no_shared_lock_is_not(argv: list[str]) -> None:
    assert load_locks.locking_command(argv) is None, argv


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
        ["cargo", "test", "-q"],
        ["taskpolicy", "-c", "user-interactive", "cargo", "build"],
        ["nice", "-n", "5", "env", "A=1", "cargo", "test", "--no-run"],
        ["/bin/sh", "-c", "cargo test"],
        ["swift", "test"],
    ],
    ids=lambda argv: " ".join(argv),
)
def test_the_tool_refuses_a_lock_taking_command_before_any_spinner(
    tmp_path: Path, command: list[str]
) -> None:
    """The 21-minute fleet-wide stall: refused with the way forward, not one spinner started."""
    result = _tool(["2", "--", *command], tmp_path)
    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)
    assert "machine-wide" in result.stderr, result.stderr
    assert "--shared-lock-ok" in result.stderr, result.stderr
    assert "--no-run" in result.stderr or "prebuilt" in result.stderr, result.stderr
    assert "spinner(s) started" not in result.stdout, result.stdout
    assert not list(tmp_path.glob("*.json")), "a refused experiment took the slot"


def test_a_prebuilt_binary_runs_under_load(tmp_path: Path) -> None:
    """The accepted shape: build first, put only the binary under load."""
    result = _tool(["1", "--", "/bin/sh", "-c", "true"], tmp_path)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert "spinner(s) started" in result.stdout, result.stdout


def test_the_escape_takes_a_reason_and_then_runs(tmp_path: Path) -> None:
    """One escape, with a written reason (a fake `cargo` on PATH stands in for the real tool)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "cargo"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    env = {
        **os.environ,
        "GOH_LOAD_NCPU": "64",
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
    }
    result = subprocess.run(
        [
            sys.executable, str(TOOL), "--dir", str(tmp_path / "slot"),
            "--shared-lock-ok", "no other cargo runs on this host", "1", "--", "cargo", "test",
        ],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=timing_bounds.patience(60),
        check=False,
        env=env,
    )  # fmt: skip
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert "spinner(s) started" in result.stdout, result.stdout


@pytest.mark.parametrize("reason", ["", "   "])
def test_an_escape_with_no_reason_excuses_nothing(tmp_path: Path, reason: str) -> None:
    result = _tool(["--shared-lock-ok", reason, "1", "--", "cargo", "test"], tmp_path)
    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)
    assert "--shared-lock-ok" in result.stderr, result.stderr
    assert "spinner(s) started" not in result.stdout, result.stdout


def test_no_spinners_hold_no_lock_against_anything(tmp_path: Path) -> None:
    """N = 0 starves nothing: the command is not under load, so it holds the lock only as long as
    it works. (Refused-by-name would only block a harmless run.)"""
    result = _tool(["0", "--", "cargo", "--version"], tmp_path)
    assert "machine-wide" not in result.stderr, result.stderr


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
        'python3 "$GOH_DIR/tools/load.py" --max 25m --oversubscribe 32 -- taskpolicy -b cargo test -q',
        "tools/load.py 8 -- cargo test --no-run 2>&1 | tail",
        "cd x && python3 ~/Projects/gates_of_heck/tools/load.py 4 -- xcodebuild test",
    ],
)
def test_the_guard_refuses_the_command_line(tmp_path: Path, command: str) -> None:
    reason = _guard(command, tmp_path)
    assert reason is not None, f"the guard allowed: {command}"
    assert "--shared-lock-ok" in reason, reason


@pytest.mark.parametrize(
    "command",
    [
        'python3 "$GOH_DIR/tools/load.py" 8 -- ./target/debug/deps/app_check-0a1b2c',
        "tools/load.py --shared-lock-ok 'single-agent host' 8 -- cargo test",
        "cargo test",
        "echo 'tools/load.py 8 -- cargo test'",
    ],
)
def test_the_guard_allows_the_rest(tmp_path: Path, command: str) -> None:
    assert _guard(command, tmp_path) is None, command
