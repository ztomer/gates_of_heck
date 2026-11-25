"""What a test gets from the pool is what a fresh clone of the template is, whatever the test before
did to it (GOH.FSEVQUIET, tests/_tree_pool.py)."""

from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path

import _pytest.tmpdir
import _tree_pool
import pytest
from _fast_git import fast_init
from _tree_clone import _clone_part, _is_sealed, _unseal_clone, clone_tree, process_template
from conftest import commit_all, git, write

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="clonefile(2) is macOS's")


def _build(tree: Path) -> None:
    fast_init(tree, "main")
    for n in range(16):
        write(tree, f"d{n % 4}/f{n}.txt", f"file {n:02}\n")
    for n in range(2):
        write(tree, f"d{n}/same.txt", "x" * n)
    (tree / "link").symlink_to("d0/f0.txt")
    (tree / "d1" / "run.sh").write_text("#!/bin/sh\n")
    (tree / "d1" / "run.sh").chmod(0o755)
    commit_all(tree)


@pytest.fixture
def template(tmp_path: Path, monkeypatch) -> Path:
    """The run's one, its pool this test's own: no other worker's test takes what this one put."""
    monkeypatch.setattr(_tree_pool, "_home", lambda src: str(tmp_path / "pool" / Path(src).name))
    return process_template("pooled", _build)


def _state(top: Path) -> dict[str, tuple]:
    out: dict[str, tuple] = {".": (stat.S_IMODE(top.lstat().st_mode),)}
    for path in sorted(top.rglob("*")):
        st = path.lstat()
        what: object = None
        if path.is_symlink():
            what = os.readlink(path)
        elif path.is_file():
            what = (path.read_bytes(), st.st_mtime_ns, st.st_nlink)
        out[str(path.relative_to(top))] = (st.st_mode, what)
    return out


def _fresh(template: Path, at: Path) -> dict[str, tuple]:
    _clone_part(str(template), str(at))  # never the pool's
    _unseal_clone(template, at)
    return _state(at)


def _forged(t: Path) -> None:
    f = t / "d0" / "f0.txt"
    st = f.stat()
    f.write_text("FILE 00\n")
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))


def _swapped(t: Path) -> None:
    (t / "d0" / "f0.txt").rename(t / "x")
    (t / "d0" / "f4.txt").rename(t / "d0" / "f0.txt")
    (t / "x").rename(t / "d0" / "f4.txt")


def _dirs_swapped(t: Path) -> None:
    """Every file's ctime is old: only what it is says it is another."""
    (t / "d1").rename(t / "x")
    (t / "d0").rename(t / "d1")
    (t / "x").rename(t / "d0")


def _linked(t: Path) -> None:
    os.link(t / "d0" / "f0.txt", t.parent / "outside")
    (t.parent / "outside").write_text("through the other name\n")


def _committed(t: Path) -> None:
    write(t, "new.txt", "x\n")
    commit_all(t)
    git(t, "config", "goh.mark", "first")
    git(t, "branch", "another")


def _dir_for_file(t: Path) -> None:
    (t / "d2" / "f2.txt").unlink()
    (t / "d2" / "f2.txt").mkdir()
    (t / "d2" / "f2.txt" / "in").write_text("x")


def _file_for_dir(t: Path) -> None:
    shutil.rmtree(t / "d3")
    (t / "d3").write_text("x")


def _link_for_file(t: Path) -> None:
    (t / "d1" / "f1.txt").unlink()
    (t / "d1" / "f1.txt").symlink_to("/etc/hosts")


CHANGES = {
    "nothing": lambda t: None,
    "forged-mtime": _forged,
    "swapped": _swapped,
    "dirs-swapped": _dirs_swapped,
    "linked": _linked,
    "committed": _committed,
    "dir-for-file": _dir_for_file,
    "file-for-dir": _file_for_dir,
    "link-for-file": _link_for_file,
    "file-mode": lambda t: (t / "d1" / "run.sh").chmod(0o644),
    "dir-mode": lambda t: (t / "d1").chmod(0o700),
    "top-mode": lambda t: t.chmod(0o700),
    "added": lambda t: (t / "d0" / "extra").write_text("x"),
    "added-dir": lambda t: (t / "d0" / "more" / "deep").mkdir(parents=True),
    "removed": lambda t: (t / "d0" / "f0.txt").unlink(),
    "link-moved": lambda t: ((t / "link").unlink(), (t / "link").symlink_to("d0/f4.txt")),
    "git-status": lambda t: git(t, "status"),
}


@pytest.mark.parametrize("change", CHANGES.values(), ids=list(CHANGES))
def test_the_next_test_gets_what_a_fresh_clone_is(tmp_path: Path, template: Path, change) -> None:
    fresh = _fresh(template, tmp_path / "fresh")
    first = tmp_path / "first"
    first.mkdir()
    clone_tree(template, first / "t")
    inode = (first / "t").stat().st_ino
    assert _state(first / "t") == fresh
    change(first / "t")
    _pytest.tmpdir.rmtree(first, ignore_errors=True)  # pytest's own, of a passed test's tmp_path
    assert not first.exists()
    clone_tree(template, tmp_path / "second")
    assert (tmp_path / "second").stat().st_ino == inode, "not the pool's: this proves nothing"
    assert _state(tmp_path / "second") == fresh
    assert git(tmp_path / "second", "status", "--porcelain") == ""


def test_what_changes_in_the_pool_is_no_test_s(tmp_path: Path, template: Path) -> None:
    """A process that outlived its test writes in the tree it knew, renamed under it."""
    fresh = _fresh(template, tmp_path / "fresh")
    (tmp_path / "a").mkdir()
    clone_tree(template, tmp_path / "a" / "t")
    with (tmp_path / "a" / "t" / "d0" / "f0.txt").open("a") as late:
        _pytest.tmpdir.rmtree(tmp_path / "a")
        late.write("later\n")
    clone_tree(template, tmp_path / "b")
    assert _state(tmp_path / "b") == fresh


def test_two_tests_at_once_never_hold_one_tree(tmp_path: Path, template: Path) -> None:
    (tmp_path / "a").mkdir()
    clone_tree(template, tmp_path / "a" / "t")
    _pytest.tmpdir.rmtree(tmp_path / "a")
    clone_tree(template, tmp_path / "b")
    clone_tree(template, tmp_path / "c")
    write(tmp_path / "b", "d0/f0.txt", "b's\n")
    assert (tmp_path / "c" / "d0" / "f0.txt").read_text() == "file 00\n"
    assert (template / "d0" / "f0.txt").read_text() == "file 00\n"


def test_an_untouched_part_of_a_clone_is_cloned_and_pooled_as_the_templates(
    tmp_path: Path, template: Path
) -> None:
    (tmp_path / "a").mkdir()
    clone_tree(template, tmp_path / "a" / "t")
    write(tmp_path / "a" / "t", "d1/f1.txt", "the test's own\n")
    (tmp_path / "a" / "t" / "d2").chmod(0o700)
    for part in ("d0", "d1", "d2"):
        clone_tree(tmp_path / "a" / "t" / part, tmp_path / "a" / part)
    assert (tmp_path / "a" / "d1" / "f1.txt").read_text() == "the test's own\n"
    inode = (tmp_path / "a" / "d0").stat().st_ino
    _pytest.tmpdir.rmtree(tmp_path / "a")
    clone_tree(template, tmp_path / "t")
    clone_tree(tmp_path / "t" / "d0", tmp_path / "d0")
    assert (tmp_path / "d0").stat().st_ino == inode
    assert _state(tmp_path / "d0") == _state(template / "d0")
    clone_tree(tmp_path / "t" / "d1", tmp_path / "d1")
    assert _state(tmp_path / "d1") == _state(template / "d1")


def _emptied(t: Path) -> None:
    for n in range(4):
        shutil.rmtree(t / f"d{n}")


def _gone(t: Path) -> None:
    shutil.rmtree(t)


def _another(t: Path) -> None:
    shutil.copytree(t, t.parent / "copy", symlinks=True)
    shutil.rmtree(t)
    (t.parent / "copy").rename(t)


@pytest.mark.parametrize("change", [_emptied, _gone, _another])
def test_a_tree_that_is_not_worth_it_is_removed(tmp_path: Path, template: Path, change) -> None:
    (tmp_path / "a").mkdir()
    clone_tree(template, tmp_path / "a" / "t")
    change(tmp_path / "a" / "t")
    assert _tree_pool.reclaim(_tree_pool.real_of(tmp_path / "a"), _clone_part, _is_sealed) == 0


def _build_db(tree: Path) -> None:
    _build(tree)
    (tree / "d0" / "goh.db").write_text("rows\n")


def test_a_clone_moved_over_another_is_not_that_one(tmp_path: Path, template: Path) -> None:
    """What was written in it is older than the clone it stands for."""
    (tmp_path / "a").mkdir()
    clone_tree(template, tmp_path / "a" / "t")
    _forged(tmp_path / "a" / "t")
    clone_tree(template, tmp_path / "a" / "u")
    shutil.rmtree(tmp_path / "a" / "u")
    (tmp_path / "a" / "t").rename(tmp_path / "a" / "u")
    assert _tree_pool.reclaim(_tree_pool.real_of(tmp_path / "a"), _clone_part, _is_sealed) == 0


def test_a_tree_with_a_database_and_one_of_a_test_s_own_are_never_pooled(
    tmp_path: Path, template: Path
) -> None:
    own = tmp_path / "own"
    own.mkdir()
    _clone_part(str(template), str(own / "t"))
    _unseal_clone(template, own / "t")
    for n, src in enumerate((process_template("pooled-db", _build_db), own / "t")):
        at = tmp_path / f"a{n}"
        at.mkdir()
        clone_tree(src, at / "t")
        assert _tree_pool.reclaim(_tree_pool.real_of(at), _clone_part, _is_sealed) == 0
