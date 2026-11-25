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
    ({"why": "exit 1"}, "", "unclassified"),
    # GOH.LANDEREXIT1: a shell's 128+N is a signal's exit (GOH.ENQUEUEVANISH's `exit 143`).
    ({"why": "exit 143"}, "", "killed"),
    # GOH.REDOTHER: the 2026-11-20..23 reds that fell to `other`, each cut from its real record.
    (
        {
            "why": "enqueue pre-step: the pre-land tests failed on 9d97162d (failing: "
            "tests/test_long_pole.py::test_no_test_every_enqueue_runs_takes_longer, "
            "tests/test_self_walk.py::test_goh_obeys_every_scanner[shell]) -- fix them"
        },
        "",
        "ratchet",
    ),
    (
        {
            "why": "enqueue pre-step: the pre-land tests failed on 2cebda6b (failing: "
            "tests/test_suite_shard.py::test_a_test_gives_the_same_verdict) -- fix them"
        },
        "",
        "pre-test",
    ),
    (
        {
            "why": "check: tests/test_cap_headroom.py::test_no_new_file_crosses_the_headroom_line "
            "refused 32408bb2 (and 1 more)"
        },
        (
            "check: guard tests 2/3: REFUSED in 19.1 s (exit 1): "
            "tests/test_cap_headroom.py::test_no_new_file_crosses_the_headroom_line, "
            "tests/test_suite_drift.py::test_a_plain_test\n"
        ),
        "pre-test",
    ),
    (
        {"why": "check: [saved_pid_kill] refused e6e1eb54 (and 2 more)"},
        'check: "$GOH_PY" ./checks/check_saved_pid_kill.py .: REFUSED in 6.0 s (exit 1): [x]\n',
        "structural",
    ),
    (
        {
            "why": "enqueue pre-step: the pre-land tests failed on 5692de9f (exit -15, its output "
            "is /tmp/none.log) -- fix them"
        },
        "",
        "killed",
    ),
    (
        {
            "why": "enqueue pre-step: the pre-land tests failed on b8be8966 (exit 1, see the "
            "output above) -- fix them"
        },
        "the pre-land tests failed on b8be8966 (exit 1, see the output above) -- fix them\n",
        "pre-test",
    ),
    (
        {
            "why": "enqueue pre-step: [diff-coverage] 2 of 328 changed executable lines uncovered "
            "on c8f73167 (the pre-step's diff coverage judge) -- cover each line it names"
        },
        "",
        "diff-cov",
    ),
    (
        {"why": "exit 1"},
        "✗ land.sh: main is at e7bf6d43 after the merge, not the gated e7bf6d43 -- nothing landed",
        "main-moved",
    ),
    (
        {"why": "exit 1"},
        (
            "39:FAILED tests/test_filer_live_rules.py::test_a_red_ship - AssertionError\n"
            "✗ land.sh: the gate failed on da8fef63 under main c58986b0's gates -- nothing landed\n"
        ),
        "gate-test",
    ),
    (
        {"why": "exit 1"},
        (
            "✗ [1/10] FAILED (exit status 1): ./gates/rust_gate.sh\n"
            "✗ rust: clippy (-D warnings) failed (exit status 101; command: cargo clippy)\n"
            "✗ land.sh: the gate failed on 43919c8a under main 499c3572's gates -- nothing landed\n"
        ),
        "gate-step",
    ),
    (
        {"why": "exit 1"},
        (
            "  ✗ structural: file length <= 500 failed (native)\n"
            'check: "$GOH_PY" ./checks/check_x.py: green in 1 s\n'
        ),
        "structural",
    ),
]


@pytest.mark.parametrize(("rec", "log", "cause"), CASES, ids=[c[2] + str(i) for i, c in
                                                              enumerate(CASES)])  # fmt: skip
def test_each_cause_is_read_from_the_lines_a_real_log_prints(rec, log, cause) -> None:
    assert lc.classify(rec, log) == cause


def test_a_pre_steps_own_log_is_read_when_the_record_only_points_at_it(tmp_path) -> None:
    """`exit 1, its output is PATH`: the failing nodes are in that log, not in the record."""
    path = tmp_path / "prestep.log"
    why = f"enqueue pre-step: the pre-land tests failed on d3dee048 (exit 1, its output is {path})"
    path.write_text("    FAILED tests/test_a.py::test_every_x - E\nERROR tests/test_b.py::t - E\n")
    assert lc.classify({"why": why}, "") == "pre-test"  # test_b's ERROR is not a ratchet
    path.write_text("    FAILED tests/test_a.py::test_every_x - E\n")
    assert lc.classify({"why": why}, "") == "ratchet"
    path.unlink()
    assert lc.classify({"why": why}, "") == "pre-test"  # the pre-land tests failed: said, unnamed


OWN = "enqueue pre-step: the pre-land tests failed on d3dee048 (exit 1, its output is {}) -- fix"
UNCOVERED = "    ✗ [diff-coverage] 1 of 1 changed executable line uncovered (base 1):\n"
JUDGE = "✗ file events (GOH.FSCHURN2): 255137446 bytes left written by a run of 3351\n"
TAIL = "✗ land.sh: the pre-land tests failed on d3dee048 in 9 s -- nothing landed\n"


@pytest.mark.parametrize(
    ("own", "cause"),
    [
        (UNCOVERED, "diff-cov"),
        ("✗ [diff-coverage] 1 allowlist entry stale, delete:\n", "diff-cov"),
        ("load: deadline of 600s expired: the command\n", "deadline"),
        ("✗ structural: file length <= 500 failed (native)\n", "structural"),
        ("✗ land.sh: GOH.X conflicts with main 93496d92 -- the rebase was aborted\n", "conflict"),
        ("  ✗ phase 'external: x' failed\n✗ external: x\n" + JUDGE, "gate-step"),
        ("✗ land.sh: the pre-land tests were green on d3 but wrote no record\n", "gate-step"),
        ("bringing up nodes...\n", "pre-test"),
        # what a failing TEST printed is no class of its land's red
        ("_____ test_x _____\nE  assert '" + UNCOVERED + "'\nload: deadline of 9s expired\n"
         "✗ structural: x failed\n✗ land.sh: GOH.X conflicts with main 1\n=== short test ===\n"
         "FAILED tests/test_a.py::test_b - E\n", "pre-test"),
    ],
    ids=["diff-cov", "stale-allow", "deadline", "structural", "conflict", "judge", "no-record",
         "silent", "quoted"],
)  # fmt: skip
def test_a_pre_step_red_is_classed_by_its_own_log(tmp_path, own: str, cause: str) -> None:
    """GOH.DIFFCOVPARSE: the record holds the `why` and an excerpt; the pre-step's log says why. The
    6 of 317 reds so misread said `pre-test` with no test failed: the file-events ceiling, a verdict
    record never written."""
    path = tmp_path / "prestep.log"
    path.write_text(own + TAIL)
    rec = {"why": OWN.format(path)}
    assert lc.classify(rec, "") == cause
    if own.endswith(JUDGE):
        assert lc.detail("", rec) == "pre-land: " + JUDGE.strip()


def test_a_test_that_quotes_a_refusal_never_classes_the_land() -> None:
    """The record's excerpt holds each failing test's section, so a test OF land.sh that failed on
    `conflicts with main` or `(exit -15)` read as the land's own."""
    for quoted in ("conflicts with main", "the gate never ran", "(exit -15)",
                   "✗ structural: x failed", "release canary: REFUSED"):  # fmt: skip
        log = f"{PRE_RED}____ test_b ____\nE  {quoted}\n{quoted}\nFAILED tests/a.py::test_b - E\n"
        assert lc.classify({"why": "exit 1"}, log) == "pre-test", quoted


def test_a_gate_step_red_names_its_step_and_the_first_line_that_says_why() -> None:
    log = (
        "✗ [5/10] printed 1 ✗ line above and exited 0\n"
        "✗ [1/10] FAILED (exit status 1): ./gates/rust_gate.sh\n"
        "47:✗ rust: clippy (-D warnings) failed (exit status 101; command: cargo clippy)\n"
    )
    assert lc.detail(log) == (
        "./gates/rust_gate.sh: ✗ rust: clippy (-D warnings) failed (exit status 101; "
        "command: cargo clippy)"
    )
    assert lc.detail("nothing of a step\n") == ""


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


def test_a_param_id_with_spaces_is_named_whole() -> None:
    """GOH.REMOTEREDSILENT: `[some other source]` was cut at the space, `...taken[some` joined a
    pre-step's set as main's red, and no xdist worker found it. A list that already cut it (an
    unclosed bracket) gives the test's base name, which pytest finds."""
    log = (
        "FAILED tests/t.py::test_x[some other source] - AssertionError: [a b]\n"
        "FAILED tests/t.py::C::test_y[a-b]\n    FAILED tests/u.py::test_z[some\n"
    )
    assert lc.failed_tests(log) == [
        "tests/t.py::test_x[some other source]",
        "tests/t.py::C::test_y[a-b]",
        "tests/u.py::test_z",
    ]


def test_a_blank_cross_line_is_never_a_steps_why() -> None:
    """GOH.LANDEREXIT1: a step that died in a traceback leaves `✗ ` lines with nothing after them
    in the digest (GOH.STATEDBS4's canary); the step is named alone, never as `step: ✗ `."""
    step = '"$GOH_PY" ./scripts/release_canary.py'
    log = f"✗ [10/10] FAILED (exit status 1): {step}\n76:✗ 1 of 10 steps failed:\n77:✗ \n"
    assert lc.detail(log) == step


@pytest.mark.parametrize(
    ("why", "log", "cause", "said"),
    [
        ("exit 1", GATE_RED + "29:FAILED tests/test_a.py::test_b - Assert\n", "gate-test",
         "exit 1: gate-test, tests/test_a.py::test_b"),
        ("exit 1", "✗ [1/9] FAILED (exit status 1): ./gates/x.sh\n✗ x: clippy failed\n",
         "gate-step", "exit 1: gate-step, ./gates/x.sh: ✗ x: clippy failed"),
        ("exit 1", "lander: the worktree is gone\n\n", "lander",
         "exit 1: lander, lander: the worktree is gone"),
        ("exit 143", "", "killed", "exit 143: killed"),
        ("timed out after 3600s", "", "lander", "timed out after 3600s: lander"),
    ],
    ids=["gate-test", "gate-step", "lander", "killed", "timeout"],
)  # fmt: skip
def test_a_bare_exit_red_is_named_by_its_cause(tmp_path, why, log, cause, said) -> None:
    """GOH.LANDEREXIT1: 91 red records of 2026-11-17..24 said only `exit 1`. The record names its
    cause class and what says so; a red nothing reads is the lander's own (`lander`), never none."""
    path = tmp_path / "land.log"
    path.write_text(log)
    got = lc.explain({"state": "red", "why": why, "log": str(path)})
    assert (got["cause"], got["why"]) == (cause, said)
    assert got["cause"] in lc.NAMED and "unclassified" not in lc.NAMED


def test_a_named_why_is_kept_and_a_landed_record_has_no_cause(tmp_path) -> None:
    why = "conflict: did not stack on 8484d05e"
    assert lc.explain({"state": "red", "why": why, "log": str(tmp_path / "none")}) == {
        "cause": "conflict", "cause_detail": ""}  # fmt: skip
    assert lc.classify({"why": "HEAD moved"}, "") == "tip-moved"  # a ship's enqueued sha moved
    assert lc.classify({"why": "HEAD moved: main is at 1"}, "") == "tip-moved"
    assert lc.explain({"state": "landed", "why": "exit 1"}) == {}
