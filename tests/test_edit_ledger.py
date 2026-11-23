"""GOH.UNDOOWNER: who edited which file is recorded at the write, and `goh undo` reads it.

GOH.UNDOMINE's undo (55629eeb) could tell "another agent touched this file" only through a hold, a
landing-queue request or a lane claim naming another item -- coarse records, and only when the
caller named its item. Now the file-tool guard (`hooks/claude/hold_guard.py`) appends each
subagent write to the worktree's ledger (`lib/edit_ledger.py`, `<git dir>/goh-edits.jsonl`), and
`goh undo` refuses a file another item or agent edited since the caller's own last write of it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, git, hermetic_env
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import edit_ledger

GUARD = REPO_ROOT / "hooks" / "claude" / "hold_guard.py"
UNDO = REPO_ROOT / "tools" / "undo.py"
ME, OTHER = "a1111111111111111", "a2222222222222222"


@pytest.fixture
def lane(repo: Path, tmp_path: Path) -> Path:
    wt = tmp_path / "lane" / "wt"
    git(repo, "worktree", "add", "-q", str(wt), "-b", "lane/mine")
    return wt


@pytest.fixture
def queue(tmp_path: Path) -> Path:
    q = tmp_path / "Projects" / ".loop" / "queue.json"
    q.parent.mkdir(parents=True)
    items = [{"id": "X.MINE", "repo": "elsewhere", "status": "in_progress", "agent": ME},
             {"id": "X.THEIRS", "repo": "b", "status": "in_progress", "agent": OTHER}]  # fmt: skip
    q.write_text(json.dumps({"items": items}))
    return q


def _edit(queue: Path, path: Path, agent: str | None) -> None:
    """The guard sees an Edit of PATH (as Claude Code sends it), then the edit happens."""
    event = {"session_id": "s", "hook_event_name": "PreToolUse", "tool_name": "Edit",
             "tool_input": {"file_path": str(path), "old_string": "a", "new_string": "b"},
             "cwd": str(path.parent)}  # fmt: skip
    if agent:
        event["agent_id"] = agent
    r = subprocess.run([sys.executable, str(GUARD)], input=json.dumps(event), capture_output=True,
                       text=True, timeout=patience(30), check=False,
                       env=hermetic_env(GOH_LOOP_QUEUE=str(queue)))  # fmt: skip
    assert r.returncode == 0 and not r.stdout.strip(), (r.stdout, r.stderr)
    path.write_text(path.read_text() + f"edit by {agent}\n")


def test_the_guard_records_each_subagent_write_with_its_items(lane: Path, queue: Path) -> None:
    before = edit_ledger._sha(str(lane / "README.md"))
    _edit(queue, lane / "README.md", ME)
    _edit(queue, lane / "README.md", None)  # the main thread is no agent: nothing recorded
    rows = edit_ledger.rows(str(lane))
    assert [(r["agent"], r["items"], r["path"], r["sha"]) for r in rows] == [
        (ME, ["X.MINE"], "README.md", before)]  # fmt: skip
    gitdir = edit_ledger.git_dir(str(lane))
    assert gitdir and os.path.isfile(os.path.join(gitdir, "goh-edits.jsonl"))
    assert (
        git(lane, "status", "--porcelain").strip() == "M README.md"
    )  # the ledger is not in the tree


def _undo(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = hermetic_env(drop_git=True, GOH_LANDQ_DIR=str(cwd.parent / "no-landq"))
    return subprocess.run([sys.executable, str(UNDO), *args], cwd=cwd, env=env,
                          capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          timeout=patience(60), check=False)  # fmt: skip


def test_undo_refuses_a_file_another_item_edited_since_mine(lane: Path, queue: Path) -> None:
    _edit(queue, lane / "README.md", ME)
    _edit(queue, lane / "README.md", OTHER)
    text = (lane / "README.md").read_text()
    r = _undo(lane, "--item", "X.MINE", "README.md")
    assert r.returncode == 1 and "X.THEIRS" in r.stderr and OTHER in r.stderr, r.stderr
    assert (lane / "README.md").read_text() == text  # nothing changed


def test_undo_of_my_own_last_write_runs(lane: Path, queue: Path) -> None:
    _edit(queue, lane / "README.md", OTHER)
    _edit(queue, lane / "README.md", ME)  # mine is the last write: the file is mine to undo
    r = _undo(lane, "--item", "X.MINE", "README.md")
    assert r.returncode == 0, r.stderr


def test_the_agent_stamp_names_the_caller_without_an_item(lane: Path, queue: Path) -> None:
    """A subagent's shell carries GOH_LOOP_AGENT (GOH.HOLDSUBPROC): undo knows the caller."""
    _edit(queue, lane / "README.md", OTHER)
    env = hermetic_env(drop_git=True, GOH_LANDQ_DIR=str(lane.parent / "no-landq"),
                       GOH_LOOP_AGENT=ME)  # fmt: skip
    r = subprocess.run([sys.executable, str(UNDO), "README.md"], cwd=lane, env=env,
                       capture_output=True, text=True, stdin=subprocess.DEVNULL,
                       timeout=patience(60), check=False)  # fmt: skip
    assert r.returncode == 1 and OTHER in r.stderr, r.stderr
