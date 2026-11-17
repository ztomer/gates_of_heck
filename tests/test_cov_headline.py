"""A coverage run that a failing test starved headlines the TEST, not the floor (GOH.COVHEADLINE).

The land log's `✗` line for GOH.PRESTEPDEADLINE's land (a0e13509, 2026-11-17) read `rust: coverage
(floor 95%) failed`, sent the reader to the floor, and the cause -- a failing test that starved the
measurement ('coverage not measured: starved by the failing tests above') -- was a warning below it.
goh_step now lets a step name its own headline (GOH_STEP_HEADLINE, a file it owns); coverage_gate.sh
writes `coverage: N test(s) failed under coverage (<first name>) -- not measured` there when tests
starved the run, and writes nothing for a real below-floor result, which keeps the floor wording.
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

from conftest import REPO_ROOT
from test_coverage_rust_exports import FULL_COVER, build_part, setup_fixture
from timing_bounds import patience

RED_TESTS = (
    "echo 'test cov::first_red ... FAILED'; echo 'test cov::second_red ... FAILED'; "
    "echo 'test result: FAILED. 0 passed; 2 failed'; exit 101"
)


def _via_goh_step(tmp_path: Path, parts: dict[str, str], floor: str, body: str | None):
    proj = setup_fixture(tmp_path, parts)
    script = textwrap.dedent(f"""
        cd '{tmp_path}'
        export PATH='{tmp_path}/bin':"$PATH"
        export CARGO_STUB_META='{tmp_path}/meta.json' CARGO_STUB_PARTS='{tmp_path}/canned-parts'
        {f'export FAKE_TEST_BODY="{body}"' if body else ""}
        unset GOH_COV_FLOOR_RUST
        . '{REPO_ROOT}/gates/_common.sh'
        goh_init rust
        goh_step "coverage (floor {floor}%)" bash '{REPO_ROOT}/gates/coverage_gate.sh' \
--lang rust --floor {floor} '{proj}'
    """)
    return subprocess.run(["/bin/bash", "-c", script], capture_output=True, text=True,
                          timeout=patience(120), stdin=subprocess.DEVNULL, check=False)  # fmt: skip


def test_a_starved_run_headlines_the_failing_test_not_the_floor(tmp_path):
    parts = {"lib.info": build_part(1, "_Za", 5, FULL_COVER)}
    r = _via_goh_step(tmp_path, parts, "100", RED_TESTS)
    assert r.returncode != 0, r.stdout + r.stderr
    out = r.stdout + r.stderr
    assert "rust: coverage: 2 tests failed under coverage (cov::first_red) -- not measured" in out
    assert "coverage (floor 100%) failed" not in out, out


def test_a_below_floor_run_still_names_the_floor(tmp_path):
    parts = {"lib.info": build_part(1, "_Za", 5, [(1, 1), (8, 0), (9, 0)], fn_end=4)}
    r = _via_goh_step(tmp_path, parts, "100", None)
    assert r.returncode != 0, r.stdout + r.stderr
    out = r.stdout + r.stderr
    assert "rust: coverage (floor 100%) failed" in out and "not measured" not in out, out


def test_a_step_that_names_no_headline_keeps_the_label_wording(tmp_path):
    script = textwrap.dedent(f"""
        cd '{tmp_path}'
        . '{REPO_ROOT}/gates/_common.sh'
        goh_init demo
        goh_step "plain" /bin/bash -c 'exit 3'
    """)
    r = subprocess.run(["/bin/bash", "-c", script], capture_output=True, text=True,
                       timeout=patience(60), stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert "demo: plain failed (exit status 3" in r.stderr, r.stderr
