"""shutil's copies in this suite are clones, and the same files (tests/_clone_copies.py)."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import _clone_copies
import pytest
from _fs_bytes import written

darwin = pytest.mark.skipif(sys.platform != "darwin", reason="clonefile(2) is macOS's")


def _src(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    (src / "d").mkdir(parents=True)
    (src / "run").write_bytes(b"#!/bin/sh\n" + b"x" * 5000)
    (src / "run").chmod(0o755)
    os.utime(src / "run", (1, 2))
    (src / "d" / "f").write_text("y" * 5000)
    (src / "empty").touch()
    (src / "link").symlink_to("run")
    return src


@darwin
def test_every_copy_shutil_makes_here_writes_no_bytes(tmp_path: Path) -> None:
    src, mine = _src(tmp_path), tmp_path / "mine"
    mine.mkdir()
    shutil.copytree(src, mine / "tree", symlinks=True)
    shutil.copy2(src / "run", mine / "a")
    shutil.copy(src / "run", mine / "b")
    shutil.copyfile(src / "run", mine / "c")
    shutil.copyfile(src / "link", mine / "d")
    total, cloned = written(mine)
    assert total == 0 and cloned == 5 * 5010 + 5000, (total, cloned)


def test_a_cloned_copy_is_what_shutils_own_copy_is(tmp_path: Path) -> None:
    src = _src(tmp_path)

    def made(name: str, copyfile) -> list:
        was, shutil.copyfile = shutil.copyfile, copyfile
        try:
            d = tmp_path / name
            shutil.copytree(src, d / "tree", symlinks=True)
            shutil.copy2(src / "run", d / "a")
            shutil.copy(src / "run", d / "b")
            shutil.copyfile(src / "run", d / "c")
            shutil.copyfile(src / "link", d / "d")
            shutil.copyfile(src / "link", d / "e", follow_symlinks=False)
        finally:
            shutil.copyfile = was
        return [
            (
                str(p.relative_to(d)),
                st.st_mode,
                os.readlink(p) if p.is_symlink() else p.is_dir() or p.read_bytes(),
                st.st_mtime if st.st_mtime < 1000 else 0,
            )
            for p in sorted(d.rglob("*"))
            for st in [p.lstat()]
        ]

    assert made("theirs", _clone_copies._COPYFILE) == made("ours", _clone_copies.copyfile)


def test_a_copy_over_a_file_or_of_none_is_shutils_own(tmp_path: Path) -> None:
    src, old = _src(tmp_path) / "run", tmp_path / "old"
    old.write_text("there before")
    ino = old.stat().st_ino
    shutil.copyfile(src, old)
    assert old.read_bytes() == src.read_bytes() and old.stat().st_ino == ino
    with pytest.raises(shutil.SameFileError):
        shutil.copyfile(src, src)
    with pytest.raises(FileNotFoundError):
        shutil.copyfile(tmp_path / "gone", tmp_path / "x")
    assert not (tmp_path / "x").exists()


def test_a_cloned_copy_is_audited_as_a_read(tmp_path: Path, monkeypatch) -> None:
    seen: list[tuple] = []
    monkeypatch.setattr(_clone_copies.sys, "audit", lambda *a: seen.append(a[:2]))
    shutil.copyfile(_src(tmp_path) / "run", tmp_path / "c")
    if _clone_copies._CLONEFILE is not None:
        assert ("open", tmp_path / "src" / "run") in seen, seen
