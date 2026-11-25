"""A test's own commits are on the disk whenever something can read them (GOH.FSEVQUIET,
tests/_quiet_pack.py), as one pack, and never where nothing does."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import _pytest.tmpdir
import _quiet_pack
import pytest
from _tree_clone import clone_tree
from conftest import commit_all, git, write


def _packs(repo: Path) -> list[str]:
    """Asked of a child's own eyes nowhere: `os.stat` alone is no ask (no audit event)."""
    with _quiet_pack.quietly():
        at = repo / ".git" / "objects" / "pack"
        return sorted(os.listdir(at)) if at.exists() else []


def _three(repo: Path) -> list[str]:
    before = _packs(repo)
    for n in range(3):
        write(repo, f"f{n}.txt", f"{n}\n")
        commit_all(repo, f"c{n}")
    assert _packs(repo) == before, "written before anything asked"
    return before


def test_three_commits_are_one_pack_git_finds_whole(repo: Path) -> None:
    before = _three(repo)
    log = git(repo, "log", "--format=%s").split()
    assert log == ["c2", "c1", "c0", "fixture"]
    assert len(_packs(repo)) == len(before) + 2
    git(repo, "fsck", "--strict", "--full")
    assert git(repo, "status", "--porcelain") == ""


def _fork(repo: Path) -> None:
    if not (child := os.fork()):
        os._exit(0)
    os.waitpid(child, 0)


ASKS = {
    "a process": lambda repo: subprocess.run(["true"], check=True, stdin=subprocess.DEVNULL),
    "a shell": lambda repo: sys.audit("os.system", b"true"),
    "a fork": _fork,
    "a look": lambda repo: os.listdir(repo / ".git"),
    "a walk": lambda repo: list(repo.rglob("*.idx")),
    "a read": lambda repo: (repo / ".git" / "HEAD").read_text(),
    "a copy": lambda repo: shutil.copytree(repo, repo.parent / "copy", symlinks=True),
    "a clone": lambda repo: clone_tree(repo, repo.parent / "copy"),
    "a move": lambda repo: repo.rename(repo.parent / "copy"),
}


@pytest.mark.parametrize("ask", ASKS.values(), ids=list(ASKS))
def test_whatever_can_read_an_object_finds_it(repo: Path, ask) -> None:
    before = _three(repo)
    ask(repo)
    at = repo.parent / "copy" if (repo.parent / "copy").exists() else repo
    assert len(_packs(at)) == len(before) + 2
    git(at, "fsck", "--strict", "--full")


def test_a_repository_its_test_only_removes_writes_none(
    tmp_path: Path, _repo_template: Path, monkeypatch
) -> None:
    (tmp_path / "t").mkdir()
    clone_tree(_repo_template, tmp_path / "t" / "r")
    _three(tmp_path / "t" / "r")
    wrote: list[str] = []
    monkeypatch.setattr(_quiet_pack, "_pack", lambda git, _objects: wrote.append(git))
    _pytest.tmpdir.rmtree(tmp_path / "t")
    assert wrote == [] and not _quiet_pack.PENDING
