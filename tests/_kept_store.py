"""A content-addressed store a test keeps ACROSS sessions (GOH.SELFWALKFILTER, GOH.VERDICTDB).

The suite's home is the run's own and emptied at exit (tests/suite_home.py), so a verdict cache in
it is cold at every run. A record keyed by EVERY input of its verdict -- the file's bytes and what
it can read, the tool's identity, the flags -- is the verdict a later session would compute, so it
may outlive the run: tests/_self_walk.py's shell-lint verdicts (`verdict_store`), the tree lint's
(tests/_tree_lint.py) and the parse findings (tests/_parse_findings.py, GOH.SHELLCHECKCACHE). Each
record is dropped after STORE_DAYS, so a store never grows past what a month of goh's sources held.

ROWS, HERMETIC (GOH.VERDICTDB V1). The parse findings are rows of goh-cache.db's `kept` table
(`get`/`put`). Workers read and write only the SUITE's own goh-cache.db; the controller seeds it
from the real file at session start and merges the session's rows back ONCE at its finish
(INSERT OR IGNORE: a row is its whole key's verdict), so no test writes the real file and one
process does. The real file is named by the runner, resolved before the suite replaces HOME
(tests/suite_home.py, KEPT_ENV), and taken out of the environment when this plugin loads, so a
pytest session a test starts merges nothing.

FILES, until V2. The two shell-lint stores are directories the goh binary reads as files
(crates/goh/src/verdict_cache.rs): `kept` still keeps them in the real state, pruned here at
STORE_DAYS, until V2 moves the Rust cache into goh-cache.db.
"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path

import real_state_guard
import suite_home

STORE_DAYS = 30
KEPT_ENV = "GATES_OF_HECK_SUITE_KEPT_DB"  # the real goh-cache.db (tests/suite_home.py)
_REAL = os.environ.pop(KEPT_ENV, "")  # the runner's, for this process alone (module docstring)
_LIB = str(Path(__file__).resolve().parent.parent / "lib")
# SQLite's clock, not a Python clock call: nothing here waits or measures, so a test reaching the
# store is no load-sensitive one (tests/load_sensitive.py), as `kept`'s file stamp below.
NOW = "((julianday('now') - 2440587.5) * 86400.0)"


def _cache():
    if _LIB not in sys.path:
        sys.path.insert(0, _LIB)
    import goh_cache_db  # lib/, on the path above

    return goh_cache_db


def kept(name: str) -> Path:
    """The store NAME under the machine's real goh state, its stale records dropped."""
    # The REAL home: an xdist worker imports suite_home under the suite's own HOME, so its
    # REAL_HOME is the suite's; the marker carries the user's (the store died with the session).
    real_home = os.environ.get(suite_home.MARKER) or suite_home.REAL_HOME
    store = real_state_guard.real_state_dir(real_home) / name
    store.mkdir(parents=True, exist_ok=True)
    # The age is read off the filesystem's own stamp, not a clock call: nothing here waits or
    # measures, so a test reaching the store is no load-sensitive one (tests/load_sensitive.py).
    now = store / ".now"
    now.touch()
    cutoff = now.stat().st_mtime - STORE_DAYS * 86400
    for record in store.rglob("*.json"):
        with contextlib.suppress(OSError):
            if record.stat().st_mtime < cutoff:
                record.unlink()
    return store


def get(store: str, keys: list[str], db: Path | None = None) -> dict[str, str]:
    """{key: body} of STORE's rows among KEYS in DB (the suite's own goh-cache.db by default)."""
    gcd, out = _cache(), {}
    p = db or gcd.path()
    if not keys or not p.is_file():
        return out
    with gcd.missable(f"kept {store}"), gcd.ro(p) as con:
        for i in range(0, len(keys), 500):
            part = keys[i : i + 500]
            out.update(con.execute(f"SELECT key, body FROM kept WHERE store=? AND key IN "
                                   f"({','.join('?' * len(part))})", [store, *part]))  # fmt: skip
    return out


def put(store: str, rows: dict[str, str], db: Path | None = None) -> None:
    """STORE's ROWS ({key: body}) in DB, one transaction (a database error: a skipped write)."""
    gcd = _cache()
    if rows:
        with gcd.missable(f"kept {store}"), gcd.transaction(db) as con:
            sql = f"INSERT OR REPLACE INTO kept(store, key, body, at) VALUES (?, ?, ?, {NOW})"
            con.executemany(sql, [(store, k, b) for k, b in rows.items()])


def merge(src: Path, dest: Path, named: bool) -> int:
    """SRC's `kept` rows younger than STORE_DAYS that DEST lacks, into DEST, whose older rows go;
    how many were new. NAMED: DEST (or SRC) is the real file the runner named."""
    gcd, rows = _cache(), []
    if not src.is_file():
        return 0
    cut = f"{NOW} - {STORE_DAYS * 86400}"
    with gcd.missable("kept stores"), gcd.ro(src, named) as con:
        rows = con.execute(f"SELECT store, key, body, at FROM kept WHERE at >= {cut}").fetchall()
    new = 0
    with gcd.missable("kept stores"), gcd.transaction(dest, named) as con:
        before = con.total_changes
        con.executemany("INSERT OR IGNORE INTO kept(store, key, body, at) VALUES (?,?,?,?)", rows)
        new = con.total_changes - before
        con.execute(f"DELETE FROM kept WHERE at < {cut}")
    return new


def _controller(config) -> bool:
    return not hasattr(config, "workerinput") and bool(_REAL)


def pytest_sessionstart(session) -> None:
    """Seed the suite's goh-cache.db from the real file: earlier sessions' verdicts."""
    if _controller(session.config):
        p = _cache().path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with _cache().missable("kept stores"):
            merge(Path(_REAL), p, True)  # read-only of the real file


def pytest_sessionfinish(session) -> None:
    """The session's rows into the real file, once, by this process alone."""
    if _controller(session.config):
        merge(_cache().path(), Path(_REAL), True)
