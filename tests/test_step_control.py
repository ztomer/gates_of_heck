"""5.3: each step's figure is the one that holds on a box that is never quiet.

Three A-B-B-A profiles of this repo's push gate (2026-10-14, a 2x load): clippy's raw CPU moved
+17% and its CPU over the four-at-once rust control +1% and +2%; the native tier's in-process steps
held raw (+4%, -1%) while every shell control tried moved 15-17%. So the step timer records each
step's KIND, a rust gate is bracketed by the rust control and divided by it, the native kind reads
raw, and the report says when a ratio is not a figure: a kind with no control is uncalibrated, and
a control that drifted between the run's two ends voids that kind's ratios.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import git, hermetic_env, write
from timing_bounds import patience

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "lib"
sys.path.insert(0, str(LIB))

import step_control
import step_timings


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
    assert "rust: uncalibrated, 1 control -- one before and one after are needed" in text, text
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
        env=env, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL, check=False,
    )  # fmt: skip
    assert r.returncode == 2 and "cobol" in r.stderr, r.stderr


def test_a_repo_with_crates_in_subdirectories_needs_the_rust_control(repo: Path) -> None:
    """A private server workspace's 29 crates have no root Cargo.toml: a root-only test skipped its
    control."""
    assert step_control.kinds_for(repo) == []
    write(repo, "crates/a/Cargo.toml", "[package]\nname = 'a'\n")
    git(repo, "add", "-A")
    assert step_control.kinds_for(repo) == ["rust"]
    write(repo, "Cargo.toml", "[workspace]\n")
    git(repo, "add", "-A")
    assert step_control.kinds_for(repo) == ["rust"]


def test_gate_profile_brackets_the_gate_with_its_kinds_controls() -> None:
    text = (ROOT / "tools" / "gate_profile.sh").read_text()
    lines = [line.strip() for line in text.splitlines()]
    calls = [i for i, line in enumerate(lines) if line == "controls"]
    gate = next(
        i for i, line in enumerate(lines) if line.startswith("bash") and "push_gate.sh" in line
    )
    assert 'step_control.py" run' in text, "the controls are not lib/step_control.py's"
    assert len(calls) == 2 and calls[0] < gate < calls[1], "a control before the gate and one after"
    assert 'step_control.py" kinds' in text, "the controls are chosen by the repo's crates"


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


# GOH.RUSTCONTROLVOID. A land's gate runs 7-25 min on a box whose load moved 10 -> 90 inside one
# land: across 115 lands (2026-11-17..19) the end-to-end drift followed ln(load_end / load_start)
# (r 0.57) and voided 31 (27%), most where the load moved more than 2x (20 of 57). Two controls at
# the gate's ends cannot stand for the load under a step in the middle, so the gate is SAMPLED
# (a control every SAMPLE_S during it) and each step is divided by the controls around it.


def _trow(label: str, cpu_ms: float, t: float, ms: float, kind: str = "rust", control: bool = False,
          load1: float | None = None) -> dict:  # fmt: skip
    row = dict(_row(label, cpu_ms, kind, control=control), ms=ms, t=t)
    if load1 is not None:
        row["load1"] = load1
    return row


def _ctl(cpu_ms: float, t: float, load1: float | None = None) -> dict:
    return _trow("control:rust", cpu_ms, t, 700.0, control=True, load1=load1)


def test_a_step_record_carries_its_end_time(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "t.jsonl"
    monkeypatch.setenv("GOH_TIMINGS", str(out))
    step_timings.record("clippy", 10.0, 0, "step", cpu_ms=8.0)
    (row,) = step_timings.load(str(out))
    assert isinstance(row.get("t"), float) and row["t"] > 1.7e9, row


def test_a_step_is_divided_by_the_controls_around_it_not_the_runs_ends() -> None:
    """The run's ends drifted +20% (the old rule voided every ratio); each step's bracket held."""
    rows = [
        _ctl(3000, 100.0),
        _trow("fmt", 400, 110.0, 5000),
        _ctl(3030, 160.0),
        _ctl(3540, 220.0),
        _trow("clippy", 7140, 240.0, 10000),
        _ctl(3600, 280.0),
    ]
    text = step_timings.calibrated(rows)
    assert "void" not in text, text
    assert "control drift +20.0% end to end" in text, text  # the ledger's number, still printed
    assert "2.00  clippy" in text, text  # 7140 / median(3540, 3600) = 7140 / 3570
    assert "0.13  fmt" in text, text  # 400 / median(3000, 3030)


def test_a_step_whose_few_controls_disagree_is_void_and_says_why() -> None:
    rows = [
        _ctl(3000, 100.0, load1=12.0),
        _trow("clippy", 7000, 150.0, 40000),
        _ctl(3600, 200.0, load1=88.0),
    ]
    text = step_timings.calibrated(rows)
    assert "clippy: void, its 2 controls spread 20.0% (load 12 -> 88)" in text, text
    assert "rust: void, no step's controls held within 15%" in text, text


def test_a_long_step_takes_the_median_of_five_or_more_controls() -> None:
    """The owner's rule for a cpu-s figure: the median of five, whatever their spread."""
    ctl = [_ctl(c, 100.0 + 60 * i) for i, c in enumerate((3000, 3100, 3200, 3900, 3300))]
    rows = [ctl[0], _trow("coverage", 64000, 340.0, 230000), *ctl[1:]]
    text = step_timings.calibrated(rows)
    assert "20.00  coverage" in text, text  # 64000 / median(3000..3900) = 64000 / 3200
    assert "void" not in text, text


def test_a_step_with_no_control_after_it_is_unbracketed() -> None:
    rows = [_ctl(3000, 100.0), _trow("clippy", 7000, 150.0, 4000), _ctl(3010, 120.0)]
    rows[2]["t"] = 140.0
    text = step_timings.calibrated(rows)
    assert "clippy: unbracketed, no control after it" in text, text


def test_a_control_row_carries_the_load_it_ran_under(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "t.jsonl"
    monkeypatch.setenv("GOH_TIMINGS", str(out))
    monkeypatch.setitem(step_control.CONTROLS, "rust", lambda cache: [])
    monkeypatch.setattr(step_control, "_run_all", lambda plan: 0)
    monkeypatch.setenv("GOH_STEP_CONTROL_CACHE", str(tmp_path / "c"))
    step_control.run("rust")
    (row,) = step_timings.load(str(out))
    assert row["control"] and isinstance(row.get("load1"), float) and "t" in row, row


@pytest.mark.load_sensitive  # a child spawned and waited on
def test_the_watch_ends_when_its_gate_is_gone(tmp_path: Path) -> None:
    """A sampler that outlived its gate would burn four cores for nobody (failure-path-no-op)."""
    dead = subprocess.Popen(["true"], stdin=subprocess.DEVNULL)
    dead.wait()
    env = hermetic_env()
    env.update(GOH_TIMINGS=str(tmp_path / "t.jsonl"), GOH_STEP_CONTROL_CACHE=str(tmp_path / "c"))
    r = subprocess.run(
        [sys.executable, str(LIB / "step_control.py"), "watch", "--pid", str(dead.pid),
         "--every", "1", "rust"],
        env=env, capture_output=True, text=True, timeout=patience(30), stdin=subprocess.DEVNULL,
        check=False,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    assert not (tmp_path / "t.jsonl").exists(), "a control ran for a gate that was gone"


def test_gate_profile_samples_the_control_during_the_gate() -> None:
    text = (ROOT / "tools" / "gate_profile.sh").read_text()
    lines = [line.strip() for line in text.splitlines()]
    gate = next(
        i for i, line in enumerate(lines) if line.startswith("bash") and "push_gate.sh" in line
    )
    watch = next(i for i, line in enumerate(lines) if 'step_control.py" watch --pid $$' in line)
    reap = next(i for i, line in enumerate(lines) if 'kill -TERM "$sampler"' in line)
    assert watch < gate < reap, "the sampler starts before the gate and is reaped after it"
    assert 'wait "$sampler"' in text, "the sampler is reaped, not left to the shell"


@pytest.mark.load_sensitive  # real children killed and reaped
def test_a_control_stopped_mid_run_leaves_no_process_behind(tmp_path: Path, monkeypatch) -> None:
    """gate_profile.sh reaps the sampler with SIGTERM, often while its four clippies run."""
    plan = [step_control.Run(tmp_path, tmp_path / "t", ["sleep", "30"]) for _ in range(2)]
    started: list[subprocess.Popen] = []
    real = subprocess.Popen

    def spy(*a, **k):
        started.append(real(*a, **k))
        if len(started) == len(plan):
            raise SystemExit(0)  # the SIGTERM handler's exit, arriving once all are running
        return started[-1]

    monkeypatch.setattr(subprocess, "Popen", spy)
    with contextlib.suppress(SystemExit):
        step_control._run_all(plan)
    assert all(p.poll() is not None for p in started[:-1]), "a clippy outlived its control"
    started[-1].kill()
    started[-1].wait()
