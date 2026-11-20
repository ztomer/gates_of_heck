"""The display-seam probe writes its shared case trees once, under goh_tmp (GOH.FSCHURN3).

Every probe wrote its ~500 case files and removed them again, and a suite runs ~50 probes (each
rule-drop calibration is a whole probe): checks/_display_seam_fixtures.py writes them once per
content and every later probe reads them.
"""

from __future__ import annotations

import os
import subprocess
import sys

from conftest import REPO_ROOT

CHECK = REPO_ROOT / "checks" / "check_display_seam.py"


def test_the_probe_writes_its_shared_cases_once_and_under_goh_tmp(tmp_path):
    """GOH.FSCHURN3: every probe wrote its ~500 case files and removed them again, ~50 probes a
    suite. The listed-directly cases are written ONCE per content under goh_tmp() and read by
    every later probe; nothing lands in the bare TMPDIR but goh.noindex."""
    env = dict(os.environ, TMPDIR=str(tmp_path))
    for _ in range(2):
        got = subprocess.run(
            [sys.executable, str(CHECK), "--probe"],
            capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL, env=env,
        )  # fmt: skip
        assert got.returncode == 0, got.stdout + got.stderr
        if _ == 0:
            (shared,) = (tmp_path / "goh.noindex").glob("goh-display-seam-cases.*")
            first = shared.stat().st_ino, (shared / "case01").stat().st_mtime_ns
    assert sorted(p.name for p in tmp_path.iterdir()) == ["goh.noindex"]
    assert [p.name for p in (tmp_path / "goh.noindex").iterdir()] == [shared.name]
    assert (shared.stat().st_ino, (shared / "case01").stat().st_mtime_ns) == first
