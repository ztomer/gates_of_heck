"""tools/pytest.sh refuses a second suite in one checkout (G2e, 2026-10-25).

Two suites of one tree share its bin/, its target/ and its proven cache, and each ran at half speed
beside the other: the full gate ran its suite twice side by side until G2e. The runner takes a
lock (a symlink to its pid, in the checkout's own git dir) and a second top-level run is refused
with the holder named; a holder that died is reaped. The stub interpreter (GOH_PY, as in
tests/test_pytest_runner_basetemp.py) records what the runner would have run.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from conftest import REPO_ROOT


def _run(tmp_path: Path, lock: Path) -> tuple[subprocess.CompletedProcess, Path]:
    seen = tmp_path / "seen"
    stub = tmp_path / "python3"
    stub.write_text(
        f'#!/bin/bash\nif [ "$1" = -c ]; then exit 0; fi\nreadlink "{lock}" > "{seen}"\nexit 0\n'
    )
    stub.chmod(0o755)
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("GATES_OF_HECK_SUITE_RUNNER", "GATES_OF_HECK_SUITE_NESTED_OK")
    }
    env.update(GOH_PY=str(stub), GOH_PY_OK=str(stub), GATES_OF_HECK_SUITE_LOCK=str(lock))
    done = subprocess.run(
        ["bash", str(REPO_ROOT / "tools" / "pytest.sh")],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    return done, seen


def test_a_running_suite_holds_the_lock_and_releases_it(tmp_path: Path) -> None:
    lock = tmp_path / "goh-suite.lock"
    done, seen = _run(tmp_path, lock)
    assert done.returncode == 0, done.stdout + done.stderr
    assert seen.read_text().strip().isdigit(), "the suite ran without holding the lock"
    assert not lock.is_symlink(), "a finished run left its lock behind"


def test_a_second_suite_in_the_checkout_is_refused_naming_the_holder(tmp_path: Path) -> None:
    lock = tmp_path / "goh-suite.lock"
    os.symlink(str(os.getpid()), lock)  # this test is alive: a live holder
    done, seen = _run(tmp_path, lock)
    assert done.returncode == 2, done.stdout + done.stderr
    assert f"another goh suite (pid {os.getpid()})" in done.stderr, done.stderr
    assert not seen.exists(), "the refused run reached pytest"
    assert os.readlink(lock) == str(os.getpid()), "the refused run took the holder's lock"


def test_a_dead_holders_lock_is_reaped(tmp_path: Path) -> None:
    gone = subprocess.Popen(["true"], stdin=subprocess.DEVNULL)
    gone.wait()
    lock = tmp_path / "goh-suite.lock"
    os.symlink(str(gone.pid), lock)
    done, seen = _run(tmp_path, lock)
    assert done.returncode == 0, done.stdout + done.stderr
    assert seen.read_text().strip() not in ("", str(gone.pid)), seen.read_text()
    assert not lock.is_symlink()
