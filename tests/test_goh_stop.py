"""`goh stop TARGET...` (`lib/proc_stop.py`, GOH.WAITSTOP): it stops processes the caller started
-- a child of the calling shell, or a stamp `goh stop --stamp` took while it was one -- with their
descendants, and refuses every other pid, stopping nothing."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import pid_owner

pytestmark = pytest.mark.load_sensitive(
    reason="each test spawns processes through bash and waits for goh stop's TERM-grace-KILL"
)
STOP = REPO_ROOT / "lib" / "proc_stop.py"
PY = sys.executable
LONG = "import time; time.sleep(60)"


def _stop(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([PY, str(STOP), *args], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, env=hermetic_env(), timeout=patience(120),
                          check=False)  # fmt: skip


def _bash(script: str, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", "-c", script, "bash", *args], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, env=hermetic_env(**env),
                          timeout=patience(120), check=False)  # fmt: skip


@pytest.fixture
def orphan() -> Iterator[pid_owner.Stamp]:
    """A process started by a shell that has exited: nobody's child, as an agent's `cmd &` from an
    earlier Bash call is. Reaped through its stamp."""
    # Another agent session's (GOH.STOPOWNSHELL): this session's own earlier process is stoppable.
    r = _bash(f'"{PY}" -c "{LONG}" >/dev/null 2>&1 & echo $!', CLAUDE_CODE_SESSION_ID="other")
    stamp = pid_owner.stamp(int(r.stdout.strip()))
    assert stamp is not None
    try:
        yield stamp
    finally:
        pid_owner.signal(stamp, signal.SIGKILL)


def test_a_child_of_the_calling_shell_is_stopped() -> None:
    r = _bash(f'"{PY}" -c "{LONG}" & child=$!; "{PY}" "$1" "$child"; echo "rc=$?";'
              ' kill -0 "$child" 2>/dev/null && echo alive || echo gone', str(STOP))  # fmt: skip
    assert "rc=0" in r.stdout and "gone" in r.stdout, r.stdout + r.stderr


def test_a_stamp_taken_by_the_starting_shell_stops_it_later(tmp_path: Path) -> None:
    pidfile = tmp_path / "x.pid"
    r = _bash(f'"{PY}" -c "{LONG}" >/dev/null 2>&1 & "{PY}" "$1" --stamp $! > "$2"',
              str(STOP), str(pidfile))  # fmt: skip
    assert r.returncode == 0, r.stderr
    stamp = pid_owner.read_stamp(pidfile)
    assert stamp is not None
    try:
        r = _stop(str(pidfile))
        assert r.returncode == 0, r.stderr
        assert pid_owner.gone(stamp)
    finally:
        pid_owner.signal(stamp, signal.SIGKILL)


def test_a_pid_the_caller_did_not_start_is_refused_and_left_running(
    orphan: pid_owner.Stamp,
) -> None:
    r = _stop(str(orphan.number))
    assert r.returncode == 2, r.stderr
    assert "not yours to stop" in r.stderr and "goh stop --stamp" in r.stderr, r.stderr
    assert pid_owner.alive(orphan)


def test_stamping_a_pid_the_caller_did_not_start_is_refused(orphan: pid_owner.Stamp) -> None:
    r = _stop("--stamp", str(orphan.number))
    assert r.returncode == 2, r.stderr
    assert r.stdout == ""


def test_one_refusal_stops_nothing(orphan: pid_owner.Stamp) -> None:
    r = _bash(f'"{PY}" -c "{LONG}" & child=$!; "{PY}" "$1" "$child" "$2"; echo "rc=$?";'
              ' kill -0 "$child" 2>/dev/null && echo alive || echo gone; kill "$child"',
              str(STOP), str(orphan.number))  # fmt: skip
    assert "rc=2" in r.stdout and "alive" in r.stdout, r.stdout + r.stderr
    assert pid_owner.alive(orphan)


def test_a_stamp_whose_number_was_reused_signals_nothing(orphan: pid_owner.Stamp) -> None:
    r = _stop(f"{orphan.number} Mon Jan  1 00:00:00 2001")
    assert r.returncode == 0, r.stderr
    assert "already gone" in r.stderr + r.stdout
    assert pid_owner.alive(orphan)


def test_the_caller_and_its_parents_are_refused() -> None:
    for pid in (os.getpid(), os.getppid(), 1):
        r = _stop(str(pid))
        assert r.returncode == 2, (pid, r.stderr)


def test_descendants_go_with_the_process(tmp_path: Path) -> None:
    kid = tmp_path / "kid"
    r = _bash(f'bash -c \'"{PY}" -c "{LONG}" & echo $! > "$0"; wait\' "$2" & sh=$!;'
              ' for _ in $(seq 100); do [ -s "$2" ] && break; sleep 0.1; done;'
              ' "$1" "$3" "$sh"; echo "rc=$?"', PY, str(kid), str(STOP))  # fmt: skip
    assert "rc=0" in r.stdout, r.stdout + r.stderr
    grandchild = int(kid.read_text().strip())
    assert pid_owner.number_gone(grandchild), r.stdout


@pytest.mark.parametrize("target", ["notapid", "/nonexistent/x.pid"])
def test_a_target_that_is_no_pid_is_refused(target: str) -> None:
    assert _stop(target).returncode == 2
