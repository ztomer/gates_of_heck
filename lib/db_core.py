"""lib/db_core.py -- the mechanics every goh SQLite file shares, with no knowledge of any store
(GOH.DBSEAM). lib/goh_db.py (goh.db) and lib/goh_cache_db.py (goh-cache.db) are its two seams;
each names a `Seam` and re-exports the API below with it bound.

    path(seam)        the file: the seam's env override (absolute), else its XDG default
    connect(seam)     a WAL connection, busy_timeout and the seam's synchronous, migrated on open
    ro(seam)          a read-only connection (`mode=ro`); a missing file is OSError, never created
    migrate(con, s)   apply every migration missing from schema_version, by SET DIFFERENCE
    transaction(s)    BEGIN IMMEDIATE ... COMMIT, ROLLBACK on any raise; its wait timed
    version(con)      the file's user_version, applied numbers and meta

MIGRATIONS ARE APPLIED BY SET DIFFERENCE. `.loop`'s queue_store applied only numbers above
MAX(version): with parallel lanes number 3 can land before number 2, and 2 then never applies.
Here every NNNN_name.sql in the seam's schema directory missing from `schema_version` is applied,
lowest first, each in its own transaction with its row.

VERSIONING (owner 2026-11-23: "please keep a version field somewhere for the dbs"). Every file
carries it twice: PRAGMA user_version (in the header, read with no table parse) is the highest
number whose every lower number is applied; the `meta` table holds db, schema_version,
created_by, last_migrated_by (the goh that wrote it) and created_at. A file holding a number this
code does not ship was written by a newer goh: it opens READ-ONLY and says so, and never migrates
down (`NewerFile` on a write, naming the migration it does not ship).

A NEWER FILE'S NUMBERS MAY BE SAFE (GOH.OLDWRITERS). A file holding numbers this code does not ship
is written all the same when every one of them is in meta.old_writers_safe, and is never swept by
it (`ahead`). lib/db_schema.py has the head lines, lib/db_safe.py judges one, lib/db_contract.py
applies the by-command ones, which an open never does. SQLite older than MIN_SQLITE is refused.

ONLY THE CODE OF RECORD MIGRATES A SHARED FILE (GOH.LANEMIGRATE, GOH.HEADCOPYRECORD): which copy
that is, and what a copy that is not may do, is lib/db_record.py's docstring.

HERMETIC. With GATES_OF_HECK_SUITE_REAL_HOME set (tests/suite_home.py), opening a file that lives
directly in the real home's goh state or cache directory raises `RealHomeOpen`: no test writes
the real goh.db, and tests/real_state_guard.py cannot diff a file the lander writes during suites.
The one exception is a file the suite's runner NAMED for its top-level run (`named=True`: the
duration table tools/pytest.sh hands its run in GOH_TEST_DURATIONS, lib/durations.py).

RULES FOR CALLERS. Open per operation, never at module level (subprocesses fork). A transaction
never spans a subprocess, an flock wait or a network call: both databases are the innermost locks
of lib/lock_order.tsv. Pinned by tests/test_db_core.py.
"""

from __future__ import annotations

import contextlib
import json
import os
import pwd
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

REAL_HOME_ENV = "GATES_OF_HECK_SUITE_REAL_HOME"  # tests/suite_home.py MARKER
REAL_XDG_STATE_ENV = "GATES_OF_HECK_SUITE_REAL_XDG_STATE_HOME"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tui.lib import warn  # noqa: E402 -- the root, on the path from the line above

sys.path.insert(0, str(ROOT / "lib"))
import db_record  # noqa: E402 -- lib/, on the path from the line above
import db_schema  # noqa: E402 -- lib/: one schema file, its head and how it is applied
import db_settings  # noqa: E402 -- lib/: the pragmas, and a test's own file
import db_waits  # noqa: E402 -- lib/: the lock-wait instrument's rows
import state_paths  # noqa: E402 -- lib/: the state root
from db_schema import (  # noqa: E402, F401 -- lib/: this module's API too
    ahead,
    migrations,
    stamp_rows,
    statements,
    top,
    version,
)
from db_waits import WAITS_ENV  # noqa: E402, F401 -- its env name, read by tests/test_db_core.py

BOOT = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS schema_version(
  version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at REAL NOT NULL, writer TEXT NOT NULL);
"""
MIN_SQLITE = (3, 44, 0)  # ORDER BY inside an aggregate, which the schema files' views use
BOOT_TABLES = ("meta", "schema_version")  # db_core's own: bounded by what migrations exist


class RealHomeOpen(RuntimeError):
    """A suite run opened the real home's goh database."""


class NewerFile(sqlite3.OperationalError):
    """A write to a file this code opened read-only: one a newer goh wrote."""


class OldSQLite(sqlite3.OperationalError):
    """This Python's SQLite is older than the schema files' SQL (MIN_SQLITE)."""


class NotOfRecord(NewerFile):
    """A write from a copy that is not the code of record to a shared file it would have to
    migrate or create: only the primary checkout does that."""


@dataclass(frozen=True)
class Seam:
    name: str  # the file's `meta.db`: goh | goh-cache
    env: str  # its absolute-path override, e.g. GOH_DB
    xdg: str  # the XDG base its default lives under: XDG_STATE_HOME | XDG_CACHE_HOME
    fallback: str  # that base's default under HOME: .local/state | .cache
    file: str  # goh.db | goh-cache.db
    schema: Path  # the NNNN_name.sql directory
    busy_env: str
    busy_ms: int
    synchronous: str  # FULL | NORMAL
    budget: int  # the whole file's byte budget (lib/db_bounds.py)
    over: str  # what a table past its bound does: "archive" (history) | "evict" (cache)
    born: tuple[tuple[str, str], ...] = ()  # the meta rows a new file is made with


def path(seam: Seam, env: Mapping[str, str] | None = None) -> Path:
    """The seam's file: its override (absolute only), else the state root's FILE
    (lib/state_paths.py), or $XDG_CACHE_HOME/goh/FILE, else under HOME."""
    env = os.environ if env is None else env
    if raw := env.get(seam.env):
        if not os.path.isabs(raw):
            raise ValueError(f"{seam.env}={raw!r} is not an absolute path")
        return Path(raw)
    if seam.xdg == "XDG_STATE_HOME":
        return state_paths.state_root(env) / seam.file
    base = env.get(seam.xdg) or os.path.join(env.get("HOME") or os.path.expanduser("~"),
                                             seam.fallback)  # fmt: skip
    return Path(base) / "goh" / seam.file


def real_dirs(env: Mapping[str, str] | None = None) -> list[Path]:
    """The real home's goh state and cache directories while a suite runs, else none."""
    env = os.environ if env is None else env
    real = env.get(REAL_HOME_ENV)
    if not real:
        return []
    dirs = [Path(real, ".local", "state", "goh"), Path(real, ".cache", "goh"),
            state_paths.state_root({"HOME": real})]  # fmt: skip
    if xdg := env.get(REAL_XDG_STATE_ENV):
        dirs.append(Path(xdg, "goh"))
    return [Path(os.path.realpath(d)) for d in dirs]


def guard(p: Path, env: Mapping[str, str] | None = None) -> None:
    """Raise RealHomeOpen when P is a file of the real home's goh directories under a suite."""
    here = Path(os.path.realpath(p)).parent
    for d in real_dirs(env):
        if here == d:
            raise RealHomeOpen(f"{p}: a suite run opens the real home's goh database ({d}); "
                               "tests point GOH_DB / GOH_CACHE_DB at tmp_path")  # fmt: skip


def why_not_of_record(root: Path = ROOT) -> str | None:
    """Why ROOT is not the code of record, or None (lib/db_record.py: decided by its commit)."""
    return db_record.why_not(root)


def of_record(root: Path = ROOT) -> bool:
    """ROOT is the code of record (why_not_of_record)."""
    return why_not_of_record(root) is None


def shared_dirs(env: Mapping[str, str] | None = None) -> list[Path]:
    """The goh directories the code of record's runs use: the account's (its passwd home, so a
    child with a scratch HOME is not counted), the real home's under a suite, and an XDG home set
    outside one."""
    env = os.environ if env is None else env
    account = Path(pwd.getpwuid(os.getuid()).pw_dir)
    dirs = [account / ".local" / "state" / "goh", account / ".cache" / "goh",
            state_paths.state_root({"HOME": str(account)}), *real_dirs(env)]  # fmt: skip
    if not env.get(REAL_HOME_ENV):
        dirs.append(state_paths.state_root(env))
        dirs += [Path(env[x], "goh") for x in ("XDG_STATE_HOME", "XDG_CACHE_HOME")
                 if os.path.isabs(env.get(x) or "")]  # fmt: skip
    return [Path(os.path.realpath(d)) for d in dirs]


def shared(p: Path, env: Mapping[str, str] | None = None) -> bool:
    return Path(os.path.realpath(p)).parent in shared_dirs(env)


def writer() -> str:
    """The goh that writes: `goh <git describe>` of this checkout, `goh unknown` without git."""
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "describe", "--tags", "--match", "v[0-9]*"],
                             capture_output=True, text=True, check=False, timeout=10,
                             stdin=subprocess.DEVNULL).stdout.strip()  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        out = ""
    if not out:  # an export: its commit, which `goh unknown` hid on 2026-11-24
        with contextlib.suppress(OSError):
            out = "export " + (ROOT / ".goh-head").read_text(encoding="utf-8").strip()[:12]
    return f"goh {out.removeprefix('v') or 'unknown'}"


def sqlite_check() -> None:
    """Raise OldSQLite under a SQLite that cannot read the schema files' views."""
    if sqlite3.sqlite_version_info < MIN_SQLITE:
        raise OldSQLite(f"SQLite {sqlite3.sqlite_version} under {sys.executable}: goh's databases "
                        f"need {'.'.join(map(str, MIN_SQLITE))} or newer")  # fmt: skip


def busy_ms(seam: Seam) -> int:
    raw = os.environ.get(seam.busy_env, "")
    return int(raw) if raw.isdigit() else seam.busy_ms


def _ro(p: Path, seam: Seam) -> sqlite3.Connection:
    sqlite_check()
    if not p.is_file():
        raise FileNotFoundError(f"{p}: no {seam.file} (a read never creates it)")
    con = sqlite3.connect(f"{p.as_uri()}?mode=ro", uri=True, isolation_level=None,
                          timeout=busy_ms(seam) / 1000)  # fmt: skip
    con.execute(f"PRAGMA busy_timeout={busy_ms(seam)}")
    return con


def newer(con: sqlite3.Connection, seam: Seam) -> str | None:
    """Why this code may not write the file: it holds a number this code does not ship that is not
    in meta.old_writers_safe (GOH.OLDWRITERS: a set check, so a `refuse` number is never hidden by
    a `safe` one after it). None when it may."""
    known = migrations(seam)
    v = version(con)
    safe = db_schema.safe_numbers(v["meta"])
    if unknown := sorted(set(v["applied"]) - set(known) - safe):
        rows = dict(con.execute(
            f"SELECT version, name || ' (by ' || writer || ')' FROM schema_version WHERE version IN"
            f" ({','.join('?' * len(unknown))})", unknown))  # fmt: skip
        return (f"{seam.file} holds migration {', '.join(rows[n] for n in unknown)}, which this "
                f"goh ({ROOT}) does not ship (it ships {sorted(known)}): opened read-only, never "
                "migrated down")  # fmt: skip
    if v["user_version"] > max([*known, *(safe & set(v["applied"]))], default=0):
        return (f"{seam.file} is version {v['user_version']}, newer than this goh's "
                f"{max(known, default=0)}: opened read-only, never migrated down")  # fmt: skip
    return None


def not_of_record(con: sqlite3.Connection, seam: Seam) -> str | None:
    """Why this copy may not write a shared file (it lacks a migration the copy ships), or None."""
    known = migrations(seam)
    if lacking := db_schema.on_open(known, set(version(con)["applied"])):
        return (f"{seam.file} lacks migration {', '.join(known[n].stem for n in lacking)}, and "
                f"{ROOT} is not the code of record ({why_not_of_record()}): only main's code "
                "migrates the shared file; opened read-only")  # fmt: skip
    return None


def _safe(seam: Seam, n: int, fresh: bool) -> bool:
    """Whether number N goes into meta.old_writers_safe: it says safe and, on a file other copies
    may have open, lib/db_safe.py finds it so (else said, and treated as refuse)."""
    if db_schema.old_writers(migrations(seam)[n].read_text(encoding="utf-8")) != "safe":
        return False
    if fresh:
        return True
    import db_safe  # only an existing file gaining a safe number pays for the replay

    bad = db_safe.faults(seam, n)
    for why in bad:
        warn(f"{seam.file}: recorded as refuse, not safe: {why}")
    return not bad


def migrate(con: sqlite3.Connection, seam: Seam) -> set[int]:
    """Apply every shipped migration missing from schema_version but a by-command contract
    (lib/db_contract.py); the numbers applied. A file that holds none takes them all, contracts too:
    it has no row a contract moves, so a new file is born in the last shape (GOH.OLDFORMS)."""
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not set(BOOT_TABLES) <= names:  # an existing file's open takes no write lock here
        con.executescript(BOOT)
    known = migrations(seam)
    have = {r[0] for r in con.execute("SELECT version FROM schema_version")}
    stamp = ""
    if known and not have:
        stamp = writer()
        safe_ones = {n for n in known if _safe(seam, n, True)}
        if db_schema.fresh(con, known, stamp, safe_ones, seam.born):
            _stamp(con, seam, stamp)
            return set(known)
        have = {r[0] for r in con.execute("SELECT version FROM schema_version")}
    missing = db_schema.on_open(known, have)
    stamp = stamp or (writer() if missing or not have else "")
    for n in missing:
        safe = _safe(seam, n, not have)
        con.execute("BEGIN IMMEDIATE")
        try:
            if n in {r[0] for r in con.execute("SELECT version FROM schema_version")}:
                con.execute("ROLLBACK")  # another process applied it while this one waited
                continue
            db_schema.apply(con, n, known[n], stamp, safe)
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
    _stamp(con, seam, stamp)
    return set(missing)


def _stamp(con: sqlite3.Connection, seam: Seam, stamp: str) -> None:
    """user_version and meta brought to the applied rows, when they are not there already."""
    if con.execute("PRAGMA user_version").fetchone()[0] == top(con) and not stamp:
        return
    created_by = stamp or writer()  # a subprocess: never inside the transaction
    con.execute("BEGIN IMMEDIATE")
    stamp_rows(con, seam, stamp, created_by)
    con.execute("COMMIT")


@contextlib.contextmanager
def _opened(
    seam: Seam, at: Path | None, named: bool
) -> Iterator[tuple[sqlite3.Connection, NewerFile | None]]:
    """connect()'s connection and, when it is read-only, the refusal a write raises."""
    sqlite_check()
    p = at or path(seam)
    if not named:
        guard(p)
    record = not shared(p) or of_record()
    fresh = not p.exists() or p.stat().st_size == 0
    if fresh and not record:
        raise NotOfRecord(
            f"{p}: no {seam.file} yet, and {ROOT} is not the code of record "
            f"({why_not_of_record()}): only main's code creates the shared file"
        )
    p.parent.mkdir(parents=True, exist_ok=True)
    fresh = fresh and not db_settings.seed(seam.schema, p, ROOT)
    con = sqlite3.connect(p, isolation_level=None, timeout=busy_ms(seam) / 1000)
    refusal: NewerFile | None = None
    try:
        db_settings.apply(con, p, busy_ms(seam), seam.synchronous, fresh)
        if why := newer(con, seam):
            refusal = NewerFile(why)
        elif not record and (why := not_of_record(con, seam)):
            refusal = NotOfRecord(why)
        if refusal:
            con.close()
            warn(str(refusal))
            con = _ro(p, seam)
        elif record:
            migrate(con, seam)
            if fresh:
                db_settings.keep(seam.schema, p, ROOT)
        yield con, refusal
    finally:
        con.close()


@contextlib.contextmanager
def connect(
    seam: Seam, at: Path | None = None, named: bool = False
) -> Iterator[sqlite3.Connection]:
    """The seam's file (AT when a caller names one), opened read-write and migrated; a newer goh's
    file, or a shared one this copy may not migrate, read-only (says so). NAMED: the suite's
    runner named AT in an input it hands its top-level run (GOH_TEST_DURATIONS), the one file a
    suite may open in the real home."""
    with _opened(seam, at, named) as (con, _):
        yield con


@contextlib.contextmanager
def ro(seam: Seam, at: Path | None = None, named: bool = False) -> Iterator[sqlite3.Connection]:
    """The seam's file (AT when named) read-only; a missing file is OSError, never created."""
    p = at or path(seam)
    if not named:
        guard(p)
    con = _ro(p, seam)
    try:
        yield con
    finally:
        con.close()


@contextlib.contextmanager
def transaction(
    seam: Seam,
    check: Callable[[], None] = lambda: None,
    at: Path | None = None,
    named: bool = False,
) -> Iterator[sqlite3.Connection]:
    """One write: BEGIN IMMEDIATE (its wait appended to $GOH_TIMINGS), COMMIT, ROLLBACK on a raise.
    CHECK runs first: the seam's lock-order check (a wait on an innermost lock)."""
    check()
    with _opened(seam, at, named) as (con, refusal):
        if refusal:
            raise refusal
        if con.execute("PRAGMA query_only").fetchone()[0]:
            raise NewerFile(f"{seam.file} is open read-only")
        t0 = time.monotonic()
        try:
            con.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as e:
            if "readonly" in str(e):
                raise NewerFile(f"{seam.file} is open read-only: {e}") from e
            timed(seam, time.monotonic() - t0, busy=True)  # gave up at its busy timeout
            raise
        timed(seam, time.monotonic() - t0)
        try:
            yield con
        except BaseException:
            con.execute("ROLLBACK")
            raise
        con.execute("COMMIT")


def timed(seam: Seam, wait_s: float, busy: bool = False) -> None:
    """The lock-wait instrument (lib/db_waits.py): one row per BEGIN IMMEDIATE."""
    db_waits.row(seam.name, wait_s, busy)


def main(seam: Seam, argv: list[str]) -> int:
    """`path | migrate | version | dump TABLE [--json] | check` for one seam."""
    import db_bounds  # the bounds read the schema files; only `check` needs them

    cmd, rest = (argv[0] if argv else "path"), argv[1:]
    if cmd == "path":
        print(path(seam))
    elif cmd == "migrate":
        with connect(seam) as con:
            print(json.dumps(version(con)))
    elif cmd == "version":
        with ro(seam) as con:
            print(json.dumps(version(con)))
    elif cmd == "dump" and rest:
        with ro(seam) as con:
            cur = con.execute(f'SELECT * FROM "{rest[0]}"')
            cols = [d[0] for d in cur.description]
            for row in cur:
                print(json.dumps(dict(zip(cols, row, strict=True))) if "--json" in rest
                      else "\t".join(map(str, row)))  # fmt: skip
    elif cmd == "check":
        import db_safe

        found = [*db_safe.check(seam), *db_bounds.unbounded(seam)]
        for line in found:
            print(line)
        return 1 if found else 0
    else:
        print(f"usage: {seam.file}: path | migrate | version | dump TABLE [--json] | check",
              file=sys.stderr)  # fmt: skip
        return 2
    return 0
