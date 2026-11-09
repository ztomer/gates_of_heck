"""GOH.GHDOOR: a subagent that reads CI straight through `gh` instead of loopctl's one door is named.

The agent definitions (hooks/claude/agents/repo-worker*.md) say CI is read only when the brief asks,
and then only through `loopctl.py ci <sha> --repo OWNER/NAME [--wait]`, never `gh run list/view/watch`
or `gh api .../actions`: direct polling is what throttled the estate's GitHub token (LOOP.GHAGENTS).
At SubagentStop, agent_guard reads the transcript and names each direct call. Transcripts are FAKE.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env

GUARD = REPO_ROOT / "hooks" / "claude" / "agent_guard.py"
AGENTS = REPO_ROOT / "hooks" / "claude" / "agents"
sys.path.insert(0, str(GUARD.parent))
import ci_door

SENTENCE = (
    "Read CI only when your brief asks for it, and then only through `python3 "
    "~/Projects/.loop/tools/loopctl.py ci <full-sha> --repo OWNER/NAME [--wait]`, never `gh run "
    "list/view/watch` or `gh api .../actions`; exit 75 means GitHub is throttled until the time it "
    "prints, which is not a failure and not a green."
)


def _use(name: str, **inp: str) -> dict:
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t", "name": name, "input": inp}]}}  # fmt: skip


def _text(recs: list[dict]) -> str:
    return "".join(json.dumps(r) + "\n" for r in recs)


@pytest.mark.parametrize(
    "command",
    [
        "gh run watch 123456 --exit-status",
        "cd /x && gh run view 99 --log-failed | tail",
        "gh run list --limit 3 --json status",
        "rtk gh run watch",
        "GH_PAGER= gh run view",
        "gh api repos/o/n/actions/runs?head_sha=abc",
        "x=$(gh api /repos/o/n/actions/runs | jq .total_count)",
    ],
)
def test_a_direct_gh_ci_call_is_named(command: str) -> None:
    (hit,) = ci_door.direct_calls(_text([_use("Bash", command=command)]))
    assert hit.startswith(("gh run", "gh api")), hit


@pytest.mark.parametrize(
    "command",
    [
        "python3 ~/Projects/.loop/tools/loopctl.py ci abcdef --repo o/n --wait",
        "grep -n 'gh run watch' hooks/claude/agents/repo-worker.md",
        "gh pr create --fill",
        "gh api repos/o/n/pulls",
        "gh release view v1.2.3",
    ],
)
def test_loopctl_a_quote_or_another_gh_call_is_not_named(command: str) -> None:
    assert ci_door.direct_calls(_text([_use("Bash", command=command)])) == []


def test_a_planted_gh_run_watch_reaches_the_session_and_the_ledger(tmp_path: Path) -> None:
    sub = tmp_path / "proj" / "subagents"
    sub.mkdir(parents=True)
    t = sub / "agent-a1.jsonl"
    report = "Status: landed\nabc1234 x"
    t.write_text(_text([
        _use("Bash", command="gh run watch 123456 --exit-status"),
        {"type": "assistant", "message": {"content": [{"type": "text", "text": report}]}},
    ]))  # fmt: skip
    event = {"session_id": "s", "hook_event_name": "SubagentStop", "agent_id": "a1",
             "agent_type": "repo-worker", "last_assistant_message": report,
             "agent_transcript_path": str(t)}  # fmt: skip
    cache = tmp_path / "cache"
    r = subprocess.run(
        [sys.executable, str(GUARD)], input=json.dumps(event), capture_output=True, text=True,
        env=hermetic_env(XDG_CACHE_HOME=str(cache)), timeout=30, check=False,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    msg = json.loads(r.stdout)["systemMessage"]
    assert "gh run watch 123456" in msg and "loopctl.py ci" in msg, msg
    (line,) = [
        json.loads(x) for x in (cache / "goh" / "agent-spawns.jsonl").read_text().splitlines()
    ]
    assert line["ci_direct"] == ["gh run watch 123456 --exit-status"]


@pytest.mark.parametrize("name", ["repo-worker.md", "repo-worker-ui.md"])
def test_both_agent_definitions_carry_the_ci_door_sentence(name: str) -> None:
    assert SENTENCE in (AGENTS / name).read_text()
