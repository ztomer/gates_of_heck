"""A test's own commits are on the disk whenever something can read them (GOH.FSEVQUIET,
tests/_quiet_pack.py), as one pack, and never where nothing does."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import weakref
from pathlib import Path

import _pytest.tmpdir
import _quiet_pack
import pytest
from _tree_clone import clone_tree
from conftest import commit_all, git, write


@pytest.fixture(autouse=True)
def _nothing_runs(monkeypatch) -> None:
    """No child an earlier test of this worker started and never waited for."""
    monkeypatch.setattr(_quiet_pack, "_CHILDREN", weakref.WeakSet())


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


def _commit(repo: Path) -> None:
    write(repo, "late.txt", "x\n")
    commit_all(repo, "late")


@pytest.mark.load_sensitive
def test_a_process_that_runs_already_reads_the_commit(repo: Path) -> None:
    """A ship waiting on the land lock, while lands arrive."""
    reads = (
        "import subprocess, sys; sys.stdin.readline(); subprocess.run(['git', 'fsck', '--strict',"
        " '--full'], check=True, stdin=subprocess.DEVNULL)"
    )
    with subprocess.Popen([sys.executable, "-c", reads], cwd=repo, stdin=subprocess.PIPE) as child:
        before = _packs(repo)
        _commit(repo)
        after = _packs(repo)
        child.communicate(b"go\n")
    assert len(after) > len(before), "kept back from a process that runs"
    assert child.returncode == 0


def test_a_process_that_may_have_left_another_behind_is_not_waited_for(repo: Path) -> None:
    subprocess.run(["true"], check=True, stdin=subprocess.DEVNULL)
    before = _packs(repo)
    _commit(repo)
    assert len(_packs(repo)) > len(before)


@pytest.mark.load_sensitive
def test_a_child_of_an_earlier_test_is_one_that_runs(repo: Path, monkeypatch) -> None:
    with subprocess.Popen([sys.executable, "-c", "input()"], stdin=subprocess.PIPE) as child:
        monkeypatch.setattr(_quiet_pack, "_WATCHING", ["another test's"])
        before = _packs(repo)
        _commit(repo)
        after = _packs(repo)
        child.communicate(b"\n")
    assert len(after) > len(before)


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
