"""A pytest a test starts numbers its dirs in a root of its own, not this session's (GOH.FLAKY2).

pytest numbers every session's dirs `pytest-N` under `<root>/pytest-of-<user>` and, at each
session's exit, deletes the old ones and unlinks a dead `pytest-current`. suite_tmp.py handed every
pytest a test starts the SUITE's root (PYTEST_DEBUG_TEMPROOT, inherited), so the nested sessions of
tests on different xdist workers rotated one directory at once: two unlinked the same dead
`pytest-current` and the second died `FileNotFoundError` at its exit (main, 2026-11-19 16:30,
test_temp_claimable.py::test_the_session_start_runs_the_sweep; 1 in 40 sessions here at load 55).
Each worker now hands its children a root under its own basetemp (suite_tmp.nested_root): one
worker runs one test at a time, so the sessions in it never overlap.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from timing_bounds import patience


def _numbered(path: Path) -> Path:
    """`<root>/pytest-of-<user>`: the directory a session numbers `path`'s pytest-N in."""
    return next(p for p in path.parents if p.name.startswith("pytest-of-"))


def test_a_pytest_a_test_starts_numbers_its_dirs_outside_this_sessions_root(
    tmp_path: Path, tmp_path_factory
) -> None:
    (tmp_path / "pytest.ini").write_text("[pytest]\n")  # the nested session's rootdir: not the repo
    seen = tmp_path / "seen"
    (tmp_path / "test_child.py").write_text(
        "import os\n"
        "def test_child(tmp_path):\n"
        "    open(os.environ['GOH_FLAKY2_SEEN'], 'w').write(str(tmp_path))\n"
    )
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:randomly",
         "-p", "no:xdist", "test_child.py"],
        cwd=tmp_path, capture_output=True, text=True, timeout=patience(120), check=False,
        env={**os.environ, "GOH_FLAKY2_SEEN": str(seen)}, stdin=subprocess.DEVNULL,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    child = Path(seen.read_text())
    ours = _numbered(tmp_path_factory.getbasetemp() / "x")
    assert _numbered(child) != ours, child  # its rotation can delete nothing of this run
    assert tmp_path_factory.getbasetemp() in child.parents, child  # and it goes with this worker's
