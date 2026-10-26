"""A git command that would throw away uncommitted work is refused before it runs.

`git checkout -- <file>` restores a file from the index, and the uncommitted edits in it are gone
with no reflog to bring them back. The house calibration loop -- break the code, watch the test go
red, restore it -- reaches for exactly that command, and on a file that held other uncommitted work
it destroyed that work: twice in one CadGoose session (2026-10-17), with the warning already
written in two skills and the umbrella roadmap. A written lesson did not hold, so
`hooks/claude/discard_guard.py` (PreToolUse, Bash) refuses the discard when its scope holds
uncommitted changes, names what would be lost, and says how to proceed. A deliberate discard says
so: `# discard-ok: <reason>` in the command.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT, hermetic_env

GUARD = REPO_ROOT / "hooks" / "claude" / "discard_guard.py"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True,
        env=hermetic_env(drop_git=True), stdin=subprocess.DEVNULL,
    )  # fmt: skip


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "r"
    fast_init(r, "main")
    (r / "a.txt").write_text("one\n")
    (r / "b.txt").write_text("two\n")
    _git(r, "add", "-A")
    _git(r, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "seed")
    return r


def _decide(command: str, cwd: Path) -> str | None:
    """The guard's verdict: its refusal reason, or None when it lets the command run."""
    r = subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}),
        capture_output=True, text=True, env=hermetic_env(drop_git=True), timeout=30, check=False,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    if not r.stdout.strip():
        return None
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny", out
    return out["permissionDecisionReason"]


def test_restoring_a_file_with_uncommitted_work_is_refused_and_names_it(repo: Path) -> None:
    (repo / "a.txt").write_text("one\nwork in progress\n")
    reason = _decide("git checkout -- a.txt", repo)
    assert reason is not None
    assert "a.txt" in reason and "discard-ok" in reason, reason


@pytest.mark.parametrize(
    "command",
    [
        "git checkout a.txt",
        "git checkout HEAD -- a.txt",
        "git restore a.txt",
        "git restore --worktree a.txt",
        "git reset --hard",
        "git reset --hard HEAD",
        "git checkout -f main",
        "git checkout .",
        "git -C {repo} checkout -- a.txt",
        "git -c core.quotepath=off -C {repo} checkout -- a.txt",
        "cd {repo} && git checkout -- a.txt",
        "echo first; git checkout -- a.txt",
    ],
)
def test_every_spelling_of_a_discard_is_seen(repo: Path, tmp_path: Path, command: str) -> None:
    (repo / "a.txt").write_text("one\nwork in progress\n")
    cwd = tmp_path if "{repo}" in command else repo
    assert _decide(command.format(repo=repo), cwd) is not None, command


def test_clean_removes_untracked_work_and_is_refused(repo: Path) -> None:
    (repo / "new.txt").write_text("not yet added\n")
    assert _decide("git clean -fd", repo) is not None


@pytest.mark.parametrize(
    "command",
    [
        "git checkout -- a.txt",  # nothing uncommitted in it
        "git checkout -- b.txt",  # another file is dirty, this one is not
        "git restore --staged a.txt",  # the index only: the worktree keeps the edit
        "git checkout -b topic",
        "git clean -nd",
        "git status",
        "git diff -- a.txt",
        "echo git checkout -- a.txt",
        "git checkout -- a.txt  # discard-ok: undoing my calibration break",
    ],
)
def test_what_loses_nothing_runs(repo: Path, command: str) -> None:
    if command != "git checkout -- a.txt":  # every other case runs beside uncommitted work
        (repo / "a.txt").write_text("one\nwork in progress\n")
    if "--staged" in command:
        _git(repo, "add", "a.txt")
    assert _decide(command, repo) is None, command


def test_outside_a_repository_nothing_is_refused(tmp_path: Path) -> None:
    assert _decide("git checkout -- a.txt", tmp_path) is None
