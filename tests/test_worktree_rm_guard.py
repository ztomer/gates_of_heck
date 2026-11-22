"""A recursive rm of a path holding a dirty git work tree is refused at the tool boundary
(LOOP.MISTAKEGUARDS, mistake 1).

THE CASE (2026-11-22). The coordinator ran `rm -rf` on a scratch directory that held two registered
git worktrees with uncommitted tracked edits; the edits went with it. The Bash guard
(`lib/worktree_rm.py`) walks each recursive rm's operand for `.git` entries and refuses when one
has uncommitted tracked changes, naming the tree and its files. Escape: `# rm-ok: <reason>`.
Every fixture lives in tmp_path; no test deletes anything (the guard only judges).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from _fast_git import fast_init
from _hook_inproc import run_hook
from conftest import REPO_ROOT, hermetic_env

GUARD = REPO_ROOT / "hooks" / "claude" / "discard_guard.py"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True, capture_output=True, env=hermetic_env(drop_git=True), stdin=subprocess.DEVNULL,
    )  # fmt: skip


def _decide(command: str, cwd: Path) -> str | None:
    r = run_hook(
        GUARD,
        json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}),
        hermetic_env(drop_git=True),
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    if not r.stdout.strip():
        return None
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny", out
    return out["permissionDecisionReason"]


@pytest.fixture
def scratch(tmp_path: Path) -> Path:
    """`tmp_path/scratch` holding two linked worktrees of `tmp_path/main` (the incident's shape):
    `wt1` with an edited tracked file, `wt2` clean, plus a plain directory."""
    main = tmp_path / "main"
    fast_init(main, "main")
    (main / "a.txt").write_text("one\n")
    (main / "b.txt").write_text("two\n")
    _git(main, "add", "-A")
    _git(main, "commit", "-qm", "seed")
    scratch = tmp_path / "scratch"
    (scratch / "deep").mkdir(parents=True)
    (scratch / "notes").mkdir()
    _git(main, "worktree", "add", "-q", "-b", "l1", str(scratch / "deep" / "wt1"))
    _git(main, "worktree", "add", "-q", "-b", "l2", str(scratch / "wt2"))
    (scratch / "deep" / "wt1" / "a.txt").write_text("edited, uncommitted\n")
    return scratch


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf {s}",  # the incident
        "rm -r {s}/",
        "rm -fR {s}/deep",
        "rm --recursive --force {s}/deep/wt1",
        "rm -rf {s}/*",
        "cd {s} && rm -rf deep",
        "sudo rm -rf {s}",
        "true; rm -rf -- {s}",
    ],
)
def test_a_recursive_rm_over_a_dirty_worktree_is_refused_and_names_it(scratch, command):
    reason = _decide(command.format(s=scratch), scratch.parent)
    assert reason is not None, command
    assert str(scratch / "deep" / "wt1") in reason and "a.txt" in reason, reason
    assert "rm-ok" in reason, reason


def test_a_relative_operand_resolves_against_the_session_cwd(scratch):
    assert _decide("rm -rf scratch", scratch.parent) is not None


def test_a_staged_or_deleted_tracked_file_counts_too(scratch):
    (scratch / "wt2" / "b.txt").unlink()
    reason = _decide(f"rm -rf {scratch / 'wt2'}", scratch.parent)
    assert reason is not None and "b.txt" in reason, reason


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf {s}/wt2",  # a clean worktree: `git worktree remove` would allow it too
        "rm -rf {s}/notes",
        "rm {s}/deep/wt1/a.txt",  # not recursive
        "echo 'rm -rf {s}'",  # text, not code
        "rm -rf {s}/does-not-exist",
        "rm -rf {s} # rm-ok: the edits were pushed from another clone",
        "ls {s} -R",
    ],
)
def test_what_loses_no_tracked_change_runs(scratch, command):
    assert _decide(command.format(s=scratch), scratch.parent) is None, command


def test_untracked_files_alone_are_not_the_rule(scratch):
    _git(scratch / "deep" / "wt1", "checkout", "--", "a.txt")
    (scratch / "deep" / "wt1" / "new.txt").write_text("untracked\n")
    assert _decide(f"rm -rf {scratch}", scratch.parent) is None


def test_a_tree_too_large_to_walk_runs_unjudged(scratch, monkeypatch):
    import worktree_rm

    monkeypatch.setattr(worktree_rm, "WALK_LIMIT", 1)
    assert worktree_rm.judge(f"rm -rf {scratch}", str(scratch.parent)) is None
