"""lib/run_path.sh, run_path.py and the Rust run_path.rs: one root, one key (GOH.CLASS.CHECKOUT).

Every goh temp, lock and stage path is minted under the run root, outside every work tree. A lock
the shell takes is one a Python process must wait on, so the faces must name the SAME directory for
a tree: both are pinned here to the vector the Rust face's unit test pins (`printf %s /a/repo |
sha256sum`), and to each other on a real directory. `goh_index_tree` writes the tree of the index
git would commit from a private copy, so it answers while another git holds the shared
`index.lock`, and it reads GIT_INDEX_FILE when a hook binds one.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, git, hermetic_env

sys.path.insert(0, str(REPO_ROOT / "lib"))
import run_path

SH = REPO_ROOT / "lib" / "run_path.sh"
VECTOR = "668405c985a3f02f"  # sha256("/a/repo")[:16], pinned in crates/goh/src/run_path.rs too


def _sh(script: str, cwd: Path, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", "-c", f'. "{SH}"; {script}'], cwd=cwd, capture_output=True,
                          text=True, env=hermetic_env(drop_git=True, **env), timeout=60,
                          stdin=subprocess.DEVNULL, check=False)  # fmt: skip


def test_the_shell_and_python_faces_name_one_directory(tmp_path: Path, monkeypatch) -> None:
    tree = tmp_path / "repo"
    tree.mkdir()
    run = tmp_path / "run"
    monkeypatch.setenv("GOH_RUN_DIR", str(run))
    done = _sh(f'goh_run_dir "{tree}"', tmp_path, GOH_RUN_DIR=str(run))
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == str(run_path.run_dir(tree))
    assert Path(done.stdout.strip()).parent == run


def test_the_key_is_the_pinned_vector() -> None:
    import hashlib

    assert hashlib.sha256(b"/a/repo").hexdigest()[:16] == VECTOR
    done = subprocess.run(["bash", "-c", f'. "{SH}"; printf %s /a/repo | _goh_run_sha256'],
                          capture_output=True, text=True, env=hermetic_env(), timeout=60,
                          stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert done.stdout.strip()[:16] == VECTOR, done.stderr


def test_the_root_defaults_under_state_and_a_relative_one_is_refused(tmp_path, monkeypatch):
    monkeypatch.delenv("GOH_RUN_DIR", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert run_path.run_root() == tmp_path / "state" / "goh" / "run"
    monkeypatch.setenv("GOH_RUN_DIR", "rel/dir")
    with pytest.raises(ValueError):
        run_path.run_root()
    done = _sh("goh_run_root", tmp_path, GOH_RUN_DIR="rel/dir")
    assert done.returncode == 2 and "absolute" in done.stderr


def test_the_index_tree_answers_while_index_lock_is_held(repo: Path, tmp_path: Path) -> None:
    run = tmp_path / "run"
    (repo / "staged.txt").write_text("staged\n")
    git(repo, "add", "staged.txt")
    want = git(repo, "write-tree").strip()
    lock = Path(git(repo, "rev-parse", "--git-dir").strip())
    lock = (lock if lock.is_absolute() else repo / lock) / "index.lock"
    lock.write_text("")  # another git holds the index
    try:
        assert (
            subprocess.run(
                ["git", "write-tree"],
                cwd=repo,
                capture_output=True,
                env=hermetic_env(drop_git=True),
                stdin=subprocess.DEVNULL,
                check=False,
            ).returncode
            != 0
        )  # calibration: the bare one is refused
        done = _sh("goh_index_tree", repo, GOH_RUN_DIR=str(run))
        assert done.returncode == 0, done.stderr
        assert done.stdout.strip() == want
        env = {**hermetic_env(drop_git=True), "GOH_RUN_DIR": str(run)}
        assert run_path.index_tree(repo, env) == want
    finally:
        lock.unlink()
    assert list((run / "index").iterdir()) == [], "the private copies are removed"


def test_the_index_tree_reads_a_bound_hook_index(repo: Path, tmp_path: Path) -> None:
    other = tmp_path / "other-index"
    env = {**hermetic_env(drop_git=True), "GIT_INDEX_FILE": str(other)}
    (repo / "only-in-other.txt").write_text("x\n")
    subprocess.run(["git", "add", "only-in-other.txt"], cwd=repo, env=env, check=True,
                   stdin=subprocess.DEVNULL)  # fmt: skip
    want = subprocess.run(
        ["git", "write-tree"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
    ).stdout.strip()
    assert want != git(repo, "write-tree").strip()  # calibration: the two indexes differ
    run = tmp_path / "run"
    done = _sh("goh_index_tree", repo, GOH_RUN_DIR=str(run), GIT_INDEX_FILE=str(other))
    assert done.stdout.strip() == want, done.stderr
    assert run_path.index_tree(repo, {**env, "GOH_RUN_DIR": str(run)}) == want
