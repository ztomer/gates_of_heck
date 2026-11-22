"""No test every enqueue runs is longer than 30 s, or says why (GOH.PRELONGPOLE, lib/long_pole.py).

The enqueue's pre-step cannot finish before its longest test does, at any worker count: on
2026-11-21 tests/test_self_walk.py (147 s) and tests/test_land_check.py (98 s) ran at every
enqueue, so no land's pre-step took under 2.5 minutes. The standing set is what the selector
chooses for an empty diff; the judge here is the ratchet over it, calibrated on synthetic tables
(each way to go red, and the stale ids of the duration table, which prunes by file only), then run
over the real tree against the suite's own table. A machine that has measured nothing (a hosted
runner) has nothing to judge: the table is local state, written by every top-level suite run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import _schedule
import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import long_pole as lp

SLOW = "tests/test_slow.py::test_a_walk"


def _tree(tmp_path: Path) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_t.py").write_text(
        "def test_a(): pass\n\n\ndef test_p(x): pass\n\n\n"
        "class TestK:\n    def test_m(self): pass\n"
    )
    return tmp_path


def test_a_test_over_the_limit_is_red_and_one_under_it_is_green() -> None:
    assert lp.judge({SLOW: 29.9}, {}) == []
    (red,) = lp.judge({SLOW: 30.1}, {})
    assert SLOW in red and "30.1 s over 30 s" in red and "declare its reads" in red


def test_an_allowance_holds_its_file_to_its_own_limit() -> None:
    allow = {"tests/test_slow.py": {"limit_s": 150, "why": "two walks"}}
    assert lp.judge({SLOW: 100.0}, allow) == []
    assert len(lp.judge({SLOW: 150.5}, allow)) == 1, "an allowance is a ceiling, not a licence"
    assert len(lp.judge({SLOW: 100.0, "tests/test_other.py::test_b": 31.0}, allow)) == 1


def test_an_allowance_that_no_longer_earns_its_place_is_red() -> None:
    allow = {"tests/test_slow.py": {"limit_s": 150, "why": "two walks"}}
    (red,) = lp.judge({SLOW: 12.0}, allow)
    assert "every test is under 15 s now: delete the entry" in red
    # a last run's number is under that run's load: 20 s is no proof the file was cut
    assert lp.judge({SLOW: 20.0}, allow) == []
    (red,) = lp.judge({"tests/test_fast.py::test_c": 1.0}, allow)
    assert "no longer runs it: delete the entry" in red


def test_an_allowance_with_no_why_or_no_room_is_red() -> None:
    reds = lp.judge({SLOW: 40.0}, {"tests/test_slow.py": {"limit_s": 60}})
    assert reds == ["tests/test_slow.py: the allowance carries no why"]
    reds = lp.judge({SLOW: 40.0}, {"tests/test_slow.py": {"limit_s": 30, "why": "w"}})
    assert any("no allowance over 30 s" in r for r in reds), reds


def test_the_cases_of_a_parametrized_test_are_judged_one_by_one() -> None:
    cases = {f"tests/test_p.py::test_x[{n}]": 20.0 for n in range(4)}  # 80 s of cases, 20 s each
    assert lp.judge(cases, {}) == []


def test_measured_keeps_what_the_standing_set_runs_and_the_tree_defines(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    table = {
        "tests/test_t.py::test_a": 4.0,
        "tests/test_t.py::test_gone": 99.0,  # a function the file no longer defines
        "tests/test_t.py::test_p": 80.0,  # from before the test was parametrized
        "tests/test_t.py::test_p[1]": 5.0,
        "tests/test_t.py::test_p[2]": 6.0,
        "tests/test_t.py::TestK::test_m": 2.0,
        "tests/test_t.py::test_a@grp": 3.0,  # an xdist group's suffix is the same test
        "tests/test_other.py::test_z": 50.0,  # not in the standing set
        "tests/test_deleted.py::test_y": 50.0,  # a file that is gone
    }
    whole = lp.measured(root, ["tests/test_t.py", "tests/test_deleted.py"], table)
    assert whole == {
        "tests/test_t.py::test_a": 4.0,
        "tests/test_t.py::test_p[1]": 5.0,
        "tests/test_t.py::test_p[2]": 6.0,
        "tests/test_t.py::TestK::test_m": 2.0,
        "tests/test_t.py::test_a@grp": 3.0,
    }
    nodes = lp.measured(root, ["tests/test_t.py::test_p", "tests/test_t.py::TestK::test_m"], table)
    assert set(nodes) == {"tests/test_t.py::test_p[1]", "tests/test_t.py::test_p[2]",
                          "tests/test_t.py::TestK::test_m"}  # fmt: skip


def test_the_report_names_the_longest_test_and_the_serial_sum() -> None:
    lines = lp.report({"a.py::t": 10.0, "b.py::t": 45.0, "c.py::t": 5.0})
    assert lines[0] == "standing set: 3 measured cases, serial sum 60 s"
    assert lines[1] == "longest single test: 45.0 s  b.py::t"
    assert lines[2:] == ["  over 30 s: 45.0 s  b.py::t"]
    assert lp.report({}) == ["no measured test in the standing set"]


def test_the_allowance_file_is_well_formed() -> None:
    allow = lp.load_allow()
    assert all(isinstance(e.get("limit_s"), int | float) for e in allow.values())
    assert list(allow) == sorted(allow)
    assert set(json.loads(lp.ALLOW.read_text())) == set(allow)


def test_no_test_every_enqueue_runs_takes_longer_than_30_s_but_the_allowed() -> None:
    """The real tree: the standing set (the selector's, on an empty diff) against the suite's own
    duration table. Red here is a test that every land's pre-step now waits for."""
    # The table this run read at its start (tests/_schedule.py): tools/pytest.sh hands its path to
    # the top-level run alone, and a test's own HOME holds none.
    table = _schedule._read.get(_schedule._KEY) or {}
    if not table:
        pytest.skip("no duration table on this machine: a suite run writes it (tools/pytest.sh)")
    reds = lp.check(table=table)
    assert reds == [], "\n".join(reds)
