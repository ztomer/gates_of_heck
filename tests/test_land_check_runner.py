"""The check's test nodes run in the environment the gate gives them (GOH.RULESLEFT).

Plain pytest gave them another: 5 tests of tests/test_suite_drift.py and 2 coverage judges were red
in `goh land --check` and green in the gate on the same tree. Every prober of a land's tests asks
lib/land_check.py `suite_argv`, which is the tree's own tools/pytest.sh; a run that stopped before
pytest is no verdict, by its reason, and never a refusal of the tree or a pass.
"""

from __future__ import annotations

import sys
from pathlib import Path

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT))
from lib import land_check

TREE_READS = ("lib/land_check.py", "lib/landq_ratchet.py", "lib/main_reading.py")

RUNNER = """\
#!/bin/sh
printf '%s\\n' "$*" "guard run: ${GOH_STAGED_GUARD_RUN-unset}" >"$(dirname "$0")/../ran.txt"
PYTHON_OK=1 exec "$GOH_PY" -m pytest -q -p no:cacheprovider "$@"
"""


def _runner_tree(tmp_path: Path, runner: str, test: str) -> Path:
    tree = tmp_path / "tree"
    (tree / "tests").mkdir(parents=True)
    (tree / "tools").mkdir()
    (tree / "tests" / "test_g.py").write_text(f"# goh-land-guard:\nimport os\n{test}\n")
    (tree / "tools" / "pytest.sh").write_text(runner)
    return tree


def test_guard_tests_run_through_the_trees_own_suite_runner(tmp_path, monkeypatch):
    monkeypatch.setenv("GOH_PY", sys.executable)
    body = "def test_in_the_gates_env():\n    assert os.environ.get('PYTHON_OK')"
    tree = _runner_tree(tmp_path, RUNNER, body)
    said: list[str] = []
    assert land_check.run(tree, None, said.append, "") == [], said
    ran = (tree / "ran.txt").read_text()
    assert "tests/test_g.py::test_in_the_gates_env" in ran and "guard run: unset" in ran, ran


def test_a_runner_that_never_reached_pytest_is_no_verdict_and_says_why(tmp_path, monkeypatch):
    monkeypatch.setenv("GOH_PY", sys.executable)
    dies = "#!/bin/sh\necho '✗ ruff is not installed here' >&2\nexit 1\n"
    tree = _runner_tree(tmp_path, dies, "def test_x(): pass")
    said: list[str] = []
    (refused,) = land_check.run(tree, None, said.append, "")
    assert land_check.blind(refused), refused
    assert any("ruff is not installed here" in s for s in said), said


def test_what_pytest_said_is_a_verdict_and_what_its_runner_said_is_not():
    assert land_check.unrun(1, "FAILED tests/test_a.py::test_b - x\n") == ""
    assert land_check.unrun(2, "ERROR tests/test_a.py\n1 error in 0.1s\n") == ""
    assert land_check.unrun(0, "") == "" and land_check.unrun(124, "") == ""  # no verdict already
    assert land_check.unrun(1, "→ x\n✗ no ruff here\n") == "✗ no ruff here"
    assert land_check.unrun(75, "") == "it said nothing"


def test_every_prober_of_a_lands_tests_asks_the_one_runner():
    for rel in TREE_READS:
        text = (REPO_ROOT / rel).read_text()
        assert "suite_argv(" in text, rel
        assert text.count('"-m", "pytest"') == (rel == "lib/land_check.py"), rel
