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


def _kill_process_group(proc: subprocess.Popen) -> None:
    """SIGKILL the child's whole process group; fall back to p.kill()."""
    pgid = None
    try:
        pgid = os.getpgid(proc.pid)
    except (OSError, AttributeError):
        # OSError: already reaped — nothing to kill.
        # AttributeError: os.getpgid is absent on this platform (it is
        # POSIX-only); fall through to the direct-child kill like any other
        # unsupported-killpg environment. Half-guarding this left a missing
        # getpgid able to escape the timeout handler as a raw traceback.
        pass
    if pgid is not None and hasattr(os, "killpg"):
        try:
            os.killpg(pgid, signal.SIGKILL)
            return
        except OSError:
            pass  # group already gone
    proc.kill()


def run_captured(cmd, *, shell: bool = False, timeout=None, cwd=None,
                 env=None, input_text=None) -> subprocess.CompletedProcess:
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
    return subprocess.CompletedProcess(
        proc.args, proc.returncode, stdout=out, stderr=err)
