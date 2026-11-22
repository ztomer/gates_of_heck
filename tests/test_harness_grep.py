"""The harness grep's `-v -q` (GOH.UGREPVQ, `lib/harness_grep.py`): Claude Code's tool-shell `grep`
is its embedded ugrep, which exits 1 for `-v -q` while a non-matching line exists. The guard
refuses such a call unless it is the system grep, and `goh shell-check`'s probe record lifts it."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT
from test_claude_load_guard import _decide

sys.path.insert(0, str(REPO_ROOT / "lib"))
import harness_grep

TODAY = "until ls $D | grep X | grep -vq OLD; do sleep 20; done"
EXE = "/Applications/Claude.app/claude"


@pytest.mark.parametrize(
    "command",
    [
        TODAY,
        "printf 'a\\nb\\n' | grep -vq a",
        "ls | grep -qv x",
        "ls | grep -v -q x && echo y",
        "ls | grep --invert-match --quiet x",
        "ls | grep -v --silent x",
        "if ls | grep -vqE '^x'; then echo y; fi",
    ],
)
def test_a_harness_grep_with_v_and_q_is_refused(command: str, tmp_path: Path) -> None:
    reason = harness_grep.judge(command, {"CLAUDE_CODE_EXECPATH": EXE}, tmp_path / "none.json")
    assert reason is not None, command
    assert "command grep" in reason and "| read" in reason


@pytest.mark.parametrize(
    "command",
    [
        "ls | command grep -vq OLD",  # the escape: the system grep, past the function
        "ls | /usr/bin/grep -vq OLD",
        "ls | grep -v OLD | read",
        "ls | grep -q OLD",
        "ls | grep -v OLD",
        "echo 'grep -vq x'",  # text, not a call
        "cat <<'EOF'\nls | grep -vq x\nEOF",
        "ls | grep -vq x  # grep-vq-ok: the probe itself",
        "ls | grep -e -vq x",  # -vq is the pattern here
    ],
)
def test_the_system_grep_text_and_other_flags_pass(command: str, tmp_path: Path) -> None:
    assert harness_grep.judge(command, {"CLAUDE_CODE_EXECPATH": EXE}, tmp_path / "x.json") is None


def test_a_probe_record_of_exit_0_for_this_binary_lifts_the_refusal(tmp_path: Path) -> None:
    state = tmp_path / "harness_grep.json"
    env = {"CLAUDE_CODE_EXECPATH": EXE}
    state.write_text(json.dumps({"execpath": EXE, "vq_exit": 1}))
    assert harness_grep.judge(TODAY, env, state) is not None
    state.write_text(json.dumps({"execpath": EXE, "vq_exit": 0}))
    assert harness_grep.judge(TODAY, env, state) is None
    # A new binary (a Claude Code update) is unprobed: refused until the next shell-check.
    assert harness_grep.judge(TODAY, {"CLAUDE_CODE_EXECPATH": EXE + "2"}, state) is not None


def test_the_state_path_follows_xdg_state_home(tmp_path: Path) -> None:
    assert harness_grep.state_path({"XDG_STATE_HOME": str(tmp_path)}) == (
        tmp_path / "goh" / "harness_grep.json"
    )
    assert harness_grep.state_path({"HOME": "/h"}) == Path("/h/.local/state/goh/harness_grep.json")


def test_the_bash_guard_refuses_todays_command(tmp_path: Path) -> None:
    reason = _decide(TODAY, tmp_path)
    assert reason is not None and "command grep" in reason, reason
    assert _decide("ls | command grep -vq OLD", tmp_path) is None
