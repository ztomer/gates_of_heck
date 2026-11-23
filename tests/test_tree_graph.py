"""Every real-tree selector pass of the suite shares its worker's graph (tests/_tree_graph.py).

GOH.SUITEDIET: a fresh lib/test_closure.py Closure of goh's tree costs a selector pass 10-13 s to
fill; a warm one 3 s. The scanner test of tests/test_affected_binary_reach.py made three fresh
passes (76 s under load, the suite's longest test). The ratchet below keeps every pass over
REPO_ROOT on the shared graph; the equivalence test pins what makes sharing sound -- a Closure's
caches are by file, so a graph that answered one change answers the next as a fresh one does.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "tools"))
import affected_tests as at

# Its whole read of the tree (lib/declared_reads.py JUDGE_READS): the suite's own Python files.
JUDGE_READS = ("tests/*.py",)

# A selector pass over the real tree with no graph handed, or a fresh graph of it.
FRESH = re.compile(
    r"\b(?:affected|readers|_binary)\(REPO_ROOT, [^()\n]*(?:\([^()\n]*\)[^()\n]*)*\)"
    r"|\bClosure\(REPO_ROOT\)"
)


def _fresh(text: str) -> list[int]:
    return [
        n
        for n, line in enumerate(text.splitlines(), 1)
        for m in FRESH.finditer(line)
        if "graph" not in m.group(0)
    ]


def test_no_real_tree_selector_pass_builds_its_own_graph() -> None:
    tests = REPO_ROOT / "tests"
    found = [
        f"tests/{p.name}:{n}"
        for p in sorted(tests.glob("*.py"))
        if p.name not in ("_tree_graph.py", Path(__file__).name)
        for n in _fresh(p.read_text(encoding="utf-8"))
    ]
    assert found == [], f"pass graph=_tree_graph.real(): {found}"


def test_the_ratchet_sees_each_fresh_spelling() -> None:
    assert _fresh('x = at.affected(REPO_ROOT, ["lib/a.py"])\n') == [1]
    assert _fresh("g = at.Closure(REPO_ROOT)\n") == [1]
    assert _fresh("r = at.readers(REPO_ROOT, sorted(moved))\n") == [1]
    assert _fresh('x = at.affected(REPO_ROOT, ["a"], graph=_tree_graph.real())\n') == []
    assert _fresh("x = at.affected(tmp_path, [])\n") == []


def _tree(root: Path) -> Path:
    for rel, text in {
        "lib/a.py": "X = 1\n",
        "lib/b.py": "from lib import a\n",
        "tests/test_a.py": "from lib import a\n\ndef test_a():\n    pass\n",
        "tests/test_b.py": "from lib import b\n\ndef test_b():\n    pass\n",
    }.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root


def test_a_graph_that_answered_one_change_answers_the_next_as_a_fresh_one(tmp_path) -> None:
    root = _tree(tmp_path)
    shared = at.Closure(root)
    for changed in (["lib/b.py"], ["lib/a.py"], ["tests/test_a.py"], []):
        assert at.affected(root, changed, graph=shared) == at.affected(
            root, changed, graph=at.Closure(root)
        ), changed
