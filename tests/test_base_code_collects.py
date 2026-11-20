"""A change to the base's CODE collects the whole suite in the pre-step (GOH.BASECODESELECT).

lib/test_closure.py's `base()` is what pytest loads before every test; a change to a base module
chooses only its readers, so a syntax error in lib/lock_order.py, which tests/conftest.py imports,
was caught only by luck: a judge happening to run, or the selector itself importing it. Here the
pre-step collects the whole suite whenever a base module (or a deleted .py) changes, a named step,
red on a broken base module even when the diff chooses no test; and a change outside the base
collects nothing.
"""

from __future__ import annotations

import os
import sys

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "tools"))
import affected_tests as at

from lib import fast_tests, test_closure

# A stand-in tools/pytest.sh: the real pytest, serial, no plugins the fixture does not have.
PYTEST_SH = (
    'cd "$(dirname "$0")/.."\n'
    "args=(); skip=0\n"
    'for a in "$@"; do\n'
    '    if [ "$skip" = 1 ]; then skip=0; continue; fi\n'
    '    [ "$a" = -n ] && { skip=1; continue; }\n'
    '    args+=("$a")\n'
    "done\n"
    'exec "$GOH_TEST_PY" -m pytest -p no:cacheprovider -p no:randomly tests "${args[@]}"\n'
)
TREE = {
    "tests/conftest.py": (
        "import sys\nfrom pathlib import Path\n"
        'sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))\n'
        "import lock_order  # noqa: F401\n"
    ),
    "lib/lock_order.py": "ORDER = ('load',)\n",
    "lib/other.py": "X = 1\n",
    "tests/test_a.py": "def test_a():\n    assert True\n",
    "tools/pytest.sh": PYTEST_SH,
}


@pytest.fixture
def tree(tmp_path, monkeypatch):
    for rel, text in TREE.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    monkeypatch.setenv("GOH_TEST_PY", sys.executable)
    for key in ("GATES_OF_HECK_SUITE_RUNNER", "PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    return tmp_path


def test_a_base_module_change_and_a_deleted_module_are_base_code(tree):
    graph = test_closure.Closure(tree)
    assert "lib/lock_order.py" in graph.base(), sorted(graph.base())
    got = graph.base_code(["lib/lock_order.py", "lib/other.py", "lib/gone.py", "README.md"])
    assert got == ["lib/lock_order.py", "lib/gone.py"], got


def test_a_syntax_error_in_a_base_module_makes_the_pre_step_red(tree, capfd):
    """The red-first case: the diff chooses no test (a change to a base module chooses only its
    readers, and here none reads it), so the run said "no test is chosen" and passed."""
    (tree / "lib/lock_order.py").write_text("def broken(:\n")
    changed = ["lib/lock_order.py"]
    chosen = at.affected(tree, changed)
    assert chosen == {}, chosen
    assert at.run(tree, changed, chosen) != 0
    out = capfd.readouterr()
    assert "the whole suite collects" in out.out + out.err, out
    assert "SyntaxError" in out.out + out.err, out


def test_a_sound_base_module_change_collects_green(tree, capfd):
    changed = ["lib/lock_order.py"]
    assert at.run(tree, changed, at.affected(tree, changed)) == 0
    out = capfd.readouterr().out
    assert "the whole suite collects" in out and "no test is chosen" in out, out
    assert "tests/test_a.py: 1" not in out, out  # the count rows are dropped


def test_a_change_outside_the_base_collects_nothing(tree, capfd):
    (tree / "lib/lock_order.py").write_text("def broken(:\n")  # broken, but not this diff's
    assert fast_tests.collects(tree, ["lib/other.py"]) == 0
    assert "collects" not in capfd.readouterr().out


def test_inside_a_running_suite_it_collects_nothing(tree, monkeypatch):
    (tree / "lib/lock_order.py").write_text("def broken(:\n")
    monkeypatch.setenv("GATES_OF_HECK_SUITE_RUNNER", str(os.getpid()))
    assert fast_tests.collects(tree, ["lib/lock_order.py"]) == 0
