"""The step wrapper's command-line contract, held by BOTH implementations (roadmap 4C.1).

`lib/bounded_run.py` bounds every gate step; `goh step` is its native port (one process instead of a
Python start-up per step). Every case runs against both, so the port is proven to say and do the
same thing: the step's own code passed through, a ceiling that times out with 124 and its message
and reaches a grandchild, a leak named, 127 for a command that does not exist, 2 for a usage error
or a non-positive ceiling, a GOH_TIMINGS line, a TERM/INT/HUP to the wrapper sweeping the step's
group, and a signal ignored at entry staying ignored (nohup's contract).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from timing_bounds import GiveUp, assert_sooner
from conftest import REPO_ROOT, hermetic_env, native_goh_path


@pytest.fixture(params=["python", "native"])
def wrapper(request) -> list[str]:
    if request.param == "python":
        return [sys.executable, str(REPO_ROOT / "lib" / "bounded_run.py")]
    return [str(native_goh_path()), "step"]


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for(path: Path, timeout: float = 15) -> str:
    give_up = GiveUp(timeout)
    while not give_up.passed():
        if path.exists() and path.read_text().strip():
            return path.read_text().strip()
        time.sleep(0.02)
    raise AssertionError(f"{path} never appeared")


def _run(wrapper, *args, **kw):
    return subprocess.run(
        [*wrapper, *args],
        capture_output=True,
        text=True,
        timeout=60,
        # A caller's own `stdin=` or `input=` wins; otherwise the child reads nothing.
        stdin=kw.pop("stdin", None if "input" in kw else subprocess.DEVNULL),
        **kw,
    )


def test_the_steps_own_code_passes_through(wrapper) -> None:
    assert _run(wrapper, "--timeout", "30", "--", "/bin/sh", "-c", "exit 0").returncode == 0
    assert _run(wrapper, "--timeout", "30", "--", "/bin/sh", "-c", "exit 7").returncode == 7


def test_a_ceiling_times_out_with_124_and_says_so(wrapper) -> None:
    t0 = time.monotonic()
    r = _run(wrapper, "--timeout", "1", "--grace", "1", "--label", "slow", "--", "sleep", "60")
    assert r.returncode == 124, r.stdout + r.stderr
    assert "TIMED OUT after 1s: slow" in r.stderr, r.stderr
    assert_sooner(time.monotonic() - t0, 2, 60, "a 1 s ceiling over a 60 s sleep")


def test_the_ceiling_sweep_reaches_a_grandchild(wrapper, tmp_path: Path) -> None:
    pid = tmp_path / "pid"
    r = _run(wrapper, "--timeout", "1", "--grace", "1", "--", "/bin/bash", "-c",
             f"sleep 300 & echo $! > {pid}; wait")  # fmt: skip
    assert r.returncode == 124
    grandchild = int(_wait_for(pid))
    give_up = GiveUp(5)
    while _alive(grandchild) and not give_up.passed():
        time.sleep(0.05)
    try:
        assert not _alive(grandchild), "the sweep left the step's grandchild running"
    finally:
        if _alive(grandchild):
            os.kill(grandchild, signal.SIGKILL)


def test_a_step_that_leaves_a_process_is_named(wrapper, tmp_path: Path) -> None:
    pid = tmp_path / "pid"
    # The leak's own output goes nowhere: holding the captured pipe, it made this run wait out its
    # whole sleep (30 s) -- the trap lib/bounded_run.py's docs name.
    r = _run(wrapper, "--timeout", "30", "--", "/bin/sh", "-c",
             f"sleep 30 >/dev/null 2>&1 & echo $! > {pid}")  # fmt: skip
    leaked = int(_wait_for(pid))
    try:
        assert r.returncode == 0, r.stderr
        assert "left 1 process(es) running under it" in r.stderr and str(leaked) in r.stderr, (
            r.stderr
        )
    finally:
        if _alive(leaked):
            os.kill(leaked, signal.SIGKILL)


def test_a_clean_step_says_nothing(wrapper) -> None:
    r = _run(wrapper, "--timeout", "30", "--", "/bin/sh", "-c", "exit 0")
    assert (r.returncode, r.stderr) == (0, ""), r.stderr


def test_a_command_that_does_not_exist_is_127(wrapper) -> None:
    assert _run(wrapper, "--timeout", "30", "--", "/nonexistent/goh-no-such").returncode == 127


@pytest.mark.parametrize("args", [[], ["--timeout", "30"], ["--timeout", "0", "--", "true"],
                                  ["--timeout", "-5", "--", "true"]])  # fmt: skip
def test_usage_and_a_non_positive_ceiling_are_exit_two(wrapper, args) -> None:
    assert _run(wrapper, *args).returncode == 2


def test_a_timed_step_writes_one_timings_line(wrapper, tmp_path: Path) -> None:
    log = tmp_path / "t.jsonl"
    # hermetic: a gate this suite runs under exports its own GOH_TIMINGS_KIND (rust_gate.sh)
    env = hermetic_env(GOH_TIMINGS=str(log))
    _run(wrapper, "--timeout", "30", "--label", "lbl", "--", "/bin/sh", "-c", "exit 3", env=env)
    (row,) = [json.loads(line) for line in log.read_text().splitlines()]
    assert (row["label"], row["rc"], row["tier"]) == ("lbl", 3, "step"), row
    assert "kind" not in row, row


def test_a_timed_step_records_its_gates_kind(wrapper, tmp_path: Path) -> None:
    """Both writers carry the kind, or the native tier's steps read as uncalibrated (5.3)."""
    log = tmp_path / "t.jsonl"
    env = hermetic_env(GOH_TIMINGS=str(log), GOH_TIMINGS_KIND="rust")
    _run(wrapper, "--timeout", "30", "--label", "lbl", "--", "/bin/sh", "-c", "exit 0", env=env)
    (row,) = [json.loads(line) for line in log.read_text().splitlines()]
    assert row["kind"] == "rust" and "cpu_ms" in row, row


def test_the_steps_child_does_not_inherit_the_gates_kind(wrapper, tmp_path: Path) -> None:
    """The kind labels the gate's own steps: a child that inherited it labelled a test suite's
    fixtures and every nested tool with the outer gate's kind (5.3, goh's own push profile)."""
    log = tmp_path / "t.jsonl"
    env = hermetic_env(GOH_TIMINGS=str(log), GOH_TIMINGS_KIND="rust")
    probe = 'printf "kind=%s" "${GOH_TIMINGS_KIND-}"'
    r = _run(wrapper, "--timeout", "30", "--label", "lbl", "--", "/bin/sh", "-c", probe, env=env)
    assert "kind=" in r.stdout and "kind=rust" not in r.stdout, r.stdout


@pytest.mark.parametrize(
    "sig", [signal.SIGTERM, signal.SIGINT, signal.SIGHUP], ids=lambda s: s.name
)
def test_a_signal_to_the_wrapper_sweeps_the_step(wrapper, tmp_path: Path, sig) -> None:
    pid = tmp_path / "pid"
    proc = subprocess.Popen(
        [*wrapper, "--timeout", "60", "--grace", "1", "--", "/bin/bash", "-c",
         f"sleep 300 & echo $! > {pid}; wait"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        preexec_fn=lambda: signal.signal(sig, signal.SIG_DFL), stdin=subprocess.DEVNULL,
    )  # fmt: skip
    grandchild = None
    try:
        grandchild = int(_wait_for(pid))
        proc.send_signal(sig)
        proc.wait(timeout=10)
        give_up = GiveUp(5)
        while _alive(grandchild) and not give_up.passed():
            time.sleep(0.05)
        assert not _alive(grandchild), f"{sig.name} left the step running"
        assert proc.returncode != 0, "an interrupted step must not read as a pass"
    finally:
        proc.kill()
        proc.wait()
        if grandchild and _alive(grandchild):
            os.kill(grandchild, signal.SIGKILL)


def test_a_signal_ignored_at_entry_stays_ignored(wrapper, tmp_path: Path) -> None:
    pid = tmp_path / "pid"
    proc = subprocess.Popen(
        [*wrapper, "--timeout", "60", "--", "/bin/bash", "-c", f"sleep 300 & echo $! > {pid}; wait"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        preexec_fn=lambda: signal.signal(signal.SIGHUP, signal.SIG_IGN), stdin=subprocess.DEVNULL,
    )  # fmt: skip
    grandchild = None
    try:
        grandchild = int(_wait_for(pid))
        proc.send_signal(signal.SIGHUP)
        time.sleep(0.5)
        assert proc.poll() is None and _alive(grandchild), "an ignored SIGHUP stopped the run"
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        if grandchild and _alive(grandchild):
            os.kill(grandchild, signal.SIGKILL)
