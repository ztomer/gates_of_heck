"""A subagent's direct `gh` CI read, past loopctl's one door (GOH.GHDOOR).

Agents polling CI straight through `gh run watch/view/list` or `gh api .../actions` spent the
estate's one GitHub token until GitHub throttled it, and every agent's CI read failed together
(LOOP.GHAGENTS, .loop 93d0fc7). The agent definitions (hooks/claude/agents/repo-worker*.md) say CI
is read only when the brief asks, and then only through `python3 ~/Projects/.loop/tools/loopctl.py
ci <full-sha> --repo OWNER/NAME [--wait]`, whose exit 75 means "throttled until <time>".

`calls_in(command)` is the ONE matcher: each `gh run watch|view|list`, `gh api <path with an
/actions segment>` or `gh pr checks --watch` at COMMAND position -- a simple command of the shell
line (split on `;`, `&&`, `||`, `|`, `(`, `)` and backticks, a `$(...)` inside a double-quoted
word included), past env assignments, wrappers and an `rtk` prefix -- so a `grep 'gh run watch'
file`, a commit message or a heredoc body that quotes the rule is not one. Two callers:

* PREVENTION (LOOP.GHDOOR, 2026-11-11: one agent's four parallel `gh run watch` spent the estate's
  whole 5,000/h budget, and every gh call estate-wide got 403 for the rest of the hour):
  `hold_guard.py`, the PreToolUse guard on every shell tool, refuses a SUBAGENT's call with
  `refusal()`, which names `loopctl ci`. The main thread (the coordinator) is not refused.
* DETECTION: `direct_calls()` reads a subagent transcript at SubagentStop (agent_guard.py) and
  names each call it tried, to the session and on the ledger's outcome line (`ci_direct`).

Pinned by tests/test_agent_guard_ci_door.py and tests/test_foreign_edit.py.
"""

from __future__ import annotations

import os
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import hold_guard  # the shell line's code, heredocs removed, and its wrapper stripping
import scratch_guard  # the transcript's tool calls, one reader
from shell_tools import COMMAND_FIELD, SHELL_TOOLS  # the ONE list of shell-running tools

_LEAD = r"(?:^|[;&|(`\n]|\$\()\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*(?:rtk\s+)?"
CALL = re.compile(  # the fallback for a line shlex cannot split (an unbalanced quote)
    _LEAD + r"(gh\s+(?:run\s+(?:watch|view|list)\b|api\s+['\"]?\S*/actions\b)[^;&|\n)`]*)"
)
_PUNCT = ";&|<>()`"
_ACTIONS = re.compile(r"(?:^|/)actions(?:/|\?|$)")
_RUN_READS = {"watch", "view", "list"}


def _is_call(seg: list[str]) -> bool:
    seg = hold_guard._strip_prefix(seg)
    while seg and os.path.basename(seg[0]) == "rtk":
        seg = seg[1:]
    if len(seg) < 2 or os.path.basename(seg[0]) != "gh":
        return False
    verb, rest = seg[1], seg[2:]
    if verb == "run":
        return bool(rest) and rest[0] in _RUN_READS
    if verb == "api":
        return any(_ACTIONS.search(a) for a in rest if not a.startswith("-"))
    if verb == "pr":
        return bool(rest) and rest[0] == "checks" and "--watch" in rest
    return False


def calls_in(command: str, _depth: int = 0) -> list[str]:
    """Each direct gh CI call at command position in one shell line, first first."""
    try:
        lex = shlex.shlex(hold_guard._code(command), posix=True, punctuation_chars=_PUNCT)
        lex.whitespace_split = True
        lex.commenters = "#"
        tokens = list(lex)
    except ValueError:
        return [m.group(1).strip() for m in CALL.finditer(command)]
    found: list[str] = []
    seg: list[str] = []
    for tok in [*tokens, ";"]:
        if tok and set(tok) <= set(_PUNCT):
            if _is_call(seg):
                found.append(" ".join(hold_guard._strip_prefix(seg)).removeprefix("rtk "))
            seg = []
            continue
        seg.append(tok)
        for mark in ("$(", "`"):  # a substitution inside a double-quoted word still runs
            if mark in tok and _depth < 3:
                found += calls_in(tok.split(mark, 1)[1], _depth + 1)
    return list(dict.fromkeys(found))


def direct_calls(text: str) -> list[str]:
    """Each distinct direct `gh` CI call the transcript's shell commands tried, first first."""
    found: list[str] = []
    for name, inp in scratch_guard.tool_uses(text):
        if name not in SHELL_TOOLS:
            continue
        for call in calls_in(str(inp.get(COMMAND_FIELD) or "")):
            if call not in found:
                found.append(call)
    return found


DOOR = "python3 ~/Projects/.loop/tools/loopctl.py ci <full-sha> --repo OWNER/NAME --wait"


def refusal(calls: list[str]) -> str:
    """The PreToolUse refusal a subagent's direct call gets (LOOP.GHDOOR)."""
    shown = "; ".join(f"`{c}`" for c in calls[:3]) + (" ..." if len(calls) > 3 else "")
    return (
        f"Refused: {shown} polls GitHub Actions straight through gh. On 2026-11-11 one agent's "
        "four parallel `gh run watch` spent the estate's whole 5,000/h API budget and every gh "
        "call estate-wide got 403 for the rest of the hour. CI has ONE door, which caches, "
        f"serializes and waits out a low budget: `{DOOR}` (exit 75 = throttled until the time "
        "it prints, not a failure and not a green). Read CI only when your brief asks for it; "
        "otherwise your work ends at the push and the coordinator reads CI."
    )


def message(calls: list[str]) -> str:
    """The one line the session sees."""
    shown = "; ".join(f"`{c}`" for c in calls[:3]) + (" ..." if len(calls) > 3 else "")
    return (
        f"⚠ subagent called gh for CI directly ({shown}; hold_guard.py refuses it when "
        "registered). "
        "CI is read only through "
        "`python3 ~/Projects/.loop/tools/loopctl.py ci <full-sha> --repo OWNER/NAME [--wait]`, "
        "whose exit 75 is a throttle, not a failure."
    )
