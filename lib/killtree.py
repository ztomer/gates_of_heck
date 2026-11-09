"""Captured subprocess runs that cannot leave orphans behind.

THE CLASS (2026-08-25). subprocess.run's timeout handling kills only the
DIRECT child. Any gate step shaped `X & Y` — background work under a shell —
left every background process running after each timeout, and repeated gate
failures accumulated stray workers nobody could see.

THE FIX. Run the child in its own SESSION (Popen(start_new_session=True)), so
the direct child and everything it spawns share one process group; on timeout
kill the WHOLE group (os.killpg SIGKILL, falling back to p.kill() where
killpg is unavailable), then drain the pipes so no grandchild holding an fd
can block the caller. subprocess.TimeoutExpired is re-raised unchanged, so
every existing caller's except-clause keeps working.
"""

import os
import signal
import subprocess
import time

TERM_GRACE_S = 5.0  # how long a SIGTERMed group is given to clean up (git removes its .lock files)


def session_pgid(proc: subprocess.Popen) -> int:
    """The process group of a child started with `start_new_session=True`: its own pid.

    KNOWN BY CONSTRUCTION, never asked for. `setsid()` makes the child the leader of a new group
    whose id is its pid, and a pid is not reused while a group of that id has members. Asking the
    kernel instead (`os.getpgid`) is wrong exactly when it matters: macOS answers ESRCH for a
    ZOMBIE (measured 2026-10-05), so a shell that backgrounded a worker and exited had "no group",
    the group kill was skipped, and the worker ran on. `lib/bounded_run.py` shared the bug: a fast
    step's leak read clean on a loaded box.
    """
    return proc.pid


def stop_group(
    pgid: int, grace: float = TERM_GRACE_S, proc: subprocess.Popen | None = None
) -> bool:
    """Stop the process group `pgid`: SIGTERM, up to `grace` seconds for it to empty, then SIGKILL.

    THE CLASS (GOH.GITKILL, 2026-11-08). A SIGKILL gives nothing a chance to clean up. `git commit`
    killed that way dies holding `index.lock` and orphans its hook, and every later commit in the
    repository is refused; on SIGTERM git's own handler removes its lock files. So the group is
    TERMed first and KILLed only if it is still there after the grace. True when the group is gone.
    `proc`, the group's leader when the caller started it, is reaped as it exits: a zombie leader
    keeps its group alive, and a group that has only exited looks like one that is ignoring TERM.
    """
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 2.0)):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return True
        except OSError:
            return False
        end = time.monotonic() + wait
        while time.monotonic() < end:
            if proc is not None:
                proc.poll()
            try:
                os.killpg(pgid, 0)
            except ProcessLookupError:
                return True
            except OSError:
                return False
            time.sleep(0.02)
    return False


def _kill_process_group(proc: subprocess.Popen) -> None:
    """Stop the child's whole process group (TERM, then KILL); fall back to p.kill()."""
    try:
        os.killpg(session_pgid(proc), 0)
    except OSError:
        proc.kill()  # no such group: the child was not started in its own session
        return
    stop_group(session_pgid(proc), proc=proc)


def run_captured(
    cmd, *, shell: bool = False, timeout=None, cwd=None, env=None, input_text=None
) -> subprocess.CompletedProcess:
    """Run `cmd` captured, own session; on timeout kill the whole group,
    drain, and re-raise TimeoutExpired."""
    popen_kwargs = {}
    if os.name == "posix":  # start_new_session is POSIX-only
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE if input_text is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=shell,
        cwd=cwd,
        env=env,
        **popen_kwargs,
    )
    try:
        out, err = proc.communicate(input=input_text, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        try:
            # Drain so grandchildren holding the pipe fds cannot block us.
            proc.communicate(timeout=10)
        except (subprocess.SubprocessError, OSError):
            pass
        raise
    return subprocess.CompletedProcess(proc.args, proc.returncode, stdout=out, stderr=err)
