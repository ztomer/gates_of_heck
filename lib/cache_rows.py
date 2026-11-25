"""lib/cache_rows.py -- the cached parts of four kv documents and one file as rows of goh-cache.db
(GOH.KVBLOBS; goh cache 0004): the one writer and the one reader of remote_secs, canary_memo,
evict_objs and verified_shas.

    secs(), put_secs(new, at)            {node: the worker's seconds} (lib/remote_lane.py)
    memo(state, kind), put_memo(...)     {key: verdict} of the release canary (lib/canary_memo.py)
    objs(ledger), put_objs(...)          {path: {uses, at, l}} (lib/cache_evict.py)
    verified(), put_verified(new), take_verified(file)
                                         {session/tool use: [sha]} (scripts/agent_audit.py)

A CACHE: a missing file, a busy one or one from before 0004 reads as no rows, and a writer answers
False when it wrote none (its caller keeps the document then, where there is one). An upsert takes a
row only when it is no older than the one there, so a writer that read long ago takes nothing back.

WHERE. A caller that names its own ledger or state file has the goh-cache.db beside it, as it has
its own goh.db (lib/small_stores.py `db`); GOH_CACHE_DB names it for all; else goh-cache.db's own.
Pinned by tests/test_cache_rows.py.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db_core  # lib/, on the path from the line above
import durations
import goh_cache_db
import state_paths

REPO = "gates_of_heck"  # whose tests the worker runs (tests/linux_safe.txt): a repo's key
KEPT_ENV = "GATES_OF_HECK_SUITE_KEPT_DB"  # the real goh-cache.db, for a suite's runner
MEMO_S = 86400  # a verdict lives one day (lib/canary_memo.py TTL_S)

Db = tuple[Path, bool]  # the file, and whether the suite's runner named it


def beside(p: Path | str) -> Db:
    """The goh-cache.db of the store whose file is P."""
    p = Path(p).expanduser()
    if os.environ.get(goh_cache_db.SEAM.env) or p.parent == state_paths.state_root():
        return goh_cache_db.path(), False
    return p.with_name(goh_cache_db.SEAM.file), False


def rates() -> Db:
    """The goh-cache.db of the duration table in force (lib/durations.py `path`): the suite's runner
    names the real one's table for a call, and the real cache beside it in KEPT_ENV."""
    named = durations.path()
    kept = os.environ.get(KEPT_ENV)
    if kept and Path(os.path.realpath(named)).parent in db_core.real_dirs():
        return Path(kept), True
    return beside(named)


def _rows(db: Db, sql: str, args: tuple = ()) -> list[tuple]:
    got: list[tuple] = []
    with goh_cache_db.missable("goh-cache.db rows"):
        try:
            with goh_cache_db.ro(*db) as con:
                got = con.execute(sql, args).fetchall()
        except FileNotFoundError:
            pass
    return got


def _wrote(db: Db, write: Callable[[sqlite3.Connection], object]) -> bool:
    done = False
    with goh_cache_db.missable("goh-cache.db rows"):
        with goh_cache_db.transaction(*db) as con:
            write(con)
        done = True
    return done


# -- remote_secs -----------------------------------------------------------------------------------


def secs() -> dict[str, float]:
    return dict(_rows(rates(), "SELECT node, seconds FROM remote_secs WHERE repo = ?", (REPO,)))


def put_secs(new: Mapping[str, float], at: float | None = None) -> bool:
    at = time.time() if at is None else at
    recs = [(REPO, k, max(0.0, float(v)), at) for k, v in new.items()]
    return _wrote(rates(), lambda con: con.executemany(
        "INSERT INTO remote_secs(repo, node, seconds, at) VALUES (?, ?, ?, ?) ON CONFLICT(repo, "
        "node) DO UPDATE SET seconds = excluded.seconds, at = excluded.at "
        "WHERE excluded.at > remote_secs.at", recs))  # fmt: skip


# -- canary_memo -----------------------------------------------------------------------------------


def memo(state: Path | str, kind: str) -> dict[str, Any]:
    got = _rows(beside(state), "SELECT key, value FROM canary_memo WHERE kind = ?", (kind,))
    return {k: json.loads(v) for k, v in got}


def put_memo(state: Path | str, kind: str, records: Mapping[str, dict]) -> bool:
    """RECORDS into KIND's rows, and the rows no run reuses any more out of them."""
    recs = [(kind, k, json.dumps(v, sort_keys=True), float(v["epoch"])) for k, v in records.items()]

    def write(con: sqlite3.Connection) -> None:
        con.executemany(
            "INSERT INTO canary_memo(kind, key, value, at) VALUES (?, ?, ?, ?) ON CONFLICT(kind, "
            "key) DO UPDATE SET value = excluded.value, at = excluded.at "
            "WHERE excluded.at > canary_memo.at", recs)  # fmt: skip
        con.execute("DELETE FROM canary_memo WHERE kind = ? AND at <= ?",
                    (kind, int(time.time()) - MEMO_S))  # fmt: skip

    return _wrote(beside(state), write)


# -- evict_objs ------------------------------------------------------------------------------------


def objs(ledger: Path | str) -> dict[str, dict[str, float]]:
    got = _rows(beside(ledger), "SELECT path, uses, at, l FROM evict_objs")
    return {p: {"uses": uses, "at": at, "l": clock} for p, uses, at, clock in got}


def newer(a: Mapping[str, dict], b: Mapping[str, dict]) -> dict[str, dict]:
    """A and B joined: of an object both have, the one used last; A's when neither is."""
    return {**b, **{k: v for k, v in a.items() if k not in b or v["at"] >= b[k]["at"]}}


def put_objs(ledger: Path | str, now: Mapping[str, dict]) -> bool:
    """NOW is every object there is: a row of another goes. False when it holds no number."""
    try:
        recs = [(k, float(v["uses"]), float(v["at"]), float(v["l"])) for k, v in now.items()]
    except (KeyError, TypeError, ValueError):
        return False

    def write(con: sqlite3.Connection) -> None:
        there = {k for (k,) in con.execute("SELECT path FROM evict_objs")}
        con.executemany("DELETE FROM evict_objs WHERE path = ?", [(k,) for k in there - set(now)])
        con.executemany(
            "INSERT INTO evict_objs(path, uses, at, l) VALUES (?, ?, ?, ?) ON CONFLICT(path) DO "
            "UPDATE SET uses = excluded.uses, at = excluded.at, l = excluded.l "
            "WHERE excluded.at >= evict_objs.at", recs)  # fmt: skip

    return _wrote(beside(ledger), write)


# -- verified_shas ---------------------------------------------------------------------------------


def verified() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for session, tool, sha in _rows(
        (goh_cache_db.path(), False),
        "SELECT session, tool_use_id, sha FROM verified_shas ORDER BY session, tool_use_id, ord",
    ):
        out.setdefault(f"{session}/{tool}", []).append(sha)
    return out


def put_verified(new: Mapping[str, list[str]], at: float | None = None) -> bool:
    at = time.time() if at is None else at
    recs = [(*name.rpartition("/")[::2], i, sha, at)
            for name, shas in new.items() for i, sha in enumerate(shas)]  # fmt: skip
    return _wrote((goh_cache_db.path(), False), lambda con: con.executemany(
        "INSERT OR IGNORE INTO verified_shas(session, tool_use_id, ord, sha, at) "
        "VALUES (?, ?, ?, ?, ?)", recs))  # fmt: skip


def take_verified(file: Path) -> bool:
    """FILE (agent-verify.json, what the rows were) into the rows, then gone; True when it is."""
    try:
        doc, at = json.loads(file.read_text(encoding="utf-8")), file.stat().st_mtime
    except FileNotFoundError:
        return True
    except (OSError, ValueError):
        return False
    if not put_verified(doc, at) or any(verified().get(k) != v for k, v in doc.items()):
        return False
    file.unlink()
    return True
