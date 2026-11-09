"""THE stopped-agent rule (LOOP.LINGERSTOP): what on disk says a subagent has been stopped.

Two readers called an agent that was TaskStopped "alive": goh's agent-stalls (the coordinator's
UserPromptSubmit hook) listed SRV.ARRLIVE's a3804c28b15885a20 as idle and waiting after TaskStop
had stopped it, and `.loop`'s looptick.linger_lines flagged RT.WEBKITFLAKE's a921d818bcfc3397e
"still alive (mid-turn)" for its whole 6 h window. Both read only the agent's own transcript, and a
stopped agent's transcript just ends.

What a stop leaves, probed 2026-11-08 on the coordinator's own session (Claude Code 2.1.317):

* a stop MID-TURN appends a user record `[Request interrupted by user]` to the agent's transcript
  (a921d818bcfc3397e: written at 01:31:58.010, TaskStop's result at 01:31:58.138);
* a stop of an agent that had already ENDED its turn writes nothing to it: not the transcript, not
  `agent-<id>.meta.json` (its `"finished": true` was set at the turn's end, as on every stop), and
  not the task output file `/private/tmp/claude-<uid>/<proj>/<session>/tasks/<id>.output`, which is
  only a symlink to the transcript (a3804c28b15885a20: last write 02:30:34, stopped 02:31:26);
* the stopper's transcript -- the session's `<proj>/<session>.jsonl`, or a sibling subagent's --
  holds the TaskStop tool_result, its `toolUseResult` `{"message": "Successfully stopped task:
  <id> (...)", "task_id": "<id>", ...}`; a TaskStop of an agent the registry no longer runs
  answers `Error: No task found with ID: <id>. Running background agents: ...`.

`stop_marker(transcript)` returns which of these it found ("interrupted", "TaskStop", "TaskStop:
no such task") or None. Stopped is finished. None means UNKNOWN -- nothing on disk says either way
-- and a reader must never call that alive. Only a record carrying the tool's own `toolUseResult`
counts, so a prompt or reply quoting the words stops nothing. Read-only; a file it cannot read says
nothing. Pinned by tests/test_agent_stalls.py.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

INTERRUPTED = "[Request interrupted by user"
STOPPED_OK = "Successfully stopped task: "
STOPPED_NONE = "No task found with ID: "
TAIL_BYTES = 1 << 16


def _json(raw: bytes) -> dict:
    try:
        rec = json.loads(raw)
    except ValueError:
        return {}
    return rec if isinstance(rec, dict) else {}


def _texts(rec: dict) -> list[str]:
    msg = rec.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, str):
        return [content]
    return [str(b.get("text", "")) for b in content or [] if isinstance(b, dict)]


def _interrupted(transcript: Path) -> bool:
    """The transcript's last user/assistant record is the interrupt a stop mid-turn appends."""
    with transcript.open("rb") as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - TAIL_BYTES))
        tail = f.read()
    recs = [r for r in map(_json, tail.splitlines()) if r.get("type") in ("user", "assistant")]
    return bool(recs) and recs[-1].get("type") == "user" and any(
        t.startswith(INTERRUPTED) for t in _texts(recs[-1])
    )  # fmt: skip


def _is_tool_result(rec: dict) -> bool:
    msg = rec.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    return isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in content
    )  # fmt: skip


def _stop_result(rec: dict, agent_id: str) -> str | None:
    """The marker a TaskStop tool_result for `agent_id` is, or None."""
    if rec.get("type") != "user" or not _is_tool_result(rec):
        return None
    res = rec.get("toolUseResult")
    if isinstance(res, dict) and res.get("task_id") == agent_id:
        return "TaskStop" if str(res.get("message", "")).startswith(STOPPED_OK) else None
    if isinstance(res, str) and re.search(rf"{STOPPED_NONE}{re.escape(agent_id)}\b", res):
        return "TaskStop: no such task"
    return None


def session_files(transcript: Path) -> list[Path]:
    """Where a TaskStop of this subagent can be recorded: the session transcript that spawned it
    (`<proj>/<session>.jsonl` beside `<proj>/<session>/subagents/`) and its sibling subagents."""
    subagents = transcript.parent
    siblings = sorted(p for p in subagents.glob("agent-*.jsonl") if p != transcript)
    return [subagents.parent.with_suffix(".jsonl"), *siblings]


def stop_marker(transcript: Path) -> str | None:
    """What says the subagent whose transcript this is was stopped, or None (unknown, never alive)."""
    try:
        if _interrupted(transcript):
            return "interrupted"
    except OSError:
        pass
    agent_id = transcript.stem.removeprefix("agent-")
    needles = (f"{STOPPED_OK}{agent_id}".encode(), f"{STOPPED_NONE}{agent_id}".encode())
    found = None
    for path in session_files(transcript):
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if not any(n in data for n in needles):
            continue  # the common case: one scan of the bytes, no JSON parsed
        for raw in data.splitlines():
            if any(n in raw for n in needles):
                marker = _stop_result(_json(raw), agent_id)
                if marker == "TaskStop":
                    return marker
                found = found or marker
    return found
