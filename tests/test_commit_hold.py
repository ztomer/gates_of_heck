"""GOH.HOLDSUBPROC: the loop's one-writer hold is judged at COMMIT, whoever runs git.

Seen by GOH.GRACEEND (2026-11-15): a commit in a repo the loop held for another item got past the
hold because a Python subprocess made it; the Claude Code hook (`hooks/claude/hold_guard.py`)
judges only the tool call it is shown, so it caught the push, never the commit. Now the Bash guard
stamps every SUBAGENT shell with its agent id (`GOH_LOOP_AGENT`, `lib/commit_hold.py stamp`), every
process that shell starts inherits it, and the repo's own pre-commit hook (`gates/hook.sh`) asks
`lib/commit_hold.py check`, which refuses a commit in a repo another agent's item holds.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from _fast_git import fast_init
from _tree_clone import from_template
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import commit_hold

HOLDER, OTHER = "a1111111111111111", "a2222222222222222"
CHECK = REPO_ROOT / "lib" / "commit_hold.py"


def _build_held(repo: Path) -> None:
    """The held repo, built once per run (GOH.GITINITGATE): its pre-commit runs the check."""
    repo.mkdir(parents=True)
    env = hermetic_env(drop_git=True)
    fast_init(repo)
    for cmd in (["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", *cmd], cwd=repo, check=True, env=env, stdin=subprocess.DEVNULL)
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{CHECK}" check\n')
    hook.chmod(0o755)


@pytest.fixture
def held(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """A real repo `held` that HOLDER's in-progress item holds; its pre-commit runs the check."""
    projects = tmp_path / "Projects"
    projects.mkdir()
    repo = from_template(tmp_path_factory, "commit-hold", _build_held, projects / "held")
    (projects / ".loop").mkdir()
    queue = projects / ".loop" / "queue.json"
    queue.write_text(json.dumps({"items": [
        {"id": "X.HELD", "repo": "held", "status": "in_progress", "agent": HOLDER}]}))  # fmt: skip
    return repo, queue


def _commit_from_python(repo: Path, queue: Path, agent: str | None) -> subprocess.CompletedProcess:
    """A Python subprocess commits (the GOH.GRACEEND shape): no tool call shows the `git commit`."""
    env = hermetic_env(drop_git=True, GOH_LOOP_QUEUE=str(queue))
    if agent:
        env[commit_hold.AGENT_ENV] = agent
    (repo / "f.txt").write_text(f"{agent}\n")
    script = (
        "import subprocess, sys\n"
        "subprocess.run(['git', 'add', 'f.txt'], check=True)\n"
        "sys.exit(subprocess.run(['git', 'commit', '-q', '-m', 'x']).returncode)\n"
    )
    return subprocess.run([sys.executable, "-c", script], cwd=repo, env=env,
                          capture_output=True, text=True, timeout=patience(60), check=False,
                          stdin=subprocess.DEVNULL)  # fmt: skip


def _commits(repo: Path) -> int:
    r = subprocess.run(["git", "rev-list", "--count", "--all"], cwd=repo, capture_output=True,
                       text=True, check=False, env=hermetic_env(drop_git=True),
                       stdin=subprocess.DEVNULL)  # fmt: skip
    return int(r.stdout.strip() or 0)


def test_a_subprocess_commit_by_another_agent_is_refused_at_commit(held: tuple[Path, Path]) -> None:
    repo, queue = held
    r = _commit_from_python(repo, queue, OTHER)
    assert r.returncode != 0 and _commits(repo) == 0
    assert "X.HELD" in r.stderr and HOLDER in r.stderr and OTHER in r.stderr


def test_the_holder_and_an_unstamped_shell_commit(held: tuple[Path, Path]) -> None:
    """The holder commits; so does a shell no subagent stamped (the coordinator, the owner)."""
    repo, queue = held
    assert _commit_from_python(repo, queue, HOLDER).returncode == 0
    assert _commit_from_python(repo, queue, None).returncode == 0
    assert _commits(repo) == 2


def test_a_missing_queue_holds_nothing(held: tuple[Path, Path], tmp_path: Path) -> None:
    repo, _ = held
    assert _commit_from_python(repo, tmp_path / "none.json", OTHER).returncode == 0


def _event(command: str, agent: str | None) -> dict:
    event = {"tool_name": "Bash", "tool_input": {"command": command, "timeout": 5}, "cwd": "/"}
    return event | ({"agent_id": agent} if agent else {})


def test_a_subagent_shell_is_stamped_with_its_agent() -> None:
    out = commit_hold.stamp(_event("git commit -m x", OTHER), None)
    new = out["hookSpecificOutput"]["updatedInput"]
    assert new["command"] == f"export {commit_hold.AGENT_ENV}={OTHER}\ngit commit -m x"
    assert new["timeout"] == 5 and "permissionDecision" not in out["hookSpecificOutput"]


def test_the_stamp_composes_with_an_earlier_rewrite() -> None:
    """The heavy-job admission already rewrote the command: the stamp goes in front of it."""
    rewritten = {"command": "eval admit\ncargo build"}
    admitted = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": rewritten}}
    out = commit_hold.stamp(_event("cargo build", OTHER), admitted)
    cmd = out["hookSpecificOutput"]["updatedInput"]["command"]
    assert cmd == f"export {commit_hold.AGENT_ENV}={OTHER}\neval admit\ncargo build"


@pytest.mark.parametrize("agent", [None, "", "a1; rm -rf /", "$(id)"])
def test_the_main_thread_and_an_unsafe_id_are_never_stamped(agent: str | None) -> None:
    assert commit_hold.stamp(_event("git commit", agent), None) is None


def test_the_bash_guard_stamps_a_subagent_shell() -> None:
    """End to end through the registered guard (hooks/claude/discard_guard.py)."""
    guard = REPO_ROOT / "hooks" / "claude" / "discard_guard.py"
    event = {"session_id": "s", "hook_event_name": "PreToolUse", **_event("git status", OTHER)}
    r = subprocess.run([sys.executable, str(guard)], input=json.dumps(event), capture_output=True,
                       text=True, timeout=patience(30), check=False,
                       env=hermetic_env())  # fmt: skip
    assert r.returncode == 0, r.stderr
    cmd = json.loads(r.stdout)["hookSpecificOutput"]["updatedInput"]["command"]
    assert cmd.startswith(f"export {commit_hold.AGENT_ENV}={OTHER}\n")


def test_the_repo_pre_commit_hook_runs_the_check_before_the_gate() -> None:
    text = (REPO_ROOT / "gates" / "hook.sh").read_text()
    pre = text[text.index("pre-commit)") :]
    assert pre.index("commit_hold.py") < pre.index("commit_gate.sh")
    assert os.access(CHECK, os.R_OK)
