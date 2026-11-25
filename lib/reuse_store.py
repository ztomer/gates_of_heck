"""lib/reuse_store.py -- where lib/reuse.py's verdict records live (GOH.VERDICTDB V1).

lib/reuse.py owns the KEY (the registered paths, their fields, the TTL and what a miss is); this
module owns only the BYTES. A record is one row of goh-cache.db's `verdicts` table (migration
0002), keyed (path, key), its body the whole record as it was a file. goh kept them as ~12k
small files under ~/.local/state/goh/reuse/<path>/<kk>/<key>.json (GOH.VERDICTDB, 1.6k of them
verdicts, 200 MB).

    db()             the file: GOH_CACHE_DB, else <GOH_REUSE_DIR>/goh-cache.db when a caller
                     names the store root (a test's; lib/cpu_window.py's rule), else goh-cache.db's
                     own path; `NAMED` (below) before all of them
    put(path, key, rec)          write one record whole; its locator
    get(path, key)               (record or None, its locator, why it is unreadable)
    rows(path)                   every record of PATH: (locator, record)
    drop(path, keys) / cap(path, n)   delete records; keep PATH's newest N

DISPOSABLE. goh-cache.db is a cache (lib/goh_cache_db.py): every database error is a miss or a
skipped write with one stderr line, never a raise, so a deleted, locked or not-yet-migrated file
costs a re-run, never a verdict. A lane's copy (a linked worktree, a landq slot) reads the shared
file read-only until main has applied 0002 (lib/db_core.py, GOH.LANEMIGRATE): its writes are
skipped until then.

THE FILES FROM BEFORE THE SWITCH are read by nothing (wave 3's delete commit): `goh state retire
reuse-records` imports each into its row, reads it back and removes them (lib/state_retire.py).

NAMED. A suite's controller records its land's verdicts into the CALLER's store
(tests/_land_verdicts.py `_in_store`): the runner names that file, and the controller sets `NAMED`
for the call only (in-process, so no child inherits it), the one real-home goh-cache.db a suite may
open (lib/db_core.py `guard`). Pinned by tests/test_reuse_store_db.py.

GOH.VERDICTDB V1: where `reuse.py`'s records live, one row of goh-cache.db's `verdicts` per (path,
key); `put`/`get`/`rows`/`drop`/`cap`, every database error a miss or a skipped write with one
stderr line. `db()`: `GOH_CACHE_DB`, else beside a named `GOH_REUSE_DIR`, else goh-cache.db's own;
`NAMED` is the caller's file a land suite's controller records into (`tests/_land_verdicts.py`).
Files from before the switch are read by nothing (`state_retire_verdicts.py` imports and removes
them). `root()` stays the reuse root of what stays files, each registered in `state_bounds.py` with
why (`profstore`, `profreach`, `binreach`; `test_reuse_root_files.py`). Pinned by `test_reuse.py`.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import goh_cache_db  # lib/, beside this file: the one door to goh-cache.db

NAMED: Path | None = None  # the caller's goh-cache.db a suite controller records into
CAP = 20000  # rows one path keeps at most, newest first (`pushed`: a year of push gates)


def root() -> Path:
    """The reuse root: GOH_REUSE_DIR (absolute), else $XDG_STATE_HOME/goh/reuse, else under HOME.
    The profile store and the audit live under it; verdict records did before the switch."""
    named = os.environ.get("GOH_REUSE_DIR", "")
    if named:
        if not named.startswith("/"):
            raise ValueError(f"GOH_REUSE_DIR must be an absolute path: {named!r}")
        return Path(named)
    state = os.environ.get("XDG_STATE_HOME", "")
    base = Path(state) if state.startswith("/") else Path.home() / ".local" / "state"
    return base / "goh" / "reuse"


def root_named() -> bool:
    """A caller named the reuse root (GOH_REUSE_DIR: a test's, or a land suite's controller's)."""
    return bool(os.environ.get("GOH_REUSE_DIR"))


def db() -> Path:
    if NAMED is not None:
        return NAMED
    if os.environ.get(goh_cache_db.SEAM.env):
        return goh_cache_db.path()
    if os.environ.get("GOH_REUSE_DIR"):
        return root() / goh_cache_db.SEAM.file
    return goh_cache_db.path()


def where(path: str, key: str) -> str:
    """A record's locator, printed where a record file's path was: <db>#<path>/<key>."""
    return f"{db()}#{path}/{key}"


def _named() -> bool:
    return NAMED is not None


def put(path: str, key: str, rec: dict) -> str:
    """Write REC whole as PATH's record KEY (a database error: one stderr line, a skipped write)."""
    body = json.dumps(rec, sort_keys=True)
    with goh_cache_db.missable(f"reuse {path} record"), \
            goh_cache_db.transaction(db(), _named()) as con:  # fmt: skip
        con.execute(
            "INSERT INTO verdicts(path, key, fields, verdict, epoch, body) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(path, key) DO UPDATE SET fields=excluded.fields, verdict=excluded.verdict,"
            " epoch=excluded.epoch, body=excluded.body",
            (path, key, json.dumps(rec.get("fields"), sort_keys=True), str(rec.get("verdict")),
             rec.get("epoch") if isinstance(rec.get("epoch"), int) else -1, body),
        )  # fmt: skip
    return where(path, key)


def _query(sql: str, args: tuple) -> list[tuple]:
    """SQL's rows from the file read-only; none for no file, no table yet or an error (said once:
    a file a newer migration has not reached yet is a miss, not a fault)."""
    p, out = db(), []
    if not p.is_file():
        return out
    with goh_cache_db.missable("reuse records"), goh_cache_db.ro(p, _named()) as con:
        out = con.execute(sql, args).fetchall()
    return out


def _parse(body: str) -> dict:
    rec = json.loads(body)
    return rec if isinstance(rec, dict) else {}  # a body that is no record is no key's


def get(path: str, key: str) -> tuple[dict | None, str, str]:
    """(PATH's record KEY or None, its locator, why it is unreadable or ""). None with no why: no
    record."""
    got = _query("SELECT body FROM verdicts WHERE path=? AND key=?", (path, key))
    body = got[0][0] if got else None
    if body is None:
        return None, where(path, key), ""
    try:
        return _parse(body), where(path, key), ""
    except ValueError as e:
        return None, where(path, key), str(e)


def rows(path: str) -> Iterator[tuple[str, dict]]:
    """Every record of PATH ({}: unreadable)."""
    for key, body in _query("SELECT key, body FROM verdicts WHERE path=?", (path,)):
        try:
            yield where(path, key), _parse(body)
        except ValueError:
            yield where(path, key), {}


def drop(path: str, locators: list[str]) -> int:
    """Delete these records of PATH (their locators); how many."""
    keys = [loc.rpartition("/")[2] for loc in locators if "#" in loc]
    if keys:
        with goh_cache_db.missable(f"reuse {path} prune"), \
                goh_cache_db.transaction(db(), _named()) as con:  # fmt: skip
            con.executemany("DELETE FROM verdicts WHERE path=? AND key=?",
                            [(path, k) for k in keys])  # fmt: skip
    return len(keys)


def cap(path: str, keep: int | None = None) -> int:
    """PATH's rows past its newest KEEP, deleted (the per-path row cap); how many."""
    if not db().is_file():
        return 0
    gone = 0
    with goh_cache_db.missable(f"reuse {path} cap"), \
            goh_cache_db.transaction(db(), _named()) as con:  # fmt: skip
        gone = con.execute(
            "DELETE FROM verdicts WHERE path=? AND key NOT IN (SELECT key FROM verdicts WHERE "
            "path=? ORDER BY epoch DESC LIMIT ?)", (path, path, CAP if keep is None else keep),
        ).rowcount  # fmt: skip
    return gone
