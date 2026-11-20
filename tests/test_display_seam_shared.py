"""The display-seam probe writes its shared case trees once, under goh_tmp (GOH.FSCHURN3).

Every probe wrote its ~500 case files and removed them again, and a suite runs ~50 probes (each
rule-drop calibration is a whole probe): checks/_display_seam_fixtures.py writes them once per
content and every later probe reads them.
"""

from __future__ import annotations

import subprocess
import sys

from conftest import REPO_ROOT, hermetic_env

CHECK = REPO_ROOT / "checks" / "check_display_seam.py"


def _probe(env) -> None:
    got = subprocess.run(
        [sys.executable, str(CHECK), "--probe"],
        capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL, env=env,
    )  # fmt: skip
    assert got.returncode == 0, got.stdout + got.stderr


def test_inside_a_run_root_the_cases_are_written_once_and_shared(tmp_path) -> None:
    """The run's own TMPDIR (GATES_OF_HECK_SUITE_TMP): one tree, kept, read by the next probe."""
    _probe(hermetic_env(TMPDIR=str(tmp_path), GATES_OF_HECK_SUITE_TMP=str(tmp_path)))
    (shared,) = (tmp_path / "goh.noindex").glob("goh-display-seam-cases.*")
    first = shared.stat().st_ino, (shared / "case01").stat().st_mtime_ns
    _probe(hermetic_env(TMPDIR=str(tmp_path), GATES_OF_HECK_SUITE_TMP=str(tmp_path)))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["goh.noindex"]
    assert [p.name for p in (tmp_path / "goh.noindex").iterdir()] == [shared.name]
    assert (shared.stat().st_ino, (shared / "case01").stat().st_mtime_ns) == first


def test_anywhere_else_the_probe_leaves_nothing(tmp_path) -> None:
    """A real gate's TMPDIR (no run owns it): the cases leave with the probe's own directory."""
    _probe(hermetic_env(TMPDIR=str(tmp_path), GATES_OF_HECK_SUITE_TMP=str(tmp_path / "other")))
    assert [p.name for p in (tmp_path / "goh.noindex").iterdir()] == []
