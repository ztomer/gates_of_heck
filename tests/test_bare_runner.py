"""GOH.BAREPYTEST: a bare `python3 -m pytest` runs on bash 5 with cargo, or stops naming the runner.

Both ways, as a lane would start it: a bare run from a PATH that leads with /bin (Apple's bash 3.2
first, no cargo) and no wrapper marker passes its probe; the same run on a machine with no bash 5
(`GOH_BASH_CANDIDATES=/bin/bash`, lib/bash5.sh's seam) stops at collection naming tools/pytest.sh.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import runner_env
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

BARE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


def test_probe_bash_is_5_and_cargo_is_found():
    """What the bare run below asserts from inside: the env a test sees."""
    r = subprocess.run(
        ["bash", "-c", "echo ${BASH_VERSINFO[0]}"],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert int(r.stdout.strip()) >= 5, r
    assert shutil.which("cargo"), os.environ["PATH"]


def test_the_wrapper_env_is_left_alone():
    fake = {"PATH": BARE_PATH, runner_env.RUNNER: "1"}
    why = runner_env.same_env(fake)
    path = fake["PATH"]
    assert why is None and path == BARE_PATH


def _bare(tmp_path: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    env = hermetic_env()  # print-safe: pytest prints a failing call's arguments
    env.pop(runner_env.RUNNER, None)
    env.update(PATH=BARE_PATH, GATES_OF_HECK_SUITE_NESTED_OK="1", **extra)
    node = "tests/test_bare_runner.py::test_probe_bash_is_5_and_cargo_is_found"
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-n", "0", node],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        timeout=patience(300),
    )


@pytest.mark.skipif(not Path("/bin/bash").exists(), reason="no system bash to lead PATH")
def test_a_bare_run_from_a_bin_first_path_gets_bash5_and_cargo(tmp_path):
    r = _bare(tmp_path)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]


def test_a_bare_run_with_no_bash5_stops_at_collection_naming_the_runner(tmp_path):
    r = _bare(tmp_path, GOH_BASH_CANDIDATES="/bin/bash", GOH_BASH_BREW="")
    out = r.stdout + r.stderr
    assert r.returncode == 4, out[-2000:]
    assert "run tools/pytest.sh" in out, out[-2000:]
