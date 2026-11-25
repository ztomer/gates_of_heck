"""The modules that read the live loop checkout are never reused (lib/live_reads.py,
GOH.REUSEOFFTEST)."""

from __future__ import annotations

import sys

import _land_audit
import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import live_reads

sys.path.insert(0, str(REPO_ROOT / "tools"))
import affected_tests as at


def test_live_is_the_modules_whose_code_reads_the_live_loop() -> None:
    """A module added (or gone) without LIVE is red here, so no pass of one is reused unnamed."""
    found = {
        f"tests/{p.name}"
        for p in (REPO_ROOT / "tests").glob("test_*.py")
        if live_reads.reads_live(p.read_text(encoding="utf-8"))
    }
    assert found == set(live_reads.LIVE)


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ('import suite_home\nL = suite_home.REAL_HOME\nP = L / ".loop"\n', True),
        ('from suite_home import REAL_HOME\nP = REAL_HOME / ".loop"\n', True),
        ("import suite_home\nL = suite_home.REAL_HOME\n", False),
        ('"""suite_home.REAL_HOME and .loop"""\n', False),
        ('P = "x/.loop"\n', False),
    ],
)
def test_a_module_that_reads_the_live_loop_is_told_from_one_that_names_it(text, want) -> None:
    assert live_reads.reads_live(text) is want


def test_no_same_tree_pass_of_a_live_module_is_reused(monkeypatch) -> None:
    """The calibration: every use answers `reuse`, and the live node runs anyway."""

    class Audit:
        def use_many(self, path, fields):
            asked.extend(f["node"] for f in fields)
            return ["reuse"] * len(fields)

    class Store:
        def __enter__(self):
            return None, Audit()

        def __exit__(self, *a):
            return False

    asked: list[str] = []
    live, dead = f"{live_reads.LIVE[0]}::test_a", "tests/test_x.py::test_b"
    plan: dict = {"lines": [], "shadows": []}
    assert _land_audit.audited(plan, {live, dead}, "ID", ".", Store) == {dead}
    assert asked == [dead], "a live node is no use of the path"
    assert any("live loop" in line for line in plan["lines"]), plan


def test_a_live_module_is_chosen_whole_on_every_diff() -> None:
    assert set(live_reads.LIVE) <= set(at.WHOLE)
