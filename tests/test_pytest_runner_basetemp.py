"""tools/pytest.sh gives every run its own temp root, so concurrent suites cannot delete each other's.

pytest keeps the three newest `pytest-of-<user>/pytest-N` directories and deletes older ones when a
new session starts. Two sessions landing into this repo at once (2026-10-10) meant the newer run
pruned the older run's root mid-run: 2277 tests errored `FileNotFoundError` on `popen-gwN`, once an
autouse fixture made every test touch its temp root. A temp root of the run's own is never
pruned by another session: `gates/_pytest_root.sh` exports pytest's own `PYTEST_DEBUG_TEMPROOT`
override, private to the run. The stub `python3` below records the root the runner hands pytest.
"""

import os
import re
import stat
import subprocess
from pathlib import Path

from conftest import REPO_ROOT


def _run(tmp_path: Path, exit_code: int):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    argv = tmp_path / "argv"
    stub = bin_dir / "python3"
    stub.write_text(
        "#!/bin/bash\n"
        'if [ "$1" = -c ]; then exit 0; fi\n'
        f'printf "%s\\n" "$PYTEST_DEBUG_TEMPROOT" > "{argv}"\n'
        f"exit {exit_code}\n"
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    done = subprocess.run(
        ["bash", str(REPO_ROOT / "tools" / "pytest.sh")],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    return done, Path(argv.read_text().strip())


def test_a_run_has_a_private_temp_root_and_a_passing_run_removes_it(tmp_path):
    done, base = _run(tmp_path, 0)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "pytest-of-" not in str(base), base
    assert not base.exists(), f"{base} outlived a passing run"


def test_a_failing_run_keeps_its_temp_root_and_its_exit(tmp_path):
    done, base = _run(tmp_path, 3)
    assert done.returncode == 3, done.stdout + done.stderr
    assert base.is_dir() and str(base) in done.stdout + done.stderr
    base.rmdir()


PYTEST_RUN = re.compile(r"(^|[\s\"])(-m pytest|pytest -q|pytest tests)")


def test_every_script_that_starts_pytest_takes_a_private_root():
    """The class: any goh script that runs a suite, for this repo or a consumer's gate."""
    runners = []
    for path in sorted([*REPO_ROOT.glob("gates/*.sh"), *REPO_ROOT.glob("tools/*.sh")]):
        code = [l for l in path.read_text().splitlines() if not l.lstrip().startswith("#")]
        if any(PYTEST_RUN.search(l) for l in code):
            runners.append(path.relative_to(REPO_ROOT).as_posix())
            assert any("goh_pytest_root" in l for l in code), (
                f"{path.name} runs pytest on the shared root"
            )
    assert {"tools/pytest.sh", "gates/py_gate.sh", "gates/coverage_gate.sh"} <= set(runners), (
        runners
    )
