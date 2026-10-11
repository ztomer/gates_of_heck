"""tests/timing_bounds.py: a time bound is placed by its failure and stretched by the box -- and
no test in tests/ asserts an elapsed time under a literal number."""

from __future__ import annotations

import re

import pytest
import timing_bounds
from conftest import REPO_ROOT

# An upper bound on an elapsed time written as a number: `elapsed < 15`, `time.time() - t0 < 5`.
LITERAL = re.compile(
    r"assert\b[^\n#]*?(?:\b(?:elapsed|waited|wall|dt|took)\b|\[\"(?:ms|cpu_ms)\"\]|"
    r"(?:monotonic|time|perf_counter)\(\)\s*-\s*\w+)\s*<=?\s*[0-9]"
    # ... or a deadline set by a number: `time.monotonic() + 5` (use `GiveUp`) ...
    r"|(?:monotonic|time|perf_counter)\(\)\s*\+\s*[0-9]"
    # ... or by the load read once, when the wait began: `time.monotonic() + patience(5)`.
    r"|(?:monotonic|time|perf_counter)\(\)\s*\+\s*patience\("
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
        '    assert 250 <= row["ms"] < 3000, row',
        "        deadline = time.monotonic() + 5",
        "        deadline = time.monotonic() + patience(5)",
        "    deadline = time.time() + patience(5)",
    ):
        assert LITERAL.search(line), line
    for line in (
        "    assert wall >= 1.2",
        "    assert elapsed < limit",
        "    assert len(x) < 3",
        "        while _alive(pid) and not give_up.passed():",
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


def test_patience_stretches_a_give_up_deadline_by_the_load(monkeypatch) -> None:
    monkeypatch.setattr(timing_bounds, "load_factor", lambda: 7.0)
    assert timing_bounds.patience(10) == 70.0


def test_a_failure_not_clear_of_its_pass_is_a_design_error() -> None:
    with pytest.raises(ValueError, match="lengthen"):
        timing_bounds.bound(2, 5)


def test_a_give_up_deadline_rereads_the_load_while_it_waits(monkeypatch) -> None:
    # 2026-10-10: a deadline read once at load 1 expired while a push loaded the box mid-wait.
    clock = [100.0]
    monkeypatch.setattr(timing_bounds.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(timing_bounds, "load_factor", lambda: 1.0)
    wait = timing_bounds.GiveUp(10)
    clock[0] += 15
    assert wait.passed()
    monkeypatch.setattr(timing_bounds, "load_factor", lambda: 4.0)
    assert not wait.passed(), "the load rose during the wait, so the wait stretched"


def test_no_test_reaches_the_machines_locks(tmp_path_factory) -> None:
    # conftest.py's autouse fixture moves every host lock under this run's temp root.
    import os

    from host_locks import HOST_LOCKS

    root = str(tmp_path_factory.getbasetemp())
    real = (
        "/tmp/mac-osaurus-gpu.lock",
        "/tmp/gates-of-heck-bench.lock",
        "/tmp/mac-desktop-ui.lock",
    )
    for name in HOST_LOCKS:
        assert os.environ[name].startswith(root) and os.environ[name] not in real, name
