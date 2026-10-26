"""The push gate runs this repo's pytest suite ONCE (G2e, 2026-10-25).

It ran twice side by side: as its own step in GOH_CI_STEPS, and inside rust_gate.sh's coverage
group, which runs the same suite over the instrumented binary (GOH_COV_RUST_EXTERNAL) and fails
when a test fails (tests/test_coverage_external.py). `cargo test --workspace` likewise ran twice:
as a step, and as the coverage group's own instrumented `cargo test`. Measured at 3a63301: a 317 s
gate whose two suites ran 196 s and 207 s beside each other. The coverage run is now the one run;
these tests pin the shape that makes it so, so neither twin can come back and neither half can
quietly go.
"""

from __future__ import annotations

import re
import sys

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "checks"))
import _calibration

GATESRC = (REPO_ROOT / ".gatesrc").read_text(encoding="utf-8")


def _key(name: str) -> str:
    m = re.search(rf"^{name}=(.+)$", GATESRC, re.MULTILINE)
    assert m, f"{name} is not set in .gatesrc"
    return m.group(1)


def _steps() -> list[str]:
    return [s.strip() for s in _key("GOH_CI_STEPS").strip("'").split(":")]


def test_no_step_runs_the_suite_beside_the_coverage_group() -> None:
    twins = [s for s in _steps() if "pytest" in s]
    assert twins == [], f"the suite already runs inside rust_gate.sh's coverage group: {twins}"


def test_the_coverage_group_is_the_suite_run() -> None:
    assert "./gates/rust_gate.sh" in _steps()
    assert "tools/pytest.sh" in _key("GOH_COV_RUST_EXTERNAL")
    assert _key("GOH_COV_FLOOR_RUST").split()[0].isdigit(), (
        "no floor: the coverage group never runs"
    )
    # The calibration reader agrees: a test cited as a proof still runs on every push.
    assert _calibration.runs_pytest(str(REPO_ROOT))


def test_cargo_test_runs_once_and_doctests_are_not_dropped() -> None:
    """The coverage group's instrumented `cargo test` covers every lib, bin and test target with
    every feature; doctests are what it does not run, so the step that remains runs exactly them."""
    cargo_tests = [s for s in _steps() if s.startswith("cargo test")]
    assert cargo_tests == ["cargo test --workspace --doc --locked"], cargo_tests
    cov = (REPO_ROOT / "gates" / "_coverage_rust.sh").read_text(encoding="utf-8")
    assert re.search(r"cargo test --locked --workspace \$KINDS --all-features", cov), (
        "the coverage group no longer runs the workspace's tests"
    )
