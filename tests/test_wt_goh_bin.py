"""A fresh lane worktree of gates_of_heck runs ./bin/goh at once (GOH.WTGOHBIN).

bin/goh is a build product (ignored), so a worktree `loopctl lane claim` made had none until
someone ran scripts/build-goh.sh, and a lane's first `./bin/goh ...` failed; lanes worked around it
by hand. goh's own `.githooks/post-checkout` (the shim, as every stock hook) runs gates/hook.sh,
which builds bin/goh (`scripts/build-goh.sh --if-stale`: the shared store's copy when it has the
stamp) in a gates_of_heck worktree checked out onto a BRANCH with no bin/goh. A detached checkout
(the push gate's and full run's export worktrees) is left alone: its gate resolves its own binary.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from _fast_git import fast_init
from _hook_goh import hook_goh
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

SHIM = (REPO_ROOT / "hooks" / "pre-commit").read_bytes()
FAKE_BUILD = """#!/usr/bin/env bash
# The fake build: what build-goh.sh leaves behind, a bin/goh that answers --version.
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$root/bin"
printf '#!/bin/sh\\necho "goh 9.9.9 built-by-hook $*"\\n' > "$root/bin/goh"
chmod +x "$root/bin/goh"
echo "$*" > "$root/bin/build-args"
"""


def _git(repo: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=False, env=env or hermetic_env(drop_git=True),
                          stdin=subprocess.DEVNULL, timeout=patience(60))  # fmt: skip


def _goh_tree(root: Path) -> Path:
    """A gates_of_heck-shaped repo: goh's hooks path, a build script, crates/goh."""
    repo = root / "gates_of_heck"
    (repo / ".githooks").mkdir(parents=True)
    hook = repo / ".githooks" / "post-checkout"
    hook.write_bytes(SHIM)
    hook.chmod(0o755)
    (repo / "scripts").mkdir()
    (repo / "scripts" / "build-goh.sh").write_text(FAKE_BUILD)
    (repo / "crates" / "goh").mkdir(parents=True)
    (repo / "crates" / "goh" / "Cargo.toml").write_text('[package]\nname = "goh"\n')
    (repo / ".gitignore").write_text("bin/\n")
    fast_init(repo)
    for kv in (("user.email", "t@t"), ("user.name", "t"), ("core.hooksPath", ".githooks")):
        _git(repo, "config", *kv)
    _git(repo, "add", "-A")
    assert _git(repo, "commit", "-q", "--no-verify", "-m", "fixture").returncode == 0
    return repo


def _env(goh: Path, home: Path) -> dict:
    return hermetic_env(drop_git=True, GOH_DIR=str(goh), HOME=str(home))


def test_a_lane_worktree_runs_its_bin_goh_at_once(tmp_path):
    goh = hook_goh(tmp_path / "goh")
    repo = _goh_tree(tmp_path)
    wt = tmp_path / "lanes" / "1" / "wt"
    made = _git(repo, "worktree", "add", "-q", "-b", "lane-x", str(wt), "HEAD",
                env=_env(goh, tmp_path / "home"))  # fmt: skip
    assert made.returncode == 0, made.stderr
    ran = subprocess.run([str(wt / "bin" / "goh"), "--version"], capture_output=True, text=True,
                         check=False, stdin=subprocess.DEVNULL)  # fmt: skip
    assert ran.returncode == 0 and "built-by-hook --version" in ran.stdout, made.stderr
    assert (wt / "bin" / "build-args").read_text().strip() == "--if-stale"


def test_a_detached_export_worktree_builds_nothing(tmp_path):
    goh = hook_goh(tmp_path / "goh")
    repo = _goh_tree(tmp_path)
    wt = tmp_path / "export"
    made = _git(repo, "worktree", "add", "-q", "--detach", str(wt), "HEAD",
                env=_env(goh, tmp_path / "home"))  # fmt: skip
    assert made.returncode == 0, made.stderr
    assert not (wt / "bin").exists(), made.stderr


def test_another_repos_branch_checkout_builds_nothing(tmp_path):
    goh = hook_goh(tmp_path / "goh")
    repo = _goh_tree(tmp_path)
    _git(repo, "rm", "-rq", "crates")
    _git(repo, "commit", "-q", "--no-verify", "-m", "not goh")
    wt = tmp_path / "lanes" / "2" / "wt"
    made = _git(repo, "worktree", "add", "-q", "-b", "lane-y", str(wt), "HEAD",
                env=_env(goh, tmp_path / "home"))  # fmt: skip
    assert made.returncode == 0, made.stderr
    assert not (wt / "bin").exists(), made.stderr


def test_gohs_own_post_checkout_is_the_shim():
    assert (REPO_ROOT / ".githooks" / "post-checkout").read_bytes() == SHIM
