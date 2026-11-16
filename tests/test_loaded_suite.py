"""tools/loaded_suite.sh: the release runs goh's suite once under tools/load.py (GOH.CLASS.HARNESS).

A load-sensitive test was found by whichever land ran on a busy box. Each land now runs the suite
under deliberate load on the tip it landed (lib/loaded_verdict.py), and the release kit reads that
verdict before its land; the verdict line names the spinners and the load the run began on. These
pin the wrapper's verdicts (a stub command, no spinners, a private load directory) and that the kit
reads it before main moves.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

SCRIPT = REPO_ROOT / "tools" / "loaded_suite.sh"
KIT = REPO_ROOT / "tools" / "release_goh.sh"


def _run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = hermetic_env(GOH_LOAD_DIR=str(tmp_path / "load"))
    return subprocess.run(
        ["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True,
        timeout=patience(120), stdin=subprocess.DEVNULL, check=False,
    )  # fmt: skip


def test_a_passing_suite_is_named_with_its_spinners_and_load(tmp_path) -> None:
    r = _run(tmp_path, "-n", "0", "--", "bash", "-c", "echo the-suite-ran")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "the-suite-ran" in r.stdout
    assert re.search(r"loaded suite: passed under 0 spinners \(load [0-9.]+ before", r.stdout), (
        r.stdout
    )


def test_a_red_suite_is_red_with_its_own_exit(tmp_path) -> None:
    r = _run(tmp_path, "-n", "0", "--", "bash", "-c", "exit 3")
    assert r.returncode == 3, r.stdout + r.stderr
    assert "loaded suite: exit 3 under 0 spinners" in r.stdout + r.stderr


def test_a_spinner_count_that_is_not_a_number_is_refused(tmp_path) -> None:
    r = _run(tmp_path, "-n", "many")
    assert r.returncode == 2 and "spinner count" in r.stderr, r.stderr


def test_the_release_reads_the_tips_verdict_before_main_moves() -> None:
    """GOH.SHIPRELREUSE R2: each land runs it on the tip it landed (.gatesrc GOH_LAND_LOADED,
    lib/loaded_verdict.py); the kit reads that verdict, never runs the suite, before its land."""
    code = [
        line.strip()
        for line in KIT.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert not any("tools/loaded_suite.sh" in ln for ln in code), "the kit runs the suite"
    wait = next(i for i, ln in enumerate(code) if "loaded_verdict.py" in ln and "wait" in ln)
    land = next(i for i, ln in enumerate(code) if ln.endswith("tools/land.sh"))
    assert wait < land, code
    assert "GOH_LAND_LOADED='tools/loaded_suite.sh'" in (REPO_ROOT / ".gatesrc").read_text()
