"""LOOP.FOREIGNEDIT and LOOP.GHDOOR: a subagent keeps to its own item's repos and off gh CI polls.

LOOP.FOREIGNEDIT (2026-11-11): WG.7.K's agent (item repo koffee_big) edited ZoneWM's
WidgetPanel.swift while ZW.SPACEWALL's agent ran `make push` in that tree.
`hooks/claude/hold_guard.py` now reads the loop queue (`lib/queue_hold.py`): a subagent's
Edit/Write/NotebookEdit, or a changing shell command, inside a repo an in-progress item OTHER
than its own holds is refused, naming the holder. The tie is exact: `loopctl start ID AGENTID`
records the agent on the item, and Claude Code
puts that id in every hook event fired inside a subagent as `agent_id` (absent on the main thread,
which is the coordinator and is never refused).

LOOP.GHDOOR (2026-11-11): four parallel `gh run watch` from one agent spent the estate's 5,000/h
GitHub budget. The same hook refuses a subagent's `gh run watch/view/list`, `gh api .../actions`
and `gh pr checks --watch`, naming `loopctl ci` (`hooks/claude/ci_door.py`, the one matcher).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT

GUARD = REPO_ROOT / "hooks" / "claude" / "hold_guard.py"
ME, OTHER = "a1111111111111111", "a2222222222222222"


@pytest.fixture
def estate(tmp_path: Path) -> Path:
    """~/Projects in miniature: four repos; OTHER's item holds `theirs`, ME's holds `mine`."""
    projects = tmp_path / "Projects"
    for name in ("mine", "theirs", "free", "games/nested"):
        (projects / name / ".git").mkdir(parents=True)
    _queue(projects, [
        {"id": "X.MINE", "repo": "mine", "status": "in_progress", "agent": ME},
        {"id": "X.THEIRS", "repo": "theirs", "status": "in_progress", "agent": OTHER},
        {"id": "X.DONE", "repo": "free", "status": "done", "agent": OTHER},
        {"id": "X.TODO", "repo": "games/nested", "status": "todo"},
    ])  # fmt: skip
    return projects


def _queue(projects: Path, items: list[dict]) -> None:
    (projects / ".loop").mkdir(exist_ok=True)
    (projects / ".loop" / "queue.json").write_text(json.dumps({"items": items}))


def _verdict(
    projects: Path, tool: str, tool_input: dict, agent: str | None = ME, cwd: str = "/"
) -> str | None:
    event = {"session_id": "s", "hook_event_name": "PreToolUse", "tool_name": tool,
             "tool_input": tool_input, "cwd": cwd}  # fmt: skip
    if agent:
        event["agent_id"], event["agent_type"] = agent, "repo-worker"
    env = {**os.environ, "GOH_LOOP_QUEUE": str(projects / ".loop" / "queue.json")}
    r = subprocess.run([sys.executable, str(GUARD)], input=json.dumps(event), capture_output=True,
                       text=True, timeout=30, check=False, env=env)  # fmt: skip
    assert r.returncode == 0, r.stderr
    if not r.stdout.strip():
        return None
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"
    return out["permissionDecisionReason"]


def _bash(projects: Path, command: str, agent: str | None = ME, tool: str = "Bash") -> str | None:
    return _verdict(projects, tool, {"command": command}, agent, cwd=str(projects / "mine"))


EDITS = [("Edit", "file_path"), ("Write", "file_path"), ("MultiEdit", "file_path"),
         ("NotebookEdit", "notebook_path")]  # fmt: skip


# ---- LOOP.FOREIGNEDIT: the file tools ---------------------------------------------------------


@pytest.mark.parametrize(("tool", "key"), EDITS)
def test_a_write_into_a_repo_another_item_holds_is_refused_naming_the_holder(
    estate: Path, tool: str, key: str
) -> None:
    reason = _verdict(estate, tool, {key: str(estate / "theirs" / "Sources" / "WidgetPanel.swift")})
    assert reason and "X.THEIRS" in reason and OTHER in reason and tool in reason
    assert "X.MINE" in reason  # and it says which item is the caller's


@pytest.mark.parametrize(("tool", "key"), EDITS)
def test_a_write_into_my_own_repo_an_unheld_one_or_no_repo_passes(
    estate: Path, tmp_path: Path, tool: str, key: str
) -> None:
    nested = estate / "games" / "nested" / "a"
    for path in (estate / "mine" / "a.py", estate / "free" / "a.py", nested, tmp_path / "s" / "n"):
        assert _verdict(estate, tool, {key: str(path)}) is None, path


def test_the_main_thread_the_coordinator_writes_anywhere(estate: Path) -> None:
    assert _verdict(estate, "Edit", {"file_path": str(estate / "theirs" / "x")}, agent=None) is None
    assert _bash(estate, f"rm {estate}/theirs/x", agent=None) is None


def test_an_agent_no_item_records_is_refused_in_a_held_repo_and_told_why(estate: Path) -> None:
    reason = _verdict(
        estate, "Write", {"file_path": str(estate / "mine" / "x")}, agent="a9999999999999999"
    )
    assert reason and "X.MINE" in reason and "loopctl start ID a9999999999999999" in reason


def test_a_linked_worktree_inside_a_held_checkout_is_its_own_repo(estate: Path) -> None:
    wt = estate / "theirs" / "build" / "wt"
    wt.mkdir(parents=True)
    (wt / ".git").write_text("gitdir: ../../.git/worktrees/wt\n")
    assert _verdict(estate, "Write", {"file_path": str(wt / "lib" / "x.py")}) is None
    assert _verdict(
        estate, "Write", {"file_path": str(estate / "theirs" / "build" / "x")}
    )  # beside it: held


def test_an_estate_item_holds_every_repo_unless_narrowed(estate: Path) -> None:
    _queue(estate, [
        {"id": "E.ALL", "repo": "estate", "status": "in_progress", "agent": OTHER},
        {"id": "X.MINE", "repo": "mine", "status": "in_progress", "agent": ME},
    ])  # fmt: skip
    assert "E.ALL" in (_verdict(estate, "Write", {"file_path": str(estate / "free" / "x")}) or "")
    assert (
        _verdict(estate, "Write", {"file_path": str(estate / "mine" / "x")}) is None
    )  # my own still mine
    _queue(estate, [{"id": "E.SOME", "repo": "estate", "holds": "theirs, games/nested",
                     "status": "in_progress", "agent": OTHER}])  # fmt: skip
    assert "E.SOME" in (
        _verdict(estate, "Write", {"file_path": str(estate / "games/nested/x")}) or ""
    )
    assert _verdict(estate, "Write", {"file_path": str(estate / "free" / "x")}) is None


def test_a_missing_or_broken_queue_holds_nothing(estate: Path) -> None:
    (estate / ".loop" / "queue.json").write_text("{not json")
    assert _verdict(estate, "Write", {"file_path": str(estate / "theirs" / "x")}) is None
    (estate / ".loop" / "queue.json").unlink()
    assert _verdict(estate, "Write", {"file_path": str(estate / "theirs" / "x")}) is None


# ---- LOOP.FOREIGNEDIT: the shell -----------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "rm {p}/theirs/x",
        "echo hi > {p}/theirs/x",
        "sed -i '' s/a/b/ {p}/theirs/x",
        "cp a.py {p}/theirs/",
        "git -C {p}/theirs commit -m wip",
        "cd {p}/theirs && git stash",
        "rm -rf {p}",  # an ancestor: a held repo lies below it
        "for f in a b; do rm {p}/theirs/$f; done",
    ],
)
def test_a_changing_command_in_a_repo_another_item_holds_is_refused(
    estate: Path, command: str
) -> None:
    reason = _bash(estate, command.format(p=estate))
    assert reason and "X.THEIRS" in reason


@pytest.mark.parametrize(
    "command",
    [
        "cat {p}/theirs/x && git -C {p}/theirs log -1",
        "cp {p}/theirs/x .",  # a copy OUT
        "git -C {p}/theirs worktree add --detach build/mine origin/main",  # the sanctioned way in
        "git -C {p}/theirs fetch -q origin",
        "rm {p}/mine/x && echo ok > {p}/free/y",
        "make push",  # in my own cwd
    ],
)
def test_a_read_a_worktree_add_or_a_write_in_my_own_or_a_free_repo_runs(
    estate: Path, command: str
) -> None:
    assert _bash(estate, command.format(p=estate)) is None


# ---- LOOP.GHDOOR ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "gh run watch 123456 --exit-status",
        "for i in 1 2 3; do gh run view 99 --log-failed; sleep 30; done",
        "gh run list --limit 3 --json status",
        "rtk gh run watch",
        "GH_PAGER= gh run view --log 5",
        "gh api repos/o/n/actions/runs?head_sha=abc",
        "x=$(gh api /repos/o/n/actions/runs | jq .total_count)",
        'echo "$(gh run view 1 --log)"',
        "gh pr checks 12 --watch",
    ],
)
@pytest.mark.parametrize("tool", ["Bash", "Monitor"])
def test_an_agents_direct_gh_ci_poll_is_refused_naming_loopctl_ci(
    estate: Path, command: str, tool: str
) -> None:
    reason = _bash(estate, command, tool=tool)
    assert reason and "loopctl.py ci <full-sha> --repo OWNER/NAME --wait" in reason


@pytest.mark.parametrize(
    "command",
    [
        f"python3 ~/Projects/.loop/tools/loopctl.py ci {'0' * 40} --repo o/n --wait",
        "grep -n 'gh run watch' hooks/claude/agents/repo-worker.md",
        'git commit -m "refuse gh run watch\n\ngh run view loops too"',
        "cat <<'EOF' > notes.txt\ngh run watch 1\nEOF",
        "gh pr create --fill && gh api repos/o/n/pulls && gh release view v1 && gh pr checks 12",
    ],
)
def test_loopctl_a_quote_a_heredoc_or_another_gh_call_runs(estate: Path, command: str) -> None:
    assert _bash(estate, command) is None


def test_the_coordinator_may_read_ci_through_gh(estate: Path) -> None:
    assert _bash(estate, "gh run watch 123456", agent=None) is None
