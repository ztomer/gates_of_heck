"""The loop's umbrella queue, read: goh's ONE reader of it (LOOP.QUEUEARCHIVE, 2026-11-23).

The loop keeps its work queue outside this repo; goh reads it (holds, phases, pending spawns, the
weekly spend review) and never writes it: `loopctl` is the queue's one writer. This module is the
documented interface, so goh never imports the loop's own code; tests/test_queue_read.py refuses a
direct parse of the queue anywhere else in goh.

The queue is NAMED by a path: GOH_LOOP_QUEUE, else the loop's default (`queue_path`). Its STORE is

  * SQLite, `queue.db` in the same directory, when that file exists (WAL mode; opened read-only
    here, so a reader never blocks the writer):
        items(id TEXT PRIMARY KEY, seq INTEGER, status, repo, batch, priority, phase, agent TEXT,
              body TEXT)        -- body: the item as a JSON object; seq: its place in queue order
        meta(key TEXT PRIMARY KEY, value TEXT)
                                -- value: a top-level key's JSON (schema, rules, phases, ...)
  * else the JSON file at the path itself: {"items": [...], "phases": [...], ...}.

`load` returns the same dict from either store. A store that is absent raises OSError, one that
does not parse ValueError (a SQLite error is re-raised as ValueError), so a caller that fails open
catches (OSError, ValueError) whichever store it met.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from urllib.parse import quote

DB_NAME = "queue.db"
ENV = "GOH_LOOP_QUEUE"


def queue_path() -> str:
    """The queue's name: GOH_LOOP_QUEUE, else the loop's default place."""
    return os.environ.get(ENV) or os.path.expanduser("~/Projects/.loop/queue.json")


def db_path(path: str | os.PathLike | None = None) -> Path:
    """Where the SQLite store for the queue named PATH lives (it may not exist)."""
    return Path(path or queue_path()).with_name(DB_NAME)


def _load_db(db: Path) -> dict:
    uri = f"file:{quote(str(db.resolve()))}?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True, timeout=5)
        try:
            meta = {k: json.loads(v) for k, v in con.execute("SELECT key, value FROM meta")}
            rows = con.execute("SELECT body FROM items ORDER BY seq").fetchall()
        finally:
            con.close()
    except sqlite3.Error as e:
        raise ValueError(f"the queue store {db} is unreadable: {e}") from None
    return {**meta, "items": [json.loads(b) for (b,) in rows]}


def load(path: str | os.PathLike | None = None) -> dict:
    """The whole queue named PATH (default `queue_path()`), from its SQLite store when that exists,
    else from the JSON file. OSError: absent; ValueError: unreadable."""
    named = Path(path or queue_path())
    db = db_path(named)
    if db.is_file():
        return _load_db(db)
    q = json.loads(named.read_text(encoding="utf-8"))
    if isinstance(q, dict):
        return q
    raise ValueError(f"the queue {named} is not a JSON object")


def items(path: str | os.PathLike | None = None) -> list[dict]:
    """The queue's items, in queue order (the objects among them)."""
    return [i for i in load(path).get("items") or [] if isinstance(i, dict)]


def items_or_empty(path: str | os.PathLike | None = None) -> list[dict]:
    """The queue's items, or [] when it is absent or unreadable: for a reader that fails open."""
    try:
        return items(path)
    except (OSError, ValueError):
        return []
