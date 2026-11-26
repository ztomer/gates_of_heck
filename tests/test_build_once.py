"""lib/build_once.py: a run builds each thing once, and a second build is refused by name
(GOH.BUILDONCE). The wiring -- a check's eight pytest runs taking one goh test binary -- was read
on a real `goh land --check` (8 builds before, 1 after); these pin the owner and the refusal."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lib import build_once


def test_a_second_build_in_one_run_is_refused_naming_both_callers(tmp_path: Path) -> None:
    build_once.claim(tmp_path, "goh test binary", "/tree", "native_goh for test_a")
    with pytest.raises(build_once.BuiltTwice) as refused:
        build_once.claim(tmp_path, "goh test binary", "/tree", "native_goh for test_b")
    said = str(refused.value)
    assert "native_goh for test_a" in said and "native_goh for test_b" in said, said
    assert "goh test binary (/tree)" in said, said


def test_another_tree_or_kind_is_its_own_build_and_each_is_counted(tmp_path: Path) -> None:
    build_once.claim(tmp_path, "goh test binary", "/tree", "a")
    build_once.claim(tmp_path, "goh test binary", "/other", "b")
    build_once.claim(tmp_path, "template", "/tree", "c")
    assert build_once.builds(tmp_path) == {"goh-test-binary": 2, "template": 1}
    assert build_once.home(tmp_path, "goh-bin", Path("/tree")) != build_once.home(
        tmp_path, "goh-bin", Path("/other")
    )


def test_the_owner_makes_and_removes_the_run_and_a_caller_inside_it_reuses_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(build_once.ENV, raising=False)
    with build_once.in_environ() as run:
        assert run.is_dir() and os.environ.get(build_once.ENV) == str(run)
        with build_once.owned() as inner:
            assert inner == run  # inside a run: the same one, not a second
        assert run.is_dir()  # the inner caller removed nothing
        assert build_once.run_dir(Path("/elsewhere")) == run
    assert not run.exists() and os.environ.get(build_once.ENV) is None
    assert build_once.run_dir(Path("/elsewhere")) == Path("/elsewhere")
