"""lib/step_timings.py -- the P0 instrument: one JSON line per gate step, in milliseconds.

The 2026-10-05 measurement found costs no per-line seconds counter shows: a 0.2 s poll per step,
44 ms per interpreter start, a 7 s lookup repeated in each of 29 crates. So the instrument has to
be exact enough to see a few hundred milliseconds, must name where a step ran so one label can be
summed across crates, must survive concurrent writers, and must never change a gate's verdict.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from conftest import hermetic_env

ROOT = Path(__file__).resolve().parent.parent
BOUNDED = ROOT / "lib" / "bounded_run.py"
TIMINGS = ROOT / "lib" / "step_timings.py"


def _env(path: Path, **extra: str) -> dict[str, str]:
    env = hermetic_env()
    env["GOH_TIMINGS"] = str(path)
    env.update(extra)
    return env


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _bounded(env, *cmd: str, label: str, timeout: int = 20) -> int:
    return subprocess.run(
        [sys.executable, str(BOUNDED), "--timeout", str(timeout), "--label", label, "--", *cmd],
        env=env,
        capture_output=True,
        timeout=60,
    ).returncode


def test_a_step_is_timed_in_milliseconds_not_seconds(tmp_path):
    """300 ms must read as ~300 ms: whole seconds would say 0, which is the defect."""
    out = tmp_path / "t.jsonl"
    assert _bounded(_env(out), "sleep", "0.3", label="nap") == 0
    (row,) = _rows(out)
    assert row["label"] == "nap" and row["rc"] == 0 and row["tier"] == "step"
    assert 250 <= row["ms"] < 3000, row


def test_the_exit_code_and_a_timeout_are_recorded(tmp_path):
    out = tmp_path / "t.jsonl"
    assert _bounded(_env(out), "false", label="red") == 1
    assert _bounded(_env(out), "sleep", "30", label="hang", timeout=1) == 124
    rows = {r["label"]: r for r in _rows(out)}
    assert rows["red"]["rc"] == 1
    assert rows["hang"]["rc"] == 124


def test_a_nested_step_names_its_parent(tmp_path):
    """A rust_gate.sh step inside a local_ci.sh step: the inner line must say which outer one."""
    out = tmp_path / "t.jsonl"
    inner = [sys.executable, str(BOUNDED), "--timeout", "5", "--label", "inner", "--", "true"]
    assert _bounded(_env(out), *inner, label="outer") == 0
    rows = {r["label"]: r for r in _rows(out)}
    assert rows["inner"]["parent"] == "outer"
    assert rows["outer"]["parent"] == ""


def test_unset_writes_nothing_and_changes_nothing(tmp_path):
    env = hermetic_env()
    assert _bounded(env, "true", label="quiet") == 0
    assert not list(tmp_path.iterdir())


def test_an_unwritable_file_never_turns_a_step_red(tmp_path):
    """The instrument must not be a reason a gate fails."""
    out = tmp_path / "no" / "such" / "dir" / "t.jsonl"
    assert _bounded(_env(out), "true", label="fine") == 0


def test_concurrent_writers_interleave_whole_lines(tmp_path):
    """A 4-way crate fan-out writes one file at once: every line must still parse."""
    out = tmp_path / "t.jsonl"
    env = _env(out)
    with ThreadPoolExecutor(max_workers=12) as pool:
        codes = list(pool.map(lambda i: _bounded(env, "true", label=f"s{i}" * 40), range(24)))
    assert codes == [0] * 24
    rows = _rows(out)
    assert sorted(r["label"] for r in rows) == sorted(f"s{i}" * 40 for i in range(24))


def test_the_report_sums_one_label_across_every_place_it_ran(tmp_path):
    """The view that found the 29-crate cost: the same label, summed."""
    out = tmp_path / "t.jsonl"
    for crate, ms in (("a", 7000), ("b", 6500), ("c", 100)):
        line = {"label": "dependency currency", "ms": ms, "rc": 0, "tier": "step"}
        line.update(parent="", cwd=f"/x/{crate}")
        with out.open("a") as fh:
            fh.write(json.dumps(line) + "\n")
    with out.open("a") as fh:
        fh.write('{"label": "trunc')  # a killed run's last line must not sink the report
    text = subprocess.run(
        [sys.executable, str(TIMINGS), "report", str(out)], capture_output=True, text=True
    ).stdout
    assert "13.60 s  x3    dependency currency" in text, text
    assert "3 step(s) recorded" in text, text


def test_goh_step_in_records_the_directory_it_ran_in(tmp_path):
    """29 crates of one repo must not all read as the repo root."""
    out = tmp_path / "t.jsonl"
    crate = tmp_path / "crate-a"
    crate.mkdir()
    script = (
        f'. "{ROOT}/gates/_common.sh"; goh_init t; goh_step_in "{crate}" "inside" true; goh_done'
    )
    r = subprocess.run(
        ["bash", "-c", script],
        env=_env(out),
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=60,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    (row,) = _rows(out)
    assert row["label"] == "inside" and Path(row["cwd"]).resolve() == crate.resolve(), row
