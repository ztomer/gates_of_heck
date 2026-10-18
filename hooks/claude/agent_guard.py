#!/usr/bin/env python3
"""A Claude Code hook on subagent spawns: name the model, carry the report contract, keep a ledger.

Subagents spend most of the tokens, so economy is set where a session delegates (owner ruling
2026-10-18, `~/.claude/CLAUDE.md` "Delegation is where the tokens go"). Its rules 1 and 3 were kept
by hand; this hook keeps them structurally (tests/test_claude_agent_guard.py):

* PreToolUse, matcher `Agent|Task`: a spawn naming no `model` is refused, and the reason lists the
  choice criteria -- an unnamed one would silently inherit the session's model. A spawn it allows
  gets the report contract appended to its prompt (once: a respawn carrying it is left alone).
  Agent types where the model choice does not matter are exempt from both (MODEL_EXEMPT).
* Every allowed spawn is one line in `$XDG_CACHE_HOME/goh/agent-spawns.jsonl`; SubagentStop adds
  the outcome line -- tokens summed from the agent's transcript, the report's `Status:` -- so a
  wrong model choice in either direction is visible as data.

Output shape, proven live on Claude Code 2.1.292: a refusal is `hookSpecificOutput`
`permissionDecision: "deny"` + `permissionDecisionReason` (the reason reaches the model as the
tool's error); `updatedInput` ALONE replaces the tool input, so no `permissionDecision: "allow"`
is sent and the permission system decides exactly what it would have without this hook.

It fails OPEN: input it cannot read, or a ledger it cannot write, never blocks a spawn.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

SPAWN_TOOLS = {"Agent", "Task"}  # `Task` is the tool's legacy name

# Agent types where the model choice does not matter: read-only lookups and meta agents, and
# `fork`, which ignores `model`. They need no model and get no contract -- none of them lands
# commits, so commits / CI / red->green do not apply -- but their spawns are still ledgered.
MODEL_EXEMPT = frozenset({"Explore", "claude-code-guide", "statusline-setup", "fork"})

CONTRACT = (
    "Report contract: your final report is at most 15 lines. Its first line is "
    "`Status: landed|blocked|failed|answered`; then commits (sha + one line each), CI run id + "
    "conclusion, red->green proof in one sentence, what is left open in one line. No narrative."
)
STATUSES = ("landed", "blocked", "failed", "answered")
STATUS_LINE = re.compile(r"\s*status:\s*(\w+)\s*$", re.IGNORECASE)

CRITERIA = (
    "Sonnet only when ALL hold: the repo and files are named; the done-state is checkable (a red "
    "test exists or is specified); the fix is known (a port, a leak, a rename, a one-file bug); no "
    "design or UI choice; one session. Opus when ANY holds: unknown root cause, design/UI, a "
    "cross-module refactor, an audit or review, security, multi-session."
)

# The declared-choice table (`Model-choice: <model> because <tags>`): per model, the tags it
# requires ("all") or accepts ("any"); the contradiction phrases a sonnet brief may not carry; and
# the audit's thresholds. HELD EMPTY by owner ruling 2026-10-18 until the criteria research lands:
# an empty table enforces nothing beyond rule 1, and the ledger records `tags: []`.
CHOICE_RULES: dict[str, dict[str, frozenset[str]]] = {}
CONTRADICTION_PHRASES: tuple[str, ...] = ()


def ledger_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "goh" / "agent-spawns.jsonl"


def _append(record: dict[str, Any]) -> str | None:
    """Append one ledger line; the warning to show when it could not be written, else None."""
    try:
        path = ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, sort_keys=True) + "\n"
        with path.open("a", encoding="utf-8") as f:  # one write: O_APPEND keeps lines whole
            f.write(line)
    except OSError as e:
        return f"agent_guard: the spawn ledger was not written ({e}); the spawn is not blocked."
    return None


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def _first_line(text: str) -> str:
    return next((ln.strip() for ln in text.splitlines() if ln.strip()), "")[:200]


def on_spawn(event: dict[str, Any]) -> dict[str, Any] | None:
    """The PreToolUse output for an Agent/Task call, or None to let it run untouched."""
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    prompt = tool_input.get("prompt")
    prompt = prompt if isinstance(prompt, str) else ""
    agent_type = tool_input.get("subagent_type") or "general-purpose"
    model = tool_input.get("model")
    model = model.strip() if isinstance(model, str) else ""
    exempt = agent_type in MODEL_EXEMPT
    if not exempt and not model:
        reason = (
            f"Refused: this {agent_type} spawn names no model, so it would inherit the session's. "
            f'{CRITERIA} Spawn again and pass model explicitly ("sonnet" or "opus").'
        )
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                       "permissionDecision": "deny",
                                       "permissionDecisionReason": reason}}  # fmt: skip
    out: dict[str, Any] = {}
    final = prompt
    if not exempt and isinstance(tool_input.get("prompt"), str) and CONTRACT not in prompt:
        final = prompt.rstrip() + "\n\n" + CONTRACT
        out["hookSpecificOutput"] = {
            "hookEventName": "PreToolUse",
            "updatedInput": dict(tool_input, prompt=final),
        }
    warning = _append({
        "event": "spawn", "time": _now(), "session_id": event.get("session_id"),
        "tool_use_id": event.get("tool_use_id"), "model": model or None,
        "subagent_type": agent_type, "exempt": exempt, "tags": [],
        "description": tool_input.get("description"),
        "prompt_sha": hashlib.sha256(final.encode()).hexdigest(), "first_line": _first_line(prompt),
    })  # fmt: skip
    if warning:
        out["systemMessage"] = warning
    return out or None


def _usage(transcript: Path) -> dict[str, int] | None:
    """Tokens the agent spent: usage summed over its assistant messages, each message ONCE (a
    message streamed as several transcript entries repeats its usage on every entry)."""
    try:
        text = transcript.read_text(encoding="utf-8")
    except OSError:
        return None
    seen: dict[str, dict[str, Any]] = {}
    for raw in text.splitlines():
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        msg = entry.get("message") if isinstance(entry, dict) else None
        if isinstance(msg, dict) and isinstance(msg.get("usage"), dict):
            seen[str(msg.get("id") or len(seen))] = msg["usage"]
    keys = {"input": "input_tokens", "output": "output_tokens",
            "cache_creation": "cache_creation_input_tokens", "cache_read": "cache_read_input_tokens"}  # fmt: skip
    tokens = {k: sum(int(u.get(v) or 0) for u in seen.values()) for k, v in keys.items()}
    tokens["total"] = sum(tokens.values())
    return tokens


def _status(report: str) -> str:
    m = STATUS_LINE.match(report.splitlines()[0]) if report.strip() else None
    word = m.group(1).lower() if m else ""
    return word if word in STATUSES else "unreported"


def on_stop(event: dict[str, Any]) -> str | None:
    """Record a finished subagent's outcome; the ledger warning, if any. Never blocks the stop."""
    raw_path = event.get("agent_transcript_path")
    transcript = Path(raw_path) if isinstance(raw_path, str) and raw_path else None
    tool_use_id = None
    if transcript is not None:
        meta = transcript.with_name(transcript.name.removesuffix(".jsonl") + ".meta.json")
        try:
            tool_use_id = json.loads(meta.read_text(encoding="utf-8")).get("toolUseId")
        except (OSError, ValueError, AttributeError):
            tool_use_id = None
    report = event.get("last_assistant_message")
    return _append({
        "event": "outcome", "time": _now(), "session_id": event.get("session_id"),
        "tool_use_id": tool_use_id, "agent_id": event.get("agent_id"),
        "agent_type": event.get("agent_type"),
        "status": _status(report if isinstance(report, str) else ""),
        "tokens": _usage(transcript) if transcript is not None else None,
    })  # fmt: skip


def main() -> int:
    try:
        event = json.load(sys.stdin)
        if not isinstance(event, dict):
            return 0
        name = event.get("hook_event_name")
        if name == "PreToolUse" and event.get("tool_name") in SPAWN_TOOLS:
            out = on_spawn(event)
            if out:
                json.dump(out, sys.stdout)
        elif name == "SubagentStop":
            warning = on_stop(event)
            if warning:
                print(warning, file=sys.stderr)
    except Exception as e:  # noqa: BLE001 -- fail OPEN: a guard bug must never block every spawn
        print(f"agent_guard: input not judged ({e!r})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
