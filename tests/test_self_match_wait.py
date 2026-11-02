"""A wait whose condition matches its OWN shell is refused at the tool boundary (2026-11-01).

THE CLASS. Three agent background shells ran
`until ! pgrep -f "git push origin main"; do sleep 30; done` for 8-9 hours. `pgrep -f` matches
every process's FULL command line except its own, and the waiting shell's command line
(`zsh -c '... pgrep -f "git push origin main" ...'`) contains the pattern, so the loop could never
end; the coordinator killed them by hand. The invariant: a wait on "no process matching P" must not
itself be a process matching P.

The Claude Code Bash guard refuses the shape (`lib/self_match.py`) and names the ways out. TEXT is
not CODE (a heredoc body, a string, a comment is not a loop), the bracket trick passes by itself,
and `# pgrep-ok: <reason>` is the one escape.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT
from test_claude_load_guard import _decide

PUSH = 'until ! pgrep -f "git push origin main"; do sleep 30; done'


@pytest.mark.parametrize(
    "command",
    [
        PUSH,  # the measured form
        'while pgrep -f "git push origin main"; do sleep 30; done',
        'until ! pgrep -fl "git push"; do sleep 3; done',
        'until ! pgrep -f -u 501 "git push"; do sleep 3; done',
        'until ! pgrep -fu 501 "git push"; do sleep 3; done',
        "until ! pgrep -f 'git push'; do sleep 3; done",
        'until [ -z "$(pgrep -f "git push")" ]; do sleep 3; done',
        "while [ \"$(pgrep -f 'git push' | wc -l)\" -gt 0 ]; do sleep 3; done",
        'until ! pkill -0 -f "git push"; do sleep 3; done',
        'until ! ps aux | grep "git push"; do sleep 3; done',
        'until ! ps aux | grep -E "git push|git pull"; do sleep 3; done',
        'until ! pgrep -fl "git push"\ndo sleep 3\ndone',
        # a shell started with the loop in its script: its own command line holds the script
        "zsh -c 'until ! pgrep -f \"git push origin main\"; do sleep 30; done'",
        # the bracket trick does not help when the plain spelling is elsewhere in the command
        'git push origin main; until ! pgrep -f "[g]it push"; do sleep 3; done',
    ],
)
def test_a_wait_that_matches_its_own_shell_is_refused(command: str, tmp_path: Path) -> None:
    reason = _decide(command, tmp_path)
    assert reason is not None, f"the guard allowed a wait that can never end: {command}"
    assert "OWN command line" in reason, reason


def test_the_refusal_names_the_ways_out(tmp_path: Path) -> None:
    """A refusal is a way forward, not a wall: every way out, and the escape."""
    reason = _decide(PUSH, tmp_path)
    assert reason is not None
    for want in ("kill -0", "[g]it push", "`wait`", "pgrep-ok"):
        assert want in reason, (want, reason)


@pytest.mark.parametrize(
    "command",
    [
        'until ! pgrep -f "[g]it push origin main"; do sleep 30; done',  # the bracket trick
        'until ! ps aux | grep "[g]it push"; do sleep 3; done',
        'until ! ps aux | grep "git push" | grep -v grep; do sleep 3; done',  # drops its own line
        'pid=$(pgrep -f "[g]it push"); while kill -0 "$pid" 2>/dev/null; do sleep 3; done',
        "while kill -0 $pid; do sleep 3; done",  # wait on the PID
        "sleep 30 & wait $!",  # wait for a job the shell started
        'pgrep -f "git push origin main"',  # no loop: it runs once and ends
        "while pgrep node; do sleep 3; done",  # no -f: matches process NAMES, not command lines
        "while pgrep -x node; do sleep 3; done",
        "bash -c 'until ! pgrep -f \"[b]uild.sh\"; do sleep 3; done'",
    ],
)
def test_the_shapes_that_can_end_are_allowed(command: str, tmp_path: Path) -> None:
    assert _decide(command, tmp_path) is None, f"the guard refused a wait that can end: {command}"


@pytest.mark.parametrize(
    "command",
    [
        f"cat > wait.sh <<'EOF'\n{PUSH}\nEOF",  # a heredoc body is TEXT
        f"echo '{PUSH}'",  # a single-quoted string is TEXT
        f'echo "{PUSH.replace(chr(34), chr(39))}"',  # so is a double-quoted one
        f"# {PUSH}\ntrue",  # and a comment
    ],
)
def test_text_is_not_a_loop(command: str, tmp_path: Path) -> None:
    """The writer of a fixture that plants the form must not be refused by the rule it tests."""
    assert _decide(command, tmp_path) is None, f"the guard refused text: {command}"


def test_pgrep_ok_is_the_one_escape(tmp_path: Path) -> None:
    assert (
        _decide(f"{PUSH}  # pgrep-ok: the pattern is a name the shell never spells", tmp_path)
        is None
    )
    assert _decide(f"{PUSH}  # pgrep-ok:", tmp_path) is not None, "an empty reason excuses nothing"


def test_a_script_file_is_not_the_class(tmp_path: Path) -> None:
    """A script's command line is `bash wait.sh`, which does not hold its patterns: not a
    self-match, so the write path is not judged."""
    from test_claude_load_guard import _decide_event

    event = {
        "tool_name": "Write",
        "tool_input": {"file_path": "wait.sh", "content": f"#!/bin/sh\n{PUSH}\n"},
    }
    assert _decide_event(event) is None


def test_the_rule_is_what_refuses(tmp_path: Path) -> None:
    """CALIBRATION: the refusal comes from `lib/self_match.py`, not from some other rule of the
    guard. Run the guard on a copy of the tree with the rule gutted: the measured form must run."""
    import shutil

    tree = tmp_path / "tree"
    for rel in ("hooks/claude", "lib"):
        shutil.copytree(REPO_ROOT / rel, tree / rel)
    gutted = tree / "lib" / "self_match.py"
    gutted.write_text(
        re.sub(
            r"\ndef judge\(.*",
            "\ndef judge(command, context=None):\n    return None\n",
            gutted.read_text(),
            flags=re.DOTALL,
        )
    )
    import json

    run = subprocess.run(
        [sys.executable, str(tree / "hooks/claude/discard_guard.py")],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": PUSH}, "cwd": str(tmp_path)}),
        capture_output=True, text=True, timeout=30, check=False,
    )  # fmt: skip
    assert run.returncode == 0 and not run.stdout.strip(), (
        "the rule removed, the loop still refused"
    )
