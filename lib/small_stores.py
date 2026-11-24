"""lib/small_stores.py -- goh's small state stores in goh.db (GOH.STATEDBS3, goh.db 0004).

    small_stores.py import [STATE_DIR] | export [STATE_DIR] --to DIR

WHY. Seven stores were files rewritten whole or appended per line: five whole-file JSON
documents (the estate audit's state, the pin push ledger and sweep registry, the cache evictor's
ledger, wf-pin's consumer pin record), the commit-gated ledger and the flake register. They move
into goh.db's `kv`, `commit_gated` and `flakes` tables (lib/goh_db_schema/0004_small_stores.sql);
the CPU idle window moves into goh-cache.db's `kv` (lib/cpu_window.py), being disposable.

WHERE. Each store keeps the path its file had (its env override: GOH_ESTATE_STATE,
GOH_CACHE_EVICT_STATE, GOH_FLAKE_DIR, ...), and its rows live in the goh.db BESIDE that path, so a
caller (a test) that names its own file has its own database, as it had its own file:

    db(p)     GOH_DB when set, else p's directory's goh.db (the default paths: goh_db.path())

THE FILES BEFORE THE SWITCH (handover plan (b)). A file is imported once, by its sha256 (the
`imports` ledger), in the first write's transaction; until then a reader reads it under the
database's rows. A kv row, once written, wins over its file (INSERT OR IGNORE); a line already in
its table is not imported twice (an old HEAD export appending to the file after the switch). A file
stays on disk until wave 3's delete commit. Pinned by tests/test_small_stores.py.
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

import calendar
import contextlib
import hashlib
import json
import os
import sqlite3
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import goh_db  # the lib directory, on the path from the line above

STORE = "small stores"  # the imports ledger's store
# kv names: each was the file `<name>.json` (kv_name), beside the database (the estate audit's,
# the pin push ledger's and the sweep registry's dir is GOH_ESTATE_STATE's)
KV_NAMES = ("estate_audit", "pin_push", "pin_sweeps", "cache-evict", "wf_pin.pins")
KV_FILES = {name: f"{name}.json" for name in KV_NAMES}
GATED_FILE = "commit-gated.jsonl"
FLAKE_DIR = "flakes"


def db(beside: Path | str) -> Path:
    """The goh.db a store whose file is BESIDE lives in: GOH_DB when set, else beside it."""
    if os.environ.get("GOH_DB"):
        return goh_db.path()
    return Path(beside).expanduser().with_name("goh.db")


def kv_name(p: Path | str) -> str:
    """A whole-file store's kv name: its file name less `.json` (estate_audit.json: estate_audit)"""
    return Path(p).name.removesuffix(".json")


def _sha(p: Path) -> str | None:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return None


def _imported(con: sqlite3.Connection, p: Path, sha: str | None) -> bool:
    """True when P is gone or its bytes are in the imports ledger."""
    return (
        sha is None
        or con.execute(
            "SELECT 1 FROM imports WHERE store=? AND source=? AND sha256=?", (STORE, str(p), sha)
        ).fetchone()
        is not None
    )


def _ledger(con: sqlite3.Connection, p: Path, sha: str, rows: int) -> None:
    con.execute("INSERT OR IGNORE INTO imports VALUES (?, ?, ?, ?, ?)",
                (STORE, str(p), sha, rows, time.time()))  # fmt: skip


def _json(p: Path) -> object:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _lines(p: Path) -> list[dict]:
    out = []
    with contextlib.suppress(OSError):
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            with contextlib.suppress(ValueError):
                if isinstance(row := json.loads(line), dict):
                    out.append(row)
    return out


def _dump(row: object) -> str:
    return json.dumps(row, sort_keys=True)


@contextlib.contextmanager
def _read(p: Path) -> Iterator[sqlite3.Connection | None]:
    """The database beside P read-only, or None when there is none (or it predates 0004)."""
    with contextlib.ExitStack() as stack:
        try:
            con = stack.enter_context(goh_db.ro(db(p)))
            con.execute("SELECT 1 FROM kv LIMIT 1")
        except (OSError, sqlite3.Error):
            con = None
        yield con


@contextlib.contextmanager
def _write(p: Path) -> Iterator[sqlite3.Connection]:
    with goh_db.transaction(db(p)) as con:
        yield con


# -- kv: the whole-file JSON stores --------------------------------------------------------------


def _import_kv(con: sqlite3.Connection, p: Path) -> int:
    sha = _sha(p)
    if _imported(con, p, sha):
        return 0
    data = _json(p)
    n = (
        0
        if data is None
        else con.execute(
            "INSERT OR IGNORE INTO kv VALUES (?, ?, ?)",
            (kv_name(p), _dump(data), p.stat().st_mtime),
        ).rowcount
    )
    _ledger(con, p, str(sha), n)
    return n


def get(p: Path | str) -> object:
    """The store whose file is P: its kv row, else the file's JSON (not yet imported), else None."""
    p = Path(p)
    with _read(p) as con:
        if con is not None:
            row = con.execute("SELECT body FROM kv WHERE name=?", (kv_name(p),)).fetchone()
            if row is not None:
                return json.loads(row[0])
    return _json(p)


def update(p: Path | str, change: Callable[[object], object]) -> object:
    """The store whose file is P read, CHANGEd and written in one transaction: CHANGE is handed the
    value (None for none) and returns the new one, returned here. Raises sqlite3.Error, OSError."""
    p = Path(p)
    with _write(p) as con:
        _import_kv(con, p)
        row = con.execute("SELECT body FROM kv WHERE name=?", (kv_name(p),)).fetchone()
        new = change(json.loads(row[0]) if row else None)
        con.execute("INSERT INTO kv VALUES (?, ?, ?) ON CONFLICT(name) DO UPDATE SET "
                    "body=excluded.body, at=excluded.at",
                    (kv_name(p), _dump(new), time.time()))  # fmt: skip
    return new


def put(p: Path | str, value: object) -> None:
    update(p, lambda _old: value)


# -- commit_gated: every tree a commit gate passed ------------------------------------------------


def _epoch(at: object) -> float:
    """An ISO stamp (`2026-11-22T21:27:12Z`) or a number as epoch seconds; 0 when neither."""
    if isinstance(at, (int, float)):
        return float(at)
    try:
        return float(calendar.timegm(time.strptime(str(at)[:19], "%Y-%m-%dT%H:%M:%S")))
    except ValueError:
        return 0.0


def _import_gated(con: sqlite3.Connection, p: Path) -> int:
    sha = _sha(p)
    if _imported(con, p, sha):
        return 0
    n = 0
    for row in _lines(p):
        tree, body = str(row.get("tree") or ""), _dump(row)
        if tree and not con.execute("SELECT 1 FROM commit_gated WHERE tree=? AND body=?",
                                    (tree, body)).fetchone():  # fmt: skip
            con.execute("INSERT INTO commit_gated(at, tree, body) VALUES (?, ?, ?)",
                        (_epoch(row.get("at")), tree, body))  # fmt: skip
            n += 1
    _ledger(con, p, str(sha), n)
    return n


def gated_add(p: Path | str, row: dict) -> None:
    """One commit-gated row ({tree, repo, at}) for the ledger whose file is P."""
    p = Path(p)
    with _write(p) as con:
        _import_gated(con, p)
        con.execute("INSERT INTO commit_gated(at, tree, body) VALUES (?, ?, ?)",
                    (_epoch(row.get("at")), str(row["tree"]), _dump(row)))  # fmt: skip


def gated(p: Path | str) -> list[dict]:
    """The commit-gated rows of the ledger whose file is P, oldest first (the file's not yet
    imported first)."""
    p = Path(p)
    with _read(p) as con:
        if con is None:
            return _lines(p)
        rows = [b for (b,) in con.execute("SELECT body FROM commit_gated ORDER BY n")]
        have = set(rows)
        base = [] if _imported(con, p, _sha(p)) else _lines(p)
        return [r for r in base if _dump(r) not in have] + [json.loads(b) for b in rows]


# -- flakes: the flake register --------------------------------------------------------------


def _flake_files(d: Path) -> list[Path]:
    return [d / "register.jsonl", *sorted((d / "by-test").glob("*.jsonl"))]


def _flake_rows(d: Path, files: list[Path]) -> list[tuple[float, int, str, dict]]:
    """(epoch, 0 record | 1 filing, test, row) of the register's records and the by-test files'
    filings, in the order they were written (a filing follows the record that made it)."""
    recs = (
        [r for r in _lines(d / "register.jsonl") if "test" in r]
        if d / "register.jsonl" in files
        else []
    )
    tests = {r["id"].partition(".")[0]: r["test"] for r in recs if isinstance(r.get("id"), str)}
    out = [(float(r.get("epoch") or 0), 0, str(r["test"]), r) for r in recs]
    for f in files:
        if f.parent.name != "by-test":
            continue
        test = tests.get(f.stem)
        for r in _lines(f):
            if "filed" in r and test:
                out.append((float(r.get("epoch") or 0), 1, test, r))
    return sorted(out, key=lambda t: (t[0], t[1]))


def _import_flakes(con: sqlite3.Connection, d: Path) -> int:
    fresh = [(f, sha) for f in _flake_files(d) if not _imported(con, f, sha := _sha(f))]
    if not fresh:
        return 0
    n = 0
    for epoch, _, test, row in _flake_rows(d, [f for f, _ in fresh]):
        body = _dump(row)
        if not con.execute("SELECT 1 FROM flakes WHERE test=? AND body=?", (test, body)).fetchone():
            con.execute(
                "INSERT INTO flakes(epoch, test, body) VALUES (?, ?, ?)", (epoch, test, body)
            )
            n += 1
    for f, sha in fresh:
        _ledger(con, f, str(sha), n)
    return n


def flake_add(d: Path | str, test: str, row: dict) -> None:
    """One row of TEST (a record, or a filing) in the register whose directory is D."""
    d = Path(d)
    with goh_db.transaction(db(d)) as con:
        _import_flakes(con, d)
        con.execute("INSERT INTO flakes(epoch, test, body) VALUES (?, ?, ?)",
                    (float(row.get("epoch") or time.time()), test, _dump(row)))  # fmt: skip


def flakes(d: Path | str, test: str | None = None) -> list[dict]:
    """The register's rows (TEST's only, when named) in the order written; records carry `id`,
    filings `filed`. The files not yet imported are read under the database's rows."""
    d = Path(d)
    files = _flake_files(d)
    with contextlib.ExitStack() as stack:
        try:
            con = stack.enter_context(goh_db.ro(db(d)))
            con.execute("SELECT 1 FROM flakes LIMIT 1")
        except (OSError, sqlite3.Error):
            con = None
        pending = files if con is None else [f for f in files if not _imported(con, f, _sha(f))]
        rows = (
            []
            if con is None
            else con.execute("SELECT test, body FROM flakes ORDER BY n").fetchall()
        )
    have = set(rows)
    base = [(t, r) for _, _, t, r in _flake_rows(d, pending) if (t, _dump(r)) not in have]
    got = base + [(t, json.loads(b)) for t, b in rows]
    return [r for t, r in got if test is None or t == test]


# -- the import and its round trip ---------------------------------------------------------------


def import_all(state: Path | str) -> dict[str, int]:
    """Every small store's file in the state directory STATE into its goh.db (`import`): rows read
    in per store. Re-runs are harmless (the imports ledger)."""
    state = Path(state)
    anchor = state / KV_FILES["estate_audit"]
    with goh_db.transaction(db(anchor)) as con:
        out = {name: _import_kv(con, state / f) for name, f in KV_FILES.items()}
        out["commit_gated"] = _import_gated(con, state / GATED_FILE)
        out["flakes"] = _import_flakes(con, state / FLAKE_DIR)
    return out


def export(state: Path | str) -> dict[str, object]:
    """Every store as its readers see it: {kv name: value, commit_gated: [row], flakes: [row]}."""
    state = Path(state)
    out: dict[str, object] = {name: get(state / f) for name, f in KV_FILES.items()}
    out["commit_gated"] = gated(state / GATED_FILE)
    out["flakes"] = flakes(state / FLAKE_DIR)
    return out


def main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="small_stores.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=("import", "export"))
    ap.add_argument("state", nargs="?", default=str(goh_db.path().parent))
    ap.add_argument("--to", type=Path, help="export: the directory each store is written to")
    a = ap.parse_args(argv)
    if a.cmd == "import":
        print(json.dumps(import_all(a.state), sort_keys=True))
        return 0
    got = export(a.state)
    if a.to is None:
        print(json.dumps(got, sort_keys=True))
        return 0
    a.to.mkdir(parents=True, exist_ok=True)
    for name, value in got.items():
        (a.to / f"{name}.json").write_text(_dump(value) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
