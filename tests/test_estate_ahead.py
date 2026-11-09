"""SessionStart names every estate repo with commits ahead of its upstream (GOH.AHEADREPORT).

A commit landed in a repo other than the item's own (a learning written into ~/.claude/skills from
another repo's session) was pushed by nobody: skills reached 17 commits ahead and no session said
so. hooks/claude/estate_advisories.py now asks each registered repo, locally, how far HEAD is ahead
of its upstream; a repo level with it, or with no upstream at all, says nothing.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from _fast_git import fast_init
from conftest import REPO_ROOT, git

HOOK = REPO_ROOT / "hooks" / "claude" / "estate_advisories.py"
ID = ["-c", "user.name=t", "-c", "user.email=t@t"]


def _commit(repo: Path, name: str) -> None:
    (repo / name).write_text(name + "\n")
    git(repo, "add", name)
    git(repo, *ID, "commit", "-q", "--no-verify", "-m", name)


def _clone(origin: Path, at: Path) -> Path:
    subprocess.run(
        ["git", "clone", "-q", str(origin), str(at)],
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    return at


def _hook(state: Path) -> str:
    r = subprocess.run(
        [sys.executable, str(HOOK)],
        input='{"source": "startup"}',
        capture_output=True,
        text=True,
        env={"GOH_ESTATE_STATE": str(state), "PATH": "/usr/bin:/bin:/opt/homebrew/bin"},
        check=False,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_a_repo_ahead_is_named_and_one_level_or_without_upstream_is_not(tmp_path: Path) -> None:
    seed = tmp_path / "seed"
    fast_init(seed, "main")
    _commit(seed, "a")
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "clone", "-q", "--bare", str(seed), str(origin)],
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    ahead = _clone(origin, tmp_path / "ahead")
    _commit(ahead, "b")
    level = _clone(origin, tmp_path / "level")
    loose = tmp_path / "loose"  # commits, and no upstream to be ahead of
    fast_init(loose, "main")
    _commit(loose, "c")
    state = tmp_path / "state" / "estate_audit.json"
    state.parent.mkdir()
    (state.parent / "installed_repos").write_text(f"{ahead}\n{level}\n{loose}\n")
    out = _hook(state)
    named = [line for line in out.splitlines() if line.startswith("estate: ")]
    assert named == [
        f"estate: {ahead} is 1 commit(s) ahead of its upstream -- unpushed; push it"
    ], out
    _commit(ahead, "d")
    assert f"estate: {ahead} is 2 commit(s) ahead" in _hook(state)
    git(ahead, "push", "-q", "origin", "HEAD")
    assert "estate: " not in _hook(state)
