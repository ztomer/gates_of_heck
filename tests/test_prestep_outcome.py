"""A run's deadline bounds its RUN, a finished run is never called a deadline, and a run says
its end once (GOH.PRESTEPMSGS).

2026-11-21/22, two enqueue pre-steps were "killed by their deadline" at 879 s of a 1500 s budget
(GOH.DIFFCOV0CFCB5A1) and 804 s of 1560 s (GOH.PRESTEPMOVED), after the log had said `green verdict
... is kept`. Neither waited for a slot (waited 0 s). THE CAUSE is CPython 3.15's
`Popen.wait(timeout=T)` on macOS: the kqueue wait returns when the child's EXIT event arrives, a
child that is exiting is not yet reapable (waitpid(WNOHANG) says 0), and the fallback then takes
the time already waited off the deadline a SECOND time (`endtime -= elapsed`): a child that ends
after more than T/2 s is a `TimeoutExpired` at the moment it ends, its real status thrown away. So
tools/load.py and lib/pre_deadline.py wait through lib/proc_deadline.py, which polls. The fixtures
below model the kernel's exiting window (`windowed.py`) and hold a slot so a run has a wait.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest
from conftest import REPO_ROOT
from test_land_prewait import _holder
from test_one_admission import _env
from test_pre_deadline import _land
from timing_bounds import patience

LOAD = REPO_ROOT / "tools" / "load.py"
PRE_DEADLINE = REPO_ROOT / "lib" / "pre_deadline.py"
pytestmark = pytest.mark.xdist_group("load_orphans")  # with tests/test_load_tool.py

# The kernel's window between a process's EXIT event and its being reapable: `waitpid(WNOHANG)`
# says 0 for `WINDOW` asks once the child has exited (peeked with WNOWAIT, which reaps nothing).
WINDOWED = textwrap.dedent(
    """
    import os, runpy, subprocess, sys
    WINDOW, state, real = 6, {}, os.waitpid
    def windowed(pid, flags):
        if pid > 0 and flags & os.WNOHANG:
            if pid not in state:
                try:
                    peek = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                except ChildProcessError:
                    peek = None
                if peek is not None:
                    state[pid] = WINDOW
            if state.get(pid):
                state[pid] -= 1
                return (0, 0)
        return real(pid, flags)
    os.waitpid = subprocess._del_safe.waitpid = windowed
    sys.argv = sys.argv[1:]
    runpy.run_path(sys.argv[0], run_name="__main__")
    """
)


def _windowed(tmp_path: Path, argv: list[str], **env: str) -> subprocess.CompletedProcess[str]:
    driver = tmp_path / "windowed.py"
    driver.write_text(WINDOWED, encoding="utf-8")
    return subprocess.run([sys.executable, str(driver), *argv], capture_output=True, text=True,
                          env=_env(tmp_path, GOH_GATE_SLOTS="2", **env), stdin=subprocess.DEVNULL,
                          timeout=patience(60), check=False)  # fmt: skip


@pytest.mark.load_sensitive
def test_a_wrapped_command_that_ends_in_the_exiting_window_keeps_its_own_status(
    tmp_path: Path,
) -> None:
    """`sleep 2` under a 3 s deadline ends after more than half of it: the stdlib wait read the
    exiting window as the deadline. Its exit 7 is the pre-step's verdict, never 124."""
    r = _windowed(tmp_path, [str(PRE_DEADLINE), "run", "--mark", str(tmp_path / "mark"),
                             "--floor", "3s", "--ceiling", "3s", "--",
                             "bash", "-c", "sleep 2; exit 7"])  # fmt: skip
    assert r.returncode == 7, r.stderr
    assert "expired" not in r.stderr, r.stderr


def test_load_keeps_a_commands_status_when_it_ends_in_the_exiting_window(tmp_path: Path) -> None:
    r = _windowed(tmp_path, [str(LOAD), "--background-ok", "test", "--max", "3", "0", "--",
                             "bash", "-c", "sleep 2; exit 7"])  # fmt: skip
    assert r.returncode == 7, r.stdout + r.stderr
    assert "deadline of" not in r.stdout + r.stderr, r.stdout + r.stderr


def _slot_wait_then(
    tmp_path: Path, seconds: float, load_args: list[str]
) -> subprocess.CompletedProcess[str]:
    """load.py (a gate of the pre lane) queued behind a held slot that is released `seconds` after
    it SAYS it waits (a fixed timer raced the interpreter's start under load: waited 0 s): a run
    that WAITED."""
    holder = _holder(tmp_path, "pre")
    lines: list[str] = []
    timers: list[threading.Timer] = []
    with subprocess.Popen(
        [sys.executable, str(LOAD), "--background-ok", "test", *load_args], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        env=_env(tmp_path, GOH_GATE_SLOTS="1", GOH_GATE_CLASS="pre"),
    ) as proc:  # fmt: skip

        def pump() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                lines.append(line)
                if "waiting for one" in line and not timers:
                    timers.append(threading.Timer(seconds, holder.release))
                    timers[0].start()

        reader = threading.Thread(target=pump)
        reader.start()
        try:
            proc.wait(timeout=patience(60))
            reader.join(timeout=patience(10))
        finally:
            for timer in timers:
                timer.cancel()
            holder.release()
            if proc.poll() is None:
                proc.kill()
    return subprocess.CompletedProcess(proc.args, proc.returncode, "".join(lines), "")


@pytest.mark.load_sensitive
def test_a_run_that_waited_for_a_slot_still_gets_its_whole_run_budget(tmp_path: Path) -> None:
    """3 s of slot wait then 2 s of run under a 4 s deadline: the deadline is the RUN's."""
    r = _slot_wait_then(tmp_path, 3, ["--max", "4", "0", "--", "sleep", "2"])
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "deadline of" not in out, out


@pytest.mark.load_sensitive
def test_a_deadline_names_run_seconds_and_wait_seconds_apart(tmp_path: Path) -> None:
    r = _slot_wait_then(tmp_path, 3, ["--max", "2", "0", "--", "sleep", "60"])
    out = r.stdout + r.stderr
    assert r.returncode == 124, out
    (line,) = [x for x in out.splitlines() if "deadline of" in x]
    assert "ran 2 s" in line and ("waited 3 s" in line or "waited 4 s" in line), line
    assert "spinners stopped" not in out, out  # one outcome: the kill, not also a clean finish


def test_a_land_whose_pre_step_hits_its_deadline_says_so_once(repo: Path, tmp_path: Path) -> None:
    r, _ = _land(tmp_path, repo, cores=12, pre="sleep 9")  # 3 s of deadline at 12 workers
    out = r.stdout + r.stderr
    ended = [x for x in out.splitlines() if "no verdict" in x or "expired" in x or "deadline" in x]
    assert len(ended) == 1, ended
    assert "waited" in ended[0] and "ran" in ended[0], ended[0]


# THE CLASS: every `.wait(timeout=...)` on a Popen in the house's own code, by file. What is left
# bounds a wait on a process that was already killed (a grace) or ticks at a short poll, where a
# late TimeoutExpired is swallowed; a deadline that decides a verdict waits through proc_deadline.
GRACE_WAITS = {
    "checks/_gate_slot_probe.py": 2,
    "checks/check_mcp_wire.py": 1,
    "lib/bounded_run.py": 2,
    "lib/idle_suite.py": 1,
    "lib/load_tree.py": 1,
    "lib/pgid_cpu.py": 1,
    "tools/load.py": 2,
}
TIMED_WAIT = __import__("re").compile(r"\.wait\(\s*timeout\s*=")


def _timed_waits(root: Path) -> dict[str, int]:
    found: dict[str, int] = {}
    for folder in ("lib", "tools", "checks", "gates", "hooks"):
        for path in sorted((root / folder).rglob("*.py")):
            if path.name == "proc_deadline.py":
                continue
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            hits = sum(1 for x in lines if TIMED_WAIT.search(x) and not x.lstrip().startswith("#"))
            if hits:
                found[str(path.relative_to(root))] = hits
    return found


def test_no_deadline_waits_through_the_stdlib_timed_wait() -> None:
    assert _timed_waits(REPO_ROOT) == GRACE_WAITS


def test_the_class_gate_sees_a_timed_wait_it_was_not_told_of(tmp_path: Path) -> None:
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "x.py").write_text("proc.wait(timeout=5)\n# proc.wait(timeout=5)\n")
    assert _timed_waits(tmp_path) == {"lib/x.py": 1}
