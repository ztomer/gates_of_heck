"""Every real-tree selector pass of the suite shares the run's graph (tests/_tree_graph.py).

GOH.SUITEDIET: a fresh lib/test_closure.py Closure of goh's tree costs a selector pass 10-13 s to
fill; a warm one 3 s. The scanner test of tests/test_affected_binary_reach.py made three fresh
passes (76 s under load, the suite's longest test). The ratchet below keeps every pass over
REPO_ROOT on the shared graph; the equivalence test pins what makes sharing sound -- a Closure's
caches are by file, so a graph that answered one change answers the next as a fresh one does.
GOH.PYSLOWTESTS: the 12 workers of a run filled 12 (about 725 s a run): the last tests pin that the
first to ask fills it, and that what the others read answers as a fresh graph.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT, hermetic_env

sys.path.insert(0, str(REPO_ROOT / "tools"))
import _tree_graph
import affected_tests as at
import pytest
from timing_bounds import patience

from checks import _prose_mask
from lib import source_closure

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


# One worker of a run: it asks for the graph of argv[1] and prints `filled` each time it fills one.
WORKER = """
import sys
from pathlib import Path
import _tree_graph as tg
fill = tg._fill
tg._fill = lambda graph: print("filled") or fill(graph)
graph = tg.shared(Path(sys.argv[1]), Path(sys.argv[2]))
print("reach", sorted(graph.reach("tests/test_b.py")[0]))
"""


@pytest.mark.load_sensitive
def test_two_workers_of_a_run_fill_one_graph(tmp_path) -> None:
    root, store = _tree(tmp_path / "tree"), tmp_path / "store"
    env = hermetic_env(PYTHONPATH=f"{REPO_ROOT}:{REPO_ROOT / 'tests'}")
    workers = [
        subprocess.Popen([sys.executable, "-c", WORKER, str(root), str(store)], env=env, text=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)
        for _ in range(2)
    ]  # fmt: skip
    said = [w.communicate(timeout=patience(60.0))[0].splitlines() for w in workers]
    assert sum(lines.count("filled") for lines in said) == 1, said
    assert [lines[-1] for lines in said] == ["reach ['lib/a.py', 'lib/b.py']"] * 2, said


def test_a_moved_tree_is_a_graph_of_its_own(tmp_path) -> None:
    root = _tree(tmp_path)
    before = _tree_graph._identity(at.Closure(root))
    assert before == _tree_graph._identity(at.Closure(root))
    (root / "lib" / "a.py").write_text("X = 2\n")
    assert before != _tree_graph._identity(at.Closure(root))


def test_the_graph_a_worker_reads_answers_as_a_fresh_one(tmp_path) -> None:
    root, store = _tree(tmp_path / "tree"), tmp_path / "store"
    filled, read = (_tree_graph.shared(root, store) for _ in range(2))
    assert read.reach("tests/test_b.py") is not filled.reach("tests/test_b.py")  # unpickled
    for changed in (["lib/b.py"], ["lib/a.py"], ["tests/test_a.py"], []):
        fresh = at.affected(root, changed, graph=at.Closure(root))
        assert at.affected(root, changed, graph=read) == fresh, changed


# Every statement that holds others, an import and a lone string at each depth (GOH.PYSLOWTESTS: the
# readers of imports and of prose walk the statements, not the expressions).
NESTED = """
import a0
def f(x=lambda: 1):
    "p1"
    import a1
async def g():
    async with x as y:
        import a2
    async for z in y:
        import a3
    else:
        "p2"
        import a4
class C:
    "p3"
    import a5
if x:
    import a6
elif y:
    import a7
else:
    import a8
for z in y:
    import a9
else:
    import b0
while x:
    import b1
else:
    import b2
try:
    import b3
except E:
    "p4"
    import b4
else:
    import b5
finally:
    import b6
try:
    pass
except* E:
    import b7
match x:
    case 1:
        "p5"
        import b8
with x:
    from b9 import c0
"""


def test_the_statement_walk_meets_every_import_and_every_prose_string() -> None:
    tree = ast.parse(NESTED)
    kinds = (ast.Import, ast.ImportFrom)
    found = [n for n in source_closure.statements(tree) if isinstance(n, kinds)]
    assert found == [n for n in ast.walk(tree) if isinstance(n, kinds)] and len(found) == 20
    assert at._imports(NESTED) >= {"a0", "a8", "b7", "b8", "b9.c0"}
    code = _prose_mask.python_code_only(NESTED)
    assert [p for p in ("p1", "p2", "p3", "p4", "p5") if p in code] == []
    assert code.count("import") == NESTED.count("import")


def test_a_script_is_found_by_its_words_as_by_its_whole_name(tmp_path) -> None:
    """`Closure._naming` asks `names` only of a script whose basename is a word of the text: each
    spelling `names` accepts is one, and a basename that is no plain word is always asked."""
    spelled = {
        "lib/whole.sh": '"lib/whole.sh"',
        "lib/parts.sh": 'ROOT / "lib" / "parts.sh"',
        "lib/rooted.sh": '"$GOH_DIR/lib/rooted.sh"',
        "lib/cell.sh": '"x\\\\tlib/cell.sh\\\\ty"',
        "lib/odd+name.sh": '"lib/odd+name.sh"',
        "top.sh": 'REPO_ROOT / "top.sh"',
    }
    for rel in (*spelled, "lib/other.sh"):
        (tmp_path / rel).parent.mkdir(exist_ok=True)
        (tmp_path / rel).write_text("true\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_n.py").write_text(
        f"X = [{', '.join(spelled.values())}]\n# other.sh\n"
    )
    graph = at.Closure(tmp_path)
    brute = [s for s in graph.scripts if graph.names("tests/test_n.py", s)]
    assert graph.named("tests/test_n.py") == brute and set(brute) == set(spelled)
