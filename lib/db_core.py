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
down (`NewerFile` on a write).

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
import re
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

MIGRATION = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")
REAL_HOME_ENV = "GATES_OF_HECK_SUITE_REAL_HOME"  # tests/suite_home.py MARKER
REAL_XDG_STATE_ENV = "GATES_OF_HECK_SUITE_REAL_XDG_STATE_HOME"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tui.lib import warn  # noqa: E402 -- the root, on the path from the line above

BOOT = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS schema_version(
  version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at REAL NOT NULL, writer TEXT NOT NULL);
"""
BOOT_TABLES = ("meta", "schema_version")  # db_core's own: bounded by what migrations exist


class RealHomeOpen(RuntimeError):
    """A suite run opened the real home's goh database."""


class NewerFile(sqlite3.OperationalError):
    """A write to a file a newer goh wrote: this code opened it read-only."""


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


def migrations(seam: Seam) -> dict[int, Path]:
    """Every migration the seam ships, by number; a malformed name or a number twice raises."""
    out: dict[int, Path] = {}
    for p in sorted(seam.schema.glob("*.sql")):
        m = MIGRATION.match(p.name)
        if not m:
            raise ValueError(f"{p}: a migration is named NNNN_name.sql")
        n = int(m.group(1))
        if n in out:
            raise ValueError(f"{p}: migration {n} is also {out[n].name}")
        out[n] = p
    return out


def path(seam: Seam, env: Mapping[str, str] | None = None) -> Path:
    """The seam's file: its override (absolute only), else $XDG.../goh/FILE, else under HOME."""
    env = os.environ if env is None else env
    if raw := env.get(seam.env):
        if not os.path.isabs(raw):
            raise ValueError(f"{seam.env}={raw!r} is not an absolute path")
        return Path(raw)
    base = env.get(seam.xdg) or os.path.join(env.get("HOME") or os.path.expanduser("~"),
                                             seam.fallback)  # fmt: skip
    return Path(base) / "goh" / seam.file


def real_dirs(env: Mapping[str, str] | None = None) -> list[Path]:
    """The real home's goh state and cache directories while a suite runs, else none."""
    env = os.environ if env is None else env
    real = env.get(REAL_HOME_ENV)
    if not real:
        return []
    dirs = [Path(real, ".local", "state", "goh"), Path(real, ".cache", "goh")]
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


def writer() -> str:
    """The goh that writes: `goh <git describe>` of this checkout, `goh unknown` without git."""
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "describe", "--tags", "--match", "v[0-9]*"],
                             capture_output=True, text=True, check=False, timeout=10,
                             stdin=subprocess.DEVNULL).stdout.strip()  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        out = ""
    return f"goh {out.removeprefix('v') or 'unknown'}"


def busy_ms(seam: Seam) -> int:
    raw = os.environ.get(seam.busy_env, "")
    return int(raw) if raw.isdigit() else seam.busy_ms


def _settings(con: sqlite3.Connection, seam: Seam, fresh: bool) -> None:
    con.execute(f"PRAGMA busy_timeout={busy_ms(seam)}")
    if fresh:  # before the first table: a file's vacuum mode is fixed at creation
        con.execute("PRAGMA auto_vacuum=INCREMENTAL")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(f"PRAGMA synchronous={seam.synchronous}")


def _ro(p: Path, seam: Seam) -> sqlite3.Connection:
    if not p.is_file():
        raise FileNotFoundError(f"{p}: no {seam.file} (a read never creates it)")
    con = sqlite3.connect(f"{p.as_uri()}?mode=ro", uri=True, isolation_level=None,
                          timeout=busy_ms(seam) / 1000)  # fmt: skip
    con.execute(f"PRAGMA busy_timeout={busy_ms(seam)}")
    return con


def version(con: sqlite3.Connection) -> dict:
    """{user_version, applied, meta} of an open file (an empty file: 0, [], {})."""
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    applied = sorted(r[0] for r in con.execute("SELECT version FROM schema_version")) if (
        "schema_version" in names) else []  # fmt: skip
    meta = dict(con.execute("SELECT key, value FROM meta")) if "meta" in names else {}
    return {"user_version": con.execute("PRAGMA user_version").fetchone()[0],
            "applied": applied, "meta": meta}  # fmt: skip


def newer(con: sqlite3.Connection, seam: Seam) -> str | None:
    """Why the file is a newer goh's (a number this code does not ship), or None."""
    known = migrations(seam)
    v = version(con)
    unknown = sorted(set(v["applied"]) - set(known))
    if v["user_version"] > max(known, default=0) or unknown:
        return (f"{seam.file} is version {v['user_version']} (applied {unknown or v['applied']}),"
                f" newer than this goh's {max(known, default=0)}: opened read-only, never "
                "migrated down")  # fmt: skip
    return None


def migrate(con: sqlite3.Connection, seam: Seam) -> set[int]:
    """Apply every shipped migration missing from schema_version; the numbers applied."""
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not set(BOOT_TABLES) <= names:  # an existing file's open takes no write lock here
        con.executescript(BOOT)
    known = migrations(seam)
    have = {r[0] for r in con.execute("SELECT version FROM schema_version")}
    missing = sorted(set(known) - have)
    stamp = writer() if missing or not have else ""
    for n in missing:
        con.execute("BEGIN IMMEDIATE")
        try:
            if n in {r[0] for r in con.execute("SELECT version FROM schema_version")}:
                con.execute("ROLLBACK")  # another process applied it while this one waited
                continue
            for stmt in statements(known[n].read_text(encoding="utf-8")):
                con.execute(stmt)
            con.execute("INSERT INTO schema_version VALUES (?, ?, ?, ?)",
                        (n, known[n].stem, time.time(), stamp))  # fmt: skip
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
    _stamp(con, seam, stamp)
    return set(missing)


def _stamp(con: sqlite3.Connection, seam: Seam, stamp: str) -> None:
    """user_version and meta: the highest number with every lower one applied."""
    have = {r[0] for r in con.execute("SELECT version FROM schema_version")}
    top = 0
    while top + 1 in have:
        top += 1
    if con.execute("PRAGMA user_version").fetchone()[0] == top and not stamp:
        return
    con.execute("BEGIN IMMEDIATE")
    con.execute(f"PRAGMA user_version={top}")
    rows = {"db": seam.name, "schema_version": str(top)}
    if stamp:
        rows["last_migrated_by"] = stamp
    con.executemany("INSERT INTO meta VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET "
                    "value=excluded.value", rows.items())  # fmt: skip
    con.executemany(
        "INSERT OR IGNORE INTO meta VALUES (?, ?)",
        [("created_by", stamp or writer()), ("created_at", f"{time.time():.3f}")],
    )
    con.execute("COMMIT")


def statements(sql: str) -> list[str]:
    """SQL's statements, whole (a `;` inside a trigger body or a string stays in its statement)."""
    out, buf = [], ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip():
                out.append(buf.strip())
            buf = ""
    if buf.strip() and not all(ln.lstrip().startswith("--") for ln in buf.strip().splitlines()):
        raise ValueError(f"an unterminated statement: {buf.strip()[:80]!r}")
    return out


@contextlib.contextmanager
def connect(
    seam: Seam, at: Path | None = None, named: bool = False
) -> Iterator[sqlite3.Connection]:
    """The seam's file (AT when a caller names one), opened read-write and migrated; a newer goh's
    file read-only (says so). NAMED: the suite's runner named AT in an input it hands its
    top-level run (GOH_TEST_DURATIONS), the one file a suite may open in the real home."""
    p = at or path(seam)
    if not named:
        guard(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    fresh = not p.exists() or p.stat().st_size == 0
    con = sqlite3.connect(p, isolation_level=None, timeout=busy_ms(seam) / 1000)
    try:
        _settings(con, seam, fresh)
        if why := newer(con, seam):
            con.close()
            warn(why)
            con = _ro(p, seam)
        else:
            migrate(con, seam)
        yield con
    finally:
        con.close()


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
    with connect(seam, at, named) as con:
        if con.execute("PRAGMA query_only").fetchone()[0] or newer(con, seam):
            raise NewerFile(newer(con, seam) or f"{seam.file} is open read-only")
        t0 = time.monotonic()
        try:
            con.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as e:
            if "readonly" in str(e):
                raise NewerFile(f"{seam.file} is open read-only: {e}") from e
            raise
        timed(seam, time.monotonic() - t0)
        try:
            yield con
        except BaseException:
            con.execute("ROLLBACK")
            raise
        con.execute("COMMIT")


def timed(seam: Seam, wait_s: float) -> None:
    """The lock-wait instrument: one $GOH_TIMINGS line per BEGIN IMMEDIATE (a failed write is
    dropped; the instrument never changes an outcome)."""
    out = os.environ.get("GOH_TIMINGS")
    if not out:
        return
    row = {"label": f"db wait {seam.name}", "ms": round(wait_s * 1000, 1), "rc": 0, "tier": "db",
           "parent": os.environ.get("GOH_TIMINGS_PARENT", "")[:300], "t": round(time.time(), 3),
           "db": seam.name, "pid": os.getpid(), "wait_s": round(wait_s, 6)}  # fmt: skip
    with contextlib.suppress(OSError):
        fd = os.open(out, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, (json.dumps(row) + "\n").encode())
        finally:
            os.close(fd)


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
        found = db_bounds.unbounded(seam)
        for line in found:
            print(line)
        return 1 if found else 0
    else:
        print(f"usage: {seam.file}: path | migrate | version | dump TABLE [--json] | check",
              file=sys.stderr)  # fmt: skip
        return 2
    return 0
