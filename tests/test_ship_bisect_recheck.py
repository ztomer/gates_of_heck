"""A ship's bisect names a culprit only when its failing tests are red AT it (GOH.BISECTRECHECK).

The v0.150.0 ship (2026-11-22, record 20261122T200020Z-10f2e996) went red on one test, its
log naming it at four spaces' indent (`    FAILED tests/test_fs_churn.py::...`). The bisect read
no failing test from that log (its pattern wanted FAILED at column 0), so it ran full suites at
two earlier lands, both green, and blamed the tip without running anything at it: the test passed
4 of 4 there afterwards, and the ship failed on a flake. Now:

  * the failing tests are read at any indent;
  * before a culprit is named its failing tests run again at it and at its parent land; a test
    green there is a flake, recorded as one (`flake`, no fault filed), and the ship runs its suite
    step once more instead of failing.

The measurer is the DUMMY of tests/test_ship_red_cause.py: the fixture's own coverage gate.
"""

from __future__ import annotations

import sys
from pathlib import Path

import test_ship_cov_bisect
from conftest import REPO_ROOT, commit_all, write
from test_ship import _calls
from test_ship_cov_bisect import _LOG, _RECORD, _ship
from test_ship_red_cause import MARK_TEST, _land
from test_ship_starve import _declare, _judged

sys.path.insert(0, str(REPO_ROOT / "lib"))
import ship_red_cause

shipped = test_ship_cov_bisect.shipped  # the fixture, by assignment

# Red on its FIRST full run of a tree with `cov.flaky`, naming test_mark the way tools/pytest.sh
# prints it (indented); every later run of that tree is green -- a load flake, as on 2026-11-22.
FLAKY = (
    _LOG
    + """
if [ -e cov.flaky ] && [ "$(grep -c "$(git rev-parse HEAD)" "$STAND_IN_LOG")" = 1 ]; then
    echo "    FAILED tests/t_mark.py::test_mark - AssertionError"; exit 1
fi
if [ -e cov.fail ]; then echo "    FAILED tests/t_mark.py::test_mark - AssertionError"; exit 1; fi
"""
    + _RECORD
)


def _declare_flaky(repo: Path) -> None:
    _declare(repo)
    write(repo, "gates/coverage_gate.sh", FLAKY.format(py=sys.executable, lib=REPO_ROOT / "lib"))
    write(repo, "tests/t_mark.py", MARK_TEST)
    commit_all(repo, "chore: the flaky dummy measurer")


def test_the_failing_tests_are_read_at_any_indent():
    log = (
        "    FAILED tests/test_fs_churn.py::test_a_planted_churny_test - AssertionError\n"
        "FAILED tests/t.py::test_b[x]\n"
        "    ERROR tests/test_gate_temp_leaks.py::test_structural - setup took 90.8 s\n"
        "    FAILED tests/t.py::test_b[x]\n"
    )
    assert ship_red_cause.failed_tests(log) == [
        "tests/test_fs_churn.py::test_a_planted_churny_test",
        "tests/t.py::test_b[x]",
        "tests/test_gate_temp_leaks.py::test_structural",
    ]


def test_a_tip_green_at_its_own_failing_tests_is_a_flake_and_the_ship_retries(
    shipped: Path, tmp_path: Path
) -> None:
    _declare_flaky(shipped)
    _land(shipped, {"land0.txt": "0\n"}, "fix: land 0")
    _land(shipped, {"land1.txt": "1\n"}, "fix: land 1")
    tip = _land(shipped, {"cov.flaky": "1\n"}, "fix: the tip, red once under load")
    code, out, res = _ship(shipped, tmp_path)
    b = res["bisect"]
    assert b["tests"] == ["tests/t_mark.py::test_mark"], b  # read from the indented line
    assert b["flake"] and b["culprit"] == "" and b["candidate"] == tip, b
    assert b["confirm"] == [{"sha": tip, "verdict": "green"}], b  # re-run AT the candidate
    assert b["fault"].startswith("not filed: a flake"), b
    assert not [c for c in _calls(tmp_path) if "file" in c], "a flake filed a fault item"
    assert _judged(tmp_path) == [tip, tip], "the suite step was not retried once"
    assert res["retried"] == 1 and code == 0 and res["status"] == "ok", out


def test_a_culprit_red_at_its_tests_and_green_at_its_parent_is_still_named(
    shipped: Path, tmp_path: Path
) -> None:
    _declare_flaky(shipped)
    first = _land(shipped, {"land0.txt": "0\n"}, "fix: land 0")
    culprit = _land(shipped, {"cov.fail": "1\n"}, "fix: the land that breaks test_mark")
    _land(shipped, {"land2.txt": "2\n"}, "fix: land 2")
    code, _out, res = _ship(shipped, tmp_path)
    b = res["bisect"]
    assert code == 1 and b["culprit"] == culprit and not b.get("flake"), b
    assert b["confirm"] == [
        {"sha": culprit, "verdict": "red"},
        {"sha": first, "verdict": "green"},
    ], b
    assert "retried" not in res and res["status"] == "failed", res
