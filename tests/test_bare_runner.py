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
    # ...and the suite's cargo shim leads (tests/_drift_guard.py): a bare run lost it, and every
    # test of the shim, and every throwaway crate's build dir, was a false red (GOH.RULESLEFT).
    import _drift_guard

    cargo = shutil.which("cargo")
    assert cargo and cargo.startswith(_drift_guard._shim_dir), (cargo, os.environ["PATH"])


def _tool(where: Path, name: str, body: str = "exit 0") -> Path:
    where.mkdir(exist_ok=True)
    path = where / name
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)
    return path


def test_a_path_whose_bash_is_5_already_is_left_alone(tmp_path, monkeypatch):
    """The wrapper changes no PATH; a bare run led it with bash 5's directory, and Homebrew's
    cargo and rustc there took the place of the ones the caller's PATH names first."""
    _tool(tmp_path / "rust", "cargo")
    bash = _tool(tmp_path / "brew", "bash")
    _tool(tmp_path / "brew", "cargo")
    monkeypatch.setattr(runner_env, "bash5", lambda environ: (str(bash), ""))
    fake = {"PATH": f"{tmp_path / 'rust'}:{tmp_path / 'brew'}:/usr/bin"}
    before = fake["PATH"]
    why = runner_env.same_env(fake)
    path = fake["PATH"]
    assert why is None and path == before


def test_the_bash_that_leads_never_takes_cargos_place(tmp_path, monkeypatch):
    _tool(tmp_path / "old", "bash")
    _tool(tmp_path / "rust", "cargo")
    bash = _tool(tmp_path / "brew", "bash")
    _tool(tmp_path / "brew", "cargo")
    monkeypatch.setattr(runner_env, "bash5", lambda environ: (str(bash), ""))
    fake = {"PATH": f"{tmp_path / 'old'}:{tmp_path / 'rust'}:{tmp_path / 'brew'}"}
    why = runner_env.same_env(fake)
    path = fake["PATH"]
    assert why is None
    cargo = shutil.which("cargo", path=path)
    assert shutil.which("bash", path=path) == str(bash)
    assert cargo == str(tmp_path / "rust" / "cargo")


def test_what_the_bash_probe_puts_on_the_path_is_kept(tmp_path, monkeypatch):
    """The probe's own imports may lead PATH (the cargo shim did): read after it, never before."""
    bash = _tool(tmp_path / "brew", "bash")
    _tool(tmp_path / "old", "bash")
    fake = {"PATH": str(tmp_path / "old")}

    def probe(environ):
        environ["PATH"] = f"{tmp_path / 'shim'}:{environ['PATH']}"
        return str(bash), ""

    monkeypatch.setattr(runner_env, "bash5", probe)
    why = runner_env.same_env(fake)
    assert why is None
    path = fake["PATH"].split(":")
    assert str(tmp_path / "shim") in path, path


def test_the_bash_probe_loads_no_drift_guard():
    """It runs before the suite has its own HOME and TMPDIR; the guard makes its shim then."""
    code = (
        "import os, sys; sys.path.insert(0, 'tests'); import runner_env; "
        "runner_env.bash5(os.environ); sys.exit('_drift_guard' in sys.modules)"
    )
    env = hermetic_env()
    r = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=patience(60),
    )
    assert r.returncode == 0, r.stderr[-2000:]


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
