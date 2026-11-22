"""Which agent session a process belongs to (GOH.STOPOWNSHELL, 2026-11-21).

THE CLASS. Each Bash tool call is a NEW shell, so a process an agent backgrounded in an earlier
call (`python3 -m http.server 8817 &`) is never "started by this shell": that shell exited and the
process was re-parented to launchd. `goh stop` judged ownership by the calling shell alone, so it
refused the agent's own page server, and the lane fell back to a bare `kill`, which nothing guards.

THE RULE. Claude Code exports `CLAUDE_CODE_SESSION_ID` into every tool shell, and a child inherits
it, so the id sits in the environment the process was started with. A process is the caller's
session's when that id is set, non-empty and equal to the caller's own. Another session's process
carries another id and stays refused; a process with no id (started outside any agent) too.

How the start environment is read: Linux `/proc/PID/environ`; macOS `sysctl` KERN_PROCARGS2 (the
kernel gives it for one's own processes only). Unreadable means "not the session's".
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys
from collections.abc import Mapping

VAR = "CLAUDE_CODE_SESSION_ID"
_CTL_KERN, _KERN_PROCARGS2 = 1, 49


def _darwin_block(pid: int) -> bytes:
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    mib = (ctypes.c_int * 3)(_CTL_KERN, _KERN_PROCARGS2, pid)
    size = ctypes.c_size_t(0)
    if libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0 or not size.value:
        return b""
    buf = ctypes.create_string_buffer(size.value)
    if libc.sysctl(mib, 3, buf, ctypes.byref(size), None, 0) != 0:
        return b""
    return buf.raw[: size.value]


def start_env_block(pid: int) -> bytes:
    """The NUL-separated block holding PID's start environment, b"" when it cannot be read."""
    if pid <= 0:
        return b""
    try:
        if sys.platform == "darwin":
            return _darwin_block(pid)
        with open(f"/proc/{pid}/environ", "rb") as f:
            return f.read()
    except (OSError, AttributeError, ValueError):
        return b""


def session_of(pid: int) -> str | None:
    """The agent session id PID was started under, or None."""
    key = VAR.encode() + b"="
    for field in start_env_block(pid).split(b"\0"):
        if field.startswith(key):
            return field[len(key) :].decode("utf-8", "replace") or None
    return None


def mine(pid: int, env: Mapping[str, str] | None = None) -> bool:
    """True when PID was started under the caller's own (non-empty) agent session."""
    own = (os.environ if env is None else env).get(VAR) or ""
    return bool(own) and session_of(pid) == own
