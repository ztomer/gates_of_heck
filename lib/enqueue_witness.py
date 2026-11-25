"""A detached enqueue that a signal ends says which signal, and who was alive to send it
(GOH.ENQUEUEVANISH).

THE CASE. `goh land --enqueue` runs its pre-step in a process detached from the caller (its own
session, parent 1). On 2026-11-23 three of GOH.TIMINGDETECT's died mid pre-step: their log ended at
"pre-step before queueing", their pre-step's group ran on under init, and no record was filed. A
Python process ended by SIGTERM, SIGHUP or most other signals prints nothing, so nothing in the log
could name the sender, and nothing on the machine was recording signals.

THE READING. `install()` gives each catchable fatal signal a handler that writes the signal's name
and a process table (`ps -axww -o pid,ppid,pgid,etime,command`) to stderr, the enqueue's log, then
dies of the same signal with its default action. A sender that TERMs and then waits, as every goh
stopper does (lib/killtree.py, lib/landq_supersede.py, lib/proc_stop.py), is in that table. A
SIGKILL cannot be caught; lib/landq_inflight.py `died` records that case from outside.

The handler is Python's (`signal.signal`): it runs in the main thread between bytecodes, never in
the kernel's signal context, so it may run `ps`. The process still dies of the signal it got, so a
supersede or a `--cancel` stops it exactly as before.

A detached enqueue that a signal ends names the signal and the processes alive then, the sender
among them (GOH.ENQUEUEVANISH): `install` gives SIGTERM, SIGHUP, SIGINT, SIGQUIT, SIGUSR1/2,
SIGALRM and SIGXCPU a handler that writes a full-width `ps` table to the enqueue log, then dies of
the same signal. Installed by `note_child`, in an enqueuer only.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import time

FATAL = ("SIGTERM", "SIGHUP", "SIGINT", "SIGQUIT", "SIGUSR1", "SIGUSR2", "SIGALRM", "SIGXCPU")
PS_TIMEOUT_S = 10.0
PS = ["ps", "-axww", "-o", "pid,ppid,pgid,etime,command"]  # -ww: whole command lines


def table() -> str:
    """The process table now, or why it could not be read."""
    try:
        got = subprocess.run(PS, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                             timeout=PS_TIMEOUT_S, check=False)  # fmt: skip
    except (OSError, subprocess.SubprocessError) as e:
        return f"(no process table: {e})\n"
    return got.stdout


def _say(text: str) -> None:
    with contextlib.suppress(OSError):
        os.write(2, text.encode("utf-8", "replace"))


def _witness(number: int, _frame: object) -> None:
    name = signal.Signals(number).name
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    _say(f"enqueue pid {os.getpid()} got {name} at {stamp} (GOH.ENQUEUEVANISH); "
         "the processes alive then, the sender among them:\n")  # fmt: skip
    _say(table())
    signal.signal(number, signal.SIG_DFL)
    os.kill(os.getpid(), number)


def install() -> None:
    """Every catchable fatal signal of this process witnessed, then obeyed."""
    for name in FATAL:
        with contextlib.suppress(AttributeError, OSError, ValueError):
            signal.signal(getattr(signal, name), _witness)
