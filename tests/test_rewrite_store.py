"""pytest's rewritten test files are kept between runs (tests/_rewrite_store.py, GOH.PYHARNESS).

A `--collect-only` of 809 files spent 8 of its 11.5 CPU seconds rewriting asserts, every run and in
every xdist worker, because the agent shells export PYTHONDONTWRITEBYTECODE=1.
"""

from __future__ import annotations

import types
from pathlib import Path

import _rewrite_store
import pytest
from _pytest.assertion import rewrite
from _pytest.assertion.rewrite import assertstate_key

SOURCE = "def test_x():\n    assert 1 == 1\n"


@pytest.fixture
def hook(pytestconfig):
    return pytestconfig.stash[assertstate_key].hook


@pytest.fixture
def rewrites(monkeypatch):
    """The files pytest rewrote, in order."""
    seen: list[str] = []
    real = rewrite._rewrite_test

    def counted(fn, config):
        seen.append(fn.name)
        return real(fn, config)

    monkeypatch.setattr(rewrite, "_rewrite_test", counted)
    return seen


def _file(tmp_path: Path, where: str, source: str = SOURCE) -> Path:
    fn = tmp_path / where / "test_one.py"
    fn.parent.mkdir(parents=True, exist_ok=True)
    fn.write_text(source)
    return fn


def test_this_session_reads_its_test_files_through_the_store():
    """The instrument: installed, and on this pytest -- a release that renames what it reads is red
    here, not a suite that is silently slow again."""
    assert _rewrite_store.install()
    assert rewrite.AssertionRewritingHook.exec_module is _rewrite_store._exec_module


def test_a_kept_rewrite_serves_the_next_run_and_every_other_worktree(tmp_path, hook, rewrites):
    store = tmp_path / "store"
    first = _file(tmp_path, "a")
    _rewrite_store.code(hook, first, store, True)
    again = _rewrite_store.code(hook, first, store, False)
    other = _file(tmp_path, "b")  # the same bytes in another worktree
    moved = _rewrite_store.code(hook, other, store, False)
    assert rewrites == ["test_one.py"], "only the first reading rewrote"
    assert again.co_filename == str(first) and moved.co_filename == str(other)
    inner = [c for c in moved.co_consts if isinstance(c, types.CodeType)]
    assert [c.co_filename for c in inner] == [str(other)], "a traceback names the file that ran"


def test_an_edit_is_never_served_the_old_rewrite(tmp_path, hook, rewrites):
    store = tmp_path / "store"
    fn = _file(tmp_path, "a")
    _rewrite_store.code(hook, fn, store, True)
    fn.write_text(SOURCE.replace("1 == 1", "1 == 2"))
    co = _rewrite_store.code(hook, fn, store, True)
    assert rewrites == ["test_one.py"] * 2
    ns: dict = {}
    exec(co, ns)  # noqa: S102 -- the code under test
    with pytest.raises(AssertionError, match="1 == 2"):
        ns["test_x"]()


def test_a_damaged_kept_file_is_a_rewrite_never_an_error(tmp_path, hook, rewrites):
    store = tmp_path / "store"
    fn = _file(tmp_path, "a")
    _rewrite_store.code(hook, fn, store, True)
    [kept] = store.iterdir()
    kept.write_bytes(kept.read_bytes()[:20])
    ns: dict = {}
    exec(_rewrite_store.code(hook, fn, store, False), ns)  # noqa: S102 -- the code under test
    ns["test_x"]()
    assert rewrites == ["test_one.py"] * 2


def test_a_store_that_cannot_be_written_is_no_error(tmp_path, hook, rewrites):
    blocked = tmp_path / "file"
    blocked.write_text("")
    _rewrite_store.code(hook, _file(tmp_path, "a"), blocked / "store", True)
    assert rewrites == ["test_one.py"]


def test_only_one_process_of_a_run_writes_and_never_inside_a_test(tmp_path, monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    def config(worker):
        return types.SimpleNamespace(**({"workerinput": {"workerid": worker}} if worker else {}))

    assert _rewrite_store.writes(config(None)) and _rewrite_store.writes(config("gw0"))
    assert not _rewrite_store.writes(config("gw1"))
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_x.py::test_y (call)")
    assert not _rewrite_store.writes(config(None)) and not _rewrite_store.writes(config("gw0"))


def test_what_is_not_written_is_not_kept(tmp_path, hook):
    _rewrite_store.code(hook, _file(tmp_path, "a"), tmp_path / "store", False)
    assert not (tmp_path / "store").exists()


def test_the_settings_that_change_a_rewrite_change_its_key(tmp_path, pytestconfig, monkeypatch):
    fn = Path("test_one.py")
    plain = _rewrite_store.kept(tmp_path, fn, b"x", pytestconfig)
    hooked = types.SimpleNamespace(getini=lambda name: name == "enable_assertion_pass_hook")
    assert _rewrite_store.kept(tmp_path, fn, b"x", hooked) != plain
    assert _rewrite_store.kept(tmp_path, fn, b"y", pytestconfig) != plain
    monkeypatch.setattr(_rewrite_store.sys, "flags", types.SimpleNamespace(optimize=2))
    assert _rewrite_store.kept(tmp_path, fn, b"x", pytestconfig) != plain


def test_the_store_is_bounded(tmp_path, hook):
    """The KEEP newest of a file, none of a neighbour's, and no other pytest's."""
    root = tmp_path / "rewrite" / "now"
    (old := root.parent / "cpython-314-pytest-9.0.0").mkdir(parents=True)
    (root.parent / "someone-elses").mkdir()
    fn = _file(tmp_path, "a")
    for n in range(_rewrite_store.KEEP + 2):
        fn.write_text(SOURCE + f"# {n}\n")
        _rewrite_store.code(hook, fn, root, True)
        if n == 0:
            (root / "test_one_more.0.pyc").write_text("a longer stem's")
    names = sorted(p.name.split(".")[0] for p in root.iterdir())
    assert names == ["test_one"] * _rewrite_store.KEEP + ["test_one_more"], names
    assert not old.exists() and (root.parent / "someone-elses").is_dir()


def test_a_file_outside_the_suite_is_pytests_own(tmp_path, hook, monkeypatch):
    """A fixture project's test, in a pytest run in this process, is not the store's."""
    native: list[str] = []
    monkeypatch.setattr(_rewrite_store, "_NATIVE", lambda self, mod: native.append(mod.__name__))
    fn = _file(tmp_path, "a")
    module = types.SimpleNamespace(
        __name__="test_one", __spec__=types.SimpleNamespace(origin=str(fn))
    )
    _rewrite_store._exec_module(hook, module)
    assert native == ["test_one"]
