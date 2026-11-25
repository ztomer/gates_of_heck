"""How many workers a narrowed suite run takes (lib/suite_width.py, GOH.PYHARNESS)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT

sys.path.append(str(REPO_ROOT / "lib"))
import suite_width

from lib import fast_tests

A, B = "tests/test_a.py", "tests/test_b.py"
TABLE = {
    f"{A}::test_one": 0.25,
    f"{A}::test_many[x]": 0.5,
    f"{A}::test_many[y]": 0.75,
    f"{A}::TestC::test_in": 0.25,
    f"{B}::test_slow": 30.0,
    "tests/test_ab.py::test_other": 900.0,
}


@pytest.fixture
def root(tmp_path, monkeypatch):
    (tmp_path / "tests").mkdir()
    (tmp_path / A).write_text(
        "def test_one(): pass\ndef test_many(p): pass\nclass TestC:\n    def test_in(self): pass\n"
    )
    (tmp_path / B).write_text("def test_slow(): pass\n")
    monkeypatch.setattr(fast_tests, "load", lambda: dict(TABLE))
    return tmp_path.resolve()


def _width(root: Path, *args: str, **env: str) -> int | None:
    return suite_width.width(args, root, env)


@pytest.mark.parametrize(
    "args",
    [(A,), (f"./{A}",), ("-q", A, "-x"), ("-k", "one", A), (A, "--tb=short", "-rf"),
     (f"{A}::test_many",), (f"{A}::test_many[y]",), (f"{A}::TestC",)],
)  # fmt: skip
def test_a_cheap_file_or_node_is_serial(root, args):
    assert _width(root, *args) == 0


def test_the_workers_follow_what_the_tests_cost(root):
    assert _width(root, B) == 20  # the runner's ceiling cuts it (tools/pytest.sh)
    assert _width(root, A, B) == 22
    assert _width(root, f"{B}::test_slow", A) == 22
    (root / A).write_text("def test_one(): pass\n" + "".join(
        f"def test_new{i}(): pass\n" for i in range(5)))  # fmt: skip
    # 1.75 s timed and five nothing has timed: the test being written is never free
    assert _width(root, A) == 3


def test_a_test_named_twice_is_paid_once(root):
    assert suite_width.cost([A, f"{A}::test_one"], TABLE, root) == 1.75


def test_a_neighbours_name_is_no_part_of_a_target(root):
    assert suite_width.cost([f"{A}::test_one"], {f"{A}::test_one_more": 9.0,
                                                 f"{A}::test_one": 1.0}, root) == 1.0  # fmt: skip


@pytest.mark.parametrize(
    "args",
    [(), ("-q",), ("-k", "one"), ("tests",), ("tests/",), ("--ignore", A), ("--ignore=" + A,),
     ("--deselect", f"{A}::test_one"), (A, "-p", "no:xdist"), (A, "--cov"), (A, "-n4"),
     ("-c", A), (A, "--rootdir=/"), ("tests/test_gone.py",), ("../outside.py",), ("--", A)],
)  # fmt: skip
def test_a_run_it_cannot_cost_is_the_ceilings(root, args):
    (root.parent / "outside.py").write_text("")
    assert _width(root, *args) is None


@pytest.mark.parametrize("key", suite_width.LAND)
def test_a_lands_run_is_the_ceilings(root, key):
    assert _width(root, A, **{key: "/x/record"}) is None


def test_an_empty_table_is_the_ceilings(root, monkeypatch):
    monkeypatch.setattr(fast_tests, "load", dict)
    assert _width(root, A) is None


def test_a_collection_starts_no_worker(root):
    assert _width(root, "--collect-only") == 0 and _width(root, "tests", "--co", "-q") == 0
