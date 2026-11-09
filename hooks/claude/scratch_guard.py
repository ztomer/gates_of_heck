"""A subagent's fixed-name file written straight into the shared scratchpad ROOT (GOH.AGENTSCRATCH).

Every subagent of a session is handed the coordinator's scratchpad
(`/private/tmp/claude-<uid>/<proj>/<session>/scratchpad`), so two agents that both write
`scratchpad/release.log` overwrite each other's file, and a third reads the wrong one (LOOP.SCRATCH,
2026-11-08). The agent definitions (hooks/claude/agents/repo-worker*.md) say to claim a per-item
path instead: `python3 ~/Projects/.loop/tools/loopctl.py scratch <ITEM-ID> --base <scratchpad>`
(a directory) or `--file NAME` (a fresh file in it). This module is the check that the rule was
kept: `loose_writes()` reads a subagent transcript and returns each scratchpad-root path one of its
tool calls WROTE -- a Write or Edit `file_path`, or a redirect (`>`, `>>`, `&>`), `tee` or
`-o`/`--output` target of every tool `lib/shell_tools.py` lists. A path under a subdirectory (the
item's own) is not loose; a read is not a write. agent_guard.py calls it at SubagentStop and names what it found to the session and on the
ledger's outcome line (`scratch_loose`). Pinned by tests/test_agent_guard_scratch.py.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from shell_tools import COMMAND_FIELD, SHELL_TOOLS  # the ONE list of shell-running tools

# The scratchpad root Claude Code hands a session: /private/tmp/claude-<uid>/<proj>/<uuid>/scratchpad
ROOT = r"(?:/private)?/tmp/claude-\d+/[^/\s'\"]+/[0-9a-f-]{36}/scratchpad"
TARGET = rf"['\"]?({ROOT}/[^\s'\"<>|;&()]+)"
ROOT_FILE = re.compile(rf"^{ROOT}/[^/]+$")
SHELL_WRITE = re.compile(
    rf"(?:&>|\d?>>?|\btee\s+(?:-a\s+)?|(?:\s-o|--output)(?:\s+|=))\s*{TARGET}"
)  # fmt: skip
FILE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")


def _tool_uses(text: str) -> list[tuple[str, dict]]:
    out = []
    for raw in text.splitlines():
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        msg = rec.get("message") if isinstance(rec, dict) else None
        content = msg.get("content") if isinstance(msg, dict) else None
        if rec.get("type") != "assistant" or not isinstance(content, list):
            continue
        for b in content:
            if (
                isinstance(b, dict)
                and b.get("type") == "tool_use"
                and isinstance(b.get("input"), dict)
            ):
                out.append((str(b.get("name")), b["input"]))
    return out


def loose_writes(text: str) -> list[str]:
    """Each distinct scratchpad-root file the transcript's tool calls wrote, first write first."""
    found: list[str] = []
    for name, inp in _tool_uses(text):
        paths: list[str] = []
        if name in FILE_TOOLS:
            paths = [str(inp.get("file_path") or inp.get("notebook_path") or "")]
        elif name in SHELL_TOOLS:
            paths = [m.group(1) for m in SHELL_WRITE.finditer(str(inp.get(COMMAND_FIELD) or ""))]
        for p in paths:
            if ROOT_FILE.match(p) and p not in found:
                found.append(p)
    return found


def message(paths: list[str]) -> str:
    """The one line the session sees."""
    return (
        f"⚠ subagent wrote {len(paths)} fixed-name file(s) into the shared scratchpad root: "
        + ", ".join(paths)
        + ". Another agent can overwrite them; claim a per-item path with `python3 "
        "~/Projects/.loop/tools/loopctl.py scratch <ITEM-ID> --base <scratchpad> [--file NAME]`."
    )
