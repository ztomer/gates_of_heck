"""lib/build_once.py: a run builds each thing once, and a second build is refused by name
(GOH.BUILDONCE). The wiring -- a check's eight pytest runs taking one goh test binary -- was read
on a real `goh land --check` (8 builds before, 1 after); these pin the owner and the refusal."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from _kv_fixture import Held

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
    assert sorted(k for k, _, _ in build_once.builds(tmp_path)) == [
        "goh-test-binary",
        "goh-test-binary",
        "template",
    ]
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


def _a_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tree: Path) -> Path:
    run = tmp_path / "run"
    run.mkdir()
    (run / "tree").write_text(os.path.realpath(tree), encoding="utf-8")
    monkeypatch.setenv(build_once.ENV, str(run))
    return run


def test_the_runs_tree_is_built_once_and_every_later_process_loads_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, made = _a_run(tmp_path, monkeypatch, tmp_path), []

    def build() -> dict:
        made.append(1)
        return {"texts": {"a.py": "x"}}

    first = build_once.shared("dependency map", tmp_path, build, "first")
    assert build_once.shared("dependency map", tmp_path, build, "again") is first  # this process
    monkeypatch.setattr(build_once, "_SHARED", {})  # another process of the run: the run's pickle
    assert build_once.shared("dependency map", tmp_path, build, "later") == first
    assert made == [1]
    assert build_once.shared("dependency map", tmp_path / "other", build, "fixture") is None
    assert [(k, c.split(" (pid")[0]) for k, c, _ in build_once.builds(run)] == [
        ("dependency-map", "first")
    ]


def test_a_second_build_of_the_runs_map_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The planted second build: the run's kept map gone, a later process must not rebuild it."""
    run = _a_run(tmp_path, monkeypatch, tmp_path)
    build_once.shared("dependency map", tmp_path, dict, "first")
    for kept in (run / "dependency-map").rglob("built.pickle.z"):
        kept.unlink()
    monkeypatch.setattr(build_once, "_SHARED", {})
    with pytest.raises(build_once.BuiltTwice, match=r"first \(pid \d+\) built it; later asked"):
        build_once.shared("dependency map", tmp_path, dict, "later")


def test_the_owner_writes_each_build_as_a_run_builds_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    held = Held().lay(monkeypatch)
    with build_once.building(tmp_path, "template", "repo", "test_x"):
        pass
    assert build_once.record(tmp_path) == 1
    rows = list(held.goh.execute("SELECT kind, caller, seconds >= 0 FROM run_builds"))
    assert [(k, c.split(" (pid")[0], ok) for k, c, ok in rows] == [("template", "test_x", 1)]


def test_a_second_build_of_one_template_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The planted second build of a template: its published tree gone, the next asker refused."""
    import shutil

    from _tree_clone import _run_template

    monkeypatch.delenv(build_once.ENV, raising=False)
    _run_template(tmp_path, "k", lambda p: p.mkdir(parents=True), keep_quiet=False)
    shutil.rmtree(tmp_path / "k")
    with pytest.raises(build_once.BuiltTwice, match=r"template \(.*/k\)"):
        _run_template(tmp_path, "k", lambda p: p.mkdir(parents=True), keep_quiet=False)


def test_a_build_that_failed_leaves_no_claim(tmp_path: Path) -> None:
    with pytest.raises(ValueError), build_once.building(tmp_path, "template", "k", "first"):
        raise ValueError("the build failed")
    with build_once.building(tmp_path, "template", "k", "again"):  # a retry is the one build
        pass
    assert [c.split(" (pid")[0] for _, c, _ in build_once.builds(tmp_path)] == ["again"]
