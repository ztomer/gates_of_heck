"""The whole process table, read the one way a loaded box allows (GOH.SELFGATEPS)."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import killtree
import pgid_cpu

# How long the process listing may spend NO CPU with nothing runnable before it is called hung.
# The listing is never capped by the wall clock: at load 50-150 a `ps -ax` is starved, not hung, and
# a 30 s wall cap failed whole lands (GOH.SELFGATEPS, tests/test_ps_table_unbounded.py).
PS_STALL = 30


def live_processes() -> list[tuple[int, int, str]]:
    """Every live process: its pid, its parent, and its whole command line.

    Judged by CPU, as the house judges every gate (lib/pgid_cpu.py): the listing runs in its own
    session, its output goes to an unlinked file (nothing reads a pipe while it runs), and only a
    STALL -- no CPU spent, nothing runnable, for PS_STALL seconds -- stops it.
    """
    with tempfile.TemporaryFile() as sink:
        proc = subprocess.Popen(
            ["ps", "-ww", "-ax", "-o", "pid=,ppid=,command="],
            stdin=subprocess.DEVNULL,
            stdout=sink,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        pgid = killtree.session_pgid(proc)
        expired = pgid_cpu.wait_for_exit(
            proc, pgid, sys.maxsize, "ps -ax", cpu_timeout=None, stall=PS_STALL
        )
        if expired is not None:
            killtree.stop_group(pgid, proc=proc)
            proc.wait()
            raise RuntimeError(f"the process listing hung: {expired}")
        sink.seek(0)
        text = sink.read().decode("utf-8", "replace")
    out: list[tuple[int, int, str]] = []
    for line in text.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) != 3:
            continue
        try:
            out.append((int(parts[0]), int(parts[1]), parts[2]))
        except ValueError:
            continue
    return out
