"""A test importing names from a test module reads what those names read (GOH.SELECTTESTIMPORTS).

GOH.PRESTEPMOVED's replay: a main move touching gates/push_gate.sh chose 114 test files, most of
them only because they import a helper or fixture from a test (tests/test_proven.py,
tests/test_gate_environment.py) whose OWN tests run the gate. The closure followed the import as if
the importer read everything the imported module's tests read. lib/test_slice.py cuts the module
to what the imported names reach; here, every shape of import that must still choose the importer
does, and the one that must not, does not.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "tools"))
import affected_tests as at

HELPER = "def helper():\n    return 1\n"
RUNS = 'def test_runs():\n    subprocess.run(["bash", str(REPO_ROOT / "tools" / "run.sh")])\n'
FIXTURE_READS = (
    "import pytest\n\n@pytest.fixture\ndef env():\n"
    '    return REPO_ROOT / "tools" / "run.sh"\n\n'
    "@pytest.fixture\ndef repo(env):\n    return env\n\n"
    "@pytest.fixture\ndef plain():\n    return 2\n"
)


def _chosen(tmp_path: Path, files: dict[str, str], changed: str = "tools/run.sh") -> dict:
    files = {"tools/run.sh": "#!/bin/bash\n", **files}
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    return at.readers(tmp_path, [changed])


def test_a_helper_imported_from_a_test_does_not_bring_that_tests_reads(tmp_path) -> None:
    """The 114-file shape: the importer takes `helper`; test_x's own test runs the script."""
    got = _chosen(
        tmp_path,
        {"tests/test_x.py": HELPER + RUNS, "tests/test_a.py": "from test_x import helper\n"},
    )
    assert sorted(got) == ["tests/test_x.py"], got


@pytest.mark.parametrize(
    ("x", "a"),
    [
        # the imported name itself runs it
        (RUNS, "from test_x import test_runs\n"),
        # a constant naming it
        ('SCRIPT = REPO_ROOT / "tools" / "run.sh"\n', "from test_x import SCRIPT\n"),
        # a helper reaching a constant through a chain of the module's own names
        (
            (
                'SCRIPT = REPO_ROOT / "tools" / "run.sh"\ndef _cmd():\n    return [SCRIPT]\n'
                "def go():\n    return _cmd()\n"
            ),
            "from test_x import go\n",
        ),
        # a fixture asking for the fixture that names it
        (FIXTURE_READS, "from test_x import repo  # noqa: F401\n"),
        # a module-level read at import, whatever is taken
        (
            HELPER + 'TEXT = (REPO_ROOT / "tools" / "run.sh").read_text()\n',
            "from test_x import helper\n",
        ),
        # all of it
        (HELPER + RUNS, "from test_x import *\n"),
        # bound as a module: an attribute naming it, or the module handed on
        (
            'SCRIPT = REPO_ROOT / "tools" / "run.sh"\n' + HELPER,
            "import test_x\nX = test_x.SCRIPT\n",
        ),
        (HELPER + RUNS, "import test_x as m\nsetattr(m, 'y', 1)\n"),
        (HELPER + RUNS, "from tests import test_x\nf(test_x)\n"),
        # loaded by a name no static import shows
        (HELPER + RUNS, "import importlib\nm = importlib.import_module('test_x')\n"),
        # a slice that looks names up at run time
        (
            HELPER.replace("return 1", "return globals()['test_runs']") + RUNS,
            "from test_x import helper\n",
        ),
    ],
)
def test_every_import_that_uses_the_read_still_chooses_the_importer(tmp_path, x, a) -> None:
    got = _chosen(tmp_path, {"tests/test_x.py": x, "tests/test_a.py": a})
    assert "tests/test_a.py" in got, got


@pytest.mark.parametrize(
    "a",
    [
        "from test_x import plain  # noqa: F401\n",
        "import test_x as m\nY = m.helper()\n",
        "from tests.test_x import helper\n",
    ],
)
def test_every_import_of_names_that_read_nothing_leaves_the_importer_out(tmp_path, a) -> None:
    got = _chosen(
        tmp_path, {"tests/test_x.py": HELPER + FIXTURE_READS + RUNS, "tests/test_a.py": a}
    )
    assert "tests/test_a.py" not in got, got


def test_a_chain_of_test_modules_passes_on_only_the_names_used(tmp_path) -> None:
    """test_a takes `f` from test_b, which takes `g` and `h` from test_x: `f` uses only `g`, so
    `h`'s read is not test_a's -- and is, once `f` calls `h`."""
    x = HELPER.replace("helper", "g") + "def h():\n    return REPO_ROOT / 'tools' / 'run.sh'\n"
    files = {"tests/test_x.py": x, "tests/test_a.py": "from test_b import f\n"}
    got = _chosen(
        tmp_path,
        {**files, "tests/test_b.py": "from test_x import g, h\ndef f():\n    return g()\n"},
    )
    assert "tests/test_a.py" not in got and "tests/test_b.py" in got, got
    got = _chosen(
        tmp_path,
        {**files, "tests/test_b.py": "from test_x import g, h\ndef f():\n    return h()\n"},
    )
    assert "tests/test_a.py" in got, got


def test_two_importers_widen_the_slice_and_a_changed_test_module_chooses_both(tmp_path) -> None:
    """A helper module taking `go` and a test taking `helper` from the same test module: the
    union is reached; and a change to the test module itself chooses whoever imports it."""
    files = {
        "tests/test_x.py": HELPER + 'def go():\n    return REPO_ROOT / "tools" / "run.sh"\n',
        "tests/_kit.py": "from test_x import go\n",
        "tests/test_a.py": "from test_x import helper\nimport _kit\n",
    }
    assert "tests/test_a.py" in _chosen(tmp_path, files)
    assert "tests/test_a.py" in _chosen(tmp_path, files, changed="tests/test_x.py")


def test_a_directory_read_is_the_importers_only_through_the_names_it_takes(tmp_path) -> None:
    """declared_reads along a chain of tests/ files reads the slice too: test_x's own test globs
    tools/*.sh; a helper of it does not."""
    glob = 'def test_all():\n    for p in (REPO_ROOT / "tools").glob("*.sh"):\n        pass\n'
    files = {"tests/test_x.py": HELPER + glob, "tests/test_a.py": "from test_x import helper\n"}
    assert "tests/test_a.py" not in _chosen(tmp_path, files)
    files["tests/test_a.py"] = "from test_x import test_all\n"
    assert "tests/test_a.py" in _chosen(tmp_path, files)


def test_a_test_with_undeclared_reads_keeps_every_test_module_it_imports_whole(tmp_path) -> None:
    """gates/undeclared_reads.txt (lib/declared_reads.py's ratchet) lists tests whose reads no
    spelling shows: the wider closure is their only cover. The replay of CLS.CIPROFILE's land: a
    broken gates/push_gate.sh turned tests/test_hold_gates.py red, which copies
    `REPO_ROOT / "hooks" / name`, and the slice of its import alone had dropped it. A test module
    on the list is whole too when the slice taken holds the unknown join."""
    x = HELPER + RUNS
    files = {"tests/test_x.py": x, "tests/test_a.py": "from test_x import helper\n"}
    files["gates/undeclared_reads.txt"] = "# the ratchet\ntests/test_a.py\n"
    assert "tests/test_a.py" in _chosen(tmp_path, files)
    dyn = "def helper(name):\n    return REPO_ROOT / name\n"
    files = {"tests/test_x.py": dyn + RUNS, "tests/test_a.py": "from test_x import helper\n"}
    files["gates/undeclared_reads.txt"] = "tests/test_x.py\n"
    assert "tests/test_a.py" in _chosen(tmp_path, files)
    files["tests/test_x.py"] = HELPER + "def _other(name):\n    return REPO_ROOT / name\n" + RUNS
    assert "tests/test_a.py" not in _chosen(tmp_path, files)  # the join is outside the slice


def test_a_python_tool_a_test_runs_brings_the_shell_script_it_runs(tmp_path) -> None:
    """tools/landq.py runs `ROOT / "tools" / "land.sh"`: tests/test_landq_batch.py, which runs
    landq.py, went red in the replay of GOH.RUFFCURRENCY's land on a broken land.sh, which it had
    reached only through a test module's own tests. A tool's prose naming the script is no run."""
    files = {
        "tools/lander.py": 'LAND = ROOT / "tools" / "run.sh"\n',
        "tests/test_a.py": 'LANDER = REPO_ROOT / "tools" / "lander.py"\n',
    }
    assert "tests/test_a.py" in _chosen(tmp_path, files)
    files["tools/lander.py"] = '"""Runs tools/run.sh."""\n# and tools/run.sh again\n'
    assert "tests/test_a.py" not in _chosen(tmp_path, files)


def test_the_slice_holds_what_runs_at_import_and_what_the_names_reach() -> None:
    import lib.test_slice as ts

    text = (
        "import os\nX = 1\nY = os.getcwd()\nZ = str(X)\n"
        "def a():\n    return b()\ndef b():\n    return X\ndef c():\n    return 3\n"
    )
    code, held = ts.slice_of(text, frozenset({"a"}))
    assert held >= {"a", "b", "X"} and "c" not in held and "Z" not in held, held
    assert "Y = os.getcwd()" in code and "def c" not in code and "Z = " not in code, code
    assert ts.slice_of("def (:\n", frozenset({"a"})) is None


def test_on_the_real_tree_a_push_gate_change_chooses_under_100_files() -> None:
    """The metric: 114 test files before (2026-11-20, main 7cb938df), 87 after. The importers that
    stay are those whose names run or copy the gate: tests/test_push_gate_race.py's `_push` runs
    it, tests/test_proven.py's `goh` fixture copies gates/ whole."""
    got = at.readers(REPO_ROOT, ["gates/push_gate.sh"])
    assert len(got) < 100, len(got)
    for kept in ("tests/test_commit_gate_structural.py", "tests/test_estate_corpus_cache.py"):
        assert kept in got, sorted(got)
    assert "tests/test_landq_batch.py" not in got, got.get("tests/test_landq_batch.py")
    assert "tests/test_hold_gates.py" in got, "undeclared reads keep the wider closure"
