"""THE HOLD: a held repo is refused at the tool boundary, naming why and the way out (2026-10-26).

ztomer/apes was archived on GitHub and its clone kept read-only (`chmod -R a-w`) with `goh.archived`
in its config. A write then failed with a bare EACCES that named neither the reason nor the lift --
and an agent's reflex on EACCES is `chmod u+w`, which undoes the layer. `lib/repo_hold.py` reads the
hold (`goh.hold`, or the legacy `goh.archived`) by PARSING the config, and
`hooks/claude/hold_guard.py` refuses Edit/Write/NotebookEdit inside a held repo and every Bash
command that would change one -- chmod included -- while read-only commands run.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, git

sys.path.insert(0, str(REPO_ROOT / "lib"))
import repo_hold

GUARD = REPO_ROOT / "hooks" / "claude" / "hold_guard.py"
REASON = "parked by owner ruling"


def _hold(repo: Path, key: str = "hold", value: str | None = None) -> Path:
    git(repo, "config", "--local", f"goh.{key}", value or f"2026-10-26: {REASON} -- lift: lift-cmd")
    return repo


def _verdict(event: dict) -> str | None:
    r = subprocess.run(
        [sys.executable, str(GUARD)], input=json.dumps(event), capture_output=True, text=True,
        timeout=30, check=False,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    if not r.stdout.strip():
        return None
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"
    return out["permissionDecisionReason"]


def _bash(command: str, cwd: Path) -> str | None:
    return _verdict({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)})


# ---- the reader -------------------------------------------------------------------------------


def test_an_unheld_repo_has_no_hold(repo: Path) -> None:
    assert repo_hold.find_hold(str(repo / "README.md")) is None


def test_the_hold_is_read_from_any_path_inside_even_one_not_yet_written(repo: Path) -> None:
    _hold(repo)
    hold = repo_hold.find_hold(str(repo / "new" / "dir" / "file.txt"))
    assert hold is not None and hold.root == str(repo)
    assert hold.reason() == f"2026-10-26: {REASON}" and hold.lift() == "lift-cmd"


def test_the_legacy_archived_key_is_a_hold_lifted_by_unarchive(repo: Path) -> None:
    _hold(repo, "archived", "2026-10-26: archived on GitHub; unarchive on GitHub first")
    hold = repo_hold.find_hold(str(repo))
    assert hold is not None and hold.key == "archived"
    assert "unarchive_repo.sh" in hold.lift()


def test_a_linked_worktree_reads_the_shared_config(repo: Path, tmp_path: Path) -> None:
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", str(wt), "-b", "side")
    assert repo_hold.find_hold(str(wt / "README.md")) is None
    _hold(repo)
    hold = repo_hold.find_hold(str(wt / "README.md"))
    assert hold is not None and hold.root == str(wt)


def test_a_nested_repo_inside_a_held_one_is_inside_the_hold(repo: Path) -> None:
    inner = repo / "vendor" / "inner"
    inner.mkdir(parents=True)
    git(inner, "init", "-q")
    _hold(repo)
    hold = repo_hold.find_hold(str(inner / "x"))
    assert hold is not None and hold.root == str(repo)


def test_quoted_values_and_comments_parse_as_git_does(tmp_path: Path) -> None:
    cfg = tmp_path / "config"
    cfg.write_text('[core]\n\tbare = false\n[goh]\n\thold = "a \\"b\\" ; c" ; note\n')
    assert repo_hold.config_holds(str(cfg)) == [("hold", 'a "b" ; c')]


def test_holds_below_finds_a_held_repo_under_an_ancestor(repo: Path) -> None:
    _hold(repo)
    assert [h.root for h in repo_hold.holds_below(str(repo.parent))] == [str(repo)]


def test_the_cli_check_refuses_with_reason_and_lift(repo: Path) -> None:
    r = subprocess.run(
        [sys.executable, str(REPO_ROOT / "lib" / "repo_hold.py"), "check", str(repo),
         "--action", "pre-commit: this commit"],
        capture_output=True, text=True, timeout=30, check=False, stdin=subprocess.DEVNULL,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    _hold(repo)
    r = subprocess.run(
        [sys.executable, str(REPO_ROOT / "lib" / "repo_hold.py"), "check", str(repo)],
        capture_output=True, text=True, timeout=30, check=False, stdin=subprocess.DEVNULL,
    )  # fmt: skip
    assert r.returncode == 1 and REASON in r.stderr and "lift-cmd" in r.stderr, r.stderr


# ---- the guard: edits -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "key"), [("Edit", "file_path"), ("Write", "file_path"), ("MultiEdit", "file_path"),
                      ("NotebookEdit", "notebook_path")],
)  # fmt: skip
def test_an_edit_inside_a_held_repo_is_refused_naming_why_and_the_lift(
    repo: Path, tool: str, key: str
) -> None:
    event = {"tool_name": tool, "tool_input": {key: str(repo / "README.md")}, "cwd": "/"}
    assert _verdict(event) is None
    _hold(repo)
    reason = _verdict(event)
    assert reason and REASON in reason and "lift-cmd" in reason and tool in reason


def test_an_edit_outside_a_held_repo_runs(repo: Path, tmp_path: Path) -> None:
    _hold(repo)
    event = {"tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "o.txt")}, "cwd": "/"}
    assert _verdict(event) is None


# ---- the guard: commands ----------------------------------------------------------------------

READS = [
    "ls -la", "cat README.md", "rg foo .", "git log --oneline", "git status", "git diff",
    "git show HEAD", "git branch", "git branch -a", "git tag -l", "git config --get goh.hold",
    "git stash list", "cp README.md /tmp/out.md", "echo 'rm -rf .' > /dev/null",
    "cat <<'EOF' > /tmp/note\nrm -rf README.md\nEOF", "grep -r x . 2>&1 | head",
]  # fmt: skip
WRITES = [
    "git commit -m x", "git push", "git add README.md", "git checkout -b x", "git reset --hard",
    "git stash", "git rm README.md", "git mv README.md b.md", "git config --unset goh.hold",
    "git branch -D main", "git tag v1", "git merge side", "git rebase main", "git pull",
    "rm README.md", "rm -rf .", "mv README.md b.md", "cp /etc/hosts README.md", "touch new.txt",
    "sed -i '' 's/a/b/' README.md", "perl -pi -e 's/a/b/' README.md", "echo x > README.md",
    "echo x >> README.md", "printf x | tee README.md", "chmod -R u+w .", "chmod u+w README.md",
    "chflags nouchg README.md", "chown me README.md", "mkdir sub", "find . -name x -delete",
    "FOO=1 sudo rm README.md", "true && rm README.md", "ls\nrm README.md",
]  # fmt: skip


@pytest.mark.parametrize("command", READS)
def test_a_read_only_command_in_a_held_repo_runs(repo: Path, command: str) -> None:
    _hold(repo)
    assert _bash(command, repo) is None


@pytest.mark.parametrize("command", WRITES)
def test_a_changing_command_in_a_held_repo_is_refused(repo: Path, command: str) -> None:
    assert _bash(command, repo) is None  # unheld: the same command runs
    _hold(repo)
    reason = _bash(command, repo)
    assert reason and REASON in reason and "lift-cmd" in reason, (command, reason)


def test_the_repo_is_reached_by_cd_and_by_git_dash_c(repo: Path, tmp_path: Path) -> None:
    _hold(repo)
    assert _bash(f"cd {repo} && git commit -m x", tmp_path)
    assert _bash(f"git -C {repo} push", tmp_path)
    assert _bash(f"rm {repo}/README.md", tmp_path)
    assert _bash(f"cd {repo} && git log", tmp_path) is None


def test_chmod_on_an_ancestor_that_would_unlock_a_held_repo_is_refused(
    repo: Path, tmp_path: Path
) -> None:
    _hold(repo)
    assert _bash(f"chmod -R u+w {repo.parent}", tmp_path)
    assert (
        _bash(f"chmod u+w {repo.parent}", tmp_path) is None
    )  # not recursive: the repo is untouched


def test_a_copy_out_of_a_held_repo_runs_and_a_copy_in_is_refused(
    repo: Path, tmp_path: Path
) -> None:
    _hold(repo)
    assert _bash(f"cp {repo}/README.md {tmp_path}/x", tmp_path) is None
    assert _bash(f"cp {tmp_path}/x {repo}/README.md", tmp_path)


def test_release_hold_is_the_owners_and_unarchive_runs(repo: Path) -> None:
    _hold(repo)
    assert _bash(f"bash {REPO_ROOT}/scripts/release_hold.sh {repo}", repo)
    assert _bash(f"{REPO_ROOT}/scripts/unarchive_repo.sh {repo}", repo) is None
