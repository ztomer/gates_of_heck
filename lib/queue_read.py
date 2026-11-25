"""The loop's umbrella queue, read: goh's ONE reader of it (LOOP.QUEUEARCHIVE, 2026-11-23).

The loop keeps its work queue outside this repo; goh reads it (holds, phases, pending spawns, the
weekly spend review) and never writes it: `loopctl` is the queue's one writer. This module is the
documented interface, so goh never imports the loop's own code; tests/test_queue_read.py refuses a
direct parse of the queue anywhere else in goh.

The queue is NAMED by a path: GOH_LOOP_QUEUE, else the loop's default (`queue_path`). Its STORE is

  * `loop.db` in the same directory, the loop's own SQLite file, when it exists (WAL; opened
    read-only here, so a reader never blocks the writer, and goh never writes it):
        items(id TEXT PRIMARY KEY, seq INTEGER, archived INTEGER, status, repo, batch, priority,
              phase, agent, sha TEXT, body TEXT)
                                -- body: the item as a JSON object; seq: its place in queue
                                   order; archived 1: an archive row, not one of the queue's items
                                   but one of `archive.items`, rebuilt here in seq order
        queue_keys(key TEXT PRIMARY KEY, seq INTEGER, value TEXT)
                                -- value: a top-level key's JSON (schema, rules, phases, ...);
                                   the `archive` key's `items` is a null placeholder for its rows
        PRAGMA user_version     -- the file's schema; never compared to a list (GOH.QUEUESCHEMA)
        queue_v1_keys(key, seq, value), queue_v1_items(id, seq, archived, body)
                                -- the loop's versioned views (LOOP.QUEUENORMAL): the same rows as
                                   the two tables above, kept by every later schema: when both exist
                                   they are read and nothing else is, whatever user_version says
                                   (archive.items already rebuilt there); without them (schema 1)
                                   the tables are read, and user_version 0 is refused. A relation
                                   or column missing is "no queue" naming it (NoQueue)
        meta, schema_version, events: the loop's version record and history, not read here
  * else, until the loop's cutover, the JSON file at the path itself: {"items": [...], ...}.

The database layout (owner ruling 2026-11-23): loop.db is the loop's and private; goh's own files
are goh.db and goh-cache.db. No database writes into another's file, and a cross-file read
ATTACHes read-only.

`load` returns the same dict from either store. Neither store present -- a machine where no loop
runs, which goh, public, must expect -- raises OSError, and one that does not parse ValueError (a
SQLite error is re-raised as ValueError); every caller catches both and answers "no queue" (no
hold, no phase, no items), never a crash (tests/test_queue_read.py holds each to it).

THE LOOP QUEUE, READ (LOOP.QUEUEARCHIVE): goh's one reader of the loop's umbrella queue and the
store's documented layout -- the loop's SQLite `loop.db` beside the named path (`GOH_LOOP_QUEUE`),
opened read-only, when it exists, else the JSON file; `load`, `items`, `items_or_empty` (fails
open). Read by `queue_hold.py`, `reuse_fault.py`, `spawn_register.py`, `model_choice_watch.py` and
`spend_watch.py`; a direct parse anywhere else is refused.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import state_paths  # lib/, on the path from the line above

DB_NAME = "loop.db"
ENV = "GOH_LOOP_QUEUE"


def queue_path() -> str:
    """The queue's name: GOH_LOOP_QUEUE, else config.toml's, else the loop's place."""
    return str(state_paths.loop_queue())


def db_path(path: str | os.PathLike | None = None) -> Path:
    """Where the SQLite store for the queue named PATH lives (it may not exist)."""
    return Path(path or queue_path()).with_name(DB_NAME)


# What this reader reads of loop.db (GOH.QUEUESCHEMA). The loop's versioned views, which every
# later schema keeps (LOOP.QUEUENORMAL), when both exist, whatever the file's user_version; else
# today's tables (a schema-1 file). No list of schema numbers: a store without what it reads is
# "no queue" naming the missing table or column, never a guess.
VIEWS = {"queue_v1_keys": ("key", "value"), "queue_v1_items": ("seq", "archived", "body")}
TABLES = {"queue_keys": ("key", "value"), "items": ("seq", "archived", "body")}


def layout(con: sqlite3.Connection) -> tuple[str, str] | str:
    """(keys, items): the two relations CON's queue is read from, or why there are none (named)."""

    def cols(rel: str) -> set[str]:
        return {r[1] for r in con.execute("SELECT * FROM pragma_table_info(?)", (rel,))}

    use = VIEWS if all(cols(v) for v in VIEWS) else TABLES
    if use is TABLES and (schema := con.execute("PRAGMA user_version").fetchone()[0]) < 1:
        return f"is at schema {schema}, not a loop store"
    for rel, need in use.items():
        if not (have := cols(rel)):
            return f"has no {rel} table"
        if gone := [f"{rel}.{c}" for c in need if c not in have]:
            return f"lacks {', '.join(gone)}"
    keys, items = use
    return keys, items


ARCHIVE, ARCHIVE_ITEMS = (
    "archive",
    "items",
)  # queue_keys' archive holds items: null; its rows are archived = 1


class NoQueue(OSError):
    """No queue goh can read here; the message says why (absent, or a store without its layout)."""


def _load_db(db: Path) -> dict:
    uri = f"file:{quote(str(db.resolve()))}?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True, timeout=5)
        try:
            rels = layout(con)
            if isinstance(rels, str):
                raise NoQueue(f"no queue: {db} {rels} (goh reads loop.db by its layout)")
            keys = {k: json.loads(v) for k, v in con.execute(f"SELECT key, value FROM {rels[0]}")}
            rows = con.execute(
                f"SELECT archived, body FROM {rels[1]} ORDER BY archived, seq"
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error as e:
        raise ValueError(f"the queue store {db} is unreadable: {e}") from None
    live = [json.loads(b) for a, b in rows if not a]
    arch = keys.get(ARCHIVE)
    if isinstance(arch, dict) and ARCHIVE_ITEMS in arch and arch[ARCHIVE_ITEMS] is None:
        # the tables hold a null in its place; queue_v1_keys holds the list already
        keys[ARCHIVE] = {**arch, ARCHIVE_ITEMS: [json.loads(b) for a, b in rows if a]}
    return {**keys, "items": live}


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
