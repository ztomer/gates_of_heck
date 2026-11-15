"""The one door goh writes into another estate repo through (lib/estate_commit.py,
GOH.CLASS.ROLLOUT): it commits exactly what the writer wrote under the paths it owns, or leaves the
tree as it was. A writer that touches anything else is put back, byte for byte, and named -- a goh
defect reported, never a dirty consumer (14 repos, 2026-11-14)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from _fast_git import fast_init
from conftest import REPO_ROOT, hermetic_env

sys.path.insert(0, str(REPO_ROOT / "lib"))
import estate_commit

sys.path.pop(0)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True, env=hermetic_env(drop_git=True),
                          stdin=subprocess.DEVNULL).stdout  # fmt: skip


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "r"
    (repo / "own").mkdir(parents=True)
    (repo / "own" / "a").write_text("a\n")
    (repo / "gate.sh").write_text("theirs\n")
    fast_init(repo)
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-verify", "-m", "fixture")
    return repo


def _status(repo: Path) -> str:
    return _git(repo, "status", "--porcelain", "--untracked-files=all")


def test_a_write_inside_its_paths_is_committed_alone(tmp_path):
    repo = _repo(tmp_path)
    (repo / "wip").write_text("someone else's\n")
    before = _status(repo)

    def write() -> None:
        (repo / "own" / "a").write_text("b\n")
        (repo / "own" / "new").write_text("n\n")

    done, said = estate_commit.commit_paths(repo, ["own"], "bump", write)
    assert done and said.startswith("committed in "), said
    assert sorted(_git(repo, "show", "--name-only", "--format=", "HEAD").split()) == [
        "own/a",
        "own/new",
    ]
    assert _status(repo) == before


def test_a_write_outside_its_paths_is_put_back_and_named(tmp_path):
    repo = _repo(tmp_path)
    (repo / "gate.sh").write_text("theirs, uncommitted\n")
    before = _status(repo)
    head = _git(repo, "rev-parse", "HEAD")

    def write() -> None:
        (repo / "own" / "a").write_text("b\n")
        with (repo / "gate.sh").open("a") as f:
            f.write("goh_dir_repair\n")  # what install.sh did to a consumer's gate.sh
        (repo / "stray").write_text("x\n")

    done, said = estate_commit.commit_paths(repo, ["own"], "bump", write)
    assert not done, said
    assert "gate.sh" in said and "stray" in said and "outside own" in said, said
    assert "left as it was" in said, said
    assert _git(repo, "rev-parse", "HEAD") == head
    assert _status(repo) == before
    assert (repo / "gate.sh").read_text() == "theirs, uncommitted\n"
    assert (repo / "own" / "a").read_text() == "a\n"


def test_a_refused_commit_leaves_the_tree_as_it_was(tmp_path):
    repo = _repo(tmp_path)
    hooks = tmp_path / "hooks"  # outside the tree, so the snapshot sees only the write
    hooks.mkdir()
    (hooks / "pre-commit").write_text("#!/bin/sh\necho refused >&2\nexit 1\n")
    (hooks / "pre-commit").chmod(0o755)
    _git(repo, "config", "core.hooksPath", str(hooks))
    before = _status(repo)
    done, said = estate_commit.commit_paths(
        repo, ["own"], "bump", lambda: (repo / "own" / "a").write_text("b\n") and None
    )
    assert not done and "refused" in said and "left as it was" in said, said
    assert _status(repo) == before
