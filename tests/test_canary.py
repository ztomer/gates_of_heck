"""Every shared gate arrives with its canary in the same commit: a case that drives it, or why not.

WHY HERE. game_asset_factory's `tools/check_gate_canary.py` held the cases and demanded one for
every gate in `checks/`. So a gate landed here, ran in every repo, and GAF's next unrelated push
went red until somebody there wrote the case -- at least eight times from 2026-10-01 on, the
last `check_ci_local_parity.py` (v0.33.0; GAF `bde863d`). The table lived in a different repo from
the set it enumerates, so the two could only ever agree after the fact. It now lives beside the
gates (`checks/_canary_cases.py`), and this file runs it in the commit that adds the gate: a gate
with no case is refused HERE, and a case that does not bite is refused here too.
"""

import os

import pytest
from _canary import CHECKS, run_case, uncanaried
from _canary_cases import CASES, EMOJI, UNCOVERED


def test_every_shared_gate_is_driven_by_a_case_or_named_with_a_reason():
    found = uncanaried()
    assert found.ok, "\n".join(found.problems(noun="shared gate"))


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_each_case_is_red_on_its_violation_and_green_on_its_clean_tree(case, tmp_path):
    verdict, message = run_case(case, str(tmp_path), 0)
    assert verdict is True, message


def test_every_exemption_says_why_in_words():
    assert all(len(why.split()) >= 8 for why in UNCOVERED.values())


def _seed(tmp_path, *names):
    checks = tmp_path / "checks"
    checks.mkdir()
    for name in names:
        (checks / name).write_text("# fixture\n")
    return str(checks)


DRIVEN = [("driven", "check_driven.py", [], {}, {})]


def test_a_gate_no_case_drives_and_no_exemption_names_is_found(tmp_path):
    found = uncanaried(_seed(tmp_path, "check_driven.py", "check_new.py"), DRIVEN, {})
    assert not found.ok
    assert [name for name, _ in found.unreached] == ["check_new.py"]


def test_the_same_gate_named_with_a_reason_passes(tmp_path):
    seeded = _seed(tmp_path, "check_driven.py", "check_new.sh")
    assert uncanaried(seeded, DRIVEN, {"check_new.sh": "why"}).ok


def test_an_exemption_for_a_gate_that_is_gone_is_stale(tmp_path):
    seeded = _seed(tmp_path, "check_driven.py")
    found = uncanaried(seeded, DRIVEN, {"check_deleted.py": "why"})
    assert [name for name, _ in found.stale] == ["check_deleted.py"]


def test_a_case_for_a_gate_that_is_gone_is_stale(tmp_path):
    # Beside the gates, a case naming no gate is a deleted gate's leftover, not one still to come.
    found = uncanaried(_seed(tmp_path, "check_other.py"), DRIVEN, {"check_other.py": "why"})
    assert [name for name, _ in found.stale] == ["check_driven.py"]


def test_helpers_are_not_gates(tmp_path):
    assert uncanaried(_seed(tmp_path, "check_driven.py", "_helper.py"), DRIVEN, {}).ok


def test_an_empty_checks_directory_fails_rather_than_passes(tmp_path):
    found = uncanaried(_seed(tmp_path), [], {})
    assert not found.ok and found.empty_scope


def test_a_gate_that_bites_is_recognised(tmp_path):
    case = ("emoji", "check_no_emoji.py", [], {"d.md": f"x {EMOJI}\n"}, {"d.md": "plain\n"})
    assert run_case(case, str(tmp_path), 0)[0] is True


def test_a_fixture_out_of_the_gates_scope_is_red_with_the_reason(tmp_path):
    # no_allow scans src/, benches/ and build.rs: a .rs at the root is never read, so the gate
    # "passes" its violation. That is the canary's own blindness, and it must say so.
    blind = ("blind", "check_no_allow.py", [], {"root.rs": "#[allow(dead_code)]\nfn x(){}\n"}, {})
    verdict, message = run_case(blind, str(tmp_path), 0)
    assert verdict is False and "did not bite" in message


def test_a_noisy_gate_is_red_with_the_reason(tmp_path):
    noisy = ("noisy", "check_no_emoji.py", [], {"d.md": f"{EMOJI}\n"}, {"d.md": f"{EMOJI}\n"})
    verdict, message = run_case(noisy, str(tmp_path), 0)
    assert verdict is False and "CLEAN tree failed" in message


def test_the_real_checks_directory_is_the_one_read():
    assert os.path.samefile(CHECKS, os.path.join(os.path.dirname(__file__), "..", "checks"))
