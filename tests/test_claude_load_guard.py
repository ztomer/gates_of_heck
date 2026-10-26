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

THE SAME CLASS, IN A FILE (2026-10-21). The Bash guard sees COMMANDS only, and at 13:00 on
2026-10-21 twenty-four orphaned spinners were burning four cores each --
`/bin/zsh /private/var/.../opencode/load.sh`, 78 minutes of CPU apiece, four and a half hours
old, parent 1, their owner long dead -- and the script was a hand-written FILE:

    for i in $(seq 1 24); do (while :; do :; done) & done

A file is the natural way to smuggle the loop in, so the guard reads the WRITE path too: a file
whose CODE runs a backgrounded busy loop is refused, with the same way forward and the same
escape. A file that is not a shell script is not judged at all: the shape is a shell construct.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT

GUARD = REPO_ROOT / "hooks" / "claude" / "discard_guard.py"

SPINNER = "while :; do :; done &"
BUSY_WAIT_MARK = "busy-waits"  # the refusal names the form it refused


def _decide(command: str, cwd: Path) -> str | None:
    """The guard's verdict on a Bash command: its refusal reason, or None when it lets it run."""
    return _decide_event({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)})


def _decide_event(event: dict) -> str | None:
    """The guard's verdict on any tool event: its refusal reason, or None when it lets it run."""
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
    """A foreground loop is not the ORPHAN leak (it dies with its shell), so the refusal it gets
    is the busy-wait one below, not the spinner's: no `tools/load.py` leak story."""
    reason = _decide("while :; do :; done", tmp_path)
    assert reason is not None, "a foreground busy loop pins a core and hangs the shell"
    assert BUSY_WAIT_MARK in reason, reason


def test_a_background_job_that_is_not_a_spinner_is_allowed(tmp_path: Path) -> None:
    """`sleep 100 &` costs no CPU and cannot spin: it is not this class."""
    assert _decide("sleep 100 &", tmp_path) is None


def test_a_conjunction_is_not_a_background_operator(tmp_path: Path) -> None:
    """`&&` is a conjunction, not a background job: reading it as one would refuse half the
    ordinary commands a shell runs."""
    assert _decide("while :; do sleep 1; break; done && true", tmp_path) is None


def test_load_ok_is_the_one_escape(tmp_path: Path) -> None:
    """A deliberate background busy loop says so, the way a deliberate discard does."""
    reason = _decide("while :; do :; done & # load-ok: measuring the queue", tmp_path)
    assert reason is None
    assert _decide("while :; do :; done & # load-ok:", tmp_path) is not None


# ── the same class, in a FILE ─────────────────────────────────────────────────────────────


def _write(path: str, content: str) -> dict:
    """The event a Write carries."""
    return {"tool_name": "Write", "tool_input": {"file_path": path, "content": content}}


def test_a_file_that_runs_a_backgrounded_busy_loop_is_refused(tmp_path: Path) -> None:
    """THE CLASS, IN A FILE. The file is the natural way to smuggle the loop in, and the guard
    reads the write path: twenty-four spinners were found on 2026-10-21 that way."""
    reason = _decide_event(
        _write("load.sh", f"#!/bin/sh\nfor i in $(seq 1 24); do ({SPINNER}) & done\n")
    )
    assert reason is not None, "the guard allowed a file that runs a spinner"
    assert "tools/load.py" in reason, reason
    assert "load.sh" in reason, reason


def test_the_file_refusal_names_the_way_and_the_escape(tmp_path: Path) -> None:
    """A refusal is a way forward, not a wall."""
    reason = _decide_event(_write("load.sh", f"#!/bin/sh\n{SPINNER}\n"))
    assert reason is not None
    assert 'python3 "$GOH_DIR/tools/load.py"' in reason, reason
    assert "load-ok" in reason, reason


def test_a_file_that_only_quotes_the_loop_is_allowed(tmp_path: Path) -> None:
    """TEXT is not CODE: `echo 'while :; do :; done'` writes a loop, it does not run one."""
    assert _decide_event(_write("load.sh", "#!/bin/sh\necho 'while :; do :; done'\n")) is None
    assert _decide_event(_write("load.sh", "#!/bin/sh\n# while :; do :; done &\n")) is None


def test_a_file_that_is_not_shell_is_not_judged(tmp_path: Path) -> None:
    """The shape is a shell construct: a Python file that carries it is not a spinner."""
    assert _decide_event(_write("tool.py", f"SPIN = '{SPINNER}'\n")) is None
    assert _decide_event(_write("NOTES.md", f"`{SPINNER}`\n")) is None


def test_a_foreground_loop_in_a_file_is_the_busy_wait_class(tmp_path: Path) -> None:
    """A foreground loop is not the ORPHAN leak, but it pins a core and hangs the shell that runs
    it: the busy-wait class (G17), judged in a FILE as it is in a command."""
    reason = _decide_event(_write("load.sh", "#!/bin/sh\nwhile :; do :; done\n"))
    assert reason is not None, "a file whose code busy-waits was allowed"
    assert BUSY_WAIT_MARK in reason, reason


def test_the_marker_in_the_file_is_the_escape(tmp_path: Path) -> None:
    """A deliberate background busy loop says so, in the file."""
    assert (
        _decide_event(
            _write("load.sh", f"#!/bin/sh\n# load-ok: measuring the queue on purpose\n{SPINNER}\n")
        )
        is None
    )
    assert _decide_event(_write("load.sh", f"#!/bin/sh\n# load-ok:\n{SPINNER}\n")) is not None


def test_an_edit_that_adds_the_loop_is_refused(tmp_path: Path) -> None:
    """The event carries the text the edit ADDS, which is where the loop arrives."""
    reason = _decide_event(
        {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "load.sh",
                "old_string": "echo hello",
                "new_string": SPINNER,
            },
        }
    )
    assert reason is not None, "the guard allowed an edit that adds a spinner"
    assert "tools/load.py" in reason, reason


def test_a_multi_edit_that_adds_the_loop_is_refused(tmp_path: Path) -> None:
    """Same for the edit list a MultiEdit carries."""
    reason = _decide_event(
        {
            "tool_name": "MultiEdit",
            "tool_input": {
                "file_path": "load.sh",
                "edits": [
                    {"old_string": "echo hello", "new_string": "echo hi"},
                    {"old_string": "echo hi", "new_string": SPINNER},
                ],
            },
        }
    )
    assert reason is not None, "the guard allowed a multi-edit that adds a spinner"
    assert "tools/load.py" in reason, reason


def test_the_house_tool_itself_may_be_written(tmp_path: Path) -> None:
    """The way the refusal names must be writable: a guard that refuses it closes every door."""
    assert (
        _decide_event(
            _write("run.sh", '#!/bin/sh\npython3 "$GOH_DIR/tools/load.py" 8 -- ./my_test.sh\n')
        )
        is None
    )


# ── the BUSY-WAIT: a loop that re-tests a condition with no pause (G16, 2026-10-26) ───────────
#
# An agent's Bash call ran `until <cond>; do :; done` in the FOREGROUND. It pinned a core until the
# condition came true (or forever) and was killed within seconds; the guard let it through because
# the spinner shape needed the CONDITION to be `:`/`true` and the loop to be backgrounded. A wait
# that does no waiting is its own class: it needs no background operator and any condition.


@pytest.mark.parametrize(
    "command",
    [
        "until [ -f /tmp/ready ]; do :; done",
        "until pgrep -x foo >/dev/null 2>&1; do :; done",
        "while ! test -f /tmp/ready; do true; done",
        "until curl -sf localhost:1/health; do :; done; echo up",
        "until [ -f /tmp/ready ]\ndo\n  :\ndone",
        "until a; b; do :; done",
        "until [ -f /tmp/ready ]; do :; done &",
        "cd x && until [ -f ready ]; do : ; done && echo ok",
    ],
)
def test_a_busy_wait_is_refused_with_the_way_to_wait(command: str, tmp_path: Path) -> None:
    reason = _decide(command, tmp_path)
    assert reason is not None, f"the guard allowed a busy-wait: {command!r}"
    assert BUSY_WAIT_MARK in reason, reason
    assert "Monitor" in reason, reason
    assert "sleep" in reason, reason
    assert 'python3 "$GOH_DIR/tools/load.py"' in reason, reason
    assert "load-ok" in reason, reason


@pytest.mark.parametrize(
    "command",
    [
        "until [ -f /tmp/ready ]; do sleep 1; done",
        "until [ -f /tmp/ready ]; do :; sleep 0.2; done",
        "while read -r line; do :; done < input.txt",
        "while IFS= read -r line; do :; done < input.txt",
        "echo 'until [ -f x ]; do :; done'",
        'echo "until [ -f x ]; do :; done"',
        "# until [ -f x ]; do :; done\necho hi",
        "cat > fixture.sh <<'EOF'\nuntil [ -f x ]; do :; done\nEOF",
        "for f in a b; do :; done",
    ],
)
def test_a_wait_that_waits_or_is_only_text_is_allowed(command: str, tmp_path: Path) -> None:
    assert _decide(command, tmp_path) is None, command


def test_load_ok_excuses_a_busy_wait(tmp_path: Path) -> None:
    assert _decide("until [ -f x ]; do :; done  # load-ok: probing a 1ms race", tmp_path) is None
    assert _decide("until [ -f x ]; do :; done  # load-ok:", tmp_path) is not None


# ── the BUSY-WAIT, in a FILE (G17, 2026-10-26) ────────────────────────────────────────────────
#
# G16 taught the COMMAND path the busy-wait shape and said "files are not judged for this shape
# yet". A busy-wait in a script that is then RUN is the same wrong loop one write earlier, so the
# Write/Edit path judges the same shape (lib/busyloop.py, ONE definition).


@pytest.mark.parametrize(
    "body",
    [
        "until [ -f /tmp/ready ]; do :; done",
        "while ! test -f /tmp/ready; do true; done",
        "until [ -f /tmp/ready ]\ndo\n  :\ndone",
        "until a; b; do :; done",
        "cd x && until [ -f ready ]; do : ; done && echo ok",
    ],
)
def test_a_file_that_busy_waits_is_refused_with_the_way_to_wait(body: str, tmp_path: Path) -> None:
    reason = _decide_event(_write("wait.sh", f"#!/bin/sh\n{body}\n"))
    assert reason is not None, f"the guard allowed a file that busy-waits: {body!r}"
    assert BUSY_WAIT_MARK in reason, reason
    assert "wait.sh" in reason, reason
    assert "Monitor" in reason, reason
    assert "sleep" in reason, reason
    assert "load-ok" in reason, reason


@pytest.mark.parametrize(
    "body",
    [
        "until [ -f /tmp/ready ]; do sleep 1; done",
        "until [ -f /tmp/ready ]; do :; sleep 0.2; done",
        "while read -r line; do :; done < input.txt",
        "echo 'until [ -f x ]; do :; done'",
        "# until [ -f x ]; do :; done",
        "cat > fixture.sh <<'EOF'\nuntil [ -f x ]; do :; done\nEOF",
        "for f in a b; do :; done",
    ],
)
def test_a_file_that_waits_or_only_quotes_the_wait_is_allowed(body: str, tmp_path: Path) -> None:
    assert _decide_event(_write("wait.sh", f"#!/bin/sh\n{body}\n")) is None, body


def test_a_busy_wait_in_a_file_that_is_not_shell_is_not_judged(tmp_path: Path) -> None:
    assert _decide_event(_write("tool.py", "CMD = 'until [ -f x ]; do :; done'\n")) is None


def test_the_marker_in_the_file_excuses_a_busy_wait(tmp_path: Path) -> None:
    body = "until [ -f x ]; do :; done"
    assert (
        _decide_event(_write("wait.sh", f"#!/bin/sh\n# load-ok: probing a 1ms race\n{body}\n"))
        is None
    )
    assert _decide_event(_write("wait.sh", f"#!/bin/sh\n# load-ok:\n{body}\n")) is not None


def test_an_edit_that_adds_a_busy_wait_is_refused(tmp_path: Path) -> None:
    reason = _decide_event(
        {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "wait.sh",
                "old_string": "echo hello",
                "new_string": "until [ -f x ]; do :; done",
            },
        }
    )
    assert reason is not None, "the guard allowed an edit that adds a busy-wait"
    assert BUSY_WAIT_MARK in reason, reason
