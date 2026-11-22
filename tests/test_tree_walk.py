"""lib/tree_walk.py: a helper's walk is of the root or of what it is handed (GOH.CLONENOTJUDGE).

Every file importing tests/_tree_clone.py was a tree reader (a judge, run on every land), because
_tree_clone walks the template it builds: ~455 s serial on every pre-step (GOH.LANDREVIEW). Here:
handing a walking helper a fixture is no tree read, handing it the root is, and a helper walking
the checkout itself still makes each user of it a reader.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "tools"))
import affected_tests as at

from lib import tree_walk

CLONER = """\
import os
from pathlib import Path

def scrub(tree):
    for here, _d, _f in os.walk(tree):
        pass

def build(key, dst):
    scrub(dst)
"""


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    return tmp_path


def _files(chosen: dict[str, str]) -> set[str]:
    return {k.split("::")[0] for k in chosen}


def test_a_helper_walking_only_its_argument_walks_what_it_is_given() -> None:
    assert tree_walk.walks(CLONER) == {
        "scrub": tree_walk.Given(("tree",), frozenset({"tree"})),
        "build": tree_walk.Given(("key", "dst"), frozenset({"dst"})),  # `key` is no walked path
    }


@pytest.mark.parametrize(
    "use",
    [
        "build('k', tmp_path / 'x')",  # a fixture of its own
        "scrub(tmp_path)",
    ],
)
def test_handing_a_walking_helper_a_fixture_is_no_tree_read(tmp_path, use) -> None:
    root = _tree(
        tmp_path,
        {
            "tests/_cloner.py": CLONER,
            "tests/test_c.py": f"from _cloner import build, scrub\n\ndef test_a(tmp_path):\n"
            f"    {use}\n",
        },
    )
    assert at.tree_readers(root) == {}
    assert at.affected(root, []) == {}


@pytest.mark.parametrize(
    "use",
    [
        "scrub(REPO_ROOT / 'tests')",
        "scrub('.')",
        "t = REPO_ROOT / 'lib'\n    scrub(t)",  # a name bound from the root
    ],
)
def test_handing_a_walking_helper_the_root_is_a_tree_read(tmp_path, use) -> None:
    root = _tree(
        tmp_path,
        {
            "tests/_cloner.py": CLONER,
            "tests/test_c.py": f"from _cloner import scrub\n\ndef test_a():\n    {use}\n\n"
            "def test_b():\n    pass\n",
        },
    )
    assert "tests/test_c.py" in at.tree_readers(root)
    assert list(at.affected(root, [])) == ["tests/test_c.py::test_a"]


@pytest.mark.parametrize(
    "helper",
    [
        "import os\ndef files():\n    return os.walk('.')\n",  # no parameter: the cwd
        "import os\ndef files(top):\n    return os.walk(REPO_ROOT / top)\n",
        "import subprocess\ndef files(x):\n    return subprocess.run(['git', 'ls-files'])\n",
        "import os\ndef _w(t):\n    os.walk(t)\ndef files():\n    _w(REPO_ROOT)\n",
    ],
)
def test_a_helper_walking_the_checkout_makes_its_user_a_reader(tmp_path, helper) -> None:
    root = _tree(
        tmp_path,
        {"tests/_walker.py": helper, "tests/test_r.py": "from _walker import files\n\n"
         "def test_a():\n    files()\n"},
    )  # fmt: skip
    assert "tests/test_r.py" in at.tree_readers(root)


def test_the_tree_clone_importers_are_judges_only_by_their_own_reads() -> None:
    """On the real tree: no file is a tree reader for importing _tree_clone, and the readers of a
    root-handed lint helper (tests/wall_clock_lint.py's census) still are."""
    readers = at.tree_readers(REPO_ROOT)
    assert [r for r, why in readers.items() if "_tree_clone" in why] == []
    assert "tests/test_wall_clock_ratchet.py" in readers
    nodes = at.affected(REPO_ROOT, [])
    assert "tests/test_proven.py" not in _files(nodes)  # it copies gates/ lib/: reads those


def test_a_star_import_brings_each_walking_def_under_its_own_name(tmp_path) -> None:
    root = _tree(
        tmp_path,
        {
            "tests/_cloner.py": CLONER,
            "tests/test_c.py": "from _cloner import *\n\ndef test_a():\n    scrub(REPO_ROOT)\n\n"
            "def test_b(tmp_path):\n    scrub(tmp_path)\n",
        },
    )
    assert list(at.affected(root, [])) == ["tests/test_c.py::test_a"]
