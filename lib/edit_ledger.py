"""Who edited which file, recorded at the write (GOH.UNDOOWNER).

GOH.UNDOMINE's `goh undo` could tell "another agent touched this file" only from coarse records --
a hold, a landing-queue request, a lane claim naming another item -- and only when the caller
named its item: there was no file-level ownership record. Now the file-tool guard
(`hooks/claude/hold_guard.py`, PreToolUse on Write/Edit/MultiEdit/NotebookEdit) appends one line
per subagent write it lets through to the worktree's own ledger, `<git dir>/goh-edits.jsonl` (the
per-worktree git dir: never in the tree, never committed, gone with the worktree):

    {"time": ISO, "agent": "<agent id>", "items": ["<its in-progress items>"], "path": "<rel>",
     "sha": "<sha256 of the file BEFORE the write, or null>"}

`goh undo` (tools/undo.py) asks `foreign_since_mine`: a later write of the path by another agent
or item than the caller's -- after the caller's own last write of it, or ever when the caller
never wrote it -- refuses the undo. A ledger it cannot read judges nothing (fail open).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os

LEDGER = "goh-edits.jsonl"


def git_dir(top: str) -> str | None:
    """The worktree's own git dir: `<top>/.git` itself, or where a linked worktree's file points."""
    dot = os.path.join(top, ".git")
    if os.path.isdir(dot):
        return dot
    try:
        with open(dot, encoding="utf-8") as fh:
            line = fh.readline().strip()
    except OSError:
        return None
    if not line.startswith("gitdir:"):
        return None
    where = line.split(":", 1)[1].strip()
    return os.path.normpath(where if os.path.isabs(where) else os.path.join(top, where))


def _sha(path: str) -> str | None:
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None


def record(top: str, path: str, agent: str, items: list[str]) -> None:
    """Append the write of PATH (absolute) by AGENT for ITEMS to TOP's ledger; never raises."""
    where = git_dir(top)
    if not where or not agent:
        return
    now = dt.datetime.now(dt.UTC).isoformat(timespec="microseconds")
    row = {"time": now, "agent": agent, "items": sorted(items),
           "path": os.path.relpath(path, top), "sha": _sha(path)}  # fmt: skip
    try:
        with open(os.path.join(where, LEDGER), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError:
        pass


def rows(top: str) -> list[dict]:
    where = git_dir(top)
    try:
        with open(os.path.join(where or "", LEDGER), encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict) and isinstance(r.get("path"), str):
            out.append(r)
    return out


def foreign_since_mine(top: str, rel: str, item: str | None, agent: str | None) -> dict | None:
    """The latest write of REL by someone other than the caller (ITEM / AGENT) after the caller's
    own last write of it; None when there is none, or when the caller names neither."""
    if not item and not agent:
        return None

    def mine(r: dict) -> bool:
        return bool(
            (item and item in (r.get("items") or [])) or (agent and r.get("agent") == agent)
        )

    seen = [r for r in rows(top) if os.path.normpath(r["path"]) == os.path.normpath(rel)]
    last_mine = max((i for i, r in enumerate(seen) if mine(r)), default=-1)
    others = [r for r in seen[last_mine + 1 :] if not mine(r)]
    return others[-1] if others else None
