"""`goh zsh-split-guard` and `goh shell-check` end to end, as the registered hooks run them
(GOH.ZSHSPLITGUARD). The rules' tables are `crates/goh/src/toolshell/tests.rs`; this pins the
wiring: the event on stdin, the denial on stdout, the tool shell read from the environment."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from conftest import hermetic_env, run_goh
from timing_bounds import patience

TODAY = 'FILES="a b"; for f in $FILES; do git checkout HEAD -- $f; done'
ARRAY = 'FILES=(a b); for f in "${FILES[@]}"; do git checkout HEAD -- "$f"; done'


def _env(shell: str | None) -> dict[str, str]:
    env = hermetic_env(SHELL="/bin/zsh")
    for name in ("CLAUDE_CODE_SHELL", "BASH_ENV"):
        env.pop(name, None)
    if shell:
        env["CLAUDE_CODE_SHELL"] = shell
    return env


def _guard(command: str, shell: str | None, tmp_path) -> str:
    event = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    r = run_goh(tmp_path, "zsh-split-guard", input=event, env=_env(shell))
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_under_zsh_the_for_loop_is_denied_and_the_array_form_runs(tmp_path) -> None:
    out = json.loads(_guard(TODAY, None, tmp_path))["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny" and "array" in out["permissionDecisionReason"]
    assert _guard(ARRAY, None, tmp_path) == ""


@pytest.mark.parametrize("command", [TODAY, ARRAY, "not json at all"])
def test_under_bash_every_command_runs(command: str, tmp_path) -> None:
    assert _guard(command, "/opt/homebrew/bin/bash", tmp_path) == ""


def test_shell_check_refuses_a_zsh_tool_shell_naming_the_fix(tmp_path) -> None:
    r = run_goh(tmp_path, "shell-check", env=_env(None))
    assert r.returncode == 1
    assert "CLAUDE_CODE_SHELL is not set" in r.stderr and "BASH_ENV is not set" in r.stderr


def test_shell_check_passes_a_bash_whose_rc_builds_the_house_env(tmp_path) -> None:
    rc = tmp_path / "rc"
    rc.write_text("export GOH_DIR=/g PYTHONPATH=/x:/g AGENT_ENV_LOADED=1\n")
    bash = shutil.which("bash")
    assert bash
    env = _env(bash)
    env["BASH_ENV"] = str(rc)
    r = run_goh(tmp_path, "shell-check", env=env)
    major = subprocess.run(
        [bash, "-c", 'printf %s "${BASH_VERSINFO[0]}"'], capture_output=True, text=True,
        timeout=patience(30), stdin=subprocess.DEVNULL, check=True,
    ).stdout  # fmt: skip
    if int(major) >= 5:
        assert r.returncode == 0 and "GOH_DIR=/g" in r.stderr, r.stderr
    else:  # macOS's own /bin/bash 3.2 first on PATH: refused, by version
        assert r.returncode == 1 and "older than 5" in r.stderr, r.stderr
