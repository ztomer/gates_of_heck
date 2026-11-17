"""lib/row_merge.py, the `goh-rows` merge driver (GOH.LANDUNION): two lanes that each add rows to
the same registries rebase onto each other cleanly; two edits to ONE row are still refused.

On 2026-11-17 three lands went red on registry rows alone (LANDREPORT against AFFECTEDBINARY2: two
adjacent docs/map.md bullets, each edited by one lane; a docs/map.md row and a subcommand). Each
test plays two lanes in a fixture repo: lane A lands on main, lane B rebases onto it as
tools/land.sh rebases.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT, commit_all, git, write

DRIVER = f"{sys.executable} {REPO_ROOT / 'lib' / 'row_merge.py'} %O %A %B %P"
WHO = ("-c", "user.name=t", "-c", "user.email=t@t")
ATTRS = (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8")

MAP = """\
# map

## Lib

| Script | Purpose | Test |
|---|---|---|
| `a.py` | A. | `test_a.py` |
| `b.py` | B. | `test_b.py` |
| (structural step) | `x.rs` | X. |

## Tools

* `tools/x.sh` -- X, pinned by `test_x.py`.
* `tools/y.sh` -- Y, pinned by `test_y.py`.
"""
CONFIG = """\
# config

| Key | Default | Meaning |
|---|---|---|
| `GOH_A` | unset | A. |
| `GOH_B` | 1 | B. |
"""
KEYS = "# never a verdict\nGOH_ONE\nGOH_TWO\n"
TABLE = "# name script about\nbudget\tlib/budget.py\tBudget\nland|landq\ttools/landq.py\tLand\n"
FILES = {
    "docs/map.md": MAP,
    "docs/config.md": CONFIG,
    "gates/verdict_free_keys.txt": KEYS,
    "crates/goh/house_scripts.tsv": TABLE,
}


Lane = dict[str, tuple[str, str]]


def _lanes(repo: Path, lane_a: Lane, lane_b: Lane, attrs: str = ATTRS) -> None:
    """main gets the base, then lane A's edits (it landed); branch `b` gets lane B's from the base.
    An edit is (old text, new text) in a file."""
    write(repo, ".gitattributes", attrs)
    for rel, text in FILES.items():
        write(repo, rel, text)
    commit_all(repo, "base")
    git(repo, "branch", "b")
    for branch, lane in (("main", lane_a), ("b", lane_b)):
        git(repo, "checkout", "-q", branch)
        for rel, (old, new) in lane.items():
            text = (repo / rel).read_text(encoding="utf-8")
            assert old in text, (rel, old)
            write(repo, rel, text.replace(old, new, 1))
        commit_all(repo, f"lane {branch}")


def _rebase(repo: Path, *driver: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *WHO, *driver, "rebase", "-q", "main"],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )


def _unmerged(repo: Path) -> list[str]:
    return git(repo, "diff", "--name-only", "--diff-filter=U").split()


A_ROWS = {
    "docs/map.md": (
        "| (structural step)",
        "| `c.py` | C, lane A. | `test_c.py` |\n| (structural step)",
    ),
    "docs/config.md": ("| `GOH_B` | 1 | B. |\n", "| `GOH_B` | 1 | B. |\n| `GOH_C` | 2 | C. |\n"),
    "gates/verdict_free_keys.txt": ("GOH_TWO\n", "GOH_TWO\nGOH_THREE\n"),
    "crates/goh/house_scripts.tsv": ("Budget\n", "Budget\ncapacity\tlib/capacity.py\tCapacity\n"),
}
B_ROWS = {
    "docs/map.md": (
        "| (structural step)",
        "| `d.py` | D, lane B. | `test_d.py` |\n| (structural step)",
    ),
    "docs/config.md": ("| `GOH_B` | 1 | B. |\n", "| `GOH_B` | 1 | B. |\n| `GOH_D` | 3 | D. |\n"),
    "gates/verdict_free_keys.txt": ("GOH_TWO\n", "GOH_TWO\nGOH_FOUR\n"),
    "crates/goh/house_scripts.tsv": ("Budget\n", "Budget\nwf-pin\tscripts/wf_pin.py\tPins\n"),
}


def test_two_lanes_adding_rows_conflict_as_text(repo: Path) -> None:
    """The instrument: with no driver defined, git merges the registries as text, and the two lanes'
    rows conflict in all four -- the red of 2026-11-17, reproduced."""
    _lanes(repo, A_ROWS, B_ROWS)
    out = _rebase(repo)
    assert out.returncode != 0
    assert sorted(_unmerged(repo)) == sorted(FILES)


def test_two_lanes_adding_rows_rebase_cleanly_by_row(repo: Path) -> None:
    _lanes(repo, A_ROWS, B_ROWS)
    out = _rebase(repo, "-c", f"merge.goh-rows.driver={DRIVER}")
    assert out.returncode == 0, out.stderr
    for rel in FILES:
        text = (repo / rel).read_text(encoding="utf-8")
        assert "<<<<<<<" not in text
        assert A_ROWS[rel][1].splitlines()[-2 if rel == "docs/map.md" else -1] in text
        assert B_ROWS[rel][1].splitlines()[-2 if rel == "docs/map.md" else -1] in text
    check = subprocess.run(
        [sys.executable, str(REPO_ROOT / "lib" / "row_merge.py"), "--check", *FILES],
        cwd=repo,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert check.returncode == 0, check.stderr
    rows = (repo / "docs/map.md").read_text(encoding="utf-8")
    assert max(rows.index("`c.py`"), rows.index("`d.py`")) < rows.index("(structural step)")


def test_adjacent_rows_edited_by_each_lane_merge(repo: Path) -> None:
    """LANDREPORT against AFFECTEDBINARY2: each lane edited one of two adjacent bullets. A union
    merge keeps four bullets (each edit beside the other side's stale copy); by row it keeps two."""
    lane_a = {
        "docs/map.md": ("X, pinned by `test_x.py`.", "X, pinned by `test_x.py`, `test_x2.py`.")
    }
    lane_b = {
        "docs/map.md": ("Y, pinned by `test_y.py`.", "Y, pinned by `test_y.py`, `test_y2.py`.")
    }
    _lanes(repo, lane_a, lane_b)
    assert _rebase(repo).returncode != 0, "the fixture must conflict as text"
    git(repo, "rebase", "--abort")
    out = _rebase(repo, "-c", f"merge.goh-rows.driver={DRIVER}")
    assert out.returncode == 0, out.stderr
    text = (repo / "docs/map.md").read_text(encoding="utf-8")
    assert text.endswith(
        "* `tools/x.sh` -- X, pinned by `test_x.py`, `test_x2.py`.\n"
        "* `tools/y.sh` -- Y, pinned by `test_y.py`, `test_y2.py`.\n"
    )


def test_two_edits_to_one_row_still_conflict(repo: Path) -> None:
    """Calibration: the driver never picks a side or keeps both. Two different edits to the `a.py`
    row (and to GOH_ONE, which one lane deletes) stop the rebase with git's markers."""
    lane_a = {
        "docs/map.md": ("| A. |", "| A, by lane A. |"),
        "gates/verdict_free_keys.txt": ("GOH_ONE\n", ""),
    }
    lane_b = {
        "docs/map.md": ("| A. |", "| A, by lane B. |"),
        "gates/verdict_free_keys.txt": ("GOH_ONE\n", "GOH_ONE  # kept\n"),
    }
    _lanes(repo, lane_a, lane_b)
    out = _rebase(repo, "-c", f"merge.goh-rows.driver={DRIVER}")
    assert out.returncode != 0
    assert sorted(_unmerged(repo)) == ["docs/map.md", "gates/verdict_free_keys.txt"]
    text = (repo / "docs/map.md").read_text(encoding="utf-8")
    assert "<<<<<<< ours:docs/map.md" in text
    assert "A, by lane A." in text and "A, by lane B." in text


def test_a_union_merge_of_one_row_is_caught_by_the_check(repo: Path) -> None:
    """Why not git's `merge=union`: it merges two edits to one row SILENTLY, keeping both. The
    registries' own check (`--check`, tests/test_registry_rows.py) names the key it left twice."""
    lane_a = {"docs/config.md": ("| `GOH_B` | 1 |", "| `GOH_B` | 2 |")}
    lane_b = {"docs/config.md": ("| `GOH_B` | 1 |", "| `GOH_B` | 3 |")}
    _lanes(repo, lane_a, lane_b, attrs="docs/config.md merge=union\n")
    assert _rebase(repo).returncode == 0
    check = subprocess.run(
        [sys.executable, str(REPO_ROOT / "lib" / "row_merge.py"), "--check", "docs/config.md"],
        cwd=repo,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert check.returncode == 1
    assert "docs/config.md: the key `GOH_B` is on two lines" in check.stderr
