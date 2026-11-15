"""A load directory inside a git work tree is refused (GOH.STRAYLOCK).

2026-11-14 07:58: a 0-byte untracked `.registry.lock` appeared at the gates_of_heck root with no
holder -- a load run whose load directory (GOH_LOAD_DIR or --dir) was the checkout. It made goh
read "1 uncommitted", which under LOOP.ORPHANTREE holds every goh item, until it was removed by
hand. The lock and the registries are machine state; a work tree is the one place they read as
somebody's change. So the directory is refused there, at the tools' door and at the lock itself
(`load_registry.locked`, which every write runs inside), and nothing is created first.
"""

from __future__ import annotations

import subprocess
import sys

import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT, hermetic_env

sys.path.insert(0, str(REPO_ROOT / "lib"))
import load_registry as reg

LOAD = REPO_ROOT / "tools" / "load.py"
REAP = REPO_ROOT / "tools" / "load_reap.py"


@pytest.fixture
def checkout(tmp_path):
    repo = tmp_path / "repo"
    fast_init(repo)
    return repo


def _run(argv, env_extra=None):
    env = hermetic_env(**(env_extra or {}))
    return subprocess.run(
        [sys.executable, *map(str, argv)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        env=env,
        stdin=subprocess.DEVNULL,
    )


def _untracked(repo) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
    ).stdout


@pytest.mark.parametrize("where", [".", "sub/load"])
def test_load_refuses_a_dir_flag_inside_a_checkout(checkout, where):
    r = _run([LOAD, "--dir", checkout / where, "0", "--", "true"])
    assert r.returncode == 2, r.stdout + r.stderr
    assert "inside the git work tree" in r.stderr, r.stderr
    assert _untracked(checkout) == "", _untracked(checkout)


def test_load_refuses_GOH_LOAD_DIR_inside_a_checkout(checkout):
    r = _run([LOAD, "--status"], {"GOH_LOAD_DIR": str(checkout)})
    assert r.returncode == 2, r.stdout + r.stderr
    assert "inside the git work tree" in r.stderr, r.stderr
    assert _untracked(checkout) == ""


def test_the_reaper_refuses_a_dir_inside_a_checkout(checkout):
    r = _run([REAP, "--dir", checkout])
    assert r.returncode == 2, r.stdout + r.stderr
    assert "inside the git work tree" in r.stderr, r.stderr
    assert _untracked(checkout) == ""


def test_the_lock_itself_refuses_a_checkout(checkout):
    # The backstop under both tools: no caller can write the lock into a work tree.
    with pytest.raises(reg.InWorkTree), reg.locked(checkout):
        pass
    assert not (checkout / reg.LOCK_NAME).exists()


def test_a_dir_outside_any_checkout_still_works(tmp_path):
    outside = tmp_path / "load"
    with reg.locked(outside):
        pass
    assert (outside / reg.LOCK_NAME).exists()
