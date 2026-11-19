"""The two halves of "a test fake never outlives its pytest" (GOH.HOLDERORPHAN, 2026-11-19).

A fake gate, holder or peer that a test starts in its OWN session waits on a file the test will
write. When the test process dies without a finalizer (an interrupted run, SIGKILL, a closed
terminal) nobody writes it: the fake waits forever, PPID 1, holding a slot or a lock. A rule that
"tests should clean up" did not stop that, so the leak is made unrepresentable twice over:

  * `wait_for_file`: the shell loop every such fake uses. It ends when the file appears (0), when
    the test process that started the fake is gone (EXIT_PARENT_GONE) and after a bounded
    deadline (EXIT_DEADLINE), whichever comes first. It is the SEAM: no fake spells its own loop.
  * `adopt`: hands the fake's Popen to the suite's owner (`load_kit`), whose per-test fixture, the
    session's last act and a SIGTERM of pytest all kill its whole process group.

`tests/test_fake_gate_orphan.py` proves both against the failure itself, and
`tests/test_fakes_cannot_orphan.py` keeps a new fake from being written without them.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
from pathlib import Path

import load_kit

EXIT_PARENT_GONE = 143  # the test process that started the fake is gone: nobody will release it
EXIT_DEADLINE = 142  # nobody released it in time: a fake never waits forever
DEADLINE_S = 900  # far past any wait a test makes (timing_bounds.patience tops out well below)


def wait_for_file(
    release: Path | str, *, parent: int | None = None, seconds: float = DEADLINE_S
) -> str:
    """Shell text: wait for `release` to exist, or leave when the test is gone or time is up.

    `parent` is the pid of the process whose death ends the wait; it defaults to the process
    building the snippet, which is the test (or the pytest worker) that is about to spawn the fake.
    """
    pid = os.getpid() if parent is None else parent
    # A counter, not $SECONDS: this text also runs under dash (`/bin/sh -c`), which has no SECONDS.
    return (
        f"__n=0; while [ ! -e '{release}' ]; do "
        f"kill -0 {pid} 2>/dev/null || exit {EXIT_PARENT_GONE}; "
        f"[ $__n -lt {int(seconds * 20)} ] || exit {EXIT_DEADLINE}; "
        "__n=$((__n + 1)); sleep 0.05; done"
    )


def wait_for_file_py(
    release: Path | str, *, parent: int | None = None, seconds: float = DEADLINE_S
) -> str:
    """The same wait as Python source, for a fake that is a `python -c` program (top level)."""
    pid = os.getpid() if parent is None else parent
    return (
        "import os as _os, sys as _sys, time as _time\n"
        "_t0 = _time.monotonic()\n"
        f"while not _os.path.exists({str(release)!r}):\n"
        "    try:\n"
        f"        _os.kill({pid}, 0)\n"
        "    except ProcessLookupError:\n"
        f"        _sys.exit({EXIT_PARENT_GONE})\n"
        "    except PermissionError:\n"
        "        pass\n"
        f"    if _time.monotonic() - _t0 >= {float(seconds)}:\n"
        f"        _sys.exit({EXIT_DEADLINE})\n"
        "    _time.sleep(0.05)\n"
    )


class Adopted(load_kit.Owned):
    """A session leader the suite did not start through `Owned`, taken over by its owner.

    Its whole PROCESS GROUP is the owner's to kill, once, even when the leader is already gone:
    the leader of a fake gate exits and leaves the slot holder it spawned (GOH.HOLDERORPHAN).
    """

    def __init__(self, proc: subprocess.Popen) -> None:
        self.log = None
        self.directory = None
        self.proc = proc
        self._group_taken = False
        load_kit.own(self)

    def stop(self) -> None:
        if self.proc.poll() is None:
            with contextlib.suppress(ProcessLookupError, OSError):
                self.proc.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.proc.wait(timeout=load_kit.GRACE_S)
        if not self._group_taken:
            self._group_taken = True
            with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                os.killpg(self.proc.pid, signal.SIGKILL)
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.proc.wait(timeout=load_kit.GRACE_S)


def adopt(proc: subprocess.Popen) -> subprocess.Popen:
    """The suite owns this session leader and its group from here on; returns it unchanged."""
    Adopted(proc)
    return proc
