"""Each timed step records its CPU time beside its wall time (BACKLOG 4.4).

The box is never quiet (2026-10-08: load 10-82 for days), so a step's wall time measures the box
as much as the step. The CPU its process tree spent -- user + sys of every descendant reaped --
is the step's own work, and moves far less with load. Both wrappers record it: `goh step` (the
native tier) and `lib/bounded_run.py`, from the rusage of the children each reaped around its
one step. Pinned by its known answer: a step that BURNS a fixed amount of CPU records at least
that, and a step that sleeps records a small fraction of its wall.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env

BOUNDED = REPO_ROOT / "lib" / "bounded_run.py"
# Spins until THIS process has used 0.4 s of CPU: the work is fixed, however slow the box.
BURN = [sys.executable, "-c", "import time\nwhile time.process_time() < 0.4: pass"]
NAP = ["sleep", "0.6"]


def _tiers(goh: Path) -> dict[str, list[str]]:
    return {"native": [str(goh), "step"], "python": [sys.executable, str(BOUNDED)]}


def _row(goh: Path, tier: str, tmp_path: Path, cmd: list[str]) -> dict:
    out = tmp_path / f"{tier}.jsonl"
    env = hermetic_env(GOH_TIMINGS=str(out))
    r = subprocess.run(
        [*_tiers(goh)[tier], "--timeout", "60", "--label", "s", "--", *cmd],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    (row,) = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    return row


@pytest.mark.parametrize("tier", ["native", "python"])
def test_a_step_that_burns_cpu_records_it(goh: Path, tmp_path: Path, tier: str) -> None:
    row = _row(goh, tier, tmp_path, BURN)
    assert row.get("cpu_ms", 0) >= 400, row


@pytest.mark.parametrize("tier", ["native", "python"])
def test_a_step_that_sleeps_records_little_of_its_wall(
    goh: Path, tmp_path: Path, tier: str
) -> None:
    row = _row(goh, tier, tmp_path, NAP)
    assert "cpu_ms" in row and row["ms"] >= 600, row
    assert row["cpu_ms"] < row["ms"] / 4, row


def test_the_report_shows_cpu_beside_wall_and_sums_it_per_label() -> None:
    sys.path.insert(0, str(REPO_ROOT / "lib"))
    import step_timings

    rows = [
        {"label": "lint", "ms": 900.0, "rc": 0, "cwd": "/a", "cpu_ms": 300.0},
        {"label": "lint", "ms": 1100.0, "rc": 0, "cwd": "/b", "cpu_ms": 500.0},
        {"label": "old", "ms": 50.0, "rc": 0, "cwd": "/a"},  # a line from before cpu_ms
    ]
    text = step_timings.report(rows)
    assert "0.30 s cpu" in text and "0.50 s cpu" in text, text
    assert "2.00 s    0.80 s cpu  x2" in text, text
