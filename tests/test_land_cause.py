"""lib/land_cause.py: each red land's cause, read from the lines real logs print (GOH.PREPARITY).

The snippets are cut from the 2026-11-17 queue logs (~/.local/state/goh/landq/logs), one per
cause, so a change to what land.sh or the gate prints that the classifier no longer reads goes red
here, not as a silent `other` in `goh land --report`.
"""

from __future__ import annotations

import sys

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import land_cause as lc

# Log text, never a run: a gate's path spelled whole reads to the test selector as one this test
# runs (tools/affected_tests.py), so the step names are joined here.
RUST, STRUCT = "./gates/" + "rust_gate.sh", "./gates/" + "structural.sh"
PRE_RED = "✗ land.sh: the pre-land tests failed on 9f3815f6 in 136 s, before the land lock\n"
GATE_RED = f"✓ pre-land green in 197 s\n✗ [1/9] FAILED (exit status 1): {RUST}\n"

CASES = [
    ({"why": "tip moved after enqueue"}, "", "tip-moved"),
    ({}, "✗ land.sh: GOH.X conflicts with main 93496d92 -- the rebase was aborted", "conflict"),
    ({}, "✗ release canary: REFUSED -- fix or seed each consumer above", "canary"),
    ({}, "..load: deadline of 600s expired: the command\n" + PRE_RED, "deadline"),
    ({}, "✗ land.sh: the gate never ran on 9f3815f6: waited 900 s for a gate slot", "no-verdict"),
    (
        {},
        GATE_RED + f"✗ [2/9] FAILED (exit status 1): {STRUCT} --full\n"
        "✗ structural: file length <= 500 failed (native)\n",
        "structural",
    ),
    (
        {},
        "FAILED tests/test_suite_drift.py::test_no_new_pool_sized_to_the_machine - Ass...\n"
        "FAILED tests/test_self_walk.py::test_goh_obeys_every_scanner@self-walk\n" + PRE_RED,
        "ratchet",
    ),
    (
        {},
        "FAILED tests/test_rust_cov_reuse.py::test_a_tree_whose_rust_is_unchanged\n" + PRE_RED,
        "pre-test",
    ),
    (
        {},
        GATE_RED + "29:FAILED tests/test_rust_gate.py::test_clean_crate_both_pass - Assert\n",
        "gate-test",
    ),
    (
        {},
        GATE_RED + "41:    FAILED tests/test_ps_table_unbounded.py::test_a_ps_slow\n",
        "gate-test",
    ),
    ({"why": "exit 1"}, "", "other"),
]


@pytest.mark.parametrize(("rec", "log", "cause"), CASES, ids=[c[2] + str(i) for i, c in
                                                              enumerate(CASES)])  # fmt: skip
def test_each_cause_is_read_from_the_lines_a_real_log_prints(rec, log, cause) -> None:
    assert lc.classify(rec, log) == cause


def test_every_cause_has_a_case_and_every_case_a_cause() -> None:
    assert {c for *_, c in CASES} == set(lc.CAUSES)


def test_a_failed_node_is_named_once_wherever_the_log_indents_it() -> None:
    log = "FAILED tests/test_a.py::t - x\n29:FAILED tests/test_a.py::t - x\n  FAILED tests/b.py\n"
    assert lc.failed_tests(log) == ["tests/test_a.py::t", "tests/b.py"]


def test_the_excerpt_keeps_each_failing_sections_last_lines_and_the_failed_lines():
    """GOH.PRESTEPLOG: what a pre-step red record keeps, bounded per test and in tests."""
    sections = "".join(
        f"{'_' * 5} test_{n} {'_' * 5}\n" + "".join(f"  line {n}.{i}\n" for i in range(5))
        for n in range(4)
    )
    log = f"noise\n{'=' * 5} FAILURES {'=' * 5}\n{sections}{'=' * 5} summary {'=' * 5}\n"
    log += "FAILED tests/test_a.py::test_0 - x\nERROR tests/test_a.py::test_9 - y\n"
    got = lc.excerpt(log, each=2, most=3).splitlines()
    assert got[:4] == [
        "_____ test_0 _____",
        "    ... 3 lines above cut",
        "  line 0.3",
        "  line 0.4",
    ]
    assert "test_3" not in " ".join(got[:-3]) and "1 more failing" in got[-3], got
    assert got[-2:] == ["FAILED tests/test_a.py::test_0 - x", "ERROR tests/test_a.py::test_9 - y"]
    assert "noise" not in got and lc.excerpt("nothing failed\n") == ""
