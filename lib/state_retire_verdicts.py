"""lib/state_retire_verdicts.py -- GOH.VERDICTDB's stores, retired by lib/state_retire.py.

    reuse-records    reuse/<path>/<kk>/<key>.json   -> goh-cache.db verdicts   imported, dropped
    reuse-audit      reuse/audit/*.json, shadow.jsonl -> goh.db reuse_audit, reuse_shadow, archived
    parse-findings   <state>/parse-findings/        goh-cache.db kept rows    dropped unread
    shell-verdicts   <state>/{self-walk,tree-lint}-verdicts/, <cache>/verdicts/   dropped unread
    identity-memo    <cache>/identity/              goh-cache.db memo rows    dropped unread

WHY THIS MODULE. lib/state_retire.py is the one module that names a moved store's files; these are
its verdict caches, split out at its line cap and listed beside it in the seam
(tests/_state_seam_scan.py SEAM). Their readers and writers went through goh-cache.db and goh.db
alone from wave 3's delete commit on, so a file left here is read by nothing.

ARCHIVE OR DROP (`drop`, the reason each store's files go with no archive):
  * reuse-records: every record is imported (INSERT OR IGNORE on (path, key)) and read back equal,
    or its row is newer (the path was written since). The rows then hold each record, and they are
    a cache: a lost one costs one gate re-run, never a verdict (the parity proof of 2026-11-24: one
    land gate's carry from the files and from the rows chose the same 764 nodes, and with the cache
    deleted it carried none and re-ran them). The archive would be a second copy, 161 MB of 1,520
    files on the owner's box (136 MB of it `carry`).
  * reuse-audit is durable (an OFF lost turns a wrong reuse back on): imported, read back,
    ARCHIVED (8.2 MB).
  * parse-findings, shell-verdicts, identity-memo are pure caches keyed by every input of their
    value, which their readers stopped reading when each moved (V1, V2): the next run recomputes a
    missing one. Importing 190k files (the shell verdicts' 1.4 GB) to read back what is recomputed
    in seconds is the waste V2 refused (the cache started cold); they are dropped unread.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import goh_cache_db  # lib/, on the path from the line above
import goh_db
import reuse_store  # lib/: the reuse root (GOH_REUSE_DIR)

RECORD_DIRS = ("affected", "carry", "gate", "pushed", "profcarry", "relset")  # reuse paths' files
AUDIT = "reuse audit"  # the imports ledger's store, as lib/reuse_audit_store.py named it


def _xdg(var: str, default: tuple[str, ...]) -> Path:
    base = os.environ.get(var, "")
    return (Path(base) if base.startswith("/") else Path.home().joinpath(*default)) / "goh"


def _state() -> Path:
    return _xdg("XDG_STATE_HOME", (".local", "state"))


def _cache() -> Path:
    return _xdg("XDG_CACHE_HOME", (".cache",))


def _all(*dirs: Path) -> list[Path]:
    return sorted(p for d in dirs if d.is_dir() for p in d.rglob("*") if p.is_file())


def _json(p: Path) -> object:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _dump(row: object) -> str:
    return json.dumps(row, sort_keys=True)


def _ledger(con: sqlite3.Connection, store: str, p: Path, rows: int) -> None:
    import hashlib

    con.execute("INSERT OR IGNORE INTO imports VALUES (?, ?, ?, ?, ?)",
                (store, str(p), hashlib.sha256(p.read_bytes()).hexdigest(), rows,
                 time.time()))  # fmt: skip


# -- reuse records (goh-cache.db 0002 verdicts) --------------------------------------------------


def _record_dirs() -> list[Path]:
    return [reuse_store.root() / d for d in RECORD_DIRS]


def _record(p: Path) -> dict | None:
    rec = _json(p)
    return rec if isinstance(rec, dict) else None  # no record: its readers read it as a miss


def _epoch(rec: dict) -> int:
    return rec["epoch"] if isinstance(rec.get("epoch"), int) else -1


def _records_load(con: sqlite3.Connection, files: list[Path]) -> None:
    """Each record file's row, as lib/reuse_store.py `put` writes it, unless its key has one."""
    for p in files:
        if (rec := _record(p)) is not None:
            con.execute(
                "INSERT OR IGNORE INTO verdicts(path, key, fields, verdict, epoch, body) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (p.parent.parent.name, p.stem, _dump(rec.get("fields")), str(rec.get("verdict")),
                 _epoch(rec), _dump(rec)),
            )  # fmt: skip


def _records_check(con: sqlite3.Connection, p: Path) -> str | None:
    if (rec := _record(p)) is None:
        return None
    row = con.execute("SELECT body, epoch FROM verdicts WHERE path=? AND key=?",
                      (p.parent.parent.name, p.stem)).fetchone()  # fmt: skip
    if row is None:
        return "no verdicts row"
    if json.loads(row[0]) == rec or row[1] > _epoch(rec):
        return None
    return "its verdicts row differs and is not newer"


# -- the reuse audit (goh.db 0008 reuse_audit, reuse_shadow) -------------------------------------


def _audit_dir() -> Path:
    return reuse_store.root() / "audit"


def _audit_files() -> list[Path]:
    d = _audit_dir()
    return sorted([*d.glob("*.json"), *d.glob("shadow.jsonl")])


def _lines(p: Path) -> list[dict]:
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _audit_load(con: sqlite3.Connection, files: list[Path]) -> None:
    for p in files:
        if p.suffix == ".json":
            rec = _json(p)
            n = 0 if not isinstance(rec, dict) else con.execute(
                "INSERT OR IGNORE INTO reuse_audit(path, body, at) VALUES (?, ?, ?)",
                (p.stem, _dump(rec), p.stat().st_mtime)).rowcount  # fmt: skip
        else:
            have = {b for (b,) in con.execute("SELECT body FROM reuse_shadow")}
            fresh = [(float(r.get("epoch") or 0), str(r.get("path") or ""), b)
                     for r in _lines(p) if (b := _dump(r)) not in have]  # fmt: skip
            con.executemany("INSERT INTO reuse_shadow(epoch, path, body) VALUES (?, ?, ?)", fresh)
            n = len(fresh)
        _ledger(con, AUDIT, p, n)


def _audit_check(con: sqlite3.Connection, p: Path) -> str | None:
    if p.suffix != ".json":
        have = {b for (b,) in con.execute("SELECT body FROM reuse_shadow")}
        short = sum(_dump(r) not in have for r in _lines(p))
        return f"{short} lines have no reuse_shadow row" if short else None
    if (rec := _json(p)) is None:
        return None
    row = con.execute("SELECT body, at FROM reuse_audit WHERE path=?", (p.stem,)).fetchone()
    if row is None:
        return "no reuse_audit row"
    same = json.loads(row[0]) == rec or float(row[1] or 0) > p.stat().st_mtime
    return None if same else "its reuse_audit row differs and is older than the file"


# -- the stores, as lib/state_retire.py's Store fields --------------------------------------------


def _none(_con: sqlite3.Connection, _files: list[Path]) -> None:
    return None  # an unread store imports nothing


def _equal(_con: sqlite3.Connection, _p: Path) -> str | None:
    return None  # ... and has nothing to read back


UNREAD = ("a cache keyed by every input of its value, read by nothing since it moved: the next "
          "run recomputes a missing one")  # fmt: skip


def _shell_dirs() -> list[Path]:
    return [_state() / "self-walk-verdicts", _state() / "tree-lint-verdicts", _cache() / "verdicts"]


STORES: dict[str, dict] = {
    "reuse-records": {
        "files": lambda: _all(*_record_dirs()),
        "load": _records_load,
        "check": _records_check,
        "db": goh_cache_db,
        "drop": "the rows hold every record read back, and they are a cache (a lost one costs a "
        "re-run): an archive would be a second copy",
        "dirs": _record_dirs,
    },
    "reuse-audit": {
        "files": _audit_files,
        "load": _audit_load,
        "check": _audit_check,
        "db": goh_db,
        "dirs": lambda: [_audit_dir()],
    },
    "parse-findings": {
        "files": lambda: _all(_state() / "parse-findings"),
        "load": _none,
        "check": _equal,
        "db": goh_cache_db,
        "drop": UNREAD,
        "unread": True,
        "dirs": lambda: [_state() / "parse-findings"],
    },
    "shell-verdicts": {
        "files": lambda: _all(*_shell_dirs()),
        "load": _none,
        "check": _equal,
        "db": goh_cache_db,
        "drop": UNREAD,
        "unread": True,
        "dirs": _shell_dirs,
    },
    "identity-memo": {
        "files": lambda: _all(_cache() / "identity"),
        "load": _none,
        "check": _equal,
        "db": goh_cache_db,
        "drop": UNREAD,
        "unread": True,
        "dirs": lambda: [_cache() / "identity"],
    },
}
GROUP = ("verdict-caches", tuple(STORES))


def prune_dirs(dirs: list[Path]) -> list[Path]:
    """Each of DIRS removed once its retire left only directories and lock files in it; what was
    left in one that stays (a file no store names)."""
    left: list[Path] = []
    for d in dirs:
        if not d.is_dir():
            continue
        rest = [p for p in d.rglob("*") if p.is_file() and not p.name.endswith(".lock")]
        if rest:
            left += rest
            continue
        for p in sorted(d.rglob("*"), key=lambda q: len(q.parts), reverse=True):
            p.unlink() if p.is_file() else p.rmdir()
        d.rmdir()
    return left


def drop(name: str, store, files: list[Path], say) -> bool:
    """STORE's FILES deleted with no archive (`store.drop` says why), its directories after."""
    for p in files:
        p.unlink()
    left(name, store, say)
    how = "dropped unread" if store.unread else "read back equal, dropped"
    say(f"{name}: {len(files)} files {how}, with no archive ({store.drop})")
    return True


def left(name: str, store, say) -> None:
    """STORE's directories removed once emptied; a file no store names keeps its own, said."""
    for p in prune_dirs(store.dirs()):
        say(f"{name}: kept {p}: no store names it, so its directory stays")
