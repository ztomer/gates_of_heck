"""lib/state_retire.py -- `goh state retire [STORE...]`: the files of a store that moved into
goh.db, imported, read back equal, archived and deleted (the database plan's wave 3, GOH.STATEDB).

    goh state retire [STORE...] [--dry-run]   each STORE (default: every one) retired
    goh state stray                           every retired store's file still on disk
    goh state test-ships [--delete]           the ship rows a test left (GOH.FAKESHIPROWS)

WHY. Each store was a set of files under goh's state or cache directory; its readers and writers
now go through goh.db alone (lib/goh_db.py), so a file left behind is read by nothing. This is
the one module that still names those files (tests/_state_seam_scan.py lists it in the seam):
it imports what a file holds that no row does (INSERT OR IGNORE, as each store's lane did), then
compares every record of every file with what goh.db reads back. Only when every one is equal
(or its row is newer than its file: the store was written since) are the files copied under
`<state>/retired/<YYYY-MM-DD>/<store>/{state,cache}/<path>`, each copy byte-compared, and the
originals deleted. One unequal record keeps every file of its store, said, and exits 1.

A file a store must keep for now (a ship whose worker still runs) is named and left; a ship
result that says running while its worker is gone is first settled as lib/ship_status.py settles
one (failed at `abandoned`), so no record is left behind as running forever.
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

import argparse
import contextlib
import filecmp
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import goh_db  # lib/, on the path from the line above
import ship_records  # the ship rows a test left (GOH.FAKESHIPROWS)
import state_retire_lands as lands  # the land records' half (GOH.STATEDB S1)
import state_retire_ships  # the ship records' half (GOH.STATEDB S2), beside this file
import state_retire_verdicts  # GOH.VERDICTDB's stores, beside this one in the seam


def state_dir() -> Path:
    """$XDG_STATE_HOME/goh, else ~/.local/state/goh."""
    base = os.environ.get("XDG_STATE_HOME", "")
    return (Path(base) if base.startswith("/") else Path.home() / ".local" / "state") / "goh"


def cache_dir() -> Path:
    """$XDG_CACHE_HOME/goh, else ~/.cache/goh."""
    base = os.environ.get("XDG_CACHE_HOME", "")
    return (Path(base) if base.startswith("/") else Path.home() / ".cache") / "goh"


@dataclass(frozen=True)
class Store:
    """A moved store: its files on disk, how they go in, and how each is judged read back."""

    files: Callable[[], list[Path]]  # the store's files still on disk
    load: Callable[[sqlite3.Connection, list[Path]], None]  # their records in (INSERT OR IGNORE)
    check: Callable[[sqlite3.Connection, Path], str | None]  # why P does not read back equal
    keep: Callable[[Path], str | None] = lambda p: None  # why P must stay for now
    db: ModuleType = goh_db  # the seam its rows live in (goh_db, or goh_cache_db for a cache)
    drop: str = ""  # why its files go with no archive (a cache its rows rebuild); "": archived
    dirs: Callable[[], list[Path]] = list  # its directories, removed once emptied
    unread: bool = False  # its files go unread: a cache whose readers left it (`drop` says why)


def _dump(row: object) -> str:
    return json.dumps(row, sort_keys=True)


def _json(p: Path) -> object:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _lines(p: Path) -> list[dict]:
    """P's whole lines that are JSON objects (a torn last line and a bad line are no record)."""
    try:
        data = p.read_bytes()
    except OSError:
        return []
    out = []
    for line in data[: data.rfind(b"\n") + 1].decode("utf-8", errors="replace").splitlines():
        with contextlib.suppress(ValueError):
            if isinstance(row := json.loads(line), dict):
                out.append(row)
    return out


def _ledger(con: sqlite3.Connection, store: str, p: Path, rows: int) -> None:
    sha = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else ""
    con.execute("INSERT OR IGNORE INTO imports VALUES (?, ?, ?, ?, ?)",
                (store, str(p), sha, rows, time.time()))  # fmt: skip


def _subset(con: sqlite3.Connection, table: str, p: Path, body=_dump) -> str | None:
    """Every line of P is a row of TABLE (as many times as P holds it), else why not."""
    have = Counter(b for (b,) in con.execute(f"SELECT body FROM {table}"))
    want = Counter(body(r) for r in _lines(p))
    short = sum((want - have).values())
    return f"{short} of {sum(want.values())} lines have no {table} row" if short else None


def _kv_equal(con: sqlite3.Connection, name: str, p: Path) -> str | None:
    """P's whole document is kv NAME's body, or NAME was written after P was (it moved on)."""
    row = con.execute("SELECT body, at FROM kv WHERE name=?", (name,)).fetchone()
    if row is None:
        return f"no kv row {name}"
    if json.loads(row[0]) == _json(p) or float(row[1] or 0) > p.stat().st_mtime:
        return None
    return f"kv {name} differs and is older than the file"


# -- hook ledgers (GOH.STATEDBS4, goh.db 0005) ----------------------------------------------------

HOOK_FILES = {"agent_spawns": ("cache", "agent-spawns.jsonl"),
              "lane_starts": ("state", "lane-starts.jsonl"),
              "fault_candidates": ("cache", "fault-candidates.jsonl"),
              "model-choice-watch": ("cache", "model-choice-watch.json")}  # fmt: skip


def _root(where: str) -> Path:
    return state_dir() if where == "state" else cache_dir()


def _hook_file(table: str) -> Path:
    where, name = HOOK_FILES[table]
    return _root(where) / name


def _hook_table(p: Path) -> str:
    return next(t for t in HOOK_FILES if _hook_file(t) == p)


def _hook_load(con: sqlite3.Connection, files: list[Path]) -> None:
    import hook_ledgers

    for p in files:
        table = _hook_table(p)
        if table == "model-choice-watch":
            if (doc := _json(p)) is not None:
                con.execute("INSERT OR IGNORE INTO kv VALUES (?, ?, ?)",
                            (table, _dump(doc), p.stat().st_mtime))  # fmt: skip
            continue
        have, added = Counter(b for (b,) in con.execute(f"SELECT body FROM {table}")), 0
        for r in _lines(p):  # in line order; a line the table already holds is taken once
            if have[_dump(r)] > 0:
                have[_dump(r)] -= 1
            elif table == "fault_candidates":
                con.execute("INSERT INTO fault_candidates(at, body) VALUES (?, ?)",
                            (p.stat().st_mtime, _dump(r)))  # fmt: skip
                added += 1
            else:
                hook_ledgers.insert(con, table, r)
                added += 1
        _ledger(con, "hook ledgers", p, added)


def _hook_check(con: sqlite3.Connection, p: Path) -> str | None:
    table = _hook_table(p)
    if table == "model-choice-watch":
        return _kv_equal(con, table, p)
    return _subset(con, table, p)


# -- small stores (GOH.STATEDBS3, goh.db 0004) ---------------------------------------------------

SMALL_KV = ("estate_audit", "pin_push", "pin_sweeps", "cache-evict", "wf_pin.pins")


def _small_files() -> list[Path]:
    st = state_dir()
    flakes = sorted((st / "flakes" / "by-test").glob("*.jsonl"))
    return _existing(*(st / f"{n}.json" for n in SMALL_KV), st / "commit-gated.jsonl",
                     st / "flakes" / "register.jsonl", *flakes)  # fmt: skip


def _flake_tests() -> dict[str, str]:
    """{record id stem: test} from the register (a by-test file is named by its test's stem)."""
    recs = _lines(state_dir() / "flakes" / "register.jsonl")
    return {r["id"].partition(".")[0]: r["test"] for r in recs
            if isinstance(r.get("id"), str) and "test" in r}  # fmt: skip


def _small_rows(p: Path) -> tuple[str, list[tuple[float, str, dict]]]:
    """P's table and its rows as (epoch, key, row): the tree for commit_gated, the test for flakes
    (a by-test file's filings only: its id lines index the register's records)."""
    if p.name == "commit-gated.jsonl":
        return "commit_gated", [(_epoch(r.get("at")), str(r.get("tree") or ""), r)
                                for r in _lines(p) if r.get("tree")]  # fmt: skip
    if p.name == "register.jsonl":
        return "flakes", [(float(r.get("epoch") or 0), str(r["test"]), r)
                          for r in _lines(p) if "test" in r]  # fmt: skip
    test = _flake_tests().get(p.stem, "")
    return "flakes", [(float(r.get("epoch") or 0), test, r)
                      for r in _lines(p) if "filed" in r and test]  # fmt: skip


def _epoch(at: object) -> float:
    if isinstance(at, (int, float)):
        return float(at)
    import calendar

    try:
        return float(calendar.timegm(time.strptime(str(at)[:19], "%Y-%m-%dT%H:%M:%S")))
    except ValueError:
        return 0.0


def _small_key(table: str) -> str:
    return "tree" if table == "commit_gated" else "test"


def _small_load(con: sqlite3.Connection, files: list[Path]) -> None:
    for p in files:
        if p.suffix == ".json":
            if (doc := _json(p)) is not None:
                name = p.name.removesuffix(".json")
                con.execute("INSERT OR IGNORE INTO kv VALUES (?, ?, ?)",
                            (name, _dump(doc), p.stat().st_mtime))  # fmt: skip
            continue
        table, rows = _small_rows(p)
        key, at = _small_key(table), "at" if table == "commit_gated" else "epoch"
        n = 0
        for epoch, k, r in sorted(rows, key=lambda t: t[0]):
            if not con.execute(f"SELECT 1 FROM {table} WHERE {key}=? AND body=?",
                               (k, _dump(r))).fetchone():  # fmt: skip
                con.execute(f"INSERT INTO {table}({at}, {key}, body) VALUES (?, ?, ?)",
                            (epoch, k, _dump(r)))  # fmt: skip
                n += 1
        _ledger(con, "small stores", p, n)


def _small_check(con: sqlite3.Connection, p: Path) -> str | None:
    if p.suffix == ".json":
        return _kv_equal(con, p.name.removesuffix(".json"), p)
    table, rows = _small_rows(p)
    key = _small_key(table)
    miss = [r for _, k, r in rows if not con.execute(
        f"SELECT 1 FROM {table} WHERE {key}=? AND body=?", (k, _dump(r))).fetchone()]  # fmt: skip
    if p.parent.name == "by-test":  # an id line names a record the register holds
        ids = {json.loads(b).get("id") for (b,) in con.execute("SELECT body FROM flakes")}
        miss += [r for r in _lines(p) if "id" in r and "filed" not in r and r["id"] not in ids]
    return f"{len(miss)} of its lines have no {table} row" if miss else None


# -- durations (GOH.DURATIONSDB, goh.db 0006) -----------------------------------------------------

DUR_FILES = {"dur": "test-durations.json", "pole": "test-durations.poles.json",
             "pole_cpu": "test-durations.poles-cpu.json", "remote_rates": "remote-rates.json",
             "linux_unsafe": "linux-unsafe.json"}  # fmt: skip


def _dur_what(p: Path) -> str:
    return next(w for w, n in DUR_FILES.items() if n == p.name)


def _numbers(data: object) -> dict[str, float]:
    if not isinstance(data, dict):
        return {}
    return {k: float(v) for k, v in data.items() if isinstance(v, (int, float))}


def _dur_load(con: sqlite3.Connection, files: list[Path]) -> None:
    """As lib/durations.py's first write once took them: a table file's entries as readings at
    its mtime under the run `import:<sha12>`, a kv file's value unless the key has one; once per
    file's bytes (the imports ledger, source the bare file name)."""
    for p in files:
        what, raw = _dur_what(p), p.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        if con.execute("SELECT 1 FROM imports WHERE store='durations' AND source=? AND sha256=?",
                       (p.name, sha)).fetchone():  # fmt: skip
            continue
        data, at = _json(p), p.stat().st_mtime
        if what in ("dur", "pole", "pole_cpu"):
            got = _numbers(data)
            run = f"import:{sha[:12]}"
            con.executemany("INSERT INTO readings(run, node, kind, seconds, loaded, at) "
                            "VALUES (?, ?, ?, ?, 0, ?)",
                            [(run, k, what, v, at) for k, v in got.items()])  # fmt: skip
            n = len(got)
        else:
            n = 0 if data is None else con.execute(
                "INSERT OR IGNORE INTO duration_kv VALUES (?, ?, ?)",
                (what, json.dumps(data), at)).rowcount  # fmt: skip
        con.execute("INSERT OR IGNORE INTO imports VALUES (?, ?, ?, ?, ?)",
                    ("durations", p.name, sha, n, time.time()))  # fmt: skip


def _dur_check(con: sqlite3.Connection, p: Path) -> str | None:
    """A kv file's value is its row's, or the row is newer; a table file's every reading is its
    import's row, or its node has a newer reading or a prune (the table moved on: KEEP)."""
    what, at = _dur_what(p), p.stat().st_mtime
    if what not in ("dur", "pole", "pole_cpu"):
        row = con.execute("SELECT value, at FROM duration_kv WHERE key=?", (what,)).fetchone()
        if row is None:
            return f"no duration_kv row {what}"
        same = json.loads(row[0]) == _json(p) or float(row[1] or 0) > at
        return None if same else f"duration_kv {what} differs and is older than the file"
    run = f"import:{hashlib.sha256(p.read_bytes()).hexdigest()[:12]}"
    miss = 0
    for node, v in _numbers(_json(p)).items():
        got = con.execute("SELECT seconds FROM readings WHERE kind=? AND node=? AND run=?",
                          (what, node, run)).fetchone()  # fmt: skip
        if got is not None and abs(float(got[0]) - v) < 1e-9:
            continue
        if con.execute("SELECT 1 FROM readings WHERE kind=? AND node=? AND at>=? UNION ALL "
                       "SELECT 1 FROM pruned WHERE kind=? AND node=?",
                       (what, node, at, what, node)).fetchone():  # fmt: skip
            continue
        miss += 1
    return f"{miss} readings read back from no row" if miss else None


# -- the stores -----------------------------------------------------------------------------------


def _existing(*paths: Path) -> list[Path]:
    return [p for p in paths if p.is_file()]


STORES: dict[str, Store] = {
    "lands": Store(lands.files, lands.load, lands.check, lands.keep),
    "ships": Store(
        files=state_retire_ships.files,
        load=state_retire_ships.load,
        check=state_retire_ships.check,
        keep=state_retire_ships.keep,
    ),
    "durations": Store(
        files=lambda: _existing(*(state_dir() / n for n in DUR_FILES.values())),
        load=_dur_load,
        check=_dur_check,
    ),
    "small-stores": Store(files=_small_files, load=_small_load, check=_small_check),
    "hook-ledgers": Store(
        files=lambda: _existing(*(_hook_file(t) for t in HOOK_FILES)),
        load=_hook_load,
        check=_hook_check,
    ),
    **{n: Store(**kw) for n, kw in state_retire_verdicts.STORES.items()},
    "landq": Store(**__import__("state_retire_landq").STORE),  # GOH.LANDQDB, beside this one
}
GROUPS = dict([state_retire_verdicts.GROUP])  # a name for several stores


def archive_dir(day: str | None = None) -> Path:
    return state_dir() / "retired" / (day or time.strftime("%Y-%m-%d"))


def _archived(p: Path, to: Path) -> Path:
    for where, root in (
        ("state", state_dir()),
        ("cache", cache_dir()),
        ("ship", state_retire_ships.base()),
    ):
        with contextlib.suppress(ValueError):
            return to / where / p.relative_to(root)
    return to / "elsewhere" / p.name


def retire(name: str, dry: bool = False, day: str | None = None, say=print) -> bool:
    """Store NAME's files imported, read back, archived and deleted; False when one stays unequal
    (then nothing of the store is deleted)."""
    store = STORES[name]
    kept = {p: why for p in store.files() if (why := store.keep(p))}
    for p, why in kept.items():
        say(f"{name}: kept {p}: {why}")
    files = [p for p in store.files() if p not in kept]
    if not files:
        state_retire_verdicts.prune_dirs(store.dirs())
        say(f"{name}: no file left to retire")
        return True
    with store.db.transaction() as con:
        store.load(con, files)
    with store.db.ro() as con:
        bad = [f"{p}: {why}" for p in files if (why := store.check(con, p))]
    if bad:
        for line in bad:
            say(f"{name}: NOT retired: {line}")
        return False
    if dry:
        how = "to drop unread" if store.unread else "read back equal"
        say(f"{name}: {len(files)} files {how} (dry run: none moved)")
        return True
    if store.drop:
        return state_retire_verdicts.drop(name, store, files, say)
    to = archive_dir(day) / name
    for p in files:
        dest = _archived(p, to)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dest)
        if not filecmp.cmp(p, dest, shallow=False):
            say(f"{name}: NOT retired: the copy of {p} differs")
            return False
    for p in files:
        p.unlink()
    state_retire_verdicts.left(name, store, say)
    say(f"{name}: {len(files)} files read back equal, archived under {to} and deleted")
    return True


def stray() -> list[Path]:
    """Every retired store's file still on disk (read by nothing since its delete commit)."""
    return [p for s in STORES.values() for p in s.files()]


def test_ships(delete: bool) -> int:
    """`goh state test-ships [--delete]`: the ship rows a test left in goh.db (GOH.FAKESHIPROWS),
    listed; with DELETE removed, through the one door, never by hand."""
    rows = ship_records.test_rows()
    for r in rows:
        print(f"test ship row: {r}")
    if delete:
        print(f"{ship_records.drop_test_rows()} test ship rows deleted")
    else:
        print(f"{len(rows)} test ship rows (dry run: none deleted; --delete removes them)")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="goh state", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("retire", help="archive and delete the moved stores' files")
    r.add_argument("store", nargs="*", help=f"one of {', '.join([*STORES, *GROUPS])} (default: "
                   "every one)")  # fmt: skip
    r.add_argument("--dry-run", action="store_true")
    sub.add_parser("stray", help="every retired store's file still on disk")
    t = sub.add_parser("test-ships", help="the ship rows a test left (result under a temp root)")
    t.add_argument("--delete", action="store_true", help="delete them (default: list only)")
    a = ap.parse_args(argv)
    if a.cmd == "test-ships":
        return test_ships(a.delete)
    a.store = [s for n in getattr(a, "store", []) for s in GROUPS.get(n, (n,))]
    if a.cmd == "retire" and (unknown := set(a.store) - set(STORES)):
        ap.error(f"no such store: {', '.join(sorted(unknown))} (stores: {', '.join(STORES)})")
    if a.cmd == "stray":
        found = stray()
        for p in found:
            print(f"stray: {p} (its store lives in goh.db; `goh state retire` archives it)")
        return 1 if found else 0
    ok = [retire(n, a.dry_run) for n in (a.store or list(STORES))]
    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
