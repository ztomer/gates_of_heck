"""A copy of a fixture directory is shutil's, and the test's own (tests/_fixture_copies.py)."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest
from _fixture_copies import FIXTURES

SRC = Path(FIXTURES) / "screen_presentation"
TREE_READS = ("tests/fixtures/screen_presentation/",)


def _state(top: Path) -> dict[str, tuple]:
    return {
        str(p.relative_to(top)): (p.lstat().st_mode, p.is_dir() or p.read_bytes())
        for p in sorted(top.rglob("*"))
    }


def test_a_copy_is_the_directorys_and_each_tests_own(tmp_path: Path) -> None:
    shutil.copytree(SRC, tmp_path / "in" / "a")  # its parents are made
    shutil.copytree(SRC, tmp_path / "b")
    want = _state(SRC)
    assert want and _state(tmp_path / "in" / "a") == _state(tmp_path / "b") == want
    first = min(want)
    (tmp_path / "b" / first).write_text("the test's")
    shutil.copytree(SRC, tmp_path / "c")
    assert _state(tmp_path / "c") == want == _state(SRC)


def test_what_is_there_an_option_or_another_tree_is_shutils(tmp_path: Path) -> None:
    (tmp_path / "there").mkdir()
    with pytest.raises(FileExistsError):
        shutil.copytree(SRC, tmp_path / "there")
    shutil.copytree(SRC, tmp_path / "there", dirs_exist_ok=True)
    assert _state(tmp_path / "there") == _state(SRC)
    drop = min(_state(SRC))
    shutil.copytree(SRC, tmp_path / "less", ignore=shutil.ignore_patterns(drop))
    assert drop not in _state(tmp_path / "less")
    (tmp_path / "own").mkdir()
    (tmp_path / "own" / "f").write_text("1")
    shutil.copytree(tmp_path / "own", tmp_path / "x")
    (tmp_path / "own" / "f").write_text("2")
    shutil.copytree(tmp_path / "own", tmp_path / "y")
    assert (tmp_path / "y" / "f").read_text() == "2"


def test_a_clones_files_are_read_as_far_as_the_audit_sees(tmp_path: Path, monkeypatch) -> None:
    shutil.copytree(SRC, tmp_path / "a")
    seen: list[str] = []
    monkeypatch.setattr(sys, "audit", lambda name, *a: seen.append(str(a[0])) if a else None)
    shutil.copytree(SRC, tmp_path / "b")
    assert {os.path.join(SRC, rel) for rel in _state(SRC)} <= set(seen)
