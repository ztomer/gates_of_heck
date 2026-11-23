"""An escape that comments out the code after it is refused (GOH.ADMITOKLINE, lib/escape_line.py).

2026-11-23: a lane wrote its restore step after `# admit-ok: <reason>` on the same line; the escape
is a shell comment, so the step was its text and never ran, and the guard the escape excused said
nothing. Every `# x-ok:` escape has that shape, so the rule reads them all, and the Bash guard
(hooks/claude/discard_guard.py) asks it first.
"""

from __future__ import annotations

import json
import sys

import pytest
from _hook_inproc import run_hook
from conftest import REPO_ROOT, hermetic_env

sys.path.insert(0, str(REPO_ROOT / "lib"))
import escape_line

GUARD = REPO_ROOT / "hooks" / "claude" / "discard_guard.py"


@pytest.mark.parametrize(
    ("command", "swallowed"),
    [
        ("cargo build # admit-ok: one probe; git restore x", [("admit-ok", "; git restore x")]),
        (
            "osascript -e 'quit app \"X\"' # quit-ok: mine && open -a X",
            [("quit-ok", "&& open -a X")],
        ),
        ("loop & # load-ok: reaped || kill %1", [("load-ok", "|| kill %1")]),
        ("make #secrets-ok: a fixture | tee log", [("secrets-ok", "| tee log")]),
        ("git reset --hard # reset-ok: scratch clone & sleep 1", [("reset-ok", "& sleep 1")]),
        # the escape last on its line, or the rest on its own line: admitted as before
        ("cargo build # admit-ok: a one-off probe", []),
        ("cargo build # admit-ok: a one-off probe\ngit restore x", []),
        ("cargo build # admit-ok: R&D probe", []),
        # text, not code: quoted, in a heredoc body, or a word that only contains a `#`
        ("echo 'x # admit-ok: y; z'", []),
        ("git commit -F - <<'EOF'\nfix # admit-ok: y; z\nEOF", []),
        ("echo a#admit-ok: b; c", []),
        ("ls # a note # admit-ok: y; z", []),  # inside an earlier comment: the line is text already
    ],
)
def test_an_escape_that_swallows_code_is_named(command, swallowed):
    assert escape_line.swallowed(command) == swallowed


def test_the_bash_guard_refuses_the_swallowed_text_and_admits_the_escape_last(tmp_path):
    def decide(command: str) -> dict:
        event = {"tool_name": "Bash", "session_id": "abcdef0123456789", "cwd": str(tmp_path),
                 "tool_input": {"command": command}}  # fmt: skip
        r = run_hook(GUARD, json.dumps(event), hermetic_env(drop_git=True, GOH_GATE_SLOTS="0"))
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)["hookSpecificOutput"] if r.stdout.strip() else {}

    out = decide("cargo build -p x # admit-ok: a probe; git restore Cargo.lock")
    assert out.get("permissionDecision") == "deny", out
    assert "; git restore Cargo.lock" in out["permissionDecisionReason"], out
    assert "`# admit-ok:`" in out["permissionDecisionReason"], out
    assert decide("ls # admit-ok: a probe").get("permissionDecision") is None
