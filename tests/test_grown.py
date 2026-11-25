"""A grown fixture is the one its kit builds, built once, and each test's own (GOH.FSEVQUIET)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from _grown import grow
from conftest import commit_all, git, write


def _build(calls: list[Path]):
    def build(repo: Path) -> None:
        calls.append(repo)
        for n in range(6):
            write(repo, f"src/f{n}.txt", f"{n}\n")
        commit_all(repo, "grown")

    return build


def _state(repo: Path) -> tuple:
    files = sorted(str(p.relative_to(repo)) for p in (repo / "src").iterdir())
    return files, git(repo, "ls-tree", "-r", "HEAD"), git(repo, "status", "--porcelain"), git(
        repo, "log", "--format=%s"
    )  # fmt: skip


def _key(tmp_path: Path) -> str:
    return f"grown-{tmp_path.parent.name}-{tmp_path.name}"


def test_it_is_built_once_and_each_is_a_tests_own(tmp_path: Path, _repo_template: Path) -> None:
    from _tree_clone import clone_tree

    calls: list[Path] = []
    trees = [tmp_path / name for name in ("a", "b", "plain")]
    for tree in trees:
        clone_tree(_repo_template, tree)
    _build([])(trees[2])
    for tree in trees[:2]:
        assert grow(tree, _key(tmp_path), _build(calls)) == tree
    assert len(calls) == 1 and calls[0] not in trees
    assert _state(trees[0]) == _state(trees[1]) == _state(trees[2])
    assert len(_state(trees[0])[0]) == 6
    write(trees[0], "src/f0.txt", "a's\n")
    commit_all(trees[0], "a's")
    assert _state(trees[1]) == _state(trees[2])


@pytest.mark.parametrize("touch", ["a file", "a mode", "the index"])
def test_a_repository_a_test_touched_is_built_where_it_is(
    repo: Path, tmp_path: Path, touch
) -> None:
    if touch == "a file":
        write(repo, "mine.txt", "x\n")
    elif touch == "a mode":
        os.chmod(repo / "README.md", 0o755)
    else:
        git(repo, "update-index", "--force-remove", "README.md")
    calls: list[Path] = []
    grow(repo, _key(tmp_path), _build(calls))
    assert calls == [repo]
    if touch == "a file":
        assert (repo / "mine.txt").read_text() == "x\n"
