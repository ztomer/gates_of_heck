"""The enqueue runs a reader whose reads are known only for a diff they cover (GOH.JUDGESBYREADS).

GOH.LANDREVIEW (2026-11-21): whole-tree tests ("judges") were 60-99% of every enqueue pre-step; a
one-file test edit paid 1,685 s serial for 18 s of chosen tests. A reader whose reads are spelled,
inferred or declared (lib/declared_reads.py `standing`) is chosen at the enqueue like any test --
when the diff touches what it reads -- and the land gate runs every tree reader on the moved main.
A reader whose reads no spelling gives (a listing, a grep, a walk of the root, an undeclared join)
still judges every diff. Outside the enqueue nothing changes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import _tree_graph
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "tools"))
import affected_tests as at

# A judge by name whose read is spelled: hooks/ iterated.
HOOKS = (
    "from conftest import REPO_ROOT\n\n"
    "def test_every_hook_is_named():\n"
    "    for p in (REPO_ROOT / 'hooks').iterdir():\n"
    "        pass\n"
)
# A judge joining the root with a variable, its reads declared.
DOCS = (
    "from conftest import REPO_ROOT\nTREE_READS = ('docs/',)\n\n"
    "def test_every_doc_is_linked(name='x.md'):\n"
    "    (REPO_ROOT / 'docs' / name).read_text()\n"
)
# A judge whose read nothing spells: it lists the tree.
LISTS = "import subprocess\n\ndef test_no_stray_file():\n    subprocess.run(['git', 'ls-files'])\n"
# The same read, only described: a docstring is no walk.
SAYS = '"""This test once ran `git ls-files` over the tree."""\n\n' + HOOKS


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    return tmp_path


def _files(chosen: dict[str, str]) -> set[str]:
    return {k.split("::")[0] for k in chosen}


def _root(tmp_path: Path) -> Path:
    return _tree(
        tmp_path,
        {
            "tests/test_hooks.py": HOOKS,
            "tests/test_docs.py": DOCS,
            "hooks/a.sh": "true\n",
            "docs/x.md": "x\n",
            "lib/other.py": "X = 1\n",
        },
    )


def test_a_change_outside_every_judges_reads_chooses_no_judge_at_the_enqueue(tmp_path) -> None:
    root = _root(tmp_path)
    assert at.affected(root, ["lib/other.py"], enqueue=True) == {}
    # outside the enqueue every tree reader still runs
    assert _files(at.affected(root, ["lib/other.py"], enqueue=False)) == {
        "tests/test_hooks.py",
        "tests/test_docs.py",
    }


def test_a_change_inside_a_judges_reads_chooses_it_at_the_enqueue(tmp_path) -> None:
    root = _root(tmp_path)
    assert _files(at.affected(root, ["hooks/a.sh"], enqueue=True)) == {"tests/test_hooks.py"}
    assert _files(at.affected(root, ["docs/x.md"], enqueue=True)) == {"tests/test_docs.py"}


def test_a_judge_whose_reads_nothing_spells_still_judges_every_diff(tmp_path) -> None:
    root = _tree(tmp_path, {"tests/test_lists.py": LISTS, "tests/test_says.py": SAYS,
                            "hooks/a.sh": "true\n", "lib/other.py": "X = 1\n"})  # fmt: skip
    assert _files(at.affected(root, ["lib/other.py"], enqueue=True)) == {"tests/test_lists.py"}


def test_an_undeclared_join_still_judges_every_diff(tmp_path) -> None:
    root = _tree(
        tmp_path,
        {"tests/test_docs.py": DOCS.replace("TREE_READS = ('docs/',)\n", ""), "docs/x.md": "x\n",
         "lib/other.py": "X = 1\n"},
    )  # fmt: skip
    assert _files(at.affected(root, ["lib/other.py"], enqueue=True)) == {"tests/test_docs.py"}


# A judge whose walk no spelling explains, with everything it reads declared (GOH.SUITEDIET): it
# lists the tracked Python files and reads them, and nothing else of the tree.
JUDGED = (
    "import subprocess\nJUDGE_READS = ('*.py',)\n\n"
    "def test_every_python_file_is_ascii():\n"
    "    subprocess.run(['git', 'ls-files', '*.py'])\n"
)


def test_a_judge_declaring_all_it_reads_runs_only_for_a_diff_they_cover(tmp_path) -> None:
    """JUDGE_READS is the file's whole read of the tree: its verdict stands while no file its
    patterns match changes (the land gate keeps a judge's verdict keyed by those files' bytes:
    unchanged, it is not chosen, and the tree's carried pass stands for it)."""
    root = _tree(tmp_path, {"tests/test_judged.py": JUDGED, "lib/other.py": "X = 1\n",
                            "docs/x.md": "x\n"})  # fmt: skip
    assert at.affected(root, ["docs/x.md"], enqueue=True) == {}
    assert _files(at.affected(root, ["lib/other.py"], enqueue=True)) == {"tests/test_judged.py"}
    # outside the enqueue it judges every diff, as every tree reader does
    assert _files(at.affected(root, ["docs/x.md"], enqueue=False)) == {"tests/test_judged.py"}
    # TREE_READS is a join's declaration, never the walk's: the same walk with it still stands
    joined = JUDGED.replace("JUDGE_READS", "TREE_READS")
    root2 = _tree(tmp_path / "t2", {"tests/test_judged.py": joined, "docs/x.md": "x\n"})
    assert _files(at.affected(root2, ["docs/x.md"], enqueue=True)) == {"tests/test_judged.py"}


def test_the_enqueue_is_read_from_the_land_pre_step(tmp_path, monkeypatch) -> None:
    """lib/land_prestep.py runs land.sh's pre-step with GOH_LAND_PRE_ONLY=1: that is the enqueue."""
    root = _root(tmp_path)
    monkeypatch.setenv("GOH_LAND_PRE_ONLY", "1")
    assert at.affected(root, ["lib/other.py"]) == {}
    monkeypatch.delenv("GOH_LAND_PRE_ONLY")
    assert at.affected(root, ["lib/other.py"]) != {}


def test_the_real_enqueue_set_drops_known_readers_and_keeps_the_rest() -> None:
    full = _tree_graph.affected([])
    enqueue = _tree_graph.affected([], enqueue=True)
    assert set(enqueue) < set(full)
    for judge in (*at.ALWAYS, "tests/test_land_check.py"):  # a whole-checkout clone and its guards
        assert judge in _files(enqueue), judge
