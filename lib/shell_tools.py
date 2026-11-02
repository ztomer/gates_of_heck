"""The Claude Code tools whose input holds a shell command (GOH.MONGUARD, 2026-11-02).

ONE list. The command guards (`hooks/claude/discard_guard.py`, `hold_guard.py`) judge a command
from any tool named here exactly as they judge Bash's, and `hooks/claude/registrations.json`
registers each command judge on every member (pinned by `tests/test_monitor_guard.py`). The field
that holds the command is `command` in each.

A tool that runs a shell command and is missing here is a tool the guards never see: Monitor was
exactly that until a ZoneWM agent's first Monitor wait was a self-matching `pgrep -f`.
"""

from __future__ import annotations

SHELL_TOOLS: tuple[str, ...] = ("Bash", "Monitor")
COMMAND_FIELD = "command"
