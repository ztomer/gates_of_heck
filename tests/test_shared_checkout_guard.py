"""A stash, reset or branch switch in a SHARED checkout is refused before it runs (GOH.NORESET).

On 2026-11-09 and 2026-11-11 agents ran a bare `git reset -q` in a checkout other sessions write
to (token-pool): harmless those times, but the same command unstages everyone's staged work. The
Bash guard (`hooks/claude/discard_guard.py`, rule `lib/shared_checkout.py`) refuses stash, reset,
a branch switch and a whole-index unstage in a PRIMARY checkout; a linked worktree is its
creator's, and a pathed `git restore --staged` and the `# reset-ok:` escape run.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, git, hermetic_env

GUARD = REPO_ROOT / "hooks" / "claude" / "discard_guard.py"


def _decide(command: str, cwd: Path) -> str | None:
    r = subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command},
                          "cwd": str(cwd)}),
        capture_output=True, text=True, env=hermetic_env(drop_git=True), timeout=30, check=False,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    if not r.stdout.strip():
        return None
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny", out
    return out["permissionDecisionReason"]


@pytest.fixture
def shared(repo: Path) -> Path:
    git(repo, "branch", "other")
    return repo


@pytest.mark.parametrize(
    "command",
    [
        "git reset -q",
        "git reset --soft HEAD~1",
        "git reset HEAD -- a.py",
        "git stash",
        "git stash push -m wip",
        "git stash pop",
        "git switch other",
        "git switch -c new",
        "git checkout other",
        "git checkout -b new",
        "git checkout -",
        "git restore --staged",
        "git restore --staged .",
        "git restore -S :/",
        "git add x && git reset -q; echo done",
        "echo hi\ngit reset -q",
        "git -c core.quotepath=off reset",
        "rtk git reset -q",
    ],
)
def test_each_form_is_refused_in_a_primary_checkout(shared: Path, command: str) -> None:
    why = _decide(command, shared)
    assert why is not None, command
    assert "worktree" in why and "reset-ok" in why and "restore --staged" in why, why


def test_a_cd_or_dash_c_into_the_shared_checkout_is_followed(shared: Path, tmp_path: Path) -> None:
    assert _decide(f"cd {shared} && git reset -q", tmp_path) is not None
    assert _decide(f"git -C {shared} stash", tmp_path) is not None


@pytest.mark.parametrize(
    "command",
    [
        "git restore --staged a.py b.py",
        "git restore --staged -- tests/x.py",
        "git checkout -- a.py",
        "git checkout HEAD -- a.py",
        "git stash list",
        "git stash show -p",
        "git status && git log --grep reset",
        "git commit -q -F - <<'EOF'\nfix: refuse a bare reset\n\ngit reset -q; git stash\nEOF",
        "echo 'git reset --hard'",
        "git reset -q  # reset-ok: my own fixture repo, nobody else writes here",
    ],
)
def test_a_pathed_unstage_a_read_text_and_the_escape_run(shared: Path, command: str) -> None:
    assert _decide(command, shared) is None, command


def test_a_linked_worktree_is_its_creators(shared: Path, tmp_path: Path) -> None:
    wt = tmp_path / "wt"
    git(shared, "worktree", "add", "-q", "-b", "mine", str(wt))
    for command in ("git reset -q", "git stash", "git switch other", "git checkout -b x2"):
        assert _decide(command, wt) is None, command


def test_outside_any_repository_nothing_is_refused(tmp_path: Path) -> None:
    assert _decide("git reset -q", tmp_path) is None
