"""GOH.RULESLEFT: `goh land --check` runs every rule test, found by its shape, never learned.

The week before this (goh.db `lands`, 2026-11-17..24) went red on policy tests the check never ran
-- the from-HEAD stanza 9 times, the git-init ratchet 6, the clock patch 3 -- because a rule
entered the check only when its file declared itself or after it had refused a land. lib/
rule_tests.py finds every rule by its shape, so a new one is in the check the moment it exists.
"""
# goh-land-guard: test_the_check_runs_every_rule_the_weeks_reds_named

from __future__ import annotations

from conftest import REPO_ROOT

from lib import land_check, rule_tests

CASES = """
import pytest

def test_every_file_is_ok():
    pass

def test_no_stray_thing(tmp_path):
    pass

@pytest.mark.parametrize("x", [1])
def test_every_case(x):
    pass

def test_a_behaviour():
    pass

def test_no_leak_in_the_tree(capsys):
    pass

class TestX:
    def test_every_method(self):
        pass
"""

# The rule tests that refused a goh land or enqueue over 2026-11-17..24 (goh.db `lands`, guards),
# each still in the tree. test_long_pole's 30/60 s tests are measurements (22 s each), over the
# check's 10 s cap: the land's own run judges them.
WEEKS_REDS = (
    "tests/test_cap_headroom.py::test_no_new_file_crosses_the_headroom_line",
    "tests/test_python_from_head.py::test_every_python_entry_point_runs_from_head",
    "tests/test_no_env_spread.py::test_no_test_spreads_os_environ_beyond_the_seed",
    "tests/test_git_init_ratchet.py::test_every_file_is_at_its_seed",
    "tests/test_declared_reads.py::test_every_test_joining_a_path_it_does_not_spell_declares_its_reads",
    "tests/test_wiring.py::test_every_script_has_a_row_in_the_map",
    "tests/test_hook_git_env.py::test_rust_test_code_spawns_git_only_through_the_testkit",
    "tests/test_base_table.py::test_every_base_file_has_one_true_row",
    "tests/test_no_env_dump.py::test_no_test_dumps_an_environment",
    "tests/test_no_global_clock_patch.py::test_no_test_patches_the_clock_of_every_thread",
    "tests/test_verdict_free_keys.py::test_every_key_the_gates_pass_on_is_verdict_free",
    "tests/test_timing_bounds.py::test_no_test_bounds_an_elapsed_time_by_a_literal",
    "tests/test_fakes_cannot_orphan.py::test_every_session_leader_has_an_owner",
    "tests/test_check_lock_order.py::test_the_clean_tree_has_no_finding",
    "tests/test_tables.py::test_every_generated_table_is_as_its_generator_writes_it",
)


def test_a_rule_is_a_plain_every_or_no_test_of_the_real_tree() -> None:
    assert rule_tests.rules_in("tests/test_x.py", CASES) == [
        "tests/test_x.py::test_every_file_is_ok",
        "tests/test_x.py::test_no_leak_in_the_tree",
    ]
    assert rule_tests.rules_in("tests/test_y.py", "def test_every_(:\n") == []


def test_the_check_runs_every_rule_the_weeks_reds_named() -> None:
    chosen = set(land_check.tests(REPO_ROOT, None, rules=True))
    missed = [n for n in WEEKS_REDS if n not in chosen]
    assert missed == [], missed


def test_check_rules_runs_no_judge_and_a_blind_job_is_never_called_green(
    repo, monkeypatch, capsys
) -> None:
    """2026-11-24: all 8 guard shards of a `goh land --check` timed out, and it said "every guard
    green in 331.8 s". `--rules` runs no judge and no learned refuser; a blind job is red, by
    name, and the check exits 1 (GOH.CHECKGIVEUP300)."""
    from lib import commit_stamp, fragment_check

    for k in [k for k in __import__("os").environ if k.startswith("GIT_")]:
        monkeypatch.delenv(k)
    seen: dict = {}

    def fake_run(root, q, say, main, **kw):
        seen.update(q=q, **kw)
        say("check: guard tests 1/8: NO VERDICT in 300.0 s (exit 124) -- red")
        return ["guard tests 1/8 (no verdict in 300.0 s, exit 124)"]

    monkeypatch.setattr(land_check, "run", fake_run)
    monkeypatch.setattr(commit_stamp, "check", lambda *a, **k: [])
    monkeypatch.setattr(fragment_check, "missing", lambda *a, **k: [])
    assert land_check.cli(str(repo), repo / "q", rules=True) == 1
    assert seen == {"q": None, "tables": False, "rules": True, "door": "rules"}, seen
    said = "".join(capsys.readouterr())
    assert "every guard green" not in said and "by guard tests 1/8 (no verdict" in said, said
