"""Compaction lands on a task boundary when the owner is told the boundary has come.

Claude Code compacts on its own only at a token threshold, which is mid-task as often as not, and
no hook, setting or model tool can start a compaction (its docs, 2026-10-10). What a hook CAN do is
speak at the end of a turn -- the boundary -- so `hooks/claude/compact_nudge.py` (Stop) says when
the context has grown past `GOH_COMPACT_NUDGE_AT`, once per `GOH_COMPACT_NUDGE_STEP` of growth, and
`hooks/claude/compact_restore.py` (SessionStart, source `compact`) re-reads the repo state a
summary may have dropped.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT, hermetic_env

NUDGE = REPO_ROOT / "hooks" / "claude" / "compact_nudge.py"
RESTORE = REPO_ROOT / "hooks" / "claude" / "compact_restore.py"


def _assistant(ctx: int, *, sidechain: bool = False) -> dict:
    third = ctx // 3
    return {
        "type": "assistant",
        "isSidechain": sidechain,
        "message": {
            "id": f"msg_{ctx}",
            "usage": {
                "input_tokens": ctx - 2 * third,
                "cache_read_input_tokens": third,
                "cache_creation_input_tokens": third,
                "output_tokens": 10,
            },
        },
    }


def _boundary(post: int) -> dict:
    return {
        "type": "system",
        "subtype": "compact_boundary",
        "compactMetadata": {"trigger": "auto", "preTokens": 500_000, "postTokens": post},
    }


def _transcript(path: Path, *entries: dict) -> Path:
    with path.open("a", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    return path


def _stop(tmp: Path, transcript: Path, session: str = "s1", **env: str):
    event = {
        "hook_event_name": "Stop",
        "session_id": session,
        "transcript_path": str(transcript),
        "stop_hook_active": False,
    }
    r = subprocess.run(
        [sys.executable, str(NUDGE)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env=hermetic_env(XDG_STATE_HOME=str(tmp / "state"), **env),
        timeout=30,
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)["systemMessage"] if r.stdout.strip() else None


def test_below_the_threshold_says_nothing(tmp_path: Path) -> None:
    t = _transcript(tmp_path / "t.jsonl", _assistant(120_000), _assistant(299_000))
    assert _stop(tmp_path, t) is None


def test_past_the_threshold_names_the_size_and_the_command(tmp_path: Path) -> None:
    t = _transcript(tmp_path / "t.jsonl", _assistant(120_000), _assistant(342_000))
    msg = _stop(tmp_path, t)
    assert msg is not None
    assert "342k" in msg and "/compact" in msg, msg


def test_it_speaks_once_per_step_not_every_turn(tmp_path: Path) -> None:
    t = _transcript(tmp_path / "t.jsonl", _assistant(310_000))
    assert _stop(tmp_path, t) is not None
    _transcript(t, _assistant(340_000))
    assert _stop(tmp_path, t) is None  # 30k of growth is under the 50k step
    _transcript(t, _assistant(361_000))
    assert _stop(tmp_path, t) is not None


def test_a_compaction_seen_at_a_stop_rearms_it(tmp_path: Path) -> None:
    t = _transcript(tmp_path / "t.jsonl", _assistant(310_000))
    assert _stop(tmp_path, t) is not None
    _transcript(t, _boundary(22_000))
    assert _stop(tmp_path, t) is None  # the boundary is the newest entry: the context is small
    _transcript(t, _assistant(320_000))
    assert _stop(tmp_path, t) is not None  # only 10k past the last nudge, but a new cycle


def test_a_compaction_inside_a_turn_rearms_it(tmp_path: Path) -> None:
    """An automatic compaction mid-turn leaves no Stop at the boundary: the shrink is the signal."""
    t = _transcript(tmp_path / "t.jsonl", _assistant(480_000))
    assert _stop(tmp_path, t) is not None
    _transcript(t, _boundary(22_000), _assistant(305_000))
    assert _stop(tmp_path, t) is not None


def test_sessions_are_counted_apart(tmp_path: Path) -> None:
    t = _transcript(tmp_path / "t.jsonl", _assistant(310_000))
    assert _stop(tmp_path, t, session="a") is not None
    assert _stop(tmp_path, t, session="b") is not None


def test_a_subagents_usage_is_not_the_sessions(tmp_path: Path) -> None:
    t = _transcript(tmp_path / "t.jsonl", _assistant(100_000), _assistant(400_000, sidechain=True))
    assert _stop(tmp_path, t) is None


def test_the_threshold_and_step_are_configurable(tmp_path: Path) -> None:
    t = _transcript(tmp_path / "t.jsonl", _assistant(150_000))
    env = {"GOH_COMPACT_NUDGE_AT": "100000", "GOH_COMPACT_NUDGE_STEP": "10000"}
    assert _stop(tmp_path, t, **env) is not None
    _transcript(t, _assistant(161_000))
    assert _stop(tmp_path, t, **env) is not None


def test_the_usage_is_found_past_a_large_tail(tmp_path: Path) -> None:
    """A long tool result can follow the last usage; the scan must not stop at a fixed window."""
    filler = {"type": "user", "message": {"content": "x" * 3_000_000}}
    t = _transcript(tmp_path / "t.jsonl", _assistant(330_000), filler)
    assert _stop(tmp_path, t) is not None


def test_a_missing_or_broken_transcript_is_silent(tmp_path: Path) -> None:
    assert _stop(tmp_path, tmp_path / "absent.jsonl") is None
    bad = tmp_path / "bad.jsonl"
    bad.write_text("not json\n{\n")
    assert _stop(tmp_path, bad) is None


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        env=hermetic_env(drop_git=True),
    )


def _restore(cwd: Path, source: str = "compact"):
    event = {"hook_event_name": "SessionStart", "source": source, "cwd": str(cwd)}
    return subprocess.run(
        [sys.executable, str(RESTORE)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env=hermetic_env(drop_git=True),
        timeout=30,
    )


def test_after_a_compaction_the_repo_state_is_reread(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "trunk")
    _git(
        repo,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "first thing",
    )
    (repo / "wip.txt").write_text("x\n")
    r = _restore(repo)
    assert r.returncode == 0, r.stderr
    assert "trunk" in r.stdout and "wip.txt" in r.stdout and "first thing" in r.stdout, r.stdout


def test_only_a_compaction_triggers_it(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    r = _restore(repo, source="startup")
    assert (r.returncode, r.stdout) == (0, ""), r.stdout + r.stderr


def test_outside_a_repo_it_is_silent(tmp_path: Path) -> None:
    r = _restore(tmp_path)
    assert (r.returncode, r.stdout) == (0, ""), r.stdout + r.stderr
