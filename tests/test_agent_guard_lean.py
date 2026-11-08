"""A write spawn goes to a lean agent type goh installs, not general-purpose (GOH.LEANAGENT).

Measured 2026-11-08: a general-purpose agent's first turn is 70.6k tokens of fixed preamble (every
MCP server's tool names, the deferred-tool and agent-type lists); `repo-worker` lists its tools,
so none of that loads. agent_guard steers a write spawn there, with a stated escape. The helpers
are test_claude_agent_guard.py's, so both files judge spawns the same way.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import REPO_ROOT
from test_claude_agent_guard import (
    BRIEF,
    GOOD,
    POOL,
    RULES,
    _allowed,
    _ledger,
    _reason,
    _run,
    _spawn,
)

LEAN = RULES.lean


@pytest.mark.parametrize("agent_type", [*LEAN["from"], None])
def test_a_write_spawn_of_a_heavy_type_is_refused_naming_the_lean_types(
    tmp_path: Path, agent_type: str | None
) -> None:
    ti = dict(GOOD, subagent_type=agent_type) if agent_type else {
        k: v for k, v in GOOD.items() if k != "subagent_type"}  # fmt: skip
    reason = _reason(_run(_spawn(ti), tmp_path))
    for needle in (LEAN["to"], LEAN["ui"], "Agent-type: ", "because", "70.6k"):
        assert needle in reason, (needle, reason)
    assert _ledger(tmp_path) == []


def test_the_heavy_types_are_general_purpose_and_claude_and_the_lean_ones_repo_worker() -> None:
    assert LEAN["from"] == ["general-purpose", "claude"]
    assert (LEAN["to"], LEAN["ui"]) == ("repo-worker", "repo-worker-ui")
    for name in (LEAN["to"], LEAN["ui"]):  # the types the reason names are ones goh installs
        assert (REPO_ROOT / "hooks" / "claude" / "agents" / f"{name}.md").exists()


def test_a_stated_escape_lets_a_general_purpose_write_spawn_run_and_is_ledgered(
    tmp_path: Path,
) -> None:
    esc = "Agent-type: general-purpose because this session started before repo-worker existed"
    ti = dict(GOOD, subagent_type="general-purpose", prompt=f"{GOOD['prompt']}\n{esc}")
    _allowed(_run(_spawn(ti), tmp_path))
    (line,) = _ledger(tmp_path)
    assert line["agent_type_escape"] == "this session started before repo-worker existed"


@pytest.mark.parametrize(
    "esc",
    ["Agent-type: general-purpose because", "Agent-type: repo-worker because it is lean",
     "Agent-type: general-purpose"],
)  # fmt: skip
def test_an_escape_without_a_reason_or_for_another_type_is_still_refused(
    tmp_path: Path, esc: str
) -> None:
    ti = dict(GOOD, subagent_type="general-purpose", prompt=f"{GOOD['prompt']}\n{esc}")
    assert "repo-worker" in _reason(_run(_spawn(ti), tmp_path))


@pytest.mark.parametrize("agent_type", ["general-purpose", "claude", "web-fetch"])
def test_a_read_only_lookup_of_any_type_is_not_steered(tmp_path: Path, agent_type: str) -> None:
    prompt = f"Find X.\nModel-choice: haiku@low because read-only\n{POOL}"
    ti = dict(BRIEF, subagent_type=agent_type, model="haiku", effort="low", prompt=prompt)
    _allowed(_run(_spawn(ti), tmp_path))


@pytest.mark.parametrize("agent_type", ["repo-worker", "repo-worker-ui"])
def test_a_write_spawn_of_a_lean_type_runs(tmp_path: Path, agent_type: str) -> None:
    _allowed(_run(_spawn(dict(GOOD, subagent_type=agent_type)), tmp_path))
    (line,) = _ledger(tmp_path)
    assert line["subagent_type"] == agent_type and line["agent_type_escape"] is None
