"""lib/hook_ledgers.py -- the hook and cache ledgers in goh.db (GOH.STATEDBS4, goh.db 0005).

    hook_ledgers.py import | export [--to DIR] | watch

WHY. Four stores were files: the spawn ledger agent_guard appends one line to per allowed spawn
and per outcome ($XDG_CACHE_HOME/goh/agent-spawns.jsonl, ~10 MB), lane admission's ledger
($XDG_STATE_HOME/goh/lane-starts.jsonl), and the weekly model-choice watch's record and fault
candidates (model-choice-watch.json, fault-candidates.jsonl, beside the spawn ledger). They are the
tables agent_spawns, lane_starts and fault_candidates and the kv row `model-choice-watch`
(lib/goh_db_schema/0005_hook_ledgers.sql), in the goh.db lib/goh_db.py names (GOH_DB, else
$XDG_STATE_HOME/goh/goh.db).

THE HOT PATH. The two hooks write on every agent spawn in every repo, so a write waits at most
HOOK_BUSY_MS for the file and then lets the spawn through: the row goes to the SPILL file beside
goh.db (one O_APPEND line), the next write drains it in order and counts it (kv BUSY), and the
caller gets a warning to show. An unchanged legacy file costs one stat and one kv read (IMPORTED).

THE FILES BEFORE THE SWITCH (handover plan (b)). A legacy file is imported by the first write after
the switch, in line order (`n`), inside that write's transaction: its byte offset, size, mtime and
the sha256 of the bytes read are kept (kv IMPORTED and the imports ledger), so a line an old HEAD
export appends later is imported by the next write, never twice; a file rewritten under the offset
(lib/state_bounds.py trims lane-starts) gives only the lines no row holds. Until then a reader
reads the file's pending lines under the table's rows. The files stay until wave 3's delete.
Pinned by tests/test_hook_ledgers.py.
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
import re
import sqlite3
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import goh_db  # the lib directory, on the path from the line above
import lock_order

STORE = "hook ledgers"  # the imports ledger's store
SPAWNS, LANES, CANDIDATES = "agent_spawns", "lane_starts", "fault_candidates"
WATCH = "model-choice-watch"  # kv: the weekly watch's record
IMPORTED = "hook-ledgers.imported"  # kv: {source: {offset, size, mtime_ns, sha256}}
BUSY = "hook-ledgers.busy"  # kv: {count, last}: rows a write spilled past HOOK_BUSY_MS
SPILL = "hook-ledgers.spill.jsonl"  # beside goh.db: {table, row} lines a busy write left
HOOK_BUSY_MS = 5000  # a hook's write waits this long for goh.db, then lets the spawn through
ITEM = re.compile(r"^\s*([A-Z][A-Z0-9]*(?:\.[A-Za-z0-9]+)+)\b")  # GOH.STATEDBS4: ...


def cache_dir() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "goh"


def spawns_file() -> Path:
    return cache_dir() / "agent-spawns.jsonl"


def lanes_file() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "goh" / "lane-starts.jsonl"


def candidates_file() -> Path:
    return cache_dir() / "fault-candidates.jsonl"


def watch_file() -> Path:
    return cache_dir() / "model-choice-watch.json"


LEGACY = {SPAWNS: spawns_file, LANES: lanes_file}


def _dump(row: object) -> str:
    return json.dumps(row, sort_keys=True)


def _epoch(at: object) -> float:
    """An ISO stamp (`2026-11-22T21:27:12+00:00`) or a number as epoch seconds; 0 when neither."""
    if isinstance(at, (int, float)):
        return float(at)
    try:
        return float(calendar.timegm(time.strptime(str(at)[:19], "%Y-%m-%dT%H:%M:%S")))
    except ValueError:
        return 0.0


def _lines(data: bytes) -> list[dict]:
    out = []
    for line in data.decode("utf-8", errors="replace").splitlines():
        with contextlib.suppress(ValueError):
            if isinstance(row := json.loads(line), dict):
                out.append(row)
    return out


def _kv(con: sqlite3.Connection, name: str) -> Any:
    row = con.execute("SELECT body FROM kv WHERE name=?", (name,)).fetchone()
    return json.loads(row[0]) if row else None


def _kv_put(con: sqlite3.Connection, name: str, value: object) -> None:
    con.execute("INSERT INTO kv VALUES (?, ?, ?) ON CONFLICT(name) DO UPDATE SET "
                "body=excluded.body, at=excluded.at",
                (name, _dump(value), time.time()))  # fmt: skip


def _insert(con: sqlite3.Connection, table: str, row: dict) -> None:
    if table == SPAWNS:
        m = ITEM.match(str(row.get("description") or "")) if row.get("event") == "spawn" else None
        con.execute("INSERT INTO agent_spawns(at, event, agent, item, body) VALUES (?, ?, ?, ?, ?)",
                    (_epoch(row.get("time")), str(row.get("event") or ""), row.get("agent_id"),
                     m.group(1) if m else None, _dump(row)))  # fmt: skip
    else:
        con.execute(f"INSERT INTO {table}(at, body) VALUES (?, ?)",
                    (_epoch(row.get("at")), _dump(row)))  # fmt: skip


# -- the legacy files ----------------------------------------------------------------------------


def _pending(con: sqlite3.Connection, table: str, p: Path) -> tuple[list[dict], dict | None]:
    """P's lines no import took yet, and the IMPORTED entry reading them would leave (None: P is
    unchanged since its import, or gone)."""
    try:
        st = p.stat()
    except OSError:
        return [], None
    seen = (_kv(con, IMPORTED) or {}).get(str(p)) or {}
    if seen.get("size") == st.st_size and seen.get("mtime_ns") == st.st_mtime_ns:
        return [], None
    data = p.read_bytes()
    end = data.rfind(b"\n") + 1  # a torn last line waits for its newline
    off = int(seen.get("offset") or 0)
    if not off:
        fresh = _lines(data[:end])
    elif off <= end and hashlib.sha256(data[:off]).hexdigest() == seen.get("sha256"):
        fresh = _lines(data[off:end])
    else:  # rewritten under the offset (a trim): the lines no row holds
        have = {b for (b,) in con.execute(f"SELECT body FROM {table}")}
        fresh = [r for r in _lines(data[:end]) if _dump(r) not in have]
    return fresh, {"offset": end, "size": st.st_size, "mtime_ns": st.st_mtime_ns,
                   "sha256": hashlib.sha256(data[:end]).hexdigest()}  # fmt: skip


def _import(con: sqlite3.Connection, table: str, p: Path) -> int:
    """P's pending lines into TABLE, in line order; the rows read."""
    fresh, entry = _pending(con, table, p)
    if entry is None:
        return 0
    for row in fresh:
        _insert(con, table, row)
    _kv_put(con, IMPORTED, (_kv(con, IMPORTED) or {}) | {str(p): entry})
    con.execute("INSERT OR IGNORE INTO imports VALUES (?, ?, ?, ?, ?)",
                (STORE, str(p), entry["sha256"], len(fresh), time.time()))  # fmt: skip
    return len(fresh)


# -- the spill: a write that waited HOOK_BUSY_MS ------------------------------------------------


def spill_file() -> Path:
    return goh_db.path().with_name(SPILL)


def _spill(table: str, row: dict) -> None:
    p = spill_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:  # one write: O_APPEND keeps the line whole beside another spill
        os.write(fd, (json.dumps({"table": table, "row": row}, sort_keys=True) + "\n").encode())
    finally:
        os.close(fd)


def _spilled(p: Path) -> list[tuple[str, dict]]:
    with contextlib.suppress(OSError):
        return [(r["table"], r["row"]) for r in _lines(p.read_bytes())
                if r.get("table") in (SPAWNS, LANES)
                and isinstance(r.get("row"), dict)]  # fmt: skip
    return []


def _drain(con: sqlite3.Connection, claimed: list[Path]) -> None:
    """The spill's rows into their tables, before this write's row, counted in kv BUSY. The file
    is claimed by a rename (another spill starts a new one); the caller removes it after COMMIT."""
    p = spill_file()
    if not p.exists():
        return
    mine = p.with_name(f"{SPILL}.{os.getpid()}.{time.monotonic_ns()}")
    try:
        os.rename(p, mine)
    except OSError:
        return  # another write drained it
    claimed.append(mine)
    rows = _spilled(mine)
    for table, row in rows:
        _insert(con, table, row)
    busy = _kv(con, BUSY) or {"count": 0}
    _kv_put(con, BUSY, {"count": int(busy.get("count") or 0) + len(rows), "last": time.time()})


@contextlib.contextmanager
def _hook_busy() -> Iterator[None]:
    """goh.db's busy timeout at HOOK_BUSY_MS for this write (a caller's lower setting stands)."""
    key, old = goh_db.SEAM.busy_env, os.environ.get(goh_db.SEAM.busy_env)
    if old is None or not old.isdigit() or int(old) > HOOK_BUSY_MS:
        os.environ[key] = str(HOOK_BUSY_MS)
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old


def append(table: str, row: dict) -> str | None:
    """One hook ledger row (SPAWNS or LANES), after its legacy file's pending lines and the spill's;
    the warning to show when goh.db could not take it (the row is spilled, the spawn goes on)."""
    claimed: list[Path] = []
    try:
        with _hook_busy(), goh_db.transaction() as con:
            _import(con, table, LEGACY[table]())
            _drain(con, claimed)
            _insert(con, table, row)
    except (sqlite3.Error, OSError, lock_order.OrderError) as e:  # an ancestor holding goh.db: open
        for mine in claimed:  # rolled back: its rows go back to the spill
            for t, r in _spilled(mine):
                _spill(t, r)
            mine.unlink(missing_ok=True)
        try:
            _spill(table, row)
        except OSError as e2:
            return f"hook_ledgers: the {table} row was not written ({e}; spill: {e2})."
        return f"hook_ledgers: goh.db did not take the {table} row ({e}); spilled, counted."
    for mine in claimed:
        mine.unlink(missing_ok=True)
    return None


# -- readers -------------------------------------------------------------------------------------


@contextlib.contextmanager
def _read(db: Path | None = None) -> Iterator[sqlite3.Connection | None]:
    """goh.db (DB when named) read-only, or None when there is none (or it predates 0005)."""
    with contextlib.ExitStack() as stack:
        try:
            con = stack.enter_context(goh_db.ro(db))
            con.execute("SELECT 1 FROM agent_spawns LIMIT 1")
        except (OSError, sqlite3.Error):
            con = None
        yield con


def rows(table: str, db: Path | None = None) -> list[dict]:
    """TABLE's rows (SPAWNS or LANES) in the order written: its legacy file's lines no import took
    yet (before the table's when the file was never imported, after them when appended since), the
    table's, then the spill's. DB: that file's rows alone (a run's own goh.db, the live proof's)."""
    if db is not None:
        with _read(db) as con:
            q = f"SELECT body FROM {table} ORDER BY n"
            return [] if con is None else [json.loads(b) for (b,) in con.execute(q)]
    p = LEGACY[table]()
    with _read() as con:
        if con is None:
            try:
                base = _lines(p.read_bytes())
            except OSError:
                base = []
            got: list[dict] = []
        else:
            base = _pending(con, table, p)[0]
            got = [json.loads(b) for (b,) in con.execute(f"SELECT body FROM {table} ORDER BY n")]
            if (_kv(con, IMPORTED) or {}).get(str(p)):  # appended since its import: after it
                base, got = [], got + base
    return base + got + [r for t, r in _spilled(spill_file()) if t == table]


def spawns(db: Path | None = None) -> list[dict]:
    return rows(SPAWNS, db)


def lanes() -> list[dict]:
    return rows(LANES)


def busy() -> dict:
    """{count, last}: the hook rows a busy goh.db spilled (drained since), else {count: 0}."""
    with _read() as con:
        return (_kv(con, BUSY) if con is not None else None) or {"count": 0}


# -- the weekly watch's record and fault candidates ------------------------------------------------


def _file_json(p: Path) -> object:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def watch() -> object:
    """The weekly watch's last record: its kv row, else the file it was (not yet imported)."""
    with _read() as con:
        got = _kv(con, WATCH) if con is not None else None
    return got if got is not None else _file_json(watch_file())


def _import_docs(con: sqlite3.Connection) -> int:
    """The watch's record and the candidates file, once each (a row already there wins)."""
    n = 0
    if _kv(con, WATCH) is None and (doc := _file_json(watch_file())) is not None:
        con.execute("INSERT OR IGNORE INTO kv VALUES (?, ?, ?)",
                    (WATCH, _dump(doc), watch_file().stat().st_mtime))  # fmt: skip
        n += 1
    p = candidates_file()
    if not con.execute("SELECT 1 FROM fault_candidates LIMIT 1").fetchone() and p.is_file():
        at = p.stat().st_mtime
        for row in _lines(p.read_bytes()):
            con.execute("INSERT INTO fault_candidates(at, body) VALUES (?, ?)", (at, _dump(row)))
            n += 1
    return n


def record_watch(state: dict, candidates: list[dict] | None) -> None:
    """One weekly run: its record, and (when the SPEND section ran) its fault candidates."""
    with goh_db.transaction() as con:
        _import_docs(con)
        _kv_put(con, WATCH, state)
        at = time.time()
        for row in candidates if candidates is not None else []:
            con.execute("INSERT INTO fault_candidates(at, body) VALUES (?, ?)", (at, _dump(row)))


def candidates() -> list[dict]:
    """The newest run's fault candidates (the file's, while not yet imported)."""
    with _read() as con:
        if con is not None and con.execute("SELECT 1 FROM fault_candidates LIMIT 1").fetchone():
            return [json.loads(b) for (b,) in con.execute(
                "SELECT body FROM fault_candidates WHERE at=(SELECT MAX(at) FROM fault_candidates)"
                " ORDER BY n")]  # fmt: skip
    try:
        return _lines(candidates_file().read_bytes())
    except OSError:
        return []


# -- the import and its round trip ---------------------------------------------------------------


def import_all() -> dict[str, int]:
    """Every hook ledger's file into goh.db (`import`): rows read per store; re-runs add nothing."""
    with goh_db.transaction() as con:
        out = {t: _import(con, t, f()) for t, f in LEGACY.items()}
        out["docs"] = _import_docs(con)
    return out


def export() -> dict[str, object]:
    """Every store as its readers see it, keyed by the file it was."""
    return {spawns_file().name: spawns(), lanes_file().name: lanes(),
            candidates_file().name: candidates(), watch_file().name: watch()}  # fmt: skip


def main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="hook_ledgers.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=("import", "export", "watch"))
    ap.add_argument("--to", type=Path, help="export: the directory each store is written to")
    a = ap.parse_args(argv)
    if a.cmd == "import":
        print(json.dumps(import_all(), sort_keys=True))
        return 0
    if a.cmd == "watch":  # the weekly record, for a reader that used to read its file
        print(json.dumps(watch(), sort_keys=True))
        return 0
    got = export()
    if a.to is None:
        print(_dump(got))
        return 0
    a.to.mkdir(parents=True, exist_ok=True)
    for name, value in got.items():
        text = (_dump(value) + "\n" if not isinstance(value, list)
                else "".join(_dump(r) + "\n" for r in value))  # fmt: skip
        (a.to / name).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
