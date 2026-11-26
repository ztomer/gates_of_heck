"""A NEW test is held to the house default at COMMIT (GOH.TESTWORK): 1 s CPU, 5 processes, no cargo
build, no tree walk (tests/test_work_ceiling.json `new`), or a named exception with its reason.

The line below makes this a source guard (gates/staged_guards.sh): a commit staging a test file runs
it, over the index, with the staged paths and the checkout they came from in
GATES_OF_HECK_STAGED_PATHS and GATES_OF_HECK_STAGED_FROM. The test functions a staged tests/ file
holds that HEAD's did not (`new_tests`) run in one nested pytest with tests/_test_work.py judging
them as NEW (`GATES_OF_HECK_TEST_WORK=new`): their own node's entry or `new`, never their file's,
so a file that stood before the rule excuses none of its new tests. Red names the test, the
dimension, the reading and the entry that would let it stand. Their pass or fail is the suite's,
at push; here only their work is judged. A run no commit named (the suite at push, a land) has no
staged paths and skips.
"""

# goh-reads-staged: tests/

from __future__ import annotations

import ast
import os
import subprocess
import sys

import pytest
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

TREE_READS = ("tests/",)  # the staged test files, read from this tree (the index's view)
PATHS_ENV, FROM_ENV = "GATES_OF_HECK_STAGED_PATHS", "GATES_OF_HECK_STAGED_FROM"
MARK = "✗ test work (GOH.TESTWORK): "


def defined_tests(src: str) -> set[str]:
    """The test functions SRC defines, as node-id tails: `test_x`, `TestY::test_x`."""
    out: set[str] = set()
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
            "test"
        ):
            out.add(node.name)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            out |= {f"{node.name}::{f.name}" for f in node.body
                    if isinstance(f, ast.FunctionDef | ast.AsyncFunctionDef)
                    and f.name.startswith("test")}  # fmt: skip
    return out


def new_tests(head: str | None, staged: str) -> list[str]:
    """The tests STAGED defines that HEAD (None: the file is new) did not."""
    return sorted(defined_tests(staged) - (defined_tests(head) if head is not None else set()))


def _head(checkout: str, path: str) -> str | None:
    r = subprocess.run(["git", "-C", checkout, "show", f"HEAD:{path}"], check=False,
                       capture_output=True, text=True, stdin=subprocess.DEVNULL,
                       timeout=patience(30))  # fmt: skip
    return r.stdout if r.returncode == 0 else None


def staged_nodes(paths: list[str], checkout: str) -> list[str]:
    """The node ids of the tests the commit adds, read from this tree (the index's view)."""
    nodes = []
    for path in paths:
        name = os.path.basename(path)
        if not (path.startswith("tests/") and name.startswith("test_") and name.endswith(".py")):
            continue
        here = REPO_ROOT / path
        if here.is_file():
            nodes += [f"{path}::{t}" for t in new_tests(_head(checkout, path), here.read_text())]
    return nodes


def test_the_tests_a_commit_adds_are_held_to_the_new_ceilings() -> None:
    paths, checkout = os.environ.get(PATHS_ENV), os.environ.get(FROM_ENV)
    if not paths or not checkout:
        pytest.skip(f"no commit named its staged paths ({PATHS_ENV}, gates/staged_guards.sh)")
    me = "tests/test_test_work_new.py::test_the_tests_a_commit_adds_are_held_to_the_new_ceilings"
    nodes = [n for n in staged_nodes(paths.splitlines(), checkout) if n != me]  # never itself
    if not nodes:
        pytest.skip("the commit adds no test function")
    env = hermetic_env(GATES_OF_HECK_SUITE_NESTED_OK="1", GATES_OF_HECK_TEST_WORK="new")
    for name in (PATHS_ENV, FROM_ENV):  # the nested run judges; it names no commit of its own
        env.pop(name, None)
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-n", "0", "--", *nodes],
        cwd=REPO_ROOT, env=env, check=False, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, timeout=patience(600),
    )  # fmt: skip
    faults = [ln.removeprefix(MARK) for ln in r.stdout.splitlines() if ln.startswith(MARK)]
    assert "→ test work:" in r.stdout, f"the new tests were not counted:\n{r.stdout[-2000:]}"
    assert not faults, "a new test is over the house default:\n" + "\n".join(faults)
