"""GOH.SPAWNREGISTER: a spawned lane is registered when its Agent call returns, not after.

The coordinator could record a lane (`loopctl start ID AGENTID`) only once the Agent tool had
returned the agent's id, and a lane often wrote first: the queue guard (`lib/queue_hold.py`) refused
its first write because the item's agent still read `pending-spawn` (LOOP.STARTID, 7 recurrences by
2026-11-19). `hooks/claude/spawn_register.py`, a PostToolUse hook on Agent|Task, reads the item id
from the brief and the agent id from the tool response and runs `loopctl start` for an in-progress
item still pending its spawn. A write that still races it is refused saying registration is pending.

The payload was probed on this machine (Claude Code transcripts, 2026-11-18): an async spawn's
response is `{"isAsync": true, "status": "async_launched", "agentId": "a96647bd1b97ecadd", ...}`,
and its text content carries `agentId: a96647bd1b97ecadd (internal ID ...)`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

HOOK = REPO_ROOT / "hooks" / "claude" / "spawn_register.py"
GUARD = REPO_ROOT / "hooks" / "claude" / "hold_guard.py"
AGENT = "a96647bd1b97ecadd"
BRIEF = "Model-choice: opus@medium because x\n\nItem GOH.LANE in gates_of_heck. Branch: b."

# A stand-in for .loop's loopctl: records how it was called and applies `start` to the queue.
FAKE_LOOPCTL = r"""
import json, os, sys
a = sys.argv[1:]
q = a[a.index("--queue") + 1]
open(os.path.join(os.path.dirname(q), "calls.jsonl"), "a").write(json.dumps(
    {"argv": a, "cwd": os.getcwd(), "pythonpath": os.environ.get("PYTHONPATH", "")}) + "\n")
rest = a[a.index("--queue") + 2:]
data = json.load(open(q))
for i in data["items"]:
    if i["id"] == rest[1]:
        i["status"], i["agent"] = "in_progress", rest[2]
json.dump(data, open(q, "w"))
"""


@pytest.fixture
def loop(tmp_path: Path) -> Path:
    d = tmp_path / "Projects" / ".loop"
    (d / "tools").mkdir(parents=True)
    (d / "tools" / "loopctl.py").write_text(FAKE_LOOPCTL)
    (tmp_path / "Projects" / "gates_of_heck" / ".git").mkdir(parents=True)
    _items(d, [{"id": "GOH.LANE", "repo": "gates_of_heck", "status": "in_progress",
                "agent": "pending-spawn"}])  # fmt: skip
    return d


def _items(loop: Path, items: list[dict]) -> None:
    (loop / "queue.json").write_text(json.dumps({"items": items}))


def _env(loop: Path) -> dict[str, str]:
    return hermetic_env(GOH_LOOP_QUEUE=str(loop / "queue.json"))


def _fire(loop: Path, prompt: str = BRIEF, response: object = None, tool: str = "Agent"):
    if response is None:
        response = {"isAsync": True, "status": "async_launched", "agentId": AGENT}
    event = {"session_id": "s", "hook_event_name": "PostToolUse", "tool_name": tool,
             "tool_input": {"prompt": prompt, "description": "d"},
             "tool_response": response}  # fmt: skip
    return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(event), text=True,
                          capture_output=True, timeout=patience(30), check=False,
                          env=_env(loop))  # fmt: skip


def _calls(loop: Path) -> list[dict]:
    f = loop / "calls.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


def _agent(loop: Path, item: str = "GOH.LANE") -> str:
    items = json.loads((loop / "queue.json").read_text())["items"]
    return next(i for i in items if i["id"] == item)["agent"]


def test_a_pending_spawn_item_is_registered_through_loopctl(loop: Path) -> None:
    r = _fire(loop)
    assert r.returncode == 0, r.stderr
    (call,) = _calls(loop)
    assert call["argv"] == ["--queue", str(loop / "queue.json"), "start", "GOH.LANE", AGENT]
    assert Path(call["cwd"]).resolve() == loop.resolve()
    assert call["pythonpath"].split(os.pathsep) == [str(REPO_ROOT), "."]
    assert _agent(loop) == AGENT


@pytest.mark.parametrize(
    "response",
    [
        [
            {
                "type": "text",
                "text": f"Async agent launched successfully.\nagentId: {AGENT} (internal)",
            }
        ],
        f"Async agent launched successfully.\nagentId: {AGENT} (internal ID)",
        {"content": [{"type": "text", "text": f"agentId: {AGENT} (internal)"}]},
    ],
    ids=["content-list", "text", "dict-content"],
)
def test_the_agent_id_is_read_from_the_response_text_too(loop: Path, response: object) -> None:
    assert _fire(loop, response=response).returncode == 0
    assert _agent(loop) == AGENT


def test_a_lowercase_item_line_and_the_task_tool_register(loop: Path) -> None:
    assert (
        _fire(loop, prompt="Resume the lane: item GOH.LANE, branch x.", tool="Task").returncode == 0
    )
    assert _agent(loop) == AGENT


@pytest.mark.parametrize(
    ("items", "prompt"),
    [
        ([{"id": "GOH.LANE", "status": "in_progress", "agent": "a0000000000000000"}], BRIEF),
        ([{"id": "GOH.LANE", "status": "todo"}], BRIEF),
        ([{"id": "GOH.LANE", "status": "in_progress", "agent": "pending-spawn"}], "no item here"),
        ([{"id": "GOH.OTHER", "status": "in_progress", "agent": "pending-spawn"}], BRIEF),
    ],
    ids=["already-registered", "not-started", "no-item-in-brief", "item-not-pending"],
)
def test_anything_but_a_pending_spawn_item_is_left_alone(
    loop: Path, items: list[dict], prompt: str
) -> None:
    _items(loop, [{"repo": "gates_of_heck", **i} for i in items])
    r = _fire(loop, prompt=prompt)
    assert (r.returncode, r.stdout) == (0, ""), r.stderr
    assert _calls(loop) == []


@pytest.mark.parametrize(
    "prompt",
    [
        "You are the lane for GOH.LANE in gates_of_heck.",
        "next(i for i in items if i['id']=='GOH.LANE')",
        "the lane for GOH.LANE.",
    ],
    ids=["lane-for", "queue-lookup", "trailing-period"],
)
def test_a_brief_naming_its_id_any_way_registers_it(loop: Path, prompt: str) -> None:
    r = _fire(loop, prompt=prompt)
    assert r.returncode == 0, r.stderr
    assert _agent(loop) == AGENT


def test_an_id_that_only_prefixes_another_token_is_not_named(loop: Path) -> None:
    assert _fire(loop, prompt="lane for GOH.LANE2 and XGOH.LANE").returncode == 0
    assert _calls(loop) == []


def test_two_pending_spawn_ids_in_one_brief_register_neither_and_say_why(loop: Path) -> None:
    _items(loop, [{"id": i, "repo": "gates_of_heck", "status": "in_progress",
                   "agent": "pending-spawn"} for i in ("GOH.LANE", "GOH.TWO")])  # fmt: skip
    r = _fire(loop, prompt="lane for GOH.LANE, and later GOH.TWO")
    assert r.returncode == 1
    assert "GOH.LANE" in r.stderr and "GOH.TWO" in r.stderr and "ambiguous" in r.stderr
    assert _calls(loop) == []


def test_no_agent_id_in_the_response_registers_nothing(loop: Path) -> None:
    assert _fire(loop, response={"status": "completed", "content": []}).returncode == 0
    assert _calls(loop) == []


def test_an_estate_item_keeps_its_narrowing(loop: Path) -> None:
    _items(loop, [
        {"id": "GOH.LANE", "repo": "estate", "holds": "a, b", "status": "in_progress",
         "agent": "pending-spawn"},
        {"id": "GOH.WIDE", "repo": "estate", "status": "in_progress", "agent": "pending-spawn"},
    ])  # fmt: skip
    _fire(loop)
    _fire(loop, prompt="Item GOH.WIDE here")
    a, b = (c["argv"][2:] for c in _calls(loop))
    assert a == ["start", "GOH.LANE", AGENT, "--repos", "a,b"]
    assert b == ["start", "GOH.WIDE", AGENT, "--estate"]


def test_a_loopctl_failure_is_a_visible_non_blocking_error(loop: Path) -> None:
    (loop / "tools" / "loopctl.py").write_text(
        "import sys; print('refused: frozen', file=sys.stderr); sys.exit(1)"
    )
    r = _fire(loop)
    assert r.returncode == 1 and "refused: frozen" in r.stderr


def test_an_empty_or_broken_event_is_a_clean_no_op(loop: Path) -> None:
    for stdin in ("{}", "not json"):
        r = subprocess.run([sys.executable, str(HOOK)], input=stdin, text=True, capture_output=True,
                           timeout=patience(30), check=False, env=_env(loop))  # fmt: skip
        assert (r.returncode, r.stdout) == (0, "")


def test_a_write_racing_the_registration_is_told_to_retry(loop: Path) -> None:
    repo = loop.parent / "gates_of_heck"
    event = {"session_id": "s", "hook_event_name": "PreToolUse", "tool_name": "Write",
             "tool_input": {"file_path": str(repo / "x.py")}, "cwd": str(repo),
             "agent_id": AGENT, "agent_type": "repo-worker"}  # fmt: skip
    r = subprocess.run([sys.executable, str(GUARD)], input=json.dumps(event), text=True,
                       capture_output=True, timeout=patience(30), check=False,
                          env=_env(loop))  # fmt: skip
    reason = json.loads(r.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "registration pending: retry in a few seconds" in reason
    assert "GOH.LANE" in reason
