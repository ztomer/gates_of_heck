"""lib/reuse_audit_store.py -- where lib/reuse_audit.py's records live (GOH.VERDICTDB V1, 0008).

    reuse_audit_store.py import [AUDIT_DIR] | export [AUDIT_DIR] [--to DIR]

WHY goh.db, not goh-cache.db. A path's record is the audit's one recorded state: OFF means a reuse
disagreed with its fresh run (lib/reuse_audit.py). Deleting it would turn a wrong cache back on,
so it is durable state (handover plan (0) 4), one row of goh.db's `reuse_audit` per registered
path; every compared sample is a row of `reuse_shadow` (the review's evidence).

    db()           NAMED, else <GOH_REUSE_DIR>/goh.db when a caller names the reuse root (every
                   test does, so each test has its own audit, as it had its own files), else
                   goh.db's own path (GOH_DB, else $XDG_STATE_HOME/goh/goh.db)
    get(name)      the record of path NAME, or None
    put(rec)       write REC whole as its path's record
    shadow_add(row)  one compared sample
    shadow()       every sample, oldest first

THE FILES BEFORE THE SWITCH (handover plan (b)). A record file (reuse/audit/<path>.json) is read
when the database has no row for its path; the first write of that path writes the whole record,
so the file is imported by its first use. `import` copies every file in (INSERT OR IGNORE on the
path, shadow.jsonl's lines by their body), recorded in the `imports` ledger by sha256 so a re-run
is harmless; `export` writes them back as the files were, the round trip's other half. The files
stay on disk until wave 3's delete commit.

A WRITE THIS COPY MAY NOT MAKE (a newer goh's file, a lane's copy before main applies 0008) is
skipped with one stderr line (goh_db.refusable): the audit's count is lost, never a gate's verdict.
NAMED: a land suite's controller counts its reuses into the caller's goh.db, which the runner
names (tests/_land_verdicts.py, GOH_LAND_VERDICTS_AUDIT_DB), the one real-home goh.db it may open.
Pinned by tests/test_reuse_audit_store.py.
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
import hashlib
import json
import os
import sqlite3
import sys
import time
from collections.abc import Iterator
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import goh_db  # lib/, beside this file: the one door to goh.db
import reuse_store  # lib/: the reuse root (GOH_REUSE_DIR)

STORE = "reuse audit"  # the imports ledger's store
NAMED: Path | None = None  # the caller's goh.db a land suite's controller counts into
SHADOW = "shadow.jsonl"


def db() -> Path:
    if NAMED is not None:
        return NAMED
    if reuse_store.root_named():
        return reuse_store.root() / goh_db.SEAM.file
    return goh_db.path()


def legacy_dir() -> Path:
    """Where the records were files before the switch (read until wave 3's delete commit)."""
    return reuse_store.root() / "audit"


def lock_path() -> Path:
    """lib/reuse_audit.py's flock, a file where it always was (handover plan (e): locks stay)."""
    return legacy_dir() / ".lock"


def _named() -> bool:
    return NAMED is not None


def _json(p: Path) -> object:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _dump(row: object) -> str:
    return json.dumps(row, sort_keys=True)


@contextlib.contextmanager
def _read() -> Iterator[sqlite3.Connection | None]:
    """The database read-only, or None when there is none (or it predates 0008)."""
    with contextlib.ExitStack() as stack:
        try:
            con = stack.enter_context(goh_db.ro(db(), _named()))
            con.execute("SELECT 1 FROM reuse_audit LIMIT 1")
        except (OSError, sqlite3.Error):
            con = None
        yield con


def get(name: str) -> dict | None:
    """Path NAME's record: its row, else its file from before the switch, else None."""
    with _read() as con:
        row = None if con is None else con.execute(
            "SELECT body FROM reuse_audit WHERE path=?", (name,)).fetchone()  # fmt: skip
    rec = json.loads(row[0]) if row else _json(legacy_dir() / f"{name}.json")
    return rec if isinstance(rec, dict) else None


def put(rec: dict) -> None:
    """REC written whole as the record of its path (`rec["path"]`)."""
    with goh_db.refusable(), goh_db.transaction(db(), _named()) as con:
        con.execute("INSERT INTO reuse_audit(path, body, at) VALUES (?, ?, ?) ON CONFLICT(path) "
                    "DO UPDATE SET body=excluded.body, at=excluded.at",
                    (str(rec["path"]), _dump(rec), time.time()))  # fmt: skip


def shadow_add(row: dict) -> None:
    """One compared sample (`row["path"]`, `row["epoch"]`)."""
    with goh_db.refusable(), goh_db.transaction(db(), _named()) as con:
        con.execute("INSERT INTO reuse_shadow(epoch, path, body) VALUES (?, ?, ?)",
                    (float(row.get("epoch") or time.time()), str(row.get("path") or ""),
                     _dump(row)))  # fmt: skip


def _lines(p: Path) -> list[dict]:
    out = []
    with contextlib.suppress(OSError):
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            with contextlib.suppress(ValueError):
                if isinstance(row := json.loads(line), dict):
                    out.append(row)
    return out


def shadow() -> list[dict]:
    """Every compared sample: the rows in the order written, then shadow.jsonl's lines no row holds
    (a file not yet imported, or an old HEAD export's append after it was)."""
    with _read() as con:
        rows = [] if con is None else [
            b for (b,) in con.execute("SELECT body FROM reuse_shadow ORDER BY n")]  # fmt: skip
        f = legacy_dir() / SHADOW
        base = [] if con is not None and _imported(con, f, _sha(f)) else _lines(f)
    have = set(rows)
    return [json.loads(b) for b in rows] + [r for r in base if _dump(r) not in have]


# -- the import and its round trip ---------------------------------------------------------------


def _sha(p: Path) -> str | None:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return None


def _imported(con: sqlite3.Connection, p: Path, sha: str | None) -> bool:
    """True when P is gone or its bytes are in the imports ledger."""
    return sha is None or con.execute(
        "SELECT 1 FROM imports WHERE store=? AND source=? AND sha256=?", (STORE, str(p), sha)
    ).fetchone() is not None  # fmt: skip


def _ledger(con: sqlite3.Connection, p: Path, sha: str, rows: int) -> None:
    con.execute("INSERT OR IGNORE INTO imports VALUES (?, ?, ?, ?, ?)",
                (STORE, str(p), sha, rows, time.time()))  # fmt: skip


def import_all(audit: Path | None = None) -> dict[str, int]:
    """Every record file and shadow.jsonl's lines under AUDIT (the legacy dir) into the database:
    rows read in, per record and for the shadow. A row already there wins (INSERT OR IGNORE); a
    shadow line already in the table is not imported twice. Re-runs are harmless (the ledger)."""
    audit = audit or legacy_dir()
    out: dict[str, int] = {}
    with goh_db.transaction(db(), _named()) as con:
        for f in sorted(audit.glob("*.json")):
            rec, sha = _json(f), _sha(f)
            if _imported(con, f, sha) or not isinstance(rec, dict):
                continue
            n = con.execute("INSERT OR IGNORE INTO reuse_audit(path, body, at) VALUES (?, ?, ?)",
                            (f.stem, _dump(rec), f.stat().st_mtime)).rowcount  # fmt: skip
            _ledger(con, f, str(sha), n)
            out[f.stem] = n
        f = audit / SHADOW
        if not _imported(con, f, sha := _sha(f)):
            have = {b for (b,) in con.execute("SELECT body FROM reuse_shadow")}
            fresh = [(float(row.get("epoch") or 0), str(row.get("path") or ""), body)
                     for row in _lines(f) if (body := _dump(row)) not in have]  # fmt: skip
            con.executemany("INSERT INTO reuse_shadow(epoch, path, body) VALUES (?, ?, ?)", fresh)
            n = len(fresh)
            _ledger(con, f, str(sha), n)
            out["shadow"] = n
    return out


def export(audit: Path | None = None) -> dict:
    """The store as its readers see it: {"records": {path: record}, "shadow": [row]}; a record
    file of a path the database has no row for is read as get() reads it."""
    audit = audit or legacy_dir()
    with _read() as con:
        names = set() if con is None else {
            n for (n,) in con.execute("SELECT path FROM reuse_audit")}  # fmt: skip
    names |= {f.stem for f in audit.glob("*.json")}
    records = {n: rec for n in sorted(names) if (rec := get(n)) is not None}
    return {"records": records, "shadow": shadow()}


def main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="reuse_audit_store.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=("import", "export"))
    ap.add_argument("audit", nargs="?", type=Path, default=None)
    ap.add_argument("--to", type=Path, help="export: the directory the files are written to")
    a = ap.parse_args(argv)
    if a.cmd == "import":
        print(json.dumps(import_all(a.audit), sort_keys=True))
        return 0
    got = export(a.audit)
    if a.to is None:
        print(_dump(got))
        return 0
    write(got["records"], got["shadow"], a.to)
    return 0


def write(records: dict, rows: list, to: Path) -> None:
    """The store as the files were: <path>.json per record (as lib/reuse_audit.py wrote them) and
    shadow.jsonl, one sorted-key line per sample."""
    to.mkdir(parents=True, exist_ok=True)
    for name, rec in records.items():
        (to / f"{name}.json").write_text(json.dumps(rec, sort_keys=True, indent=1),
                                         encoding="utf-8")  # fmt: skip
    (to / SHADOW).write_text("".join(_dump(r) + "\n" for r in rows), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
