"""The loop queue's holds: one writer per repo, at the tool boundary (LOOP.FOREIGNEDIT, 2026-11-11).

The coordinator runs one agent per queue item, and an item in progress HOLDS the repos its `repo`
field names (`~/Projects/.loop/queue.json`; an `estate` item narrowed by `--repos` holds its
`holds` field, an un-narrowed one every repo). `loopctl start ID AGENTID` records the agent's id on
the item, and Claude Code gives every hook fired inside a subagent that same id as `agent_id`
(absent on the main thread: the coordinator is no agent, and nothing here ever refuses it). So a
subagent's write is judged EXACTLY, never guessed: the path's repo is the nearest enclosing git
worktree (a directory holding `.git`, a dir or a linked worktree's file), and it is refused when an
in-progress item whose `agent` is not the caller holds that repo -- unless one of the caller's own
items holds it too.

Why it exists: WG.7.K's agent (item repo koffee_big) edited ZoneWM's WidgetPanel.swift while
ZW.SPACEWALL's agent ran `make push` in that tree; one writer per repo held only by luck.

A linked worktree (`git worktree add build/x` inside a held checkout) is its own repo here: it is
the sanctioned way for a second agent to change a repo another holds (goh's `tools/land.sh`).

Seams: GOH_LOOP_QUEUE names the queue file (default ~/Projects/.loop/queue.json); repo tokens are
read under the queue's own projects directory (the queue's grandparent), `~/` tokens under $HOME.
A queue that is missing or does not parse holds nothing: the guard fails OPEN.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

ESTATE = "estate"


def queue_path() -> str:
    return os.environ.get("GOH_LOOP_QUEUE") or os.path.expanduser("~/Projects/.loop/queue.json")


def _real(path: str) -> str:
    return os.path.realpath(os.path.expanduser(path))


def repo_root(path: str, cwd: str | None = None) -> str | None:
    """The nearest enclosing git worktree top of `path` (which need not exist), or None."""
    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        path = os.path.join(cwd or os.getcwd(), path)
    here = _real(os.path.normpath(path))
    while True:
        if os.path.exists(os.path.join(here, ".git")):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            return None
        here = parent


@dataclass(frozen=True)
class QueueHold:
    root: str  # the held repo's worktree top
    item: str  # the in-progress item holding it
    agent: str  # that item's agent
    caller: str  # the agent whose write was refused
    mine: tuple[str, ...]  # the caller's own in-progress items

    def describe(self, action: str) -> str:
        """The refusal, one paragraph: what, where, who holds it, and what to do instead."""
        items = "item" if len(self.mine) == 1 else "items"
        own = (
            f"Your {items}: {', '.join(self.mine)}; write only inside the repos they hold."
            if self.mine
            else f"No in-progress item records agent {self.caller} "
            f"(`loopctl start ID {self.caller}`)."
        )
        return (
            f"{self.root} is held by in-progress item {self.item} (agent {self.agent}): one "
            f"writer per repo, so {action} by agent {self.caller} is refused. {own} A change "
            "this repo needs goes in your report for the coordinator, who sequences it after the "
            "holder; a repo you must change concurrently is changed from your own worktree "
            "(`git worktree add --detach build/<name> origin/main`)."
        )


@dataclass(frozen=True)
class _Holder:
    item: str
    agent: str
    roots: tuple[str, ...]  # repo tops; empty with `everything`
    everything: bool  # an un-narrowed estate item: every repo under the projects directory


def _holders(queue: str) -> tuple[list[_Holder], str]:
    try:
        with open(queue, encoding="utf-8") as fh:
            q = json.load(fh)
    except (OSError, ValueError):
        return [], ""
    projects = os.path.dirname(os.path.dirname(_real(queue)))
    out: list[_Holder] = []
    items = q.get("items") if isinstance(q, dict) else None
    for i in items if isinstance(items, list) else []:
        if not isinstance(i, dict) or i.get("status") != "in_progress":
            continue
        toks = [
            t.strip() for t in str(i.get("holds") or i.get("repo") or "").split(",") if t.strip()
        ]
        roots = tuple(
            _real(t) if t.startswith("~") else _real(os.path.join(projects, t))
            for t in toks
            if t != ESTATE
        )
        out.append(
            _Holder(str(i.get("id")), str(i.get("agent") or "").strip(), roots, ESTATE in toks)
        )
    return out, projects


def _covers(h: _Holder, root: str, projects: str) -> bool:
    return root in h.roots or (h.everything and root.startswith(projects + os.sep))


def find(
    path: str, cwd: str | None, caller: str | None, queue: str | None = None
) -> QueueHold | None:
    """The hold another item has on the repo enclosing `path`, against agent `caller`; None for the
    main thread (no `caller`), a path in no repo, an unheld repo, or a repo the caller holds."""
    if not caller:
        return None
    root = repo_root(path, cwd)
    if root is None:
        return None
    holders, projects = _holders(queue or queue_path())
    return _judge(holders, projects, root, caller)


def holds_below(
    path: str, cwd: str | None, caller: str | None, queue: str | None = None
) -> list[QueueHold]:
    """Repos at or below `path` another item holds, for a recursive verb on an ancestor."""
    if not caller:
        return []
    path = os.path.expanduser(path)
    top = _real(path if os.path.isabs(path) else os.path.join(cwd or os.getcwd(), path))
    holders, projects = _holders(queue or queue_path())
    roots = {r for h in holders for r in h.roots if r == top or r.startswith(top + os.sep)}
    found = [_judge(holders, projects, r, caller) for r in sorted(roots)]
    return [f for f in found if f]


def _judge(holders: list[_Holder], projects: str, root: str, caller: str) -> QueueHold | None:
    mine = [h for h in holders if h.agent == caller]
    if any(_covers(h, root, projects) for h in mine):
        return None
    for h in holders:
        if h.agent != caller and _covers(h, root, projects):
            return QueueHold(
                root, h.item, h.agent or "unrecorded", caller, tuple(x.item for x in mine)
            )
    return None
