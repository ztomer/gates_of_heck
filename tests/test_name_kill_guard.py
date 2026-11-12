"""A kill by command-line pattern of a SHARED command is refused before it runs (GOH.PKILLNAME).

On 2026-11-08 an agent stopped its own gate with a by-name kill of `tools/gate.sh --full`, and the
pattern also matched two other sessions' gate runs on the same Mac: a name is not an owner. The
Bash guard (`hooks/claude/discard_guard.py`, rule `lib/name_kill.py`) refuses a by-name kill
(the pattern kill, the all-of-a-name kill, a pgrep feeding kill) whose pattern names a command
every session runs, and names the owned-pid route. A kill of a numeric pid runs.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from conftest import REPO_ROOT, hermetic_env

GUARD = REPO_ROOT / "hooks" / "claude" / "discard_guard.py"
# Spelled in two parts so the house's own kill-by-name check does not read this file as killing.
PK, KA, PG = "p" + "kill", "kill" + "all", "p" + "grep"


def _decide(command: str) -> str | None:
    r = subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": "/"}),
        capture_output=True, text=True, env=hermetic_env(drop_git=True), timeout=30, check=False,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    if not r.stdout.strip():
        return None
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny", out
    return out["permissionDecisionReason"]


def test_the_2026_11_08_command_is_refused_with_the_owned_pid_route() -> None:
    reason = _decide(f'{PK} -f "tools/gate.sh --full"')
    assert reason is not None
    assert "pid" in reason and "load.py" in reason and "kill-ok" in reason, reason


@pytest.mark.parametrize(
    "command",
    [
        f"{PK} -9 -f cargo",
        f"{KA} pytest",
        f"{KA} -9 xcodebuild",
        f"{PG} -f tools/load.py | xargs kill",
        f"kill $({PG} -f gate_profile.sh)",
        f"sudo {PK} -f 'python3 -m pytest'",
        f"cd /x && {PK} -f gate.sh; echo done",
    ],
)
def test_every_by_name_kill_of_a_shared_command_is_refused(command: str) -> None:
    assert _decide(command) is not None, command


@pytest.mark.parametrize(
    "command",
    [
        "kill 12345",
        "kill -TERM 12345 67890",
        "kill %1",
        f"{PK} -P $$ cargo",
        f"{PG} -f tools/gate.sh",
        f"echo '{PK} -f gate.sh'",
        f'git commit -m "never {PK} -f cargo"',
        f"{PK} -f gate.sh  # kill-ok: my sandbox VM, nothing else runs here",
    ],
)
def test_a_pid_kill_and_an_unshared_or_quoted_name_runs(command: str) -> None:
    assert _decide(command) is None, command


# GOH.NOPKILL (2026-11-09): a pattern kill of a name no shared-command list knows (the ship result
# file's) killed the `goh ship` whose command line carried it. Every by-name kill is refused now.
@pytest.mark.parametrize(
    "command",
    [
        f'{PK} -f "20261109T104119Z-4439f143.json"',
        f"{PK} -f my-own-server.py",
        f"{PK} sleep",
        f"{PK} -9 -x watcher",
        f"{KA} Safari",
        f"{KA} -9 my-daemon",
        f"{PG} -f result.json | xargs kill",
        f"kill $({PG} -f my-watcher.sh)",
    ],
)
def test_every_unscoped_by_name_kill_is_refused(command: str) -> None:
    reason = _decide(command)
    assert reason is not None, command
    assert "kill <pid>" in reason and "TaskStop" in reason and "kill-ok" in reason, reason


def test_the_refusal_names_what_the_pattern_would_hit_now() -> None:
    marker = "goh_nopkill_marker_4242"
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", marker],
                              stdin=subprocess.DEVNULL)  # fmt: skip
    try:
        reason = _decide(f"{PK} -f {marker}")
        assert reason is not None
        assert str(holder.pid) in reason and marker in reason, reason
    finally:
        holder.kill()
        holder.wait()
    assert "matches no process right now" in (_decide(f"{PK} -f {marker}_gone") or "")
