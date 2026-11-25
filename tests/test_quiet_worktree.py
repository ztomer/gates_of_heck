"""tests/_quiet_worktree.py makes the work tree git makes: each case is run by both."""

from __future__ import annotations

from pathlib import Path

import _quiet_git
import pytest
from test_quiet_git import _env, _git, _served, _state, made  # noqa: F401 (its fixture)

WT = ("-q", "-b", "lane", "../wt")


def _tree(d: Path) -> dict[str, tuple[int, bytes]]:
    return {
        str(p.relative_to(d)): (
            (0o120000, str(p.readlink()).encode()) if p.is_symlink()
            else (p.stat().st_mode & 0o111, p.read_bytes())
        )
        for p in d.rglob("*") if (p.is_symlink() or p.is_file()) and p.name != ".git"
    }  # fmt: skip


def _repo(made, name: str) -> Path:  # noqa: F811
    d = made(name)
    (d / "sub").mkdir()
    (d / "a.txt").write_text("one\n")
    (d / "sub" / "b").write_bytes(b"\0\xff")
    (d / "run.sh").write_text("#!/bin/sh\n")
    (d / "run.sh").chmod(0o755)
    (d / "link").symlink_to("a.txt")
    for args in (("add", "-A"), ("commit", "-qm", "first")):
        _git(d, *args, env=_env(False))
    return d


@pytest.mark.parametrize("args", [
    ("-b", "lane", "{wt}"), ("-b", "lane", "{wt}", "HEAD"), ("--detach", "{wt}"),
    ("--detach", "{wt}", "main"),
])  # fmt: skip
def test_the_work_tree_is_the_real_one(made, monkeypatch, args) -> None:  # noqa: F811
    """The files, the index, HEAD, the branch and what git lists."""
    served, real = [], _quiet_git.serve
    mine, gits = _repo(made, "mine"), _repo(made, "gits")
    monkeypatch.setattr(_quiet_git, "serve", lambda *a: served.append(real(*a)) or served[-1])
    states = []
    for d in (mine, gits):
        wt = d / f"../{d.name}-wt"
        served.clear()
        _git(d, "worktree", "add", "-q", *(a.format(wt=wt) for a in args), env=_env(d == gits))
        assert served[-1] == (d == mine)
        wt = wt.resolve()
        assert _git(wt, "diff-files") == "", "a stat word that is not the file's"
        listed = _git(d, "worktree", "list", "--porcelain").replace(d.name, "R")
        states.append((_state(wt, "HEAD"), _tree(wt), listed.replace(str(d.resolve()), "R"),
                       _git(d, "for-each-ref"), _git(wt, "diff", "--cached"),
                       (wt / ".git").read_text().replace(d.name, "R")))  # fmt: skip
    assert states[0] == states[1]


def test_git_goes_on_from_it(made) -> None:  # noqa: F811
    mine = _repo(made, "r")
    assert _served(["git", "worktree", "add", *WT], mine)
    wt = mine.parent / "wt"
    (wt / "a.txt").write_text("git goes on\n")
    _git(wt, "commit", "-qam", "in the lane")
    assert _git(mine, "status", "--porcelain") == ""
    _git(mine, "fsck", "--strict", "--full")
    _git(mine, "worktree", "remove", "--force", str(wt))
    assert not wt.exists()
    assert str(wt) not in _git(mine, "worktree", "list")


def _edit(d: Path) -> None:
    (d / "a.txt").write_text("not HEAD's\n")


def _mode(d: Path) -> None:
    (d / "run.sh").chmod(0o644)


def _gone(d: Path) -> None:
    (d / "a.txt").unlink()


def _staged(d: Path) -> None:
    (d / "a.txt").write_text("staged\n")
    _git(d, "add", "-A", env=_env(False))


def _attributes(d: Path) -> None:
    (d / "sub" / ".gitattributes").write_text("* text\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "attributes")


def _hook(d: Path) -> None:
    (d / ".git" / "hooks").mkdir(exist_ok=True)
    (d / ".git" / "hooks" / "post-checkout").write_text("#!/bin/sh\n")


def _taken(d: Path) -> None:
    (d.parent / "wt").mkdir()


def _named(d: Path) -> None:
    (d / ".git" / "worktrees" / "wt").mkdir(parents=True)


def _branch(d: Path) -> None:
    _git(d, "branch", "lane")


def _packed(d: Path) -> None:
    _git(d, "branch", "lane/in")
    _git(d, "pack-refs", "--all", "--prune")


def _second(d: Path) -> None:
    _git(d, "commit", "-q", "--allow-empty", "-m", "second", env=_env(False))


@pytest.mark.parametrize(("plant", "args"), [
    *((p, WT) for p in (_edit, _mode, _gone, _staged, _attributes, _hook, _taken, _named, _branch,
                        _packed)),
    (_second, (*WT, "HEAD~1")), (None, WT[1:]), (None, ("-q", "../wt")), (None, (*WT, "--lock")),
    (None, ("-q", "-B", "lane", "../wt")), (None, ("-q", "-b", "a..b", "../wt")),
    (None, ("-q", "-b", "x.lock", "../wt")), (None, ("-q", "-b", "lane", "../no/wt")),
    (None, ("-q", "-b", "lane", ".git/wt")), (None, ("-q", "-b", "lane", "../../wt")),
])  # fmt: skip
def test_what_git_would_judge_is_gits(made, plant, args) -> None:  # noqa: F811
    d = _repo(made, "r")
    if plant is not None:
        plant(d)
    before = _tree(d / ".git"), sorted(d.parent.iterdir())
    assert not _served(["git", "worktree", "add", *args], d)
    assert (_tree(d / ".git"), sorted(d.parent.iterdir())) == before, "declined, and written"


def test_a_lane_s_own_lane_is_gits(made) -> None:  # noqa: F811
    d = _repo(made, "r")
    assert _served(["git", "worktree", "add", *WT], d)
    assert not _served(["git", "worktree", "add", "-q", "--detach", "../wt2"], d.parent / "wt")
