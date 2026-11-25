"""GOH.SPAWNKEY: the queue items a brief names (hooks/claude/spawn_items.py), as the spawn's row
keeps them (agent_guard.py) and the lane is registered by (spawn_register.py; its own tests are
tests/test_spawn_register.py)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from _hook_inproc import run_hook
from conftest import REPO_ROOT, hermetic_env

sys.path.insert(0, str(REPO_ROOT / "hooks" / "claude"))
import spawn_items

GUARD = REPO_ROOT / "hooks" / "claude" / "agent_guard.py"

HEAD = "Model-choice: opus@high because cross-module\nPool: unfit because it edits A.POOL\n\n"
WORDS = [  # (the brief, its description, the items)
    ("Items: GOH.A, CG.2.N5d\nItem GOH.OTHER is not this one's", "GOH.TITLE: x",
     ["GOH.A", "CG.2.N5d"]),
    ("> **Items: `GOH.A` COVUP.APP_UPDATES**", "x", ["GOH.A", "COVUP.APP_UPDATES"]),
    ("- Items: `GOH.A` `COVUP.APP_UPDATES`, GOH.A", "x", ["GOH.A", "COVUP.APP_UPDATES"]),
    ("Do it.", "goh lane GOH.A + GOH.B", ["GOH.A", "GOH.B"]),
    ("Item GOH.C first", "GOH.A: what", ["GOH.A"]),
    ("Queue item GOH.A (step S1 of GOH.PLAN), repo x", "GOH: A lane", ["GOH.A"]),
    ("Queue items GOH.A, then GOH.B and GOH.C (map first); GOH.D holds", "d",
     ["GOH.A", "GOH.B", "GOH.C"]),
    ("Read AGENTS.md. Your item is GOH.A in x", "d", ["GOH.A"]),
    ("You are the goh lane for GOH.A and GOH.B (p1) after GOH.C.", "d", ["GOH.A", "GOH.B"]),
    ("Item: GOH.A. Work only there; GOH.B holds it.", "d", ["GOH.A"]),
    ("Items, in this order:\n1. GOH.A -- x\n2) `GOH.B`: y\n- GOH.C", "d",
     ["GOH.A", "GOH.B", "GOH.C"]),
    ("You close GOH.A in ~/x, after GOH.B.", "d", ["GOH.A"]),
    ("Read AGENTS.md and MODELS.md, v2.0\nthen GOH.A", "d", None),
    ("", None, None),
]  # fmt: skip


@pytest.mark.parametrize(("said", "title", "items"), WORDS, ids=[str(n) for n in range(len(WORDS))])
def test_a_briefs_items_are_the_ones_it_says_are_its_own(said, title, items) -> None:
    got = spawn_items.fields(HEAD + said, title)
    assert got == {"items": items, "no_item_why": None if items else spawn_items.NONE}


@pytest.mark.parametrize(
    ("line", "why"),
    [
        ("Items: none because it reads the queue, GOH.A too", "it reads the queue, GOH.A too"),
        ("Items: none", "the brief says `Items: none`"),
        ("Items: none -- a probe", "a probe"),
    ],
)  # fmt: skip
def test_a_brief_may_say_it_is_no_items_and_why(line: str, why: str) -> None:
    got = spawn_items.fields(f"{HEAD}{line}\nitem GOH.B", "GOH.A: x")
    assert got == {"items": None, "no_item_why": why}


def _spawned(tmp_path: Path, said: str, title: str) -> list[tuple]:
    prompt = (
        f"Model-choice: sonnet@high because checkable, known-fix\nPool: unfit because x\n{said}"
    )
    event = {"session_id": "s", "hook_event_name": "PreToolUse", "tool_name": "Agent",
             "tool_use_id": "T", "tool_input": {
                 "prompt": prompt, "description": title, "subagent_type": "repo-worker",
                 "model": "sonnet", "effort": "high"}}  # fmt: skip
    env = hermetic_env(XDG_CACHE_HOME=str(tmp_path), GOH_DB=str(tmp_path / "goh.db"))
    r = run_hook(GUARD, json.dumps(event), env)
    assert r.returncode == 0 and "deny" not in r.stdout, r.stdout + r.stderr
    import sqlite3

    con = sqlite3.connect(tmp_path / "goh.db")
    try:
        return con.execute(
            "SELECT s.tool_use_id, s.no_item_why, s.rest, s.first_line, group_concat("
            "l.value) FROM agent_spawns s LEFT JOIN spawn_lists l ON l.n = s.n AND l.kind = 'item'"
            " GROUP BY s.n").fetchall()  # fmt: skip
    finally:
        con.close()


def test_the_guard_writes_a_spawns_items_and_tool_id_as_columns(tmp_path: Path) -> None:
    first = "Model-choice: sonnet@high because checkable, known-fix"
    got = _spawned(tmp_path, "Items: GOH.A GOH.B", "x")
    assert got == [("T", None, "{}", first, "GOH.A,GOH.B")]
    got = _spawned(tmp_path, "Fix it.", "fix")
    assert got[1:] == [("T", spawn_items.NONE, "{}", first, None)]
