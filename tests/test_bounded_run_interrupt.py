"""A step in its own session must still stop when the gate running it is stopped.

`bounded_run.py` starts every step with `start_new_session` so the ceiling can kill the whole
tree. The same session detaches the step from the signals that stop the GATE: Ctrl-C reaches the
terminal's foreground group, which the step has left, and a SIGTERM to the wrapper killed the
wrapper alone. Either way the step ran on, unowned -- the orphan this machinery exists to prevent,
made by the machinery. More likely with GOH_CI_JOBS, where several steps are in flight at once.
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import REPO_ROOT


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_for(path: Path, seconds: float = 10) -> str:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if path.exists() and path.read_text().strip():
            return path.read_text().strip()
        time.sleep(0.05)
    raise AssertionError(f"{path} never written")


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT, signal.SIGHUP])
def test_a_signal_to_the_wrapper_reaps_the_step(tmp_path, sig):
    pidfile = tmp_path / "step.pid"
    wrapper = subprocess.Popen(
        [
            sys.executable,
            str(REPO_ROOT / "lib" / "bounded_run.py"),
            "--timeout",
            "60",
            "--grace",
            "1",
            "--",
            "/bin/bash",
            "-c",
            f"sleep 300 & echo $! > {pidfile}; wait",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        # The signal's disposition is the TEST's to set: under a non-job-control shell (this suite
        # run as a local_ci step) a background job starts with SIGINT ignored, and an ignore
        # inherited at entry is honoured -- measured, that made this test red only under local_ci.
        preexec_fn=lambda: signal.signal(sig, signal.SIG_DFL),
    )
    try:
        step = int(_wait_for(pidfile))
        wrapper.send_signal(sig)
        wrapper.wait(timeout=10)
        deadline = time.monotonic() + 5
        while _alive(step) and time.monotonic() < deadline:
            time.sleep(0.05)
        why = wrapper.stderr.read().decode(errors="replace") if wrapper.stderr else ""
        assert not _alive(step), (
            f"{sig.name} stopped the wrapper (rc {wrapper.returncode}) and left its step running; "
            f"wrapper said: {why!r}"
        )
        assert wrapper.returncode != 0, "an interrupted step must not read as a pass"
    finally:
        wrapper.kill()
        wrapper.wait()
        try:
            os.kill(int(pidfile.read_text()), signal.SIGKILL)
        except (OSError, ValueError):
            pass


def test_a_signal_ignored_at_entry_stays_ignored(tmp_path):
    """nohup's contract: the caller ignored SIGHUP on purpose, and the wrapper must not undo it."""
    pidfile = tmp_path / "step.pid"
    wrapper = subprocess.Popen(
        [
            sys.executable,
            str(REPO_ROOT / "lib" / "bounded_run.py"),
            "--timeout",
            "60",
            "--",
            "/bin/bash",
            "-c",
            f"sleep 300 & echo $! > {pidfile}; wait",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        preexec_fn=lambda: signal.signal(signal.SIGHUP, signal.SIG_IGN),
    )
    try:
        step = int(_wait_for(pidfile))
        wrapper.send_signal(signal.SIGHUP)
        time.sleep(0.5)
        assert wrapper.poll() is None and _alive(step), "an ignored SIGHUP stopped the run"
    finally:
        wrapper.terminate()
        wrapper.wait(timeout=10)
        try:
            os.kill(int(pidfile.read_text()), signal.SIGKILL)
        except (OSError, ValueError):
            pass


def test_a_term_to_a_concurrent_local_ci_reaps_every_running_step(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    pids = [tmp_path / f"s{i}.pid" for i in range(2)]
    steps = ":".join(f"sleep 300 & echo $! > {p}; wait" for p in pids)
    (repo / ".gatesrc").write_text(f"GOH_CI_STEPS='{steps}'\nGOH_CI_JOBS=2\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GOH_CI_")}
    ci = subprocess.Popen(
        ["/bin/bash", str(REPO_ROOT / "gates" / "local_ci.sh")],
        cwd=repo,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        steps_running = [int(_wait_for(p)) for p in pids]
        ci.send_signal(signal.SIGTERM)
        ci.wait(timeout=15)
        deadline = time.monotonic() + 8
        while any(map(_alive, steps_running)) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not any(map(_alive, steps_running)), "TERM to local_ci left its running steps alive"
        assert ci.returncode != 0
    finally:
        ci.kill()
        ci.wait()
        for p in pids:
            try:
                os.kill(int(p.read_text()), signal.SIGKILL)
            except (OSError, ValueError):
                pass
