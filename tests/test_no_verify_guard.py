"""An agent's `git commit` / `git push` that skips the hooks is refused at the tool boundary
(GOH.NOVERIFYGUARD).

THE CASE (2026-11-17). GOH.PREPARITY lane 2 made two commits with `git commit --no-verify`; the
bash-5 guard its new gates/_proven_identity.sh lacked was caught by the enqueue pre-step (200 s)
instead of the commit hook (seconds). The lane rules already said never; nothing enforced it. The
guard (`lib/no_verify.py`) refuses `--no-verify`, commit's `-n`, and a `core.hooksPath` set on the
command line. The escape is `# no-verify-ok: <reason>`.
"""

from __future__ import annotations

import pytest
from test_claude_load_guard import _decide


@pytest.mark.parametrize(
    "command",
    [
        "git commit --no-verify -m x",
        "git commit -n -m x",
        "git commit -anm x",
        "git -c core.hooksPath=/dev/null commit -m x",
        "git -c core.hookspath=/dev/null push",
        "git push --no-verify",
        "git push --no-verify origin main",
        "git -C /tmp/r commit --no-verify -m x",
        "cd /tmp && git add . && git commit -n -m 'wip'",
        "git commit --amend --no-edit --no-verify",
    ],
)
def test_a_hook_skip_is_refused(tmp_path, command):
    reason = _decide(command, tmp_path)
    assert reason is not None, command
    assert "no-verify-ok" in reason and "200 s" in reason, reason


@pytest.mark.parametrize(
    "command",
    [
        "git commit --no-verify -m x  # no-verify-ok: bootstrapping the hook itself",
        "git commit -n -m x # no-verify-ok: hook is under test",
        "git -c core.hooksPath=/dev/null commit # no-verify-ok: fixture repo",
        "git push --no-verify # no-verify-ok: reason",
        "git commit -m 'mentions --no-verify'",  # a flag inside a message is not a flag
        'git commit -m "skip with -n"',
        "git commit -m -n",  # -m's value
        "git commit -F msg.txt",
        "git commit -m x",
        "git push -n origin main",  # push's -n is --dry-run
        "git log -n 3",
        "git -c user.name=x commit -m y",
        "echo git commit --no-verify",
        "git commit -m x -- -n",  # a pathspec after --
    ],
)
def test_an_ordinary_commit_runs(tmp_path, command):
    assert _decide(command, tmp_path) is None, command
