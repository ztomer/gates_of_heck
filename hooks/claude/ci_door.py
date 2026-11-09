"""A subagent's direct `gh` CI read, past loopctl's one door (GOH.GHDOOR).

Agents polling CI straight through `gh run watch/view/list` or `gh api .../actions` spent the
estate's one GitHub token until GitHub throttled it, and every agent's CI read failed together
(LOOP.GHAGENTS, .loop 93d0fc7). The agent definitions (hooks/claude/agents/repo-worker*.md) say CI
is read only when the brief asks, and then only through `python3 ~/Projects/.loop/tools/loopctl.py
ci <full-sha> --repo OWNER/NAME [--wait]`, whose exit 75 means "throttled until <time>".

This module is the check that the rule was kept: `direct_calls()` reads a subagent transcript and
returns each shell command that RAN such a call -- `gh` at command position (the line's start, or
after `;`, `&&`, `||`, `|`, `(`, `$(` or a backtick, past env assignments and an `rtk` prefix), so
a `grep 'gh run watch' file` that quotes the rule is not one. agent_guard.py calls it at
SubagentStop and names what it found to the session and on the ledger's outcome line
(`ci_direct`). Pinned by tests/test_agent_guard_ci_door.py.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import scratch_guard  # the transcript's tool calls, one reader
from shell_tools import COMMAND_FIELD, SHELL_TOOLS  # the ONE list of shell-running tools

_LEAD = r"(?:^|[;&|(`\n]|\$\()\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*(?:rtk\s+)?"
CALL = re.compile(
    _LEAD + r"(gh\s+(?:run\s+(?:watch|view|list)\b|api\s+['\"]?\S*/actions\b)[^;&|\n)`]*)"
)


def direct_calls(text: str) -> list[str]:
    """Each distinct direct `gh` CI call the transcript's shell commands ran, first first."""
    found: list[str] = []
    for name, inp in scratch_guard.tool_uses(text):
        if name not in SHELL_TOOLS:
            continue
        for m in CALL.finditer(str(inp.get(COMMAND_FIELD) or "")):
            call = m.group(1).strip()
            if call not in found:
                found.append(call)
    return found


def message(calls: list[str]) -> str:
    """The one line the session sees."""
    shown = "; ".join(f"`{c}`" for c in calls[:3]) + (" ..." if len(calls) > 3 else "")
    return (
        f"⚠ subagent read CI straight through gh ({shown}). CI is read only through "
        "`python3 ~/Projects/.loop/tools/loopctl.py ci <full-sha> --repo OWNER/NAME [--wait]`, "
        "whose exit 75 is a throttle, not a failure."
    )
