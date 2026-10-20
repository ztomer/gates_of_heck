"""The raw form of a load experiment is refused at the tool boundary, and the house tool is named.

THE CLASS (2026-10-20). Three times in one day an agent that needed load wrote it by hand --
`for j in $(seq 1 12); do (while :; do :; done) & done` -- and the shell that owned the spinners was
killed before it could reap them. The spinners were re-parented to launchd and burned a core each
for hours (24 of them, 3h12m, load average 206). A lesson in a file does not stop the next agent;
a refusal at the tool boundary does.

So the Claude Code Bash guard refuses a command that spawns a backgrounded busy loop, and names
`$GOH_DIR/tools/load.py` -- the one way to make load, which reaps itself.

The narrowness matters as much as the refusal: a busy loop inside a string or a heredoc is TEXT
(the writer of a fixture that proves the guard refuses it must not be refused by it), a foreground
busy loop dies with the shell that runs it, and `# load-ok: <reason>` is the one escape.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO_ROOT

GUARD = REPO_ROOT / "hooks" / "claude" / "discard_guard.py"


def _decide(command: str, cwd: Path) -> str | None:
    """The guard's verdict: its refusal reason, or None when it lets the command run."""
    result = subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )  # fmt: skip
    assert result.returncode == 0, result.stderr
    if not result.stdout.strip():
        return None
    out = json.loads(result.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny", out
    return out["permissionDecisionReason"]


@pytest.mark.parametrize(
    "command",
    [
        "for j in $(seq 1 12); do (while :; do :; done) & done",
        "while :; do :; done &",
        "(while :; do :; done) &",
        "while true; do :; done &",
        "for j in 1 2 3; do (while : ; do : ; done) & done",
        "{ while :; do :; done; } &",
    ],
)
def test_a_backgrounded_busy_loop_is_refused(command: str, tmp_path: Path) -> None:
    """The measured form, and the shapes around it."""
    reason = _decide(command, tmp_path)
    assert reason is not None, f"the guard allowed: {command}"
    assert "tools/load.py" in reason, reason


def test_the_refusal_names_the_way_to_make_load(tmp_path: Path) -> None:
    """The refusal is a way forward, not a wall."""
    reason = _decide("while :; do :; done &", tmp_path)
    assert reason is not None
    assert 'python3 "$GOH_DIR/tools/load.py"' in reason, reason
    assert "load-ok" in reason, reason


def test_the_house_tool_itself_is_allowed(tmp_path: Path) -> None:
    """The way the refusal names must run: a guard that refuses it would close every door."""
    command = 'python3 "$GOH_DIR/tools/load.py" 8 -- ./my_test.sh'
    assert _decide(command, tmp_path) is None


def test_a_quoted_mention_is_not_a_busy_loop(tmp_path: Path) -> None:
    """`echo 'while :; do :; done'` writes a loop, it does not run one."""
    assert _decide("echo 'while :; do :; done'", tmp_path) is None
    assert _decide('echo "while :; do :; done &"', tmp_path) is None


def test_a_heredoc_that_plants_the_raw_form_is_allowed(tmp_path: Path) -> None:
    """The writer of a fixture that proves the guard refuses the raw form must not be refused."""
    command = "cat > fixture.sh <<'EOF'\nwhile :; do :; done &\nEOF"
    assert _decide(command, tmp_path) is None


def test_a_foreground_busy_loop_is_not_the_leak_class(tmp_path: Path) -> None:
    """A foreground loop dies with the shell that runs it; the leak is the BACKGROUNDED one."""
    assert _decide("while :; do :; done", tmp_path) is None


def test_a_background_job_that_is_not_a_spinner_is_allowed(tmp_path: Path) -> None:
    """`sleep 100 &` costs no CPU and cannot spin: it is not this class."""
    assert _decide("sleep 100 &", tmp_path) is None


def test_a_conjunction_is_not_a_background_operator(tmp_path: Path) -> None:
    """`&&` is a conjunction, not a background job: reading it as one would refuse half the
    ordinary commands a shell runs."""
    assert _decide("while :; do :; done && true", tmp_path) is None


def test_load_ok_is_the_one_escape(tmp_path: Path) -> None:
    """A deliberate background busy loop says so, the way a deliberate discard does."""
    reason = _decide("while :; do :; done & # load-ok: measuring the queue", tmp_path)
    assert reason is None
    assert _decide("while :; do :; done & # load-ok:", tmp_path) is not None
