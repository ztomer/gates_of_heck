#!/usr/bin/env python3
"""The loop's one-writer hold, judged at COMMIT whoever runs git (GOH.HOLDSUBPROC).

THE GAP (GOH.GRACEEND, 2026-11-15). `hooks/claude/hold_guard.py` judges the tool call Claude Code
shows it, so a commit a Python subprocess made in a held repo went through; only the push was
caught. A hook is told the subagent's `agent_id`, a process it starts is told nothing.

THE STAMP. The Bash guard (`hooks/claude/discard_guard.py`, the one hook that rewrites a shell
command) prefixes every SUBAGENT's command with `export GOH_LOOP_AGENT=<its agent id>` through
`updatedInput` (no permission decision is made), in front of any earlier rewrite (the heavy-job
admission). Every process that shell starts -- python, make, a script calling git -- inherits it.
The main thread has no agent id and is never stamped: the coordinator and the owner are no agent.

THE CHECK. The repo's own pre-commit hook (`gates/hook.sh`) runs `commit_hold.py check` before the
commit gate: in a repo an in-progress item holds (`lib/queue_hold.py`, the same judgement the file
tools get), a commit by a stamped agent that is not the holder is refused, naming the holder. An
unstamped commit passes, as does any commit when the queue is missing or unreadable (fail open,
like the tool guard). Pinned by tests/test_commit_hold.py.
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import queue_hold

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tui.lib import err, warn

AGENT_ENV = "GOH_LOOP_AGENT"
_SAFE = re.compile(r"[A-Za-z0-9_-]{1,64}")


def stamp(event: dict, output: dict | None) -> dict | None:
    """OUTPUT (a PreToolUse answer, or None) with a subagent Bash command stamped with its agent."""
    agent = str(event.get("agent_id") or "")
    tool_input = event.get("tool_input")
    if event.get("tool_name") != "Bash" or not isinstance(tool_input, dict):
        return output
    if not _SAFE.fullmatch(agent):
        return output
    spec = (output or {}).get("hookSpecificOutput") or {}
    base = spec.get("updatedInput") or tool_input
    command = f"export {AGENT_ENV}={agent}\n{base.get('command') or ''}"
    spec = {"hookEventName": "PreToolUse", **spec, "updatedInput": {**base, "command": command}}
    return {**(output or {}), "hookSpecificOutput": spec}


def check(cwd: str, env: dict[str, str]) -> str | None:
    """Why a commit in CWD by the agent ENV names is refused, or None."""
    agent = env.get(AGENT_ENV, "").strip()
    if not agent:
        return None
    hold = queue_hold.find(cwd, cwd, agent)
    return hold.describe(f"a commit (git, from any process) in {hold.root}") if hold else None


def main(argv: list[str]) -> int:
    if argv[1:] != ["check"]:
        print("usage: commit_hold.py check", file=sys.stderr)
        return 2
    try:
        why = check(os.getcwd(), dict(os.environ))
    except Exception as e:  # noqa: BLE001 -- the hold fails open, saying why
        warn(f"commit hold: not judged ({type(e).__name__}: {e})")
        return 0
    if why:
        err(f"commit refused (GOH.HOLDSUBPROC): {why}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
