"""Every tool that runs a shell command is judged like Bash (GOH.MONGUARD, 2026-11-02).

THE CLASS. The Claude Code guards (`discard_guard.py`, `hold_guard.py`) were registered on `Bash`
and nothing else, and Bash is not the only tool that runs a shell command: Monitor runs one too,
in the background, and is the tool the guard's own refusal tells the agent to wait with. A ZoneWM
agent's first Monitor wait was `until ! pgrep -f "git push" ...; do sleep 15; done`, the
self-matching wait the Bash guard exists to refuse, and no guard saw it. The invariant: a tool
whose input holds a shell command gets every command check Bash gets.

The Monitor input field is `command` (a logged sample from the session transcripts:
`{"command": "until ! pgrep -f \\"git push\\" ...; do sleep 15; done"}`, beside `description`,
`timeout_ms`, `persistent`). `lib/shell_tools.py` is the ONE list of such tools; the registrations
cover each member, so the next shell-running tool is one line there.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, git
from test_claude_load_guard import _decide_event
from test_repo_hold import _hold, _verdict

sys.path.insert(0, str(REPO_ROOT / "lib"))
import shell_tools

REGS = json.loads((REPO_ROOT / "hooks" / "claude" / "registrations.json").read_text())["hooks"]
SELF_MATCH = 'until ! pgrep -f "git push origin main"; do sleep 15; done'
CLEAN = 'until ! pgrep -f "[g]it push origin main"; do sleep 15; done'


def _monitor(command: str, cwd: Path) -> dict:
    """A Monitor tool event shaped like the logged ones: `command` beside its other fields."""
    return {
        "tool_name": "Monitor",
        "tool_input": {
            "command": command, "description": "wait", "timeout_ms": 600000, "persistent": False,
        },
        "cwd": str(cwd),
    }  # fmt: skip


def _bash(command: str, cwd: Path) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}


def test_the_shell_tools_are_bash_and_monitor() -> None:
    assert set(shell_tools.SHELL_TOOLS) == {"Bash", "Monitor"}


def test_a_monitor_wait_that_matches_its_own_shell_is_refused(tmp_path: Path) -> None:
    reason = _decide_event(_monitor(SELF_MATCH, tmp_path))
    assert reason is not None, "the guard let a self-matching wait through Monitor"
    assert "OWN command line" in reason, reason


def test_a_monitor_busy_loop_is_refused(tmp_path: Path) -> None:
    reason = _decide_event(_monitor("until [ -f ready ]; do :; done", tmp_path))
    assert reason is not None and "busy-waits" in reason, reason


def test_a_clean_monitor_command_passes(tmp_path: Path) -> None:
    for command in (CLEAN, "until [ -f ready ]; do sleep 2; done", "tail -f build.log"):
        assert _decide_event(_monitor(command, tmp_path)) is None, command


@pytest.mark.parametrize(
    "command",
    [SELF_MATCH, CLEAN, "until [ -f ready ]; do :; done", "while :; do :; done &", "ls"],
)
def test_bash_and_monitor_get_the_same_verdict(command: str, tmp_path: Path) -> None:
    assert _decide_event(_bash(command, tmp_path)) == _decide_event(_monitor(command, tmp_path))


def test_bash_behaviour_is_unchanged(tmp_path: Path) -> None:
    reason = _decide_event(_bash(SELF_MATCH, tmp_path))
    assert reason is not None and "OWN command line" in reason, reason
    assert _decide_event(_bash(CLEAN, tmp_path)) is None


def test_a_tool_that_is_not_a_shell_is_not_judged_as_one(tmp_path: Path) -> None:
    event = {"tool_name": "Read", "tool_input": {"command": SELF_MATCH}, "cwd": str(tmp_path)}
    assert _decide_event(event) is None


def test_the_hold_guard_judges_a_monitor_command_like_bash(repo: Path) -> None:
    _hold(repo)
    command = f"git -C {repo} commit --allow-empty -m x"
    bash = _verdict(_bash(command, repo))
    assert bash is not None
    assert _verdict(_monitor(command, repo)) == bash


def test_the_hold_guard_lets_a_clean_monitor_command_run(repo: Path) -> None:
    git(repo, "status")
    assert _verdict(_monitor("tail -f build.log", repo)) is None


@pytest.mark.parametrize("guard", ["discard_guard.py", "hold_guard.py", "skill_edit.sh"])
def test_every_registration_of_a_command_judge_covers_every_shell_tool(guard: str) -> None:
    entries = [e for es in REGS.values() for e in es if f"hooks/claude/{guard}" in e["command"]]
    assert entries, guard
    for entry in entries:
        matcher = set(re.split(r"\|", entry["matcher"]))
        missing = set(shell_tools.SHELL_TOOLS) - matcher
        assert not missing, f"{guard} is registered without {sorted(missing)}: {entry['matcher']}"
