"""The display-seam probe's cases are in memory, one of them a repository on disk (GOH.FSCHURN3).

Every probe wrote its ~500 case files and removed them again, and a suite runs ~50 probes (each
rule-drop calibration is a whole probe). checks/_display_seam_fixtures.py hands the checker's own
readers a tree that is on no disk; that a probe still goes red when a rule is dropped is
tests/test_display_seam.py's and tests/test_display_frames.py's.
"""

from __future__ import annotations

import subprocess
import sys

import pytest
from _mutated_check import mutated_probe
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

CHECKS = REPO_ROOT / "checks"
sys.path.insert(0, str(CHECKS))

import _display_seam_probe as probe  # noqa: E402
import check_display_seam  # noqa: E402
from _display_seam_fixtures import case_tree, held_in_memory  # noqa: E402


@pytest.mark.parametrize("owner", ["the run's", "a gate's"])
def test_the_probe_leaves_nothing(tmp_path, owner) -> None:
    """In a run's own TMPDIR (GATES_OF_HECK_SUITE_TMP) as in a real gate's, which no run owns."""
    run = tmp_path if owner == "the run's" else tmp_path / "other"
    env = hermetic_env(TMPDIR=str(tmp_path), GATES_OF_HECK_SUITE_TMP=str(run))
    argv = [sys.executable, str(CHECKS / "check_display_seam.py"), "--probe"]
    got = subprocess.run(argv, capture_output=True, text=True, check=False, env=env,
                         stdin=subprocess.DEVNULL, timeout=patience(60))  # fmt: skip
    assert got.returncode == 0, got.stdout + got.stderr
    assert [p.name for p in (tmp_path / "goh.noindex").iterdir()] == []


def _readers() -> list:
    names = ("repo_root", "listed_files", "content_bytes", "is_file", "policy_text")
    return [getattr(check_display_seam, n) for n in names]


def test_a_tree_in_memory_is_judged_as_the_same_tree_through_git() -> None:
    """One case of each verdict, both ways; and the checker has its own readers back after."""
    real = _readers()
    seen = {}
    for label, files, policy, want in probe.CASES:
        if want in seen:
            continue
        tree = case_tree(files, policy, probe.SEAM, probe.BASE)
        argv = ["--policy", "policy.json"]
        with held_in_memory(check_display_seam, tree):
            assert _readers() != real
            seen[want] = probe._drive(argv)
        assert _readers() == real
        assert seen[want] == probe._through_git(tree, argv) == want, label
    assert sorted(seen) == [0, 1, 2]


@pytest.mark.parametrize("name", ["check_display_seam", "_display_frames"])
def test_the_rule_drop_harness_is_green_over_a_module_as_it_stands(name) -> None:
    got = mutated_probe(name, (CHECKS / f"{name}.py").read_text())
    assert got.returncode == 0, got.stdout + got.stderr
