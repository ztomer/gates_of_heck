#!/usr/bin/env python3
"""Register a spawned lane on its queue item the moment its Agent call returns (GOH.SPAWNREGISTER).

The coordinator starts an item before it spawns the lane (`agent` reads `pending-spawn`) and could
record the lane's id (`loopctl start ID AGENTID`) only after the Agent tool returned it -- by which
time the lane was often writing, and the queue guard (`lib/queue_hold.py`) refused its first write
(LOOP.STARTID, 7 recurrences by 2026-11-19). This PostToolUse hook on Agent|Task closes the gap:
it fires as the tool returns, before the coordinator's next turn.

* the item: the first `Item <ID>` / `item <ID>` in `tool_input.prompt` (the brief) that names an
  in-progress item whose agent is still `pending-spawn`; anything else is left alone;
* the agent id, as probed on this machine (Claude Code transcripts, 2026-11-18): an async spawn's
  response is `{"isAsync": true, "status": "async_launched", "agentId": "<id>", ...}`; its text
  form says `agentId: <id> (internal ID ...)`, read too in case the hook sees only the content;
* one writer: it runs `.loop`'s own `loopctl --queue Q start ID AGENT` (an estate item keeps its
  narrowing: `--repos` its `holds`, else `--estate`), from the queue's directory with
  `PYTHONPATH=$GOH_DIR:.` and that directory's `.venv` python when there is one.

Seam: GOH_LOOP_QUEUE (queue_hold.queue_path). Exit 0 on a no-op; a loopctl that refuses or fails
is exit 1 with its words on stderr: a visible, non-blocking hook error. Never exit 2.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "lib"))
import queue_hold  # noqa: E402  (the queue path seam and the pending-spawn marker)

ITEM = re.compile(r"\b[Ii]tem\s+([A-Z][A-Z0-9_]*(?:\.[A-Za-z0-9_]+)+)")
AGENT_TEXT = re.compile(r"\bagentId:\s*([A-Za-z0-9_-]+)")


def agent_id(response: object) -> str | None:
    """The spawned agent's id from a PostToolUse Agent response, structured or textual."""
    if isinstance(response, dict):
        for key in ("agentId", "agent_id"):
            if isinstance(response.get(key), str) and response[key].strip():
                return response[key].strip()
        return agent_id(response.get("text")) or agent_id(response.get("content"))
    if isinstance(response, list):
        return next((a for a in map(agent_id, response) if a), None)
    if isinstance(response, str):
        m = AGENT_TEXT.search(response)
        return m.group(1) if m else None
    return None


def pending_item(prompt: str, queue: str) -> dict | None:
    """The first item the brief names that is in progress with its agent still pending."""
    try:
        with open(queue, encoding="utf-8") as fh:
            items = json.load(fh).get("items") or []
    except (OSError, ValueError, AttributeError):
        return None
    by_id = {i.get("id"): i for i in items if isinstance(i, dict)}
    for item_id in ITEM.findall(prompt):
        i = by_id.get(item_id.rstrip("."))
        if i and i.get("status") == "in_progress" and i.get("agent") == queue_hold.PENDING_SPAWN:
            return i
    return None


def start_argv(queue: str, item: dict, agent: str) -> list[str]:
    loop = os.path.dirname(os.path.abspath(queue))
    venv = os.path.join(loop, ".venv", "bin", "python")
    python = venv if os.access(venv, os.X_OK) else sys.executable
    argv = [python, os.path.join(loop, "tools", "loopctl.py"), "--queue", queue, "start",
            str(item["id"]), agent]  # fmt: skip
    if str(item.get("repo") or "").strip() == queue_hold.ESTATE:
        holds = [t.strip() for t in str(item.get("holds") or "").split(",") if t.strip()]
        argv += ["--repos", ",".join(holds)] if holds else ["--estate"]
    return argv


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    if not isinstance(event, dict):
        return 0
    prompt = str((event.get("tool_input") or {}).get("prompt") or "")
    agent = agent_id(event.get("tool_response"))
    if not prompt or not agent:
        return 0
    queue = queue_hold.queue_path()
    item = pending_item(prompt, queue)
    if item is None:
        return 0
    loop = os.path.dirname(os.path.abspath(queue))
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([_ROOT, "."])}
    try:
        r = subprocess.run(start_argv(queue, item, agent), cwd=loop, env=env, text=True,
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=20,
                           check=False)  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"spawn_register: loopctl start {item['id']} {agent}: {exc}", file=sys.stderr)
        return 1
    if r.returncode != 0:
        print(f"spawn_register: loopctl start {item['id']} {agent} refused: "
              f"{(r.stderr or r.stdout).strip()}", file=sys.stderr)  # fmt: skip
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
