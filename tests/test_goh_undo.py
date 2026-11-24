"""`goh undo FILE...`: put back only your OWN edits in your OWN worktree, keeping a copy.

65 refusals in 7 days stopped agents discarding uncommitted work -- 44 by goh's discard guard and 21
by Claude Code's own check -- and each time the agent was reaching for `git checkout --`, `git
stash` or `git reset` to undo its OWN edits; one lane restored files from HEAD by a broken loop and
lost its edits. There was no safe undo, so agents reached for the destructive one. `goh undo`
(`tools/undo.py`) restores a file the caller changed in its own linked worktree since its base,
keeps the replaced text under the worktree's `.goh-undo/` for one step back (`goh undo --back`),
and refuses a path outside the caller's worktree, the shared main checkout, a held repo and a
worktree goh's lane records give to another item. The discard guard names the command.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import landq_state
import pytest
from _hook_inproc import run_hook
from conftest import REPO_ROOT, git, hermetic_env
from timing_bounds import patience

UNDO = REPO_ROOT / "tools" / "undo.py"
GUARD = REPO_ROOT / "hooks" / "claude" / "discard_guard.py"
ORIGINAL = "# fixture\n"
MINE = "# fixture\nmy edit\n"


def _undo(cwd: Path, *args: str, landq: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = hermetic_env(drop_git=True, GOH_LANDQ_DIR=str(landq or cwd.parent / "no-landq"))
    return subprocess.run(
        [sys.executable, str(UNDO), *args], cwd=cwd, env=env, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, timeout=patience(60), check=False,
    )  # fmt: skip


@pytest.fixture
def lane(repo: Path, tmp_path: Path) -> Path:
    """A linked worktree of `repo` on its own branch: the caller's own worktree."""
    wt = tmp_path / "lane" / "wt"
    git(repo, "worktree", "add", "-q", str(wt), "-b", "lane/mine")
    return wt


def test_own_file_is_restored_and_the_edit_kept(lane: Path) -> None:
    (lane / "README.md").write_text(MINE)
    r = _undo(lane, "README.md")
    assert r.returncode == 0, r.stderr
    assert (lane / "README.md").read_text() == ORIGINAL
    assert (lane / ".goh-undo" / "README.md").read_text() == MINE
    assert "--back" in r.stdout, r.stdout
    assert git(lane, "status", "--porcelain") == "", "the kept copy must not show as a change"


def test_back_puts_the_edit_back(lane: Path) -> None:
    (lane / "README.md").write_text(MINE)
    assert _undo(lane, "README.md").returncode == 0
    r = _undo(lane, "--back", "README.md")
    assert r.returncode == 0, r.stderr
    assert (lane / "README.md").read_text() == MINE


def test_a_new_file_is_removed_and_back_brings_it_back(lane: Path) -> None:
    (lane / "new.txt").write_text("scratch\n")
    assert _undo(lane, "new.txt").returncode == 0
    assert not (lane / "new.txt").exists()
    assert _undo(lane, "--back", "new.txt").returncode == 0
    assert (lane / "new.txt").read_text() == "scratch\n"


def test_a_committed_change_goes_back_to_the_base_only_when_asked(lane: Path) -> None:
    (lane / "README.md").write_text(MINE)
    git(lane, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "mine")
    r = _undo(lane, "README.md")
    assert r.returncode != 0 and "--base" in r.stderr, r.stderr
    assert (lane / "README.md").read_text() == MINE
    assert _undo(lane, "--base", "README.md").returncode == 0
    assert (lane / "README.md").read_text() == ORIGINAL


def test_an_unchanged_file_is_refused(lane: Path) -> None:
    r = _undo(lane, "README.md")
    assert r.returncode != 0 and "nothing of yours" in r.stderr, r.stderr


def test_a_file_in_another_worktree_is_refused(repo: Path, lane: Path, tmp_path: Path) -> None:
    other = tmp_path / "other" / "wt"
    git(repo, "worktree", "add", "-q", str(other), "-b", "lane/theirs")
    (other / "README.md").write_text("their edit\n")
    r = _undo(lane, str(other / "README.md"))
    assert r.returncode != 0 and "outside your worktree" in r.stderr, r.stderr
    assert (other / "README.md").read_text() == "their edit\n"


def test_the_shared_checkout_is_refused(repo: Path) -> None:
    (repo / "README.md").write_text(MINE)
    r = _undo(repo, "README.md")
    assert r.returncode != 0 and "shared" in r.stderr, r.stderr
    assert (repo / "README.md").read_text() == MINE
    assert not (repo / ".goh-undo").exists()


def test_a_held_repo_is_refused(repo: Path, lane: Path) -> None:
    git(repo, "config", "goh.hold", "2026-11-21: frozen -- lift: true")
    (lane / "README.md").write_text(MINE)
    r = _undo(lane, "README.md")
    assert r.returncode != 0 and "hold" in r.stderr, r.stderr
    assert (lane / "README.md").read_text() == MINE


def test_a_worktree_another_item_lands_from_is_refused(lane: Path, tmp_path: Path) -> None:
    landq = tmp_path / "landq"
    record = {"lane": str(lane), "item": "GOH.THEIRS", "repo": "x", "sha": "0" * 40}
    landq_state.put(landq, "1-GOH.THEIRS", record, "running")  # a goh.db row (GOH.LANDQDB)
    (lane / "README.md").write_text(MINE)
    r = _undo(lane, "--item", "GOH.MINE", "README.md", landq=landq)
    assert r.returncode != 0 and "GOH.THEIRS" in r.stderr, r.stderr
    assert (lane / "README.md").read_text() == MINE
    assert _undo(lane, "--item", "GOH.THEIRS", "README.md", landq=landq).returncode == 0


def test_one_refused_path_changes_nothing(lane: Path) -> None:
    (lane / "README.md").write_text(MINE)
    r = _undo(lane, "README.md", "/etc/hosts")
    assert r.returncode != 0
    assert (lane / "README.md").read_text() == MINE


def _guard(command: str, cwd: Path) -> str:
    r = run_hook(
        GUARD,
        json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}),
        hermetic_env(drop_git=True),
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.parametrize(
    "command", ["git checkout -- README.md", "git restore README.md", "git reset --hard"]
)
def test_the_discard_guard_names_the_undo_command(lane: Path, command: str) -> None:
    (lane / "README.md").write_text(MINE)
    assert "`goh undo README.md`" in _guard(command, lane)


def test_the_stash_refusal_names_the_undo_command(lane: Path) -> None:
    (lane / "README.md").write_text(MINE)
    assert "goh undo" in _guard("git stash", lane)


def test_goh_dispatches_undo_to_the_tool() -> None:
    table = (REPO_ROOT / "crates" / "goh" / "house_scripts.tsv").read_text().splitlines()
    rows = [ln.split("\t") for ln in table if ln and not ln.startswith("#")]
    assert ["undo", "tools/undo.py"] in [r[:2] for r in rows]
