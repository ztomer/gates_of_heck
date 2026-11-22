"""A sleeping `until`/`while` wait with no deadline (GOH.WAITDEADLINE, `lib/wait_deadline.py`): the
coordinator's `until <cond>; do sleep 20; done` ran 2 h 50 min on a condition that could never turn
true. The Bash guard refuses the shape unless a `timeout N` wraps it or the loop checks a deadline,
and names `goh wait`."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT
from test_claude_load_guard import _decide, _decide_event

sys.path.insert(0, str(REPO_ROOT / "lib"))
import wait_deadline

TODAY = "until ls $D | command grep X | command grep -vq OLD; do sleep 20; done"


@pytest.mark.parametrize(
    "command",
    [
        TODAY,
        "while [ ! -f ready ]; do sleep 2; done; echo up",
        "until curl -fs localhost:8817 >/dev/null; do\n  sleep 1\ndone",
        "while true; do if [ -f x ]; then break; fi; sleep 5; done",
    ],
)
def test_an_unbounded_sleeping_wait_is_refused_naming_goh_wait(command: str) -> None:
    reason = wait_deadline.judge(command)
    assert reason is not None, command
    assert "goh wait" in reason and "timeout" in reason


@pytest.mark.parametrize(
    "command",
    [
        f"timeout 600 bash -c '{TODAY}'",
        "timeout 600 bash -c 'until [ -f x ]; do sleep 2; done'",
        "until [ -f x ] || [ $SECONDS -ge 600 ]; do sleep 2; done",
        (
            "end=$(( $(date +%s) + 600 )); until [ -f x ]; do [ $(date +%s) -lt $end ] || break;"
            " sleep 2; done"
        ),
        "i=0; while [ $i -lt 30 ]; do i=$((i+1)); sleep 1; done",
        "while read -r line; do echo $line; done < f",  # no sleep: no wait
        "echo 'until x; do sleep 1; done'",  # text
        "until [ -f x ]; do sleep 2; done  # wait-ok: bounded by the tool's own timeout",
    ],
)
def test_a_bounded_or_non_waiting_loop_passes(command: str) -> None:
    assert wait_deadline.judge(command) is None, command


def test_the_bash_guard_refuses_todays_loop_and_runs_it_under_timeout(tmp_path: Path) -> None:
    reason = _decide(TODAY, tmp_path)
    assert reason is not None and "goh wait" in reason, reason
    assert _decide(f"timeout 600 bash -c '{TODAY}'", tmp_path) is None


def test_a_monitor_loop_gets_the_bash_verdict(tmp_path: Path) -> None:
    """Every shell-running tool gets every command check Bash gets (GOH.MONGUARD)."""
    event = {"tool_name": "Monitor", "tool_input": {"command": TODAY}, "cwd": str(tmp_path)}
    reason = _decide_event(event)
    assert reason is not None and "goh wait" in reason, reason
