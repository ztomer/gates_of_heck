"""No test file grows a new on-disk database or clone of goh (tests/disk_shapes.py)."""

from __future__ import annotations

import json

import disk_shapes
from conftest import REPO_ROOT

# goh-land-guard: test_the_tree_is_the_list
TREE_READS = ("tests/",)  # every .py there, and the list
LIST = REPO_ROOT / "tests" / "disk_shapes.json"
SEEDED = 47  # the seed's sites, only ever lowered


def _tree() -> dict[str, dict[str, int]]:
    out = {}
    for path in sorted((REPO_ROOT / "tests").glob("*.py")):
        if n := disk_shapes.counts(path.read_text(encoding="utf-8")):
            out[path.name] = n
    return out


def test_the_tree_is_the_list() -> None:
    lists = json.loads(LIST.read_text())
    assert disk_shapes.faults(_tree(), lists) == []
    assert sum(sum(per.values()) for per in lists["seed"].values()) <= SEEDED


def test_each_shape_is_seen_and_each_way_out_is_not() -> None:
    planted = (
        "import sqlite3, subprocess\n"
        "def test_a(tmp_path):\n"
        "    sqlite3.connect(tmp_path / 'x.db')\n"
        "    sqlite3.connect(f'{tmp_path.as_uri()}?mode=ro', uri=True)\n"
        "    subprocess.run(['git', 'clone', '-q', str(REPO_ROOT), 'd'])\n"
        "    _git(ROOT, 'worktree', 'add', 'd')\n"
        "    sqlite3.connect(':memory:')\n"
        "    sqlite3.connect('file:a?mode=memory&cache=shared', uri=True)\n"
        "    subprocess.run(['git', 'clone', '-q', str(tmp_path), 'd'])\n"
        "    _git(ROOT, 'status')\n"
        "def _build_x(tree):\n"
        "    subprocess.run(['git', 'clone', str(REPO_ROOT), str(tree)])\n"
    )
    assert disk_shapes.sites(planted) == [
        ("sqlite", 3), ("sqlite", 4), ("goh-clone", 5), ("goh-clone", 6)]  # fmt: skip


def test_a_new_site_is_refused_a_gone_one_is_stale_and_a_reason_is_asked() -> None:
    lists = {"seed": {"a.py": {"sqlite": 2}}, "allowed": {}}
    assert disk_shapes.faults({"a.py": {"sqlite": 2}}, lists) == []
    (new,) = disk_shapes.faults({"a.py": {"sqlite": 3}}, lists)
    assert "3 `sqlite` sites, 2 listed" in new
    (stale,) = disk_shapes.faults({}, lists)
    assert "lower the list" in stale
    said = {"seed": {}, "allowed": {"b.py": {"goh-clone": [1, "reads every file of the tree"]}}}
    assert disk_shapes.faults({"b.py": {"goh-clone": 1}}, said) == []
    said["allowed"]["b.py"]["goh-clone"][1] = "needed"
    assert "no reason" in disk_shapes.faults({"b.py": {"goh-clone": 1}}, said)[0]
