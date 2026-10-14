"""5.3: each step's figure is the one that holds on a box that is never quiet.

Three A-B-B-A profiles of this repo's push gate (2026-10-14, a 2x load): clippy's raw CPU moved
+17% and its CPU over the four-at-once rust control +1% and +2%; the native tier's in-process steps
held raw (+4%, -1%) while every shell control tried moved 15-17%. So the step timer records each
step's KIND, a rust gate is bracketed by the rust control and divided by it, the native kind reads
raw, and the report says when a ratio is not a figure: a kind with no control is uncalibrated, and
a control that drifted between the run's two ends voids that kind's ratios.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from conftest import hermetic_env

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "lib"
sys.path.insert(0, str(LIB))

import step_control  # noqa: E402
import step_timings  # noqa: E402


def _row(label: str, cpu_ms: float, kind: str = "", control: bool = False) -> dict:
    row = {"label": label, "ms": cpu_ms, "rc": 0, "tier": "step", "parent": "", "cwd": "/r"}
    row["cpu_ms"] = cpu_ms
    if kind:
        row["kind"] = kind
    if control:
        row["control"] = True
    return row


def test_a_step_records_the_kind_its_gate_declares(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "t.jsonl"
    monkeypatch.setenv("GOH_TIMINGS", str(out))
    monkeypatch.setenv("GOH_TIMINGS_KIND", "rust")
    step_timings.record("clippy", 10.0, 0, "step", cpu_ms=8.0)
    monkeypatch.delenv("GOH_TIMINGS_KIND")
    step_timings.record("unkinded", 10.0, 0, "step", cpu_ms=8.0)
    rows = step_timings.load(str(out))
    assert rows[0]["kind"] == "rust" and "control" not in rows[0], rows
    assert "kind" not in rows[1], rows


def test_the_ratio_is_the_step_over_its_kinds_control_mean() -> None:
    rows = [
        _row("control:rust", 400, "rust", control=True),
        _row("clippy", 5400, "rust"),
        _row("control:rust", 420, "rust", control=True),
    ]
    text = step_timings.calibrated(rows)
    assert "13.17  clippy" in text, text  # 5400 / 410
    assert "control drift +5.0%" in text, text


def test_a_control_that_drifted_voids_its_kinds_ratios() -> None:
    """Rule 5 of a sitting: the load moved under the run, so no ratio from it is a figure."""
    rows = [
        _row("control:rust", 3000, "rust", control=True),
        _row("clippy", 7000, "rust"),
        _row("control:rust", 3600, "rust", control=True),
    ]
    text = step_timings.calibrated(rows)
    assert "rust: void, the control drifted +20.0%" in text, text
    assert "clippy" not in text, text


def test_a_kind_without_both_controls_is_uncalibrated_not_divided() -> None:
    rows = [
        _row("control:rust", 400, "rust", control=True),
        _row("clippy", 7000, "rust"),
    ]
    text = step_timings.calibrated(rows)
    assert "rust: uncalibrated, 1 control(s) -- one before and one after are needed" in text, text
    assert "clippy" not in text, text


def test_a_run_with_no_kinds_prints_no_calibrated_section() -> None:
    assert step_timings.calibrated([_row("x", 10)]) == ""


def test_the_report_carries_the_calibrated_section() -> None:
    rows = [
        _row("control:rust", 400, "rust", control=True),
        _row("clippy", 5400, "rust"),
        _row("control:rust", 400, "rust", control=True),
    ]
    assert "-- CPU over a same-kind control" in step_timings.report(rows), step_timings.report(rows)


def test_the_native_kind_reads_raw_cpu_and_needs_no_control() -> None:
    """Its raw CPU held (+4%, -1%) where every shell control moved 15-17%: a ratio would have
    carried the control's drift into the figure."""
    rows = [_row("emoji", 2500.0, "native"), _row("markers", 1200.0, "native")]
    text = step_timings.calibrated(rows)
    assert "native: raw CPU holds" in text and "uncalibrated" not in text, text
    assert "2.50 s  emoji" in text and "1.20 s  markers" in text, text


def test_every_kind_a_gate_declares_has_a_control_or_reads_raw() -> None:
    """A gate that declared a kind with neither would print uncalibrated forever."""
    assert set(step_control.CONTROLS) == {"rust"}
    assert set(step_timings.RAW_KINDS) == {"native"}
    assert not set(step_control.CONTROLS) & set(step_timings.RAW_KINDS)
    for gate, kind in (("structural.sh", "native"), ("rust_gate.sh", "rust")):
        text = (ROOT / "gates" / gate).read_text()
        assert f"export GOH_TIMINGS_KIND={kind}" in text, gate


def test_the_rust_control_runs_four_copies_at_once() -> None:
    """A serial control lost -19% under load where the step lost -8.9%: the control matches the
    step's concurrency (four rustc at once), not only its kind."""
    plan = step_control.rust_plan(Path("/c"))
    assert len(plan) == step_control.RUST_COPIES == 4
    assert len({p.cwd for p in plan}) == 4 and len({p.target for p in plan}) == 4
    assert all(p.argv[:2] == ["cargo", "clippy"] and "--offline" in p.argv for p in plan)
    assert step_control.RUST_CRATE == ("regex-syntax", "0.8.11")


def test_an_unknown_kind_is_a_usage_error(tmp_path: Path) -> None:
    env = hermetic_env()
    env.update(GOH_TIMINGS=str(tmp_path / "t.jsonl"), GOH_STEP_CONTROL_CACHE=str(tmp_path / "c"))
    r = subprocess.run(
        [sys.executable, str(LIB / "step_control.py"), "run", "cobol"],
        env=env, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
    )  # fmt: skip
    assert r.returncode == 2 and "cobol" in r.stderr, r.stderr


def test_gate_profile_brackets_the_gate_with_its_kinds_controls() -> None:
    text = (ROOT / "tools" / "gate_profile.sh").read_text()
    lines = [line.strip() for line in text.splitlines()]
    calls = [i for i, line in enumerate(lines) if line == "controls"]
    gate = next(
        i for i, line in enumerate(lines) if line.startswith("bash") and "push_gate.sh" in line
    )
    assert 'step_control.py" run' in text, "the controls are not lib/step_control.py's"
    assert len(calls) == 2 and calls[0] < gate < calls[1], "a control before the gate and one after"
    assert "Cargo.toml" in text, "the Rust control runs only for a repo that builds Rust"


def test_a_step_that_runs_a_gate_takes_the_gates_kind() -> None:
    """`./gates/structural.sh --full` is a CI step with no kind of its own; its steps are native."""
    rows = [
        _row("./gates/structural.sh --full", 5000.0),
        dict(_row("emoji", 10.0, "native"), parent="./gates/structural.sh --full"),
        _row("mixed", 4000.0),
        dict(_row("a", 10.0, "native"), parent="mixed"),
        dict(_row("b", 10.0, "rust"), parent="mixed"),
    ]
    text = step_timings.calibrated(rows)
    assert "5.00 s  ./gates/structural.sh --full" in text, text
    assert "mixed" not in text, "a step over two kinds took one of them"
