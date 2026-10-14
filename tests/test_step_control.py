"""5.3: a step's CPU over a same-kind control, so a figure holds on a box that is never quiet.

Raw CPU moved +36% across a 10x load and wall +84%; the same step's CPU divided by a control of its
own kind, run in the same sitting, moved +3.3% (shell) and -8.9% (Rust, a control of the step's
concurrency). So the step timer records each step's KIND, a gate run is bracketed by its kinds'
controls, and the report divides -- and says so when it cannot: a kind with no control is
uncalibrated, and a control that drifted between the run's two ends voids that kind's ratios.
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
    monkeypatch.setenv("GOH_TIMINGS_KIND", "shell")
    step_timings.record("shellcheck", 10.0, 0, "step", cpu_ms=8.0)
    monkeypatch.delenv("GOH_TIMINGS_KIND")
    step_timings.record("unkinded", 10.0, 0, "step", cpu_ms=8.0)
    rows = step_timings.load(str(out))
    assert rows[0]["kind"] == "shell" and "control" not in rows[0], rows
    assert "kind" not in rows[1], rows


def test_the_ratio_is_the_step_over_its_kinds_control_mean() -> None:
    rows = [
        _row("control:shell", 400, "shell", control=True),
        _row("shellcheck", 5400, "shell"),
        _row("control:shell", 420, "shell", control=True),
    ]
    text = step_timings.calibrated(rows)
    assert "13.17  shellcheck" in text, text  # 5400 / 410
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
        _row("control:shell", 400, "shell", control=True),
        _row("shellcheck", 5400, "shell"),
        _row("clippy", 7000, "rust"),
    ]
    text = step_timings.calibrated(rows)
    assert "shell: uncalibrated, 1 control(s) -- one before and one after are needed" in text, text
    assert "rust: uncalibrated, 0 control(s)" in text, text
    assert "shellcheck" not in text and "clippy" not in text, text


def test_a_run_with_no_kinds_prints_no_calibrated_section() -> None:
    assert step_timings.calibrated([_row("x", 10)]) == ""


def test_the_report_carries_the_calibrated_section() -> None:
    rows = [
        _row("control:shell", 400, "shell", control=True),
        _row("shellcheck", 5400, "shell"),
        _row("control:shell", 400, "shell", control=True),
    ]
    assert "-- CPU over a same-kind control" in step_timings.report(rows), step_timings.report(rows)


def test_every_kind_has_a_control_and_the_gates_declare_only_those() -> None:
    """A gate that declared a kind with no control would print uncalibrated forever."""
    assert set(step_control.CONTROLS) == {"shell", "rust"}
    for gate, kind in (("structural.sh", "shell"), ("rust_gate.sh", "rust")):
        text = (ROOT / "gates" / gate).read_text()
        assert f"export GOH_TIMINGS_KIND={kind}" in text, gate


def test_the_shell_control_runs_pinned_files_and_records_a_control_row(tmp_path: Path) -> None:
    out = tmp_path / "t.jsonl"
    env = hermetic_env()
    env.update(GOH_TIMINGS=str(out), GOH_STEP_CONTROL_CACHE=str(tmp_path / "cache"))
    r = subprocess.run(
        [sys.executable, str(LIB / "step_control.py"), "run", "shell"],
        env=env, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    (row,) = step_timings.load(str(out))
    assert row["label"] == "control:shell" and row["kind"] == "shell" and row["control"] is True
    assert row["cpu_ms"] > 0, row
    files = sorted(
        p.relative_to(tmp_path / "cache" / "shell")
        for p in (tmp_path / "cache" / "shell").rglob("*.sh")
    )
    assert [str(f) for f in files if str(f) in step_control.SHELL_FILES] == sorted(
        step_control.SHELL_FILES
    )


def test_the_shell_control_is_pinned_to_one_commit() -> None:
    """A control read off the working tree changes with every commit, so two runs' ratios would
    divide by different work."""
    assert len(step_control.SHELL_COMMIT) >= 7 and len(step_control.SHELL_FILES) == 8


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
