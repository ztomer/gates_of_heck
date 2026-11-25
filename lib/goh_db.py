"""lib/goh_db.py -- the seam for goh.db, goh's durable state (GOH.DBSEAM).

    goh_db.py path | migrate | version | dump TABLE [--json] | check

The one door to goh.db: `path()`, `connect()`, `ro()`, `migrate(con)` and `transaction()` are
lib/db_core.py's, bound to this file. Its migrations are lib/goh_db_schema/NNNN_name.sql, one
file per migration (numbers are assigned per lane, applied by set difference), each table bounded
by a `-- bound:` header (lib/db_bounds.py): past it, the oldest rows move to goh-archive-YYYY.db.

    file      GOH_DB (absolute), else $XDG_STATE_HOME/goh/goh.db, else ~/.local/state/goh/goh.db
    waits     busy_timeout 30 s (GOH_DB_BUSY_MS); synchronous=FULL: it holds the land queue
    missing   a read is OSError (`known()`: Unknown, never zero); a corrupt file fails loudly and
              is never recreated
    uncut     a file from before the cutover is refused at every door (`Uncut`) but the
              cutover's own
    budget    256 MB for the whole file (owner 2026-11-23: "need also size limit")

VIEWS ARE THE INTERFACE to other readers (handover plan (d)): goh publishes versioned views
(`lands_v1`, ...) in a migration and changes one only by adding `_v2` beside it.

`goh_db.py` / `goh_cache_db.py`: GOH.DBSEAM: the one door to each database, `db_core`'s API bound
to it. goh.db (`GOH_DB`, else `$XDG_STATE_HOME/goh/goh.db`): durable state, busy_timeout 30 s
(`GOH_DB_BUSY_MS`), synchronous=FULL, a missing or corrupt file fails loudly; migrations in
`goh_db_schema/`. goh-cache.db (`GOH_CACHE_DB`, else `$XDG_CACHE_HOME/goh/goh-cache.db`):
disposable, busy_timeout 2 s (`GOH_CACHE_DB_BUSY_MS`), synchronous=NORMAL, `missable()` turns an
error into a miss with one stderr line; migrations in `goh_cache_schema/`. `goh_db.refusable()`: a
store writer's NewerFile/NotOfRecord is one stderr line, never a raise (GOH.HEADCOPYRECORD). Each
`transaction` waits on its `lib/lock_order.tsv` lock (`goh_db`, `goh_cache_db`: innermost).
`goh_db.py path|migrate|version|dump TABLE [--json]|check`. `tests/test_state_seam.py` is the
ratchet of every store the database plan still has to move into them. Invoked by the store lanes of
the database move.

`lib/goh_db_schema/`: GOH.DBSEAM: one `NNNN_name.sql` per migration (numbers assigned per lane, so
no two lanes edit one file), each table under a `-- bound:` header. goh.db 0001 `imports` (the
import ledger every store move writes), 0002 `lands`, `land_guards`, `red_causes`,
`selection_misses` and the view `lands_v1` (GOH.STATEDB S1), 0003 `ships`, `ship_pins` and the
`ships_v1` view (`ship_records.py`), 0005 `agent_spawns`, `lane_starts` and `fault_candidates`
(`hook_ledgers.py`), 0006 `readings`, `pruned`, `duration_kv` and the `durations` view
(`durations.py`), 0007 `requests`, `slot_owners`, `early` and the `landq_v1` view (GOH.LANDQDB,
`landq_state.py`, `land_slot.py`), 0008 `reuse_audit` and `reuse_shadow` (`reuse_audit_store.py`,
GOH.VERDICTDB V1), 0022 `push_sweeps` (`push_sweep_rows.py`, GOH.PINSWEEP), 0023 `land_judgings`
(`land_judge_rows.py`, GOH.ONEGATE), 0024 `run_ends`, `run_phases` and `cpu_owners` (`run_ends.py`,
`cpu_owners.py`, GOH.TIMEACCOUNT); goh-cache.db 0001 `kv`, 0002 `verdicts` (`reuse_store.py`),
`kept` and `memo` (GOH.VERDICTDB V1), 0003 `shell_verdicts` (the goh binary's per-file verdicts,
`crates/goh/src/verdict_cache.rs`, GOH.VERDICTDB V2). Pinned by `test_db_bounds.py` (every shipped
table bounded).
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # outside any checkout: nothing to re-run from
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import contextlib
import contextvars
import os
import sqlite3
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db_core  # the lib directory, on the path from the line above
import lock_order
import state_paths

SEAM = db_core.Seam(
    name="goh", env="GOH_DB", xdg="XDG_STATE_HOME", fallback=".local/state", file="goh.db",
    schema=Path(__file__).resolve().with_name("goh_db_schema"), busy_env="GOH_DB_BUSY_MS",
    busy_ms=30_000, synchronous="FULL", budget=256 << 20, over="archive",
    born=(("paths", state_paths.TOKEN),),
)  # fmt: skip
_UNCUT = contextvars.ContextVar("goh_db_uncut", default=False)


class Uncut(RuntimeError):
    """The file is from before the cutover (documents, absolute paths). No sqlite3.Error: no reader
    takes it for a file without rows."""


def not_cut(con: sqlite3.Connection) -> str | None:
    """Why CON's file is refused, or None. Tokens come last, so a file of tokens is whole; one that
    holds no schema number yet has nothing to cut."""
    if state_paths.tokens(con):
        return None
    try:
        held = con.execute("SELECT 1 FROM schema_version LIMIT 1").fetchone()
    except sqlite3.OperationalError as e:
        if "no such table" not in str(e):
            raise
        held = None
    return held and (f"{con.execute('PRAGMA database_list').fetchone()[2] or 'goh.db'} is from "
                     "before the cutover: neither read nor written until `goh state cutover` has "
                     "run on it")  # fmt: skip


@contextlib.contextmanager
def uncut() -> Iterator[None]:
    """The block may open such a file: the cutover's own modules alone."""
    was = _UNCUT.set(True)
    try:
        yield
    finally:
        _UNCUT.reset(was)


def _cut(con: sqlite3.Connection) -> sqlite3.Connection:
    if not _UNCUT.get() and (why := not_cut(con)):
        raise Uncut(why)
    return con


def path() -> Path:
    return db_core.path(SEAM)


@contextlib.contextmanager
def connect(at: Path | None = None, named: bool = False) -> Iterator[sqlite3.Connection]:
    with db_core.connect(SEAM, at, named) as con:
        yield _cut(con)


@contextlib.contextmanager
def ro(at: Path | None = None, named: bool = False) -> Iterator[sqlite3.Connection]:
    with db_core.ro(SEAM, at, named) as con:
        yield _cut(con)


def migrate(con: sqlite3.Connection) -> set[int]:
    return db_core.migrate(con, SEAM)


def _order() -> None:
    lock_order.check("goh_db")  # innermost: nothing is waited on while a write holds it


@contextlib.contextmanager
def transaction(at: Path | None = None, named: bool = False) -> Iterator[sqlite3.Connection]:
    """One write to goh.db (AT: a file a caller names, e.g. lib/durations.py's)."""
    with db_core.transaction(SEAM, _order, at, named) as con:
        yield _cut(con)


class Unknown(FileNotFoundError):
    """There is no goh.db: a moved store's records are unknown, never none (handover plan (d))."""


@contextlib.contextmanager
def known(at: Path | None = None) -> Iterator[sqlite3.Connection]:
    """goh.db read-only for a moved store's reader: `Unknown` when there is no file, so a missing
    database never reads as zero lands, ships or spawns (its files are gone since wave 3)."""
    p = at or path()
    if not p.exists():
        raise Unknown(f"no goh.db at {p}: its records are unknown, not none")
    with ro(at) as con:
        yield con


def refused(e: BaseException) -> bool:
    """E is a write this copy may not make: the file is a newer goh's, or a shared file only main's
    code migrates (db_core.NewerFile and its NotOfRecord). Said on one stderr line, never raised."""
    if not isinstance(e, db_core.NewerFile):
        return False
    db_core.warn(f"goh.db: not written ({e})")
    return True


@contextlib.contextmanager
def refusable() -> Iterator[None]:
    """A side record whose write this copy may not make is skipped (`refused`), never a raise: a
    store writer fails open, so a step that records its result (the release canary, a commit's
    stamp, a ship's pins) is not red because the file belongs to main's code or a newer goh's
    (GOH.HEADCOPYRECORD: every land went red while a lane's 0009 sat in the real goh.db)."""
    try:
        yield
    except db_core.NewerFile as e:
        refused(e)


RETRY_S = 1.0  # a record's one retry waits this long (a lock past busy_timeout, a full disk freed)


def must(write: Callable[[], object], what: str) -> None:
    """WRITE, a record with no other home (a land's, a ship's), tried once more after RETRY_S; a
    second failure is said and raised, so a land or a ship never ends without its record
    (GOH.STATEDB S1; the class of GOH.SHIPNORECORD: both once said "no goh.db row" and went on)."""
    try:
        write()
        return
    except (OSError, sqlite3.Error) as e:
        db_core.warn(f"goh.db: {what}: not written ({e}); trying once more")
    time.sleep(RETRY_S)
    try:
        write()
    except (OSError, sqlite3.Error) as e:
        db_core.warn(f"goh.db: {what}: not written twice ({e}): it has no record")
        raise


if __name__ == "__main__":
    sys.exit(db_core.main(SEAM, sys.argv[1:]))
