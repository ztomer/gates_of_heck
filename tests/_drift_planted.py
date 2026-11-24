"""Helpers for the drift guard's tests: a planted suite run in a child pytest under the REAL guard
(tests/test_suite_drift.py, tests/test_suite_drift_setup.py)."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import _drift_guard
from timing_bounds import patience

TESTS = Path(__file__).resolve().parent


def planted(
    tmp_path: Path,
    body: str,
    ceiling: float = 30,
    load: float = 1.0,
    setup_ceiling: float | None = None,
    stretch: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """A one-test suite whose conftest is the real guard's, run in a child pytest, at a pinned
    load factor -- the box running this test has its own, and the planted ceilings are tenths.
    STRETCH pins what the guard's probe measures on the test's thread (`pole_stretch`), for every
    phase however short: the QoS clamp's reading, which the run queue never shows."""
    (tmp_path / "conftest.py").write_text(
        f"import sys\nsys.path.insert(0, {str(TESTS)!r})\n"
        "import _drift_guard\n"
        "from _drift_guard import pytest_runtest_makereport, pytest_runtest_setup  # noqa: F401\n"
        f"_drift_guard.CEILING_S = {ceiling}\n"
        f"_drift_guard.load_factor = lambda *_window: {load}\n"
        + (f"_drift_guard.SETUP_CEILING_S = {setup_ceiling}\n" if setup_ceiling else "")
        + (
            f"_drift_guard.POLE_PROBE_AFTER_S = 0\n_drift_guard.pole_stretch = lambda: {stretch}\n"
            if stretch
            else ""
        )
    )
    (tmp_path / "test_planted.py").write_text(body)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist",
         str(tmp_path)],
        capture_output=True, text=True, cwd=tmp_path, timeout=patience(60), check=False,
        env=dict(os.environ, **{_drift_guard.SPEED_ENV: "1"}), stdin=subprocess.DEVNULL,
    )  # fmt: skip


def planted_load(
    tmp_path: Path, body: str, setup_ceiling: float | None = None
) -> subprocess.CompletedProcess[str]:
    """A one-test suite under the real guard and its real sampler, on a box of 16 cores whose run
    queue is FAKED by phase: 64 until the test body says `run`, 8 (a quiet box) while it runs,
    and 8 again once it says `end` -- os.getloadavg is what the sampler and the end reading ask."""
    (tmp_path / "conftest.py").write_text(
        f"import os, sys\nsys.path.insert(0, {str(TESTS)!r})\n"
        f"os.environ[{_drift_guard.SPEED_ENV!r}] = '1'\n"
        "import _drift_guard\n"
        "from _drift_guard import pytest_runtest_makereport, pytest_runtest_setup  # noqa: F401\n"
        "_drift_guard.CEILING_S = 0.3\n"
        "_drift_guard.SAMPLE_S = 0.02\n"
        + (f"_drift_guard.SETUP_CEILING_S = {setup_ceiling}\n" if setup_ceiling else "")
        + "os.cpu_count = lambda: 16\n"
        "LOADS = {'before': 64.0, 'run': 64.0, 'end': 8.0}\n"
        "def _fake():\n"
        "    n = LOADS[os.environ.get('FAKE_LOAD_PHASE', 'before')]\n"
        "    return (n, n, n)\n"
        "os.getloadavg = _fake\n"
    )
    (tmp_path / "test_planted.py").write_text(body)
    env = {k: v for k, v in os.environ.items() if k != "FAKE_LOAD_PHASE"}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist",
         str(tmp_path)],
        capture_output=True, text=True, cwd=tmp_path, timeout=patience(60), check=False, env=env,
        stdin=subprocess.DEVNULL,
    )  # fmt: skip


def took_over_ceiling(out: str, name: str, ceiling: float) -> float | None:
    """The seconds the guard reports for `name` ("<name> took <n> s, over its <ceiling> s
    ceiling"), or None when it does not report it. The measured <n> depends on the machine
    (v0.139.22's hosted CI, tracing under coverage, printed 0.7 for a 0.6 s sleep), so a test
    asserts the verdict and a bound on <n>, never the exact digits (GOH.CIRED013922)."""
    m = re.search(rf"{re.escape(name)} took (\d+(?:\.\d+)?) s, over its {ceiling:g} s ceiling", out)
    return float(m.group(1)) if m else None
