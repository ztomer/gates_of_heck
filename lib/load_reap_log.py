"""The backstop's log lines (GOH.REAPLOG, 2026-11-02).

A kill the log cannot place in time is a kill nobody can match to the moment a test or a gate lost
its process. So every line the minute job writes starts with an ISO-8601 UTC timestamp, and a kill
line carries the pid, the process's command (truncated), and the rule that killed it. The log is
bounded: `rotate` moves a log past its cap to `<log>.1`, so it holds at most two caps' worth.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import load_reap_report

COMMAND_CHARS = 120
MAX_LOG_BYTES = 256 * 1024


def stamp(when: datetime | None = None) -> str:
    """`2026-11-02T14:03:05Z`: ISO-8601, UTC, to the second."""
    return (when or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")


def line(message: str, when: datetime | None = None) -> str:
    """One stamped log line."""
    return f"{stamp(when)} load-reap: {message}"


def one_line(command: str, limit: int = COMMAND_CHARS) -> str:
    """The command on one line, control characters flattened, cut to `limit` with an ellipsis."""
    flat = " ".join("".join(c if c.isprintable() else " " for c in command).split())
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


def kill_line(kill: load_reap_report.Kill, when: datetime | None = None) -> str:
    """The line one kill leaves: when, pid, rule, command."""
    return line(f"killed pid={kill.pid} rule={kill.rule} cmd={one_line(kill.command)}", when)


def rotate(path: Path, max_bytes: int = MAX_LOG_BYTES) -> bool:
    """Move a log past its cap to `<path>.1` (replacing the last one). True when it moved.

    Run AFTER the pass has written: launchd opens the log per run, so a run's own output is
    already in the file it was handed, and the next run opens a fresh one.
    """
    try:
        if path.stat().st_size <= max_bytes:
            return False
        os.replace(path, path.with_name(path.name + ".1"))
    except OSError:
        return False
    return True
