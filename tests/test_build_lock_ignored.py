"""A goh build, running or killed, leaves `git status` clean in the checkout (GOH.BUILDLOCKIGNORE).

`scripts/build-goh.sh` serialises builders on `bin/.build.lock/` (a directory holding the owner's
pid), removed by an EXIT trap. While a build ran, and after one was killed, the checkout read
`?? bin/` (ci lane, 2026-11-14): the same class as the half-copied `bin/goh.<pid>.staging` that
failed v0.116.0's ship at verify (GOH.STAGINGSTRAY). Naming each transient in `.gitignore` one at a
time is how the lock was missed, so goh's `.gitignore` ignores `/bin/` whole: nothing under it is
ever tracked, and the next transient there is ignored without anyone remembering to add it.
Since GOH.CLASS.CHECKOUT part 2 the lock is not in the tree at all: it is the checkout's
`build.lock` under the run root (lib/run_path.sh), so even `git status --ignored` stays empty.

The build runs for real from a copy of the script in a git checkout carrying goh's `.gitignore`,
with a `cargo` on PATH that announces itself and hangs, so the lock is held when status is read.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT, git, hermetic_env
from timing_bounds import GiveUp

FAKE_CARGO = '#!/bin/sh\necho "$$" > "$CARGO_STARTED"\nexec sleep 60\n'


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """A committed checkout with goh's .gitignore and what build-goh.sh sources, and no crates:
    a tree with no stamp builds in place, as a tarball does."""
    repo = tmp_path / "goh"
    fast_init(repo)
    for rel in (
        ".gitignore",
        "scripts/build-goh.sh",
        "lib/bash5.sh",
        "lib/house_python.sh",
        "lib/run_path.sh",
    ):
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / rel, repo / rel)
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x")
    return repo


def _status(repo: Path) -> str:
    return git(repo, "status", "--porcelain", "--untracked-files=all")


def _every_file(repo: Path) -> str:
    """Ignored files too: the lock lives under the run root, so nothing at all is in the tree."""
    return git(repo, "status", "--porcelain", "--untracked-files=all", "--ignored")


def _lock(run: Path) -> Path:
    """The one build lock under the run root (lib/run_path.sh: one directory per checkout)."""
    locks = sorted(run.glob("*/build.lock"))
    assert len(locks) == 1, locks
    return locks[0]


def test_a_running_and_a_killed_build_leave_the_checkout_clean(checkout, tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "cargo").write_text(FAKE_CARGO)
    (tools / "cargo").chmod(0o755)
    started = tmp_path / "cargo.pid"
    run = tmp_path / "run"
    env = hermetic_env(drop_git=True, PATH=f"{tools}:{os.environ['PATH']}",
                       CARGO_STARTED=str(started), GOH_RUN_DIR=str(run))  # fmt: skip
    build = subprocess.Popen(["bash", str(checkout / "scripts" / "build-goh.sh")], env=env,
                             cwd=checkout, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, start_new_session=True)  # fmt: skip
    try:
        give_up = GiveUp(30)
        while not started.exists():
            assert build.poll() is None, build.communicate()[0]
            assert not give_up.passed(), "the build never reached cargo"
            time.sleep(0.05)
        assert _lock(run).is_dir()  # calibration: the lock is held now
        assert _every_file(checkout) == "", "a running build left a file in the work tree"
        os.killpg(build.pid, signal.SIGKILL)  # killed: no EXIT trap removes the lock
        build.wait(timeout=30)
        assert _lock(run).is_dir()  # calibration: the lock outlived it
        assert _every_file(checkout) == "", "a killed build left a file in the work tree"
    finally:
        if build.poll() is None:
            os.killpg(build.pid, signal.SIGKILL)
            build.wait(timeout=30)


def test_a_new_transient_under_bin_is_ignored_too(checkout):
    """The class, not the instance: a file goh has not named yet is ignored under bin/."""
    (checkout / "bin").mkdir(exist_ok=True)
    (checkout / "bin" / ".some-future.tmp").write_text("x")
    assert _status(checkout) == ""
