"""lib/ship_records.py -- goh ship's records in goh.db (GOH.STATEDB S2, goh.db 0003).

    ship_records.py import [--dir DIR]       every record and pin file into goh.db (idempotent)
    ship_records.py roundtrip [--dir DIR]    each imported file read back JSON-equal; 1 on a miss

A `goh ship` result was one JSON file per run under ship/<repo>/, rewritten at each step, and its
install step's pins a `<result>.pins.json` sidecar. Both are rows now: `ships` and `ship_pins`,
keyed by the RESULT path every caller already names a record by (GOH_SHIP_RESULT, `goh ship
--status RESULT`, loopctl's --shipped), with the stem as `run`, the join key. The step logs, the
sweep's record, the suite's profiles and the coverage summary stay files in the ship directory.

THE WINDOW. Until the delete commit of the plan's wave 3 (after the loop's readers move to the
ships_v1 view), `save` also writes the file, and every reader here reads the database first and a
file only for a result with no row (one written before the switch, or by an old HEAD export still
running old code). `import_files` puts those files in: INSERT OR IGNORE on the result, each file's
bytes in the `imports` ledger, so a re-run is harmless and never overwrites a newer row; a record
still running is left to its file until it ends (an older goh's worker rewrites it across the
switch, and a frozen row would outrank it). Nothing here deletes a file. Pinned by
tests/test_ship_records.py.
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
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from collections.abc import Iterator
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import goh_db  # lib/, beside this file: the one door to goh.db

PINS = ".pins.json"
# The siblings that are no record: the sweep's record, the suite's profiles, the coverage summary.
NOT_RECORD = re.compile(r"(\.pins|\.sweep|\.profiles)\.json$|^covsrc\.json$")
LIVE = ("running", "bisecting")  # a record its worker still rewrites
COLUMNS = ("item", "sha", "status", "started", "finished", "version")


def ship_dir() -> Path:
    """Where ship results and their step logs live: GOH_SHIP_DIR, else ~/.local/state/goh/ship."""
    return Path(os.environ.get("GOH_SHIP_DIR") or Path.home() / ".local/state/goh/ship")


def pins_path(result: Path | str) -> Path:
    """The pin record's file beside RESULT (the window's copy of a ship's `ship_pins` row, and
    wf-pin's own record when no ship runs it)."""
    return Path(result).with_suffix(PINS)


def is_record_name(name: str) -> bool:
    return name.endswith(".json") and not NOT_RECORD.search(name) and not name.startswith(".")


def _row(res: dict, result: str, now: float) -> tuple:
    p = Path(result)
    vals = [res.get(c) for c in COLUMNS]
    return (result, p.stem, p.parent.name, *(None if v is None else str(v) for v in vals),
            json.dumps(res), now)  # fmt: skip


def put(res: dict) -> None:
    """Write RES (a whole ship record) as its result's row."""
    with goh_db.refusable(), goh_db.transaction() as con:
        con.execute("INSERT OR REPLACE INTO ships VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    _row(res, str(res["result"]), time.time()))  # fmt: skip


def put_pins(result: Path | str, body: dict) -> None:
    with goh_db.refusable(), goh_db.transaction() as con:
        con.execute("INSERT OR REPLACE INTO ship_pins VALUES (?,?,?,?)",
                    (str(result), Path(result).stem, json.dumps(body), time.time()))  # fmt: skip


@contextlib.contextmanager
def _read() -> Iterator[sqlite3.Connection | None]:
    """goh.db read-only, or None when there is none yet (or a newer goh's file without the
    tables): the files are then the whole truth."""
    with contextlib.ExitStack() as stack:
        try:
            con = stack.enter_context(goh_db.ro())
            con.execute("SELECT 1 FROM ships LIMIT 0")
        except (OSError, sqlite3.OperationalError):
            con = None
        yield con


def _file(p: Path) -> dict | None:
    try:
        got = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return got if isinstance(got, dict) else None


def get(result: Path | str) -> dict | None:
    """RESULT's record: its row, else its file (one from before the switch), else None."""
    with _read() as con:
        row = (
            con and con.execute("SELECT body FROM ships WHERE result=?", (str(result),)).fetchone()
        )
    return json.loads(row[0]) if row else _file(Path(result))


def get_pins(result: Path | str) -> dict | None:
    with _read() as con:
        row = con and con.execute("SELECT body FROM ship_pins WHERE result=?",
                                  (str(result),)).fetchone()  # fmt: skip
    return json.loads(row[0]) if row else _file(pins_path(result))


def in_dir(directory: Path) -> list[tuple[Path, dict]]:
    """Every record of one ship directory (a repo's), by result path: the rows whose result lives
    there, then each record file there with no row."""
    directory = Path(directory)
    out: dict[str, dict] = {}
    with _read() as con:
        lo = str(directory) + os.sep
        for result, body in con.execute("SELECT result, body FROM ships WHERE result >= ? AND "
                                        "result < ?", (lo, lo[:-1] + chr(ord(os.sep) + 1))) if (
                                            con) else ():  # fmt: skip
            if Path(result).parent == directory:
                out[result] = json.loads(body)
    for p in sorted(directory.glob("*.json")):
        if is_record_name(p.name) and str(p) not in out and (rec := _file(p)) is not None:
            out[str(p)] = rec
    return [(Path(k), out[k]) for k in sorted(out)]


def repo_dirs(base: Path | None = None) -> list[Path]:
    """Each repo's ship directory under BASE: the ones on disk and the ones rows name."""
    base = base or ship_dir()
    dirs = {p for p in base.iterdir() if p.is_dir()} if base.is_dir() else set()
    with _read() as con:
        for (result,) in con.execute("SELECT result FROM ships") if con else ():
            if Path(result).parent.parent == base:
                dirs.add(Path(result).parent)
    return sorted(dirs)


def every(base: Path | None = None) -> list[tuple[Path, dict]]:
    """Every record under BASE (the ship directory), each repo's in turn."""
    return [rec for d in repo_dirs(base) for rec in in_dir(d)]


def _files(base: Path) -> Iterator[tuple[str, Path]]:
    for p in sorted(base.glob("*/*.json")):
        if p.name.endswith(PINS):
            yield "ship_pins", p
        elif is_record_name(p.name):
            yield "ships", p


def import_files(base: Path | None = None) -> dict[str, int]:
    """Every record and pin file under BASE not yet imported, INSERT OR IGNORE on its result (a
    row the writer already has is newer than its file). {table: rows added}."""
    base, now, added = base or ship_dir(), time.time(), {"ships": 0, "ship_pins": 0}
    with goh_db.transaction() as con:
        have = {t: {r[0] for r in con.execute(f"SELECT result FROM {t}")} for t in added}
        for table, p in _files(base):
            key = str(p)[: -len(PINS)] + ".json" if table == "ship_pins" else str(p)
            if key in have[table]:
                continue  # its row is in (the writer's, or this file's import): never re-read
            try:
                raw = p.read_bytes()
                body = json.loads(raw)
            except (OSError, ValueError):
                continue
            sha = hashlib.sha256(raw).hexdigest()
            if (
                not isinstance(body, dict)
                or con.execute(
                    "SELECT 1 FROM imports WHERE store=? AND source=? AND sha256=?",
                    (table, str(p), sha),
                ).fetchone()
            ):
                continue
            if table == "ships" and body.get("status") in LIVE:
                continue  # its worker (an older goh's) still rewrites the file: imported once done
            if table == "ships":
                row = _row(body, str(p), p.stat().st_mtime)
                cur = con.execute("INSERT OR IGNORE INTO ships VALUES (?,?,?,?,?,?,?,?,?,?,?)", row)
            else:
                pin = (key, Path(key).stem, raw.decode(), p.stat().st_mtime)
                cur = con.execute("INSERT OR IGNORE INTO ship_pins VALUES (?,?,?,?)", pin)
            added[table] += cur.rowcount
            con.execute("INSERT OR IGNORE INTO imports VALUES (?,?,?,?,?)",
                        (table, str(p), sha, cur.rowcount, now))  # fmt: skip
    return added


def _row_body(table: str, result: str) -> dict | None:
    with _read() as con:
        row = con and con.execute(f"SELECT body FROM {table} WHERE result=?", (result,)).fetchone()
    return json.loads(row[0]) if row else None


def roundtrip(base: Path | None = None) -> list[str]:
    """Each record and pin file under BASE whose ROW does not read back JSON-equal (no file
    fallback here: the row alone answers once the files go)."""
    bad = []
    for table, p in _files(base or ship_dir()):
        disk = _file(p)
        if disk is None:
            continue
        result = str(p)[: -len(PINS)] + ".json" if table == "ship_pins" else str(p)
        got = _row_body(table, result)
        if got != disk:
            bad.append(f"{p}: {'no row' if got is None else 'row differs'}")
    return bad


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="ship_records.py", description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=("import", "roundtrip"))
    ap.add_argument("--dir", type=Path, help="the ship directory (default: GOH_SHIP_DIR's)")
    a = ap.parse_args(argv)
    if a.cmd == "import":
        print(json.dumps(import_files(a.dir)))
        return 0
    bad = roundtrip(a.dir)
    print("\n".join(bad) or "every record and pin file reads back equal")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
