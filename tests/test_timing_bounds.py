"""tests/timing_bounds.py: a time bound is placed by its failure and stretched by the box -- and
no test in tests/ asserts an elapsed time under a literal number."""

from __future__ import annotations

import ast
import re

import pytest
import timing_bounds
from _this_thread import this_thread
from conftest import REPO_ROOT

# THE SCAN READS EACH FILE'S AST (GOH.TIMINGDETECT). A text pattern knew a duration by a few names
# and missed `secs = time.monotonic() - t0` returned from a helper and asserted `secs < 10`
# (tests/test_release_canary_memo.py). It flags, on the line it starts:
# * an UPPER bound on a duration by a number in an assert: `elapsed < 15`, `10 > secs`, `row["ms"]
#   < 3000`, where a duration is a clock's difference (`time.monotonic() - t0`), a name the file
#   binds to one anywhere, a conventional name (NAMED), or a `["ms"]`/`["cpu_ms"]` field;
# * a deadline set by a number, or by the load read once when the wait began: `time.monotonic() +
#   5`, `time.monotonic() + patience(5)` (use `GiveUp`);
# * an upper bound by a number times `load_factor()` ALONE: the run queue cannot see a lane's QoS
#   clamp (GOH.SUITEDRIFTBUILD); `timing_bounds.stretch`, `bound`, `patience` and `assert_sooner`
#   stretch by the clamp measured on the thread too, and are the correct bound;
# * an ABSENCE watched with a give-up wait: `assert not wait_until(ran.exists, 2.0)`. Stretched by
#   the load, its 2 s ran past the literal --wait-max of the child it watched (x50 at load 38 on 3
#   slow cores; v0.140.1's CI, GOH.CIRED01401): see the child say it waits.
CLOCKS = {"monotonic", "monotonic_ns", "perf_counter", "perf_counter_ns", "time", "time_ns"}
NAMED = {"elapsed", "waited", "wall", "dt", "took"}
KEYS = {"ms", "cpu_ms"}
WORD = re.compile(
    rf"\b(?:{'|'.join(sorted(CLOCKS))})\s*\(|\b(?:{'|'.join(sorted(NAMED))})\b"
    r"|\b(?:cpu_)?ms[\"']|\bwait_until\b"
)
# Ten minutes and more is a plausibility window or a hang guard, which no load a test meets reaches:
# tests/stamp_age.py holds a stamp "just taken" at -5..600 s, ruling out one from another hour.
HANG_S = 600.0


def _called(n: ast.AST) -> str:
    f = n.func if isinstance(n, ast.Call) else None
    return f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""


def _clock(n: ast.AST) -> bool:
    f = n.func if isinstance(n, ast.Call) else None
    if isinstance(f, ast.Attribute):
        return f.attr in CLOCKS and isinstance(f.value, ast.Name) and f.value.id == "time"
    return isinstance(f, ast.Name) and f.id in CLOCKS - {"time"}


def _since(n: ast.AST) -> bool:
    return isinstance(n, ast.BinOp) and isinstance(n.op, ast.Sub) and _clock(n.left)


def _seconds(n: ast.AST) -> float | None:
    if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub):
        v = _seconds(n.operand)
        return None if v is None else -v
    ok = isinstance(n, ast.Constant) and type(n.value) in (int, float)
    return float(n.value) if ok else None  # type: ignore[union-attr]


def _number(n: ast.AST) -> bool:
    return _seconds(n) is not None


def _run_queue_only(n: ast.AST) -> float | None:
    """The seconds of `number * load_factor()` (either order), else None: a limit stretched by
    the run queue alone, blind to the QoS clamp; `stretch()` and its kin are not it."""
    if not (isinstance(n, ast.BinOp) and isinstance(n.op, ast.Mult)):
        return None
    for num, scale in ((n.left, n.right), (n.right, n.left)):
        if _called(scale) == "load_factor" and (v := _seconds(num)) is not None:
            return v
    return None


def bounds(src: str) -> list[int]:
    """The lines of `src` that bound an elapsed time by a literal (above)."""
    if not WORD.search(src):
        return []  # no clock, no duration's name, no wait: not parsed
    nodes = list(ast.walk(ast.parse(src)))
    names = set(NAMED)
    for n in nodes:
        if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None and _since(n.value):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            names |= {t.id for t in targets if isinstance(t, ast.Name)}

    def per_second(e: ast.AST) -> float:
        """How many of `e`'s units make a second; 0 when `e` is no duration."""
        key = e.slice if isinstance(e, ast.Subscript) else None
        if isinstance(key, ast.Constant) and key.value in KEYS:
            return 1000.0
        return 1.0 if _since(e) or (isinstance(e, ast.Name) and e.id in names) else 0.0

    def tight(d: ast.AST, limit: ast.AST) -> bool:
        v, unit = _seconds(limit), per_second(d)
        v = _run_queue_only(limit) if v is None else v
        return bool(unit) and v is not None and v / unit < HANG_S

    found: set[int] = set()
    for n in nodes:
        deadline = isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add) and _clock(n.left)
        if deadline and (_number(n.right) or _called(n.right) == "patience"):
            found.add(n.lineno)
        if not isinstance(n, ast.Assert):
            continue
        test = n.test
        negated = isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not)
        if negated and _called(test.operand) == "wait_until":
            found.add(n.lineno)
        for c in ast.walk(test):
            if not isinstance(c, ast.Compare):
                continue
            for a, op, b in zip([c.left, *c.comparators[:-1]], c.ops, c.comparators, strict=True):
                upper = isinstance(op, (ast.Lt, ast.LtE)) and tight(a, b)
                if upper or (isinstance(op, (ast.Gt, ast.GtE)) and tight(b, a)):
                    found.add(n.lineno)
    return sorted(found)


def test_no_test_bounds_an_elapsed_time_by_a_literal() -> None:
    found = [
        f"{p.relative_to(REPO_ROOT)}:{n}: {text.splitlines()[n - 1].strip()}"
        for p in sorted((REPO_ROOT / "tests").glob("*.py"))
        if p.name != "test_timing_bounds.py"  # its own shape table
        for text in [p.read_text(encoding="utf-8")]
        for n in bounds(text)
    ]
    assert found == [], (
        "use timing_bounds.assert_sooner, a same-run control, or the child saying it waits:\n"
        + "\n".join(found)
    )


def _stmt(line: str) -> str:
    """One table line as a statement the scan parses (a block header gets its body)."""
    return line.strip() + (" pass" if line.rstrip().endswith(":") else "")


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
        '    assert not load_kit.wait_until(ran.exists, 2.0), "ran"',
    ):
        assert bounds(_stmt(line)), line
    for line in (
        "    assert wall >= 1.2",
        "    assert elapsed < limit",
        "    assert len(x) < 3",
        "    assert -5 <= time.time() - t0 <= 600, 'a stamp from another hour'",
        '    assert row["ms"] < 600_000',
        "        while _alive(pid) and not give_up.passed():",
        "    if not wait_until(done):",
    ):
        assert not bounds(_stmt(line)), line


def test_a_duration_named_where_it_was_measured_is_seen_where_it_is_bounded() -> None:
    """GOH.TIMINGDETECT: tests/test_release_canary_memo.py measured `secs = time.monotonic() - t0`
    in a helper, returned it, and a test asserted `secs < 10` -- a box-tuned literal the text
    pattern, which knows durations by a few names, never saw. The scan reads the file's AST: a
    name bound from a clock's difference anywhere in the file is a duration wherever it is
    compared against a number."""
    src = (
        "import time\n"
        "def _run():\n    t0 = time.monotonic()\n    secs = time.monotonic() - t0\n"
        "    return secs\n"
        "def test_x():\n    secs = _run()\n    assert secs < 10, secs\n"
        "def test_y():\n    n = len('ab')\n    assert n < 10\n"
        "def test_z():\n    secs = _run()\n    assert 10 > secs\n"
    )
    assert bounds(src) == [8, 14]


def test_the_clamp_aware_stretch_is_a_bound_and_the_run_queue_alone_is_not() -> None:
    """GOH.TIMINGDETECT over GOH.SUITEDRIFTBUILD: a bound stretched by `timing_bounds.stretch`
    (the larger of the run queue and the clamp measured on the thread, `thread_stretch`), placed by
    `bound` or `assert_sooner`, or judged by `_drift_guard.judged_s`, is correct; a literal times
    `load_factor()` alone is the run queue's reading, which cannot see `taskpolicy -c utility` (x3.2
    where a clamped thread ran x25-35), and is flagged as a literal is."""
    for line in (
        "    assert elapsed < 5 * load_factor()",
        "    assert time.monotonic() - t0 < load_factor() * 2.5",
        "    assert waited <= 10 * _drift_guard.load_factor()",
    ):
        assert bounds(_stmt(line)), line
    for line in (
        "    assert elapsed < 5 * stretch()",
        "    assert elapsed < 5 * timing_bounds.stretch()",
        "    assert elapsed < 5 * max(load_factor(), thread_stretch())",
        "    assert elapsed < bound(2, 60)",
        "    assert waited < patience(5)",
        "    assert judged_s(wall, factor, pole_s) <= 20",
        "    timing_bounds.assert_sooner(secs, 0.44, 10, 'canary')",
    ):
        assert not bounds(_stmt(line)), line


@pytest.mark.load_sensitive
@pytest.mark.parametrize("load", [1.0, 4.0, 40.0])
def test_a_bound_is_placed_by_its_failure_at_any_load(monkeypatch, load) -> None:
    """GOH.TEMPLEAKSETUP: a pass was bounded by its quiet time stretched by a model of the load,
    and a 1.5 s start read 9.7 s where the model said x2.5. The failure takes 60 s at any load."""
    monkeypatch.setattr(timing_bounds, "stretch", lambda: load)
    assert timing_bounds.bound(2, 60) == 45.0
    timing_bounds.assert_sooner(44.0, 2, 60, "a pass the load stretched x22")
    with pytest.raises(AssertionError, match="a hang"):
        timing_bounds.assert_sooner(60.0, 2, 60, "a hang")


def test_patience_stretches_a_give_up_deadline_by_the_load(monkeypatch) -> None:
    monkeypatch.setattr(timing_bounds, "thread_stretch", lambda: 1.0)
    monkeypatch.setattr(timing_bounds, "load_factor", lambda: 7.0)
    assert timing_bounds.patience(10) == 70.0


def test_a_clamped_thread_stretches_a_bound_past_the_run_queue(monkeypatch) -> None:
    """GOH.SUITEDRIFTBUILD: under the lane clamp the run queue read x3.2 where a clamped thread
    ran x25-35; a give-up deadline stretched by the run queue alone expired at a tenth of it."""
    monkeypatch.setattr(timing_bounds, "load_factor", lambda: 3.0)
    monkeypatch.setattr(timing_bounds, "thread_stretch", lambda: 12.0)
    assert timing_bounds.patience(10) == 120.0
    monkeypatch.setattr(timing_bounds, "thread_stretch", lambda: 1.0)
    assert timing_bounds.patience(10) == 30.0


def test_no_test_plants_the_run_queue_and_leaves_the_clamp_to_the_host() -> None:
    """GOH.GATESLOTSTRETCH: a bound stretches by the larger of `load_factor` and `thread_stretch`,
    so a test planting the run queue alone reads its host's measured clamp: test_gate_slot planted
    x5, expected 300 s and read 302.4 in a land gate whose thread had measured x5.04. A test plants
    `stretch`, or both of its inputs."""
    import ast

    found = []
    for p in sorted((REPO_ROOT / "tests").glob("*.py")):
        text = p.read_text(encoding="utf-8")
        for fn in ast.walk(ast.parse(text)) if "load_factor" in text else ():
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            planted = {
                c.args[1].value
                for c in ast.walk(fn)
                if isinstance(c, ast.Call)
                and isinstance(c.func, ast.Attribute)
                and c.func.attr == "setattr"
                and len(c.args) >= 2
                and isinstance(c.args[1], ast.Constant)
                and ast.unparse(c.args[0]).endswith("timing_bounds")
            }
            if "load_factor" in planted and "thread_stretch" not in planted:
                found.append(f"{p.relative_to(REPO_ROOT)}:{fn.lineno}: {fn.name}")
    assert found == [], "plant timing_bounds.stretch (or thread_stretch too):\n" + "\n".join(found)


def test_a_failure_not_clear_of_its_pass_is_a_design_error() -> None:
    with pytest.raises(ValueError, match="lengthen"):
        timing_bounds.bound(2, 5)
    with pytest.raises(ValueError, match="lengthen"):
        timing_bounds.bound(1.5, 8)  # goh ship's detached start, red at load 40


@pytest.mark.load_sensitive
def test_a_give_up_deadline_rereads_the_load_while_it_waits(monkeypatch) -> None:
    # 2026-10-10: a deadline read once at load 1 expired while a push loaded the box mid-wait.
    clock = [100.0]
    real = timing_bounds.time.monotonic
    monkeypatch.setattr(timing_bounds.time, "monotonic", this_thread(lambda: clock[0], real))
    monkeypatch.setattr(timing_bounds, "thread_stretch", lambda: 1.0)
    monkeypatch.setattr(timing_bounds, "load_factor", lambda: 1.0)
    wait = timing_bounds.GiveUp(10)
    clock[0] += 15
    assert wait.passed()
    monkeypatch.setattr(timing_bounds, "thread_stretch", lambda: 1.0)
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
