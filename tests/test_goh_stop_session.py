"""`goh stop` and the agent session (GOH.STOPOWNSHELL, `lib/session_owner.py`): each Bash tool call
is a new shell, so a process an agent backgrounded in an EARLIER call is nobody's child. goh stop
accepts it when its start environment carries the caller's `CLAUDE_CODE_SESSION_ID`, still refuses
another session's, and the kill-by-name refusal names `goh stop` only for pids it would accept."""

from __future__ import annotations

import signal
import subprocess
import sys
from collections.abc import Iterator

import pytest
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import paved_path
import pid_owner
import session_owner

pytestmark = pytest.mark.load_sensitive(reason="spawns a process and waits for goh stop's TERM")
STOP = REPO_ROOT / "lib" / "proc_stop.py"
PY = sys.executable
ME = "goh-test-session-mine"


def _spawn(session: str) -> pid_owner.Stamp:
    """A process an earlier "tool call" (a shell that has exited) started under SESSION."""
    script = f'"{PY}" -c "import time; time.sleep(60)" >/dev/null 2>&1 & echo $!'
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False,
                       stdin=subprocess.DEVNULL, timeout=patience(60),
                       env=hermetic_env(CLAUDE_CODE_SESSION_ID=session))  # fmt: skip
    stamp = pid_owner.stamp(int(r.stdout.strip()))
    assert stamp is not None
    return stamp


@pytest.fixture
def mine() -> Iterator[pid_owner.Stamp]:
    stamp = _spawn(ME)
    yield stamp
    pid_owner.signal(stamp, signal.SIGKILL)


@pytest.fixture
def theirs() -> Iterator[pid_owner.Stamp]:
    stamp = _spawn("goh-test-session-other")
    yield stamp
    pid_owner.signal(stamp, signal.SIGKILL)


def _stop(pid: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run([PY, str(STOP), str(pid)], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=patience(120), check=False,
                          env=hermetic_env(CLAUDE_CODE_SESSION_ID=ME))  # fmt: skip


def test_the_session_of_a_process_is_read_from_its_start_environment(
    mine: pid_owner.Stamp, theirs: pid_owner.Stamp
) -> None:
    assert session_owner.session_of(mine.number) == ME
    assert session_owner.mine(mine.number, {session_owner.VAR: ME})
    assert not session_owner.mine(theirs.number, {session_owner.VAR: ME})
    assert not session_owner.mine(mine.number, {})  # no session of one's own: nothing is mine


def test_an_earlier_calls_process_of_this_session_is_stopped(mine: pid_owner.Stamp) -> None:
    r = _stop(mine.number)
    assert r.returncode == 0, r.stderr
    assert pid_owner.gone(mine)


def test_another_sessions_process_is_refused_and_left_running(theirs: pid_owner.Stamp) -> None:
    r = _stop(theirs.number)
    assert r.returncode == 2, r.stderr
    assert "not yours to stop" in r.stderr
    assert pid_owner.alive(theirs)


def test_the_refusal_names_goh_stop_only_for_pids_it_would_accept(
    mine: pid_owner.Stamp, theirs: pid_owner.Stamp, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(session_owner.VAR, ME)
    line = paved_path.stop_command([theirs.number, mine.number])
    assert f"goh stop {mine.number}`" in line and str(theirs.number) not in line, line
    only_theirs = paved_path.stop_command([theirs.number])
    assert "--stamp" in only_theirs and str(theirs.number) not in only_theirs, only_theirs
