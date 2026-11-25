"""A test leaves no more than it reads (GOH.FSEVQUIET): no `current` link, a module's clone put back
for the next (tests/_quiet_tmp.py), one checkout of the gates for the modules that only run them
(tests/_todays_gates.py), a clone of the session's verdicts (tests/_tree_lint.py)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import _quiet_tmp
import _tree_clone
import _tree_lint
import _tree_pool
import pytest
from _fs_bytes import clone_of
from _kept_store import _cache, put_shell
from _todays_gates import _clean_checkout_of_todays_gates, todays_gates_read
from _tree_clone import clone_tree, process_template

pytestmark = pytest.mark.skipif(_tree_clone._CLONEFILE is None, reason="no clonefile here")


def _build(tree: Path) -> None:
    for n in range(6):
        (tree / f"d{n}").mkdir(parents=True)
        (tree / f"d{n}" / "f").write_text(str(n))


def _clone(factory: pytest.TempPathFactory) -> Path:
    top = factory.mktemp("module")
    clone_tree(process_template("quiet-tmp", _build), top / "tree")
    return top


def _finalize(value: object, scope: str = "module", failed: int = 0, error: object = None) -> None:
    _quiet_tmp.pytest_fixture_post_finalizer(
        SimpleNamespace(scope=scope, cached_result=(value, None, error)),
        SimpleNamespace(session=SimpleNamespace(testsfailed=failed)),
    )


def test_a_tests_directory_has_no_current_link(tmp_path: Path) -> None:
    assert tmp_path.is_dir()
    assert not [p.name for p in tmp_path.parent.iterdir() if p.name.endswith("current")]


@pytest.mark.parametrize("held", ["its parent", "itself", "a pair", "a mapping"])
def test_a_modules_clone_is_the_next_ones(
    tmp_path: Path, tmp_path_factory, held: str, monkeypatch
) -> None:
    monkeypatch.setattr(_tree_pool, "_home", lambda src: str(tmp_path / "pool"))  # no other's
    top = _clone(tmp_path_factory)
    inode = (top / "tree").stat().st_ino
    (top / "tree" / "d3" / "f").write_text("a test wrote this")
    _finalize({"its parent": top, "itself": top / "tree", "a pair": (1, top / "tree"),
               "a mapping": {"x": [top]}}[held])  # fmt: skip
    assert not (top / "tree").exists() and top.is_dir()
    again = _clone(tmp_path_factory) / "tree"
    assert again.stat().st_ino == inode, "a clone was made where one was free"
    assert (again / "d3" / "f").read_text() == "3"


def test_a_clone_another_fixture_holds_stays(tmp_path_factory) -> None:
    top = _clone(tmp_path_factory)
    _finalize(top.parent / "another")
    _finalize(top, scope="session")  # every module's, until the run ends
    _finalize(top, scope="function")  # its tmp_path's end is the one that tells
    _finalize(top, failed=1)  # the failure's evidence
    _finalize(top, error=ValueError("the fixture did not set up"))
    _finalize("no path")
    assert (top / "tree" / "d0" / "f").read_text() == "0"


def test_the_gates_a_module_only_runs_are_the_runs_one(tmp_path: Path) -> None:
    one = todays_gates_read()
    assert one == todays_gates_read() and (one / "gates" / "rust_gate.sh").is_file()
    own = _clean_checkout_of_todays_gates(tmp_path)
    assert not own.samefile(one)
    templates = {p.name for p in one.parent.parent.iterdir()}
    assert {"todays-gates", "todays-gates-read"} <= templates, "a test's clone is of another tree"


def test_a_private_copy_of_the_verdicts_is_a_clone_and_holds_what_the_wal_does(
    tmp_path: Path,
) -> None:
    """The session's file, which the run holds open: what one test commits stays in its WAL."""
    db, row = _tree_lint.shell_verdicts(), ("quiet-tmp", str(tmp_path), "{}")
    put_shell(db, [row])
    try:
        own = _tree_lint.private_copy(db, tmp_path / "own")
        assert _tree_lint.records(own, "quiet-tmp") >= {row[1]}
        _ident, shared, private = clone_of(str(own))
        assert shared > 1 or private < own.stat().st_size, "the month's verdicts were written again"
        put_shell(own, [("quiet-tmp", "its own", "{}")])
        assert "its own" not in _tree_lint.records(db, "quiet-tmp")
    finally:
        with _cache().transaction(db) as con:  # the run merges its rows into the kept file
            con.execute("DELETE FROM shell_verdicts WHERE checker='quiet-tmp'")
