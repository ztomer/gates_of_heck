"""push_gate.sh refuses a push whose branch MOVED while it was being gated (ZoneWM D-0211).

`git push <https-remote> main` hands the pre-push hook a sha, and the remote helper sends what
`main` is when it SENDS -- after the hook returns. ZoneWM, 2026-10-05: the hook gated d0cf1968 for
25 minutes, two commits landed meanwhile, and GitHub received f79cf19f, which no gate ran on
(PushEvent before=c149e3bb head=f79cf19f). A bare file remote sends the gated sha, so a real push
in a test would report "fine"; the defect is the hook's to catch, not git's to prevent.

So after gating, the hook re-reads every pushed local ref: one that no longer names the gated
commit is refused, naming both shas, and the push stops before anything is sent.
"""

import os
import subprocess
from pathlib import Path

from test_push_gate import GATE, PUSH_GATE, REPO_ROOT, _git, _repo

# The gate commits to the PUSHING checkout while it runs -- the 25-minute window, compressed.
MOVING_GATE = GATE.replace(
    "exit 0\n",
    'git -C "$GOH_CROSS_REPO_ROOT" commit -q --allow-empty -m "landed during the gate"\nexit 0\n',
)


def _push(repo: Path, tmp_path: Path, local_ref: str) -> subprocess.CompletedProcess:
    sha = _git(repo, "rev-parse", "HEAD")
    env = dict(
        os.environ,
        GATE_REPORT=str(tmp_path / "report.txt"),
        GOH_DIR=str(REPO_ROOT),
        GOH_PUSH_LOGS=str(tmp_path / "push-logs"),
        GOH_PUSH_WORKTREES=str(tmp_path / "exports"),
    )
    return subprocess.run(
        ["bash", str(PUSH_GATE), "origin"],
        cwd=repo,
        env=env,
        text=True,
        input=f"{local_ref} {sha} refs/heads/main {'0' * 40}\n",
        capture_output=True,
        timeout=60,
    )


def _moving_repo(tmp_path: Path) -> Path:
    repo = _repo(tmp_path)
    (repo / "tools" / "gate.sh").write_text(MOVING_GATE)
    _git(repo, "commit", "-q", "-am", "a gate that moves main")
    return repo


def test_a_branch_that_moved_during_the_gate_is_refused(tmp_path):
    repo = _moving_repo(tmp_path)
    gated = _git(repo, "rev-parse", "HEAD")
    r = _push(repo, tmp_path, "refs/heads/main")
    moved = _git(repo, "rev-parse", "HEAD")
    assert moved != gated  # the plant worked: main moved while the gate ran
    assert r.returncode != 0, r.stdout + r.stderr
    assert gated[:12] in r.stderr and moved[:12] in r.stderr, r.stderr


def test_a_pinned_sha_push_is_not_refused_when_the_branch_moves(tmp_path):
    """`git push origin <sha>:refs/heads/main` sends the sha, whatever the branch does: the pinned
    form ZoneWM now uses must stay green."""
    repo = _moving_repo(tmp_path)
    gated = _git(repo, "rev-parse", "HEAD")
    r = _push(repo, tmp_path, gated)
    assert r.returncode == 0, r.stdout + r.stderr
