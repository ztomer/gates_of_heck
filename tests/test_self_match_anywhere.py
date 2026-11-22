"""A `pgrep -f` / `pkill -f` anywhere whose pattern the running shell itself holds (GOH.PGREPSELF).

THE CLASS. `pgrep -f PAT` matches the FULL command line of every process but its own, and the
shell that runs it carries the whole command in ITS command line (`zsh -c '...'`, or the `sh -c`
sshd starts for `ssh host '...'`). So a command that spells the pattern a second time (a
`pgrep -f foo || foo &`, a wait that ends in `; ./foo`), or whose pgrep sits inside a shell string
or an ssh remote command, matches its own shell: the "is it running" answer is always yes, and a
wait on it never ends (two lane loops on 2026-11-15, fleet-deploy over ssh, the camoufox
neighbour pkill). `lib/self_match.py` already refused the `while`/`until` form (2026-11-01); this
is the same invariant for every other place the call can stand.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from source_closure import copy_closure
from test_claude_load_guard import _decide
from timing_bounds import patience

PKILL = "p" + "kill"  # split, so the house's kill-by-name check does not read the fixtures as calls
WAIT_OK = "  # wait-ok: this fixture judges another rule (GOH.WAITDEADLINE refuses unbounded waits)"
REFUSED = [
    'pgrep -f "camoufox" || camoufox &',  # the pattern is spelled again: the shell holds it
    'if pgrep -f "git push"; then echo waiting; fi; git push origin main',
    'n=$(pgrep -f "git push" | wc -l); git push origin main',
    "for i in 1 2 3; do pgrep -f build.sh >/dev/null || break; sleep 1; done; ./build.sh",
    f'git push origin main & sleep 1; {PKILL} -f "git push"',
    "bash -c 'pgrep -f foo'",  # inside a shell string, the shell's own command line holds it
    'zsh -c "pgrep -fl camoufox && echo up"',
    'ssh media "pgrep -f camoufox"',  # the remote shell's command line holds it
    "ssh -p 22 -o BatchMode=yes media 'pgrep -f camoufox && echo up'",
    f"ssh media '{PKILL} -f camoufox'",
]

ALLOWED = [
    'pgrep -f "[c]amoufox" || camoufox &',  # the bracket form cannot match its own spelling
    "ssh media \"pgrep -f '[c]amoufox'\"",
    "bash -c 'pgrep -f \"[f]oo\"'",
    'pgrep -f "git push origin main"',  # one spelling at top level: nothing else holds it
    'pgrep -f foo | grep -v "$$"',  # excludes its own pid
    "ssh media 'pgrep -f camoufox | grep -vx \"$$\"'",
    "pgrep foo || foo &",  # no -f: matches process NAMES, not command lines
    "pgrep -x foo; echo foo",
    "git push origin main & pid=$!; while kill -0 $pid; do sleep 1; done",  # the pid, not a name
    'echo "pgrep -f foo || foo"',  # text, not code
    "cat > w.sh <<'EOF'\npgrep -f foo || foo\nEOF",
    "pgrep -f foo || foo  # pgrep-ok: the name is a socket path the shell never spells",
]


@pytest.mark.parametrize("command", REFUSED)
def test_a_pgrep_the_shell_itself_matches_is_refused(command: str, tmp_path: Path) -> None:
    reason = _decide(command, tmp_path)
    assert reason is not None, f"the guard allowed a pgrep that matches its own shell: {command}"
    assert "OWN command line" in reason, reason


def test_the_refusal_names_the_safe_forms(tmp_path: Path) -> None:
    reason = _decide(REFUSED[0], tmp_path)
    assert reason is not None
    for want in ("pid file", "$!", "log line", "[f]oo", "pgrep-ok"):
        assert want in reason, (want, reason)


@pytest.mark.parametrize("command", ALLOWED)
def test_the_safe_forms_run(command: str, tmp_path: Path) -> None:
    assert _decide(command + WAIT_OK, tmp_path) is None, f"the guard refused a safe form: {command}"


def test_an_empty_escape_excuses_nothing(tmp_path: Path) -> None:
    assert _decide("pgrep -f foo || foo  # pgrep-ok:", tmp_path) is not None


def test_the_rule_is_what_refuses(tmp_path: Path) -> None:
    """CALIBRATION: with the new rule gutted, the guard lets the ssh form through."""
    tree = tmp_path / "tree"
    copy_closure(
        tree,
        "hooks/claude/discard_guard.py",
        "lib/self_match.py",
        extra=("lib/busyloop_shapes.json",),
    )
    gutted = tree / "lib" / "self_match.py"
    gutted.write_text(
        re.sub(
            r"\ndef judge_anywhere\(.*?\n(?=\S)",
            "\ndef judge_anywhere(command):\n    return None\n\n",
            gutted.read_text(),
            flags=re.DOTALL,
        )
    )
    assert "def judge_anywhere(command):\n    return None" in gutted.read_text(), "not gutted"
    run = subprocess.run(
        [sys.executable, str(tree / "hooks/claude/discard_guard.py")],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": REFUSED[7]},
                          "cwd": str(tmp_path)}),
        capture_output=True, text=True, timeout=patience(30), check=False,
    )  # fmt: skip
    assert run.returncode == 0 and not run.stdout.strip(), "the rule removed, still refused"
