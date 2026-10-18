"""A goh Claude Code hook whose script is missing never blocks every call.

Claude Code reads exit 2 from a PreToolUse hook as "block this tool call" and from a Stop hook as
"do not stop". `python3 <script>` exits 2 on its own when the script is not there -- a moved
GOH_DIR, a checkout mid-rebase, a worktree removed -- so a bare `python3 "<path>"` registration
turns a missing file into every Bash command (discard_guard) or every subagent spawn (agent_guard)
refused. `hooks/claude/registrations.json` is the registration of record; each python entry ends
`|| exit 1`, which Claude Code shows as a non-blocking hook error: loud, never a wall.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env

REGS = json.loads((REPO_ROOT / "hooks" / "claude" / "registrations.json").read_text())["hooks"]
ENTRIES = [(event, e) for event, es in REGS.items() for e in es]
BLOCKS_ON_2 = {"PreToolUse", "Stop", "SubagentStop", "UserPromptSubmit"}


def _sh(command: str, goh_dir: Path, stdin: str = "{}") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/sh", "-c", command], input=stdin, capture_output=True, text=True, timeout=30,
        env=hermetic_env(GOH_DIR=str(goh_dir)), check=False,
    )  # fmt: skip


def test_the_instrument_a_bare_python_registration_on_a_missing_script_exits_2(
    tmp_path: Path,
) -> None:
    bare = 'python3 "${GOH_DIR:-$HOME/Projects/gates_of_heck}/hooks/claude/agent_guard.py"'
    assert _sh(bare, tmp_path).returncode == 2  # the failure this file exists to prevent


@pytest.mark.parametrize(
    ("event", "entry"), ENTRIES, ids=[f"{e}-{x['command'][-30:]}" for e, x in ENTRIES]
)
def test_a_missing_hook_script_never_blocks(tmp_path: Path, event: str, entry: dict) -> None:
    r = _sh(entry["command"], tmp_path)  # GOH_DIR names an empty directory
    assert r.returncode != 2, (event, entry["command"], r.stderr)
    if event in BLOCKS_ON_2:
        assert r.returncode != 0, "a missing hook must still be visible as a hook error"


@pytest.mark.parametrize(
    ("event", "entry"), ENTRIES, ids=[f"{e}-{x['command'][-30:]}" for e, x in ENTRIES]
)
def test_every_registered_script_exists_and_runs_clean_on_an_empty_event(
    event: str, entry: dict
) -> None:
    (path,) = re.findall(r"/(hooks/claude/[\w.]+)", entry["command"])
    assert (REPO_ROOT / path).is_file(), path
    if event != "PostToolUse":  # skill_edit.sh needs a real edit event; its own suite runs it
        assert _sh(entry["command"], REPO_ROOT).returncode == 0
