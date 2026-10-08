"""tests/timing_bounds.py: a time bound is placed by its failure and stretched by the box -- and
no test in tests/ asserts an elapsed time under a literal number."""

from __future__ import annotations

import re

import pytest
import timing_bounds
from conftest import REPO_ROOT

# An upper bound on an elapsed time written as a number: `elapsed < 15`, `time.time() - t0 < 5`.
LITERAL = re.compile(
    r"assert\b[^\n#]*?(?:\b(?:elapsed|waited|wall|dt|took)\b|(?:monotonic|time|perf_counter)\(\)"
    r"\s*-\s*\w+)\s*<=?\s*[0-9]"
)


def test_no_test_bounds_an_elapsed_time_by_a_literal() -> None:
    found = [
        f"{p.relative_to(REPO_ROOT)}:{n}: {line.strip()}"
        for p in sorted((REPO_ROOT / "tests").glob("*.py"))
        if p.name != "test_timing_bounds.py"  # its own pattern table
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if LITERAL.search(line)
    ]
    assert found == [], "use timing_bounds.assert_sooner, or a same-run control:\n" + "\n".join(
        found
    )


def test_the_pattern_sees_each_shape() -> None:
    for line in (
        "    assert elapsed < 15, 'x'",
        "    assert r.ok and waited < 9, (waited,)",
        "    assert time.monotonic() - t0 < 3.0",
        "    assert time.time() - t0 <= 5",
    ):
        assert LITERAL.search(line), line
    for line in (
        "    assert wall >= 1.2",
        "    assert elapsed < limit",
        "    assert len(x) < 3",
    ):
        assert not LITERAL.search(line), line


def test_a_quiet_box_bounds_at_slack_times_the_pass(monkeypatch) -> None:
    monkeypatch.setattr(timing_bounds, "load_factor", lambda: 1.0)
    assert timing_bounds.bound(2, 60) == 6.0


def test_a_loaded_box_stretches_it_but_never_toward_the_failure(monkeypatch) -> None:
    monkeypatch.setattr(timing_bounds, "load_factor", lambda: 4.0)
    assert timing_bounds.bound(2, 60) == 24.0
    monkeypatch.setattr(timing_bounds, "load_factor", lambda: 40.0)
    assert timing_bounds.bound(2, 60) == 45.0


def test_a_failure_not_clear_of_its_pass_is_a_design_error() -> None:
    with pytest.raises(ValueError, match="lengthen"):
        timing_bounds.bound(2, 5)
