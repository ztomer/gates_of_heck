"""The commit-msg hook runs the repo's own `tools/commit_msg.sh` with no hook repository variables.

GOH.ENVLEAK, 2026-11-11. pre-commit, pre-push, post-rewrite and pre-applypatch drop git's
repository variables at entry (gates/_git_env.sh `goh_hook_unbind`); commit-msg did not, so a
commit from a LINKED worktree handed the consumer's `tools/commit_msg.sh` an absolute GIT_DIR
(and, under `commit -a`, GIT_INDEX_FILE) -- every scratch-repo init that script's children ran
re-initialised the real repository, the media_server corruption (contract #12) by the one hook
left open. Each case commits for real from a linked worktree, so git itself sets the variables.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT, hermetic_env

REPORTER = """#!/usr/bin/env bash
printf 'GIT_DIR=%s\\nGIT_INDEX_FILE=%s\\n' "${GIT_DIR-unset}" "${GIT_INDEX_FILE-unset}" > "$REPORT"
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    ).stdout.strip()


@pytest.mark.parametrize("how", [(), ("-a",)], ids=["plain", "commit-a"])
def test_the_consumer_commit_msg_script_never_sees_the_hooks_git_dir(tmp_path, how):
    main = tmp_path / "main"
    (main / ".githooks").mkdir(parents=True)
    shutil.copy(REPO_ROOT / "hooks" / "commit-msg", main / ".githooks" / "commit-msg")
    (main / "tools").mkdir()
    (main / "tools" / "commit_msg.sh").write_text(REPORTER)
    (main / "tools" / "commit_msg.sh").chmod(0o755)
    (main / "README.md").write_text("consumer\n")
    fast_init(main, "main")
    _git(main, "config", "user.email", "t@t")
    _git(main, "config", "user.name", "t")
    _git(main, "add", "-A")
    _git(main, "commit", "-q", "--no-verify", "-m", "one")
    _git(main, "config", "core.hooksPath", ".githooks")
    wt = tmp_path / "wt"
    _git(main, "worktree", "add", "-q", str(wt))
    (wt / "README.md").write_text("changed\n")
    if not how:
        _git(wt, "add", "README.md")
    report = tmp_path / "report"
    done = subprocess.run(
        ["git", "-C", str(wt), "commit", "-q", "-m", "ours", *how],
        env=hermetic_env(drop_git=True, REPORT=str(report)),
        capture_output=True,
        text=True,
        timeout=120,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    # Calibration: the hook really ran the consumer script, so "unset" is a reading, not silence.
    assert report.read_text() == "GIT_DIR=unset\nGIT_INDEX_FILE=unset\n"
