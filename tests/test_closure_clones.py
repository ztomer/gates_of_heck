"""tests/_closure_clones.py: the second copy of one closure is the first one's clone, and a test's
own: a write in it reaches neither the first nor the next."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import source_closure
from _fs_bytes import written
from conftest import REPO_ROOT

TREE_READS = ("gates/", "lib/", "tui/")
PATH = "gates/_proven.sh"


def test_a_copy_is_what_copy_closure_makes_and_no_other_tests(tmp_path: Path) -> None:
    files = source_closure.copy_closure(tmp_path / "a", PATH)
    want, _ = source_closure.closure([PATH], REPO_ROOT)
    assert files == sorted(want) and PATH in files
    (tmp_path / "b").mkdir()  # a directory that is there already is filled
    (tmp_path / "b" / "mine").write_text("x")
    source_closure.copy_closure(tmp_path / "b", PATH)
    (tmp_path / "a" / PATH).write_text("edited")
    for rel in files:
        src, got = REPO_ROOT / rel, tmp_path / "b" / rel
        assert got.read_bytes() == src.read_bytes() and got.stat().st_mode == src.stat().st_mode
    if sys.platform == "darwin":
        assert written(tmp_path)[0] < 100, "the copies were written, not cloned"


def test_another_tree_and_a_file_that_is_there_are_copied_as_before(tmp_path: Path) -> None:
    source_closure.copy_closure(tmp_path / "a", PATH)
    (tmp_path / "a" / PATH).write_text("#!/bin/bash\n# another tree's\n")
    source_closure.copy_closure(tmp_path / "b", PATH, root=tmp_path / "a")
    assert "another tree" in (tmp_path / "b" / PATH).read_text()
    source_closure.copy_closure(tmp_path / "a", PATH)
    assert (tmp_path / "a" / PATH).read_bytes() == (REPO_ROOT / PATH).read_bytes()


def test_a_clones_files_are_read_as_far_as_the_audit_sees(tmp_path: Path, monkeypatch) -> None:
    source_closure.copy_closure(tmp_path / "a", PATH)
    seen: list[str] = []
    monkeypatch.setattr(sys, "audit", lambda name, *a: seen.append(a[0]))
    source_closure.copy_closure(tmp_path / "b", PATH)
    assert str(REPO_ROOT / PATH) in seen


@pytest.mark.parametrize("extra", [(), ("README.md",)])
def test_one_call_is_never_anothers(tmp_path: Path, extra) -> None:
    files = source_closure.copy_closure(tmp_path / "a", PATH, extra=extra)
    assert ("README.md" in files) is bool(extra)
