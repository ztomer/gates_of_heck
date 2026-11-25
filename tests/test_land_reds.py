"""lib/land_reds.py: every red land record's failing tests and cause class (GOH.REDREVIEW).

The fixtures are the 2026-11-22 reds (~/.local/state/goh/landq/done and logs/): the record's
`why`, the log lines the classifier reads, and the failing test's source as it stood at the red
commit. A change to what land.sh, the pre-step or pytest prints that the rules no longer read goes
red here, not as a silent `other` in `goh land-reds`.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import _goh_db_fixture as gdb
import land_records
import land_reds as lr
from timing_bounds import patience

PRE = "enqueue pre-step: the pre-land tests failed on 19b89a0b (failing: {}) -- fix them, or ..."
# Log text, never a run: a gate's path spelled whole reads to the selector as one this test runs.
RUST = "./gates/" + "rust_gate.sh"
GATE_RED = (
    "✓ pre-land green in 782 s\n"
    f"✗ [1/10] FAILED (exit status 1): {RUST}\n"
    "FAILED tests/test_binary_source_identity.py::test_rebuilt[untagged-head] - AssertionError\n"
    '✗ rust: external (plain): GOH_TEST_BIN="$D/goh" GOH_COV_GOH="$D/goh" ./tools/pytest.sh '
    "failed (exit status 2; command: env -u X bash -c ...)\n"
)
BIN_TEST = "tests/test_binary_source_identity.py::test_rebuilt[untagged-head]"
PINNED_SRC = """def test_the_push_gate_takes_a_slot_before_the_export():
    gate = (REPO_ROOT / "gates" / "a_gate.sh").read_text()
    take = gate.index('gate_slot_acquire "$(basename "$root")"')
    assert take < gate.index('git -C "$root" worktree add --detach'), (
        "the export ran before the slot"
    )
"""

CASES = [
    # (why, log, the test's source by id, flaky, retried, cause)
    ("tip moved after enqueue", "", {}, set(), False, "tip-moved"),
    ("conflict: did not stack on e6b10fff", "", {}, set(), False, "conflict"),
    # CARRYBASECODE: a base_files.tsv row the diff did not add
    (
        PRE.format("tests/test_base_table.py::test_every_base_file_has_one_true_row"),
        "",
        {},
        set(),
        False,
        "consistency-table",
    ),
    # SCCACHEPATHS: check_cargo_env_spread's allowlist
    (
        PRE.format(
            "tests/test_cargo_env_spread.py::"
            "test_no_goh_code_sets_a_cargo_config_variable_for_a_cargo_it_runs"
        ),
        "",
        {},
        set(),
        False,
        "consistency-table",
    ),
    # RUFFBUMPITEM: the tool version manifest
    (
        PRE.format(
            "tests/test_tool_version_manifest.py::"
            "test_no_tool_version_is_written_outside_the_manifest"
        ),
        "",
        {},
        set(),
        False,
        "consistency-table",
    ),
    # SCCACHEPATHS's other red: a docs/map.md row, the structural step
    (
        "exit 1",
        f"✗ [2/10] FAILED (exit status 1): ./gates/{'structural.sh'} --full\n",
        {},
        set(),
        False,
        "consistency-table",
    ),
    # a table named only in the assertion
    (
        "exit 1",
        (
            "FAILED tests/test_x.py::test_y - AssertionError\n"
            "E       AssertionError: assert ['lib/proc_de...se_files.tsv'] == []\n"
        ),
        {},
        set(),
        False,
        "consistency-table",
    ),
    # SCCACHEENV: a test of the environment rustc sees
    (
        PRE.format(
            "tests/test_suite_drift.py::"
            "test_the_shim_keeps_its_build_dir_out_of_rustcs_environment[--config]"
        ),
        "",
        {},
        set(),
        False,
        "env-leak",
    ),
    # a test green at pre-land that reads a variable the gate step sets
    (
        "exit 1",
        GATE_RED,
        {BIN_TEST: 'def test_rebuilt():\n    assert os.environ.get("GOH_COV_GOH") is None\n'},
        set(),
        False,
        "env-leak",
    ),
    # BUILDDIRSHARE: a test pinning a gate script's old text
    (
        PRE.format("tests/test_gate_slot.py::test_the_push_gate_takes_a_slot_before_the_export"),
        "",
        {"tests/test_gate_slot.py::test_the_push_gate_takes_a_slot_before_the_export": PINNED_SRC},
        set(),
        False,
        "pinned-text",
    ),
    (
        "exit 1",
        "FAILED tests/test_x.py::test_y - ValueError\nE   ValueError: substring not found\n",
        {},
        set(),
        False,
        "pinned-text",
    ),
    # COVGOHBIN: a test the gate step's variables never reach
    (
        "exit 1",
        GATE_RED,
        {BIN_TEST: "def test_rebuilt(tmp_path):\n    assert run(tmp_path).returncode == 0\n"},
        set(),
        False,
        "real-bug",
    ),
    (
        PRE.format("tests/test_suite_shard.py::test_a_verdict"),
        "",
        {},
        {"tests/test_suite_shard.py::test_a_verdict"},
        False,
        "flake",
    ),
    (PRE.format("tests/test_suite_shard.py::test_a_verdict"), "", {}, set(), True, "flake"),
    (PRE.format("tests/test_suite_shard.py::test_a_verdict"), "", {}, set(), False, "real-bug"),
    # CARRYBASECODE's killed pre-step: no test to name
    (
        "enqueue pre-step: the pre-land tests failed on bed7d75d (exit -15, see the output above)",
        "",
        {},
        set(),
        False,
        "other",
    ),
]


@pytest.mark.parametrize(("why", "log", "sources", "flaky", "retried", "cause"), CASES)
def test_each_red_of_the_day_gets_its_class(why, log, sources, flaky, retried, cause):
    got = lr.classify({"why": why}, log, sources=sources, flaky=flaky, retried=retried)
    assert got["cause"] == cause, got
    assert got["cause"] in lr.CLASSES and got["rule"]


def test_the_failing_tests_come_from_the_why_and_the_log_once_each():
    why = PRE.format("tests/a.py::test_one, tests/b.py::test_two")
    log = "FAILED tests/b.py::test_two - x\nFAILED tests/c.py::test_three - y\n"
    assert lr.tests_of({"why": why}, log) == [
        "tests/a.py::test_one",
        "tests/b.py::test_two",
        "tests/c.py::test_three",
    ]


def test_a_test_body_is_cut_from_its_file_by_name():
    src = "import os\n\n\n" + PINNED_SRC + "\n\ndef test_next():\n    pass\n"
    body = lr.test_body(
        src, "tests/test_gate_slot.py::test_the_push_gate_takes_a_slot_before_the_export"
    )
    assert body.startswith("def test_the_push_gate") and "test_next" not in body
    assert '"the export ran before the slot"' in body and body.rstrip().endswith(")")
    assert lr.test_body(src, "tests/x.py::test_absent") == ""


def test_fields_judge_only_a_red_and_never_raise(tmp_path):
    assert lr.fields({"state": "landed"}) == {}
    got = lr.fields({"state": "red", "why": "exit 1", "log": str(tmp_path / "gone.log")})
    assert got["red_cause"]["cause"] == "other", got


def _done(q, name, rec):
    gdb.land(q, name.removesuffix(".json"), rec)


def test_backfill_classes_the_reds_that_have_none_and_the_report_counts_them(tmp_path, monkeypatch):
    gdb.use(monkeypatch, tmp_path)
    q = tmp_path / "landq"
    t = "tests/test_base_table.py::test_every_base_file_has_one_true_row"
    _done(q, "01-GOH.A.json", {"state": "red", "sha": "a1", "item": "GOH.A", "why": PRE.format(t)})
    _done(q, "02-GOH.B.json", {"state": "red", "sha": "b1", "item": "GOH.B",
                               "why": PRE.format("tests/x.py::test_y")})  # fmt: skip
    _done(q, "03-GOH.B.json", {"state": "landed", "sha": "c1", "enqueued": "b1", "item": "GOH.B"})
    kept = {"cause": "conflict", "rule": "kept", "tests": []}
    _done(
        q, "04-GOH.C.json", {"state": "red", "why": "tip moved", "item": "GOH.C", "red_cause": kept}
    )
    before = dict(land_records.records(q))
    assert lr.backfill(q) == 2
    after = dict(land_records.records(q))
    assert {r: {**rec, "red_cause": None} for r, rec in after.items()} == {
        r: {**rec, "red_cause": None} for r, rec in before.items()}  # fmt: skip  # nothing else
    assert after["04-GOH.C"]["red_cause"]["rule"] == "kept" and lr.backfill(q) == 0
    side = land_records.causes(q)
    assert side["01-GOH.A.json"]["cause"] == "consistency-table"
    assert side["02-GOH.B.json"]["cause"] == "flake"  # the same commit landed later
    rep = lr.report(q)
    assert (rep["records"], rep["red"]) == (4, 3)
    assert {c: r["count"] for c, r in rep["classes"].items()} == {
        "consistency-table": 1,
        "flake": 1,
        "conflict": 1,  # a record's own field wins
    }
    assert lr.report(q, last=1)["red"] == 1
    assert rep["classes"]["consistency-table"]["examples"][0]["tests"] == [t]


def test_goh_land_reds_prints_the_classes_and_json(tmp_path, monkeypatch):
    gdb.use(monkeypatch, tmp_path)  # the CLI below reads the same goh.db
    q = tmp_path / "landq"
    _done(q, "01-GOH.A.json", {"state": "red", "item": "GOH.A", "why": "tip moved after enqueue"})
    _done(q, "02-GOH.B.json", {"state": "landed", "item": "GOH.B"})
    env = {"GOH_LANDQ_DIR": str(q), "GOH_LIVE": "1", "PATH": "/usr/bin:/bin",
           "GOH_DB": str(tmp_path / "goh.db")}  # fmt: skip
    run = [sys.executable, str(REPO_ROOT / "lib" / "land_reds.py")]
    kw = {"capture_output": True, "text": True, "env": env}
    text = subprocess.run(
        [*run, "--backfill", "--last", "5"],
        check=True,
        timeout=patience(60),
        stdin=subprocess.DEVNULL,
        **kw,
    ).stdout
    assert text.splitlines()[0] == "1 of 2 land records red (50%)", text
    assert "tip-moved" in text and "GOH.A" in text
    got = json.loads(
        subprocess.run(
            [*run, "--json"], check=True, timeout=patience(60), stdin=subprocess.DEVNULL, **kw
        ).stdout
    )
    assert got["classes"]["tip-moved"]["count"] == 1


def test_the_table_rule_reads_a_test_id_alone_and_names_the_tables_an_assertion_names():
    """GOH.TABLESEARLY: `goh land --check` selects consistency-table tests by this rule, the one
    `classify` judges a red by, never a hand list."""
    assert lr.judges_a_table("tests/test_wiring.py::test_every_script_has_a_row_in_the_map")
    assert lr.judges_a_table("tests/test_cap_headroom.py::test_no_new_file_crosses_the_headroom")
    assert not lr.judges_a_table("tests/test_landq.py::test_a_red_is_recorded")
    rec = {"why": PRE.format("tests/test_wiring.py::test_every_script_has_a_row_in_the_map")}
    assert lr.classify(rec, "")["cause"] == "consistency-table"
    log = (
        "E   AssertionError: lib/x.py has no row in docs/map.md\n"
        "E   assert 'tools/y.sh' in house_scripts.tsv rows; see tests/cap_headroom_allow.txt\n"
        "lib/z.py is not in docs/map.md (an untested line, not an E line)\n"
    )
    assert lr.tables(log) == ["docs/map.md", "house_scripts.tsv", "tests/cap_headroom_allow.txt"]
    assert lr.tables("E   assert 1 == 2\n") == []
    assert lr.tables("E   assert ['lib/main_re...se_files.tsv'] == []\n") == []  # pytest's cut
