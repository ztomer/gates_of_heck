"""A coverage gate shows the value it measured, at the precision it compares (GOH.COVSHOW).

Found setting agent-feed's floors (AF.GATEFLOORS, 2026-11-12): swift_gate.sh's coverage step
swallowed check_swift_coverage.py's OK line with the rest of a passing step's log, and the line
showed one decimal while the comparison used the whole float, so the passing floor had to be
bracketed by hand (89.2 passed, 89.3 failed). Now every coverage checker prints its verdict as a
tui `measure` line (`↔`), goh_step and local_ci.sh show a passing step's `↔` lines, and the Swift
checkers show the value cut to the floor's own places and judge exactly that value
(lib/cov_shown.py). Red first: on 2e38f15 the checker printed `89.2%` for 357/400, and a passing
step's measurement reached nobody.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from conftest import REPO_ROOT, run_check
from test_local_ci import gatesrc, run_ci
from test_swift_coverage import SCRIPT, _write_spm
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
from cov_shown import places, shown


@pytest.mark.parametrize(
    ("pct", "floor", "text", "met"),
    [
        (89.25, 89.2, "89.25", True),
        (89.25, 89.25, "89.25", True),
        (89.25, 89.26, "89.25", False),
        (89.2549, 89.255, "89.254", False),  # cut, never rounded up into a pass it is not
        (89.2551, 89.255, "89.255", True),
        (100.0 * 3570 / 4000, 89.25, "89.25", True),  # 89.24999999999999 in binary
        (50.0, 80, "50.00", False),
    ],
)
def test_the_shown_value_is_the_judged_value(pct, floor, text, met):
    assert shown(pct, floor) == (text, met)


@pytest.mark.parametrize("pct", [0.0, 12.3456, 66.666666, 89.25, 99.999, 100.0])
def test_the_shown_value_as_the_floor_passes_and_one_unit_more_fails(pct):
    text, _ = shown(pct, 0)
    unit = 10 ** -places(float(text))
    assert shown(pct, float(text))[1] is True
    assert shown(pct, float(text) + unit)[1] is (pct >= float(text) + unit)


def test_the_swift_checker_shows_the_floor_precision_and_measure_mark(repo):
    _write_spm(repo, ".build/a/debug/codecov/a.json", [("A.swift", 357, 400)])  # 89.25%
    ok = run_check(repo, SCRIPT, "--min", "89.25")
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "↔ [swift_cov] OK — 89.25% >= 89.25%" in ok.stdout, ok.stdout
    under = run_check(repo, SCRIPT, "--min", "89.26")
    assert under.returncode == 1, under.stdout + under.stderr
    assert "89.25% is under the 89.26% floor" in under.stderr, under.stderr


def _step(tmp_path: Path, body: str) -> subprocess.CompletedProcess:
    script = textwrap.dedent(f"""
        cd '{tmp_path}'
        . '{REPO_ROOT}/gates/_common.sh'
        goh_init outer
        {body}
        goh_done
    """)
    return subprocess.run(
        ["/bin/bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=patience(60),
        check=False,
        stdin=subprocess.DEVNULL,
    )


def test_a_passing_steps_measurement_is_shown(tmp_path):
    r = _step(
        tmp_path,
        'goh_step "coverage" /bin/bash -c ". \'$GOH_ROOT/tui/lib.sh\'; '
        "measure 'coverage 89.25% >= 89.2%'; echo chatter\"",
    )
    assert r.returncode == 0, r.stderr
    out = r.stdout + r.stderr
    assert "↔ coverage 89.25% >= 89.2%" in out, out
    assert "chatter" not in out, out


def test_local_ci_shows_a_passing_steps_measurement(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    (repo / "step.sh").write_text("echo '↔ coverage 91.50% >= 90%'\necho chatter\n")
    gatesrc(repo, "bash step.sh")
    r = run_ci(repo)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "↔ coverage 91.50% >= 90%" in out and "chatter" not in out, out
