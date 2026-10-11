"""checks/_gitutil.is_work_tree -- one answer to "is this directory a repository" (BACKLOG 4.1).

The class: `os.path.isdir(root/.git)` was the question in four places across two repos, and it
is False in every LINKED worktree, where `.git` is a file naming the real git dir -- so a gate
that asked it inspected nothing and passed. The answer is git's own (`rev-parse
--show-toplevel`), compared to the path with `realpath` on both sides, so a firmlinked or
symlinked spelling of the same directory is the same directory, and a subdirectory of a repo
is not the repo.
"""

import os

from _gitutil import is_work_tree
from conftest import git


def test_a_plain_repository_is_a_work_tree(repo):
    assert is_work_tree(repo)


def test_a_linked_worktree_is_a_work_tree_though_its_git_is_a_file(repo, tmp_path):
    linked = tmp_path / "linked"
    git(repo, "worktree", "add", "-q", "--detach", str(linked))
    assert (linked / ".git").is_file(), "the case this helper exists for: .git is a FILE"
    assert not os.path.isdir(linked / ".git"), "the old question answers False here"
    assert is_work_tree(linked)


def test_a_subdirectory_of_a_repository_is_not_one(repo):
    sub = repo / "sub"
    sub.mkdir()
    assert not is_work_tree(sub)


def test_a_plain_directory_and_a_missing_path_are_not_repositories(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert not is_work_tree(plain)
    assert not is_work_tree(tmp_path / "missing")


def test_a_symlinked_spelling_of_a_repository_is_the_same_repository(repo, tmp_path):
    alias = tmp_path / "alias"
    alias.symlink_to(repo)
    assert is_work_tree(alias)


def test_a_hooks_git_dir_does_not_make_every_directory_its_repository(repo, tmp_path, monkeypatch):
    """Under a hook GIT_DIR names the gated repo, and a bare `git rev-parse` anywhere answers for
    it: the helper asks about the directory it was given, never the hook's."""
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.setenv("GIT_DIR", str(repo / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(plain))
    assert not is_work_tree(plain)
    assert is_work_tree(repo)
