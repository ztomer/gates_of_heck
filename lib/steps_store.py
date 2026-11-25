"""lib/steps_store.py -- a run's gate steps as rows of goh-cache.db's `steps` (GOH.STEPTIMES; goh
cache 0004), the one writer and the one reader of the table.

    steps_store.py ingest SPOOL...   each spool's rows into the table, then the spool gone
    steps_store.py sweep             every land's and ship's spool nobody writes any more
    steps_store.py report RUN [PART] a run's step table (lib/step_timings.py report)

    named(spool)     (run, part): the land or ship it belongs to, and which gate or step of it
    rows(spool)      its steps as lib/step_timings.py's rows: from the spool while there is one
                     (a run still writing), else from the table; None when neither has it
    ingest(spool)    True when the table has them

THE SPOOL. Every step of a gate appends a line to $GOH_TIMINGS: hundreds of processes, two
languages, some on another host. That file is the run's while it runs, and its rows are the table's
when it ends: one transaction replaces (run, part), the spool is removed after the commit. A kill in
between, a busy or a newer file leaves the spool: read as before, ingested by the next sweep
(lib/state_bounds.py), which adds nothing twice. No goh-cache.db: the spool stays.

ONE TIMESTAMP. `at` is the step's end, epoch seconds: the line's `t`; a line without one (the native
tier's) takes the nearest line's before it, else the first after it, else the file's own time.

THE SMALL ONES. Three rows in four were a native check under 250 ms that passed. Those of one parent
are one row (FOLDED: their milliseconds summed, `calls` of them, the first one's `seq`), but one a
step nests under, so what lib/step_attribution.py sums under a parent is the same sum.
Pinned by tests/test_steps_store.py.

GOH.STEPTIMES: a run's gate steps in goh-cache.db's `steps`, from the spool (`$GOH_TIMINGS`) its
processes appended to: `ingest` when a land or a ship ends (`tools/land.sh`, `ship_status.py`,
`pin_sweep.py`), `sweep` for the spools a killed run left, `rows` for the readers.
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
import math
import os
import re
import sqlite3
import sys
import time
from collections.abc import Iterator
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import goh_cache_db
import state_paths
import step_timings

SUFFIX = ".timings.jsonl"
SHIP = re.compile(r"(\d{8}T\d{6}Z-[0-9a-f]+|v\d+\.\d+\.\d+)\.(.+)")
LAND = re.compile(r"(\d{10,}-.+?)(?:\.(gate\d+))?")
FOLDED = "native steps under 250 ms"
FOLD_MS = 250
NUMBERS = ("cpu_ms", "load1", "wait_s")
TEXTS = ("kind", "cache", "db")
COLUMNS = ("run", "part", "seq", "at", "label", "tier", "parent", "dir", "ms", "rc", "control",
           "calls", "pid", *NUMBERS, *TEXTS)  # fmt: skip
INSERT = (f"INSERT INTO steps({', '.join(COLUMNS)}) "
          f"VALUES ({', '.join(':' + c for c in COLUMNS)})")  # fmt: skip
# How long after its last line a spool is nobody's: a land's is moved beside its log when the land
# is over, a ship step's is written where it is, and a step may be long.
LAND_IDLE_S, SHIP_IDLE_S = 3600, 6 * 3600
FAULTS = (sqlite3.Error, OSError, RuntimeError)


def named(spool: Path | str) -> tuple[str, str]:
    base = Path(spool).name.removesuffix(SUFFIX)
    if m := SHIP.fullmatch(base) or LAND.fullmatch(base):
        return m.group(1), m.group(2) or ""
    return str(state_paths.encode(str(spool).removesuffix(SUFFIX))), ""  # no run's name: its path


def _number(v: object) -> float | None:
    ok = isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    return float(v) if ok else None  # type: ignore[arg-type]


def _ends(lines: list[dict], mtime: float) -> list[float]:
    """Each line's end: its `t`, else the last one before it, else the first after, else MTIME."""
    ends = [_number(r.get("t")) for r in lines]
    last = next((t for t in ends if t is not None), mtime)
    return [last := last if t is None else t for t in ends]


def _folds(lines: list[dict]) -> dict[int, list[int]]:
    """The lines that go in a FOLDED row, by the first of them: one such row a parent."""
    parents = {r.get("parent") for r in lines}
    by: dict[str, list[int]] = {}
    for i, r in enumerate(lines):
        if (r.get("tier") == "native" and r.get("rc") == 0 and not r.get("control")
                and (_number(r.get("ms")) or 0.0) < FOLD_MS
                and r["label"] not in parents):  # fmt: skip
            by.setdefault(str(r.get("parent") or ""), []).append(i)
    return {found[0]: found for found in by.values() if len(found) > 1}


def _record(r: dict, seq: int, at: float) -> dict:
    rc, pid, cwd = r.get("rc"), r.get("pid"), r.get("cwd")
    return {
        "seq": seq, "at": at, "label": str(r["label"]), "tier": str(r.get("tier") or ""),
        "parent": str(r.get("parent") or "") or None,
        "dir": state_paths.encode(str(cwd)) if cwd else None,
        "ms": max(0.0, _number(r.get("ms")) or 0.0), "rc": rc if isinstance(rc, int) else -1,
        "control": int(r.get("control") is True), "calls": 1,
        "pid": pid if isinstance(pid, int) else None,
        **{c: _number(r.get(c)) for c in NUMBERS},
        **{c: str(r[c]) if r.get(c) else None for c in TEXTS}}  # fmt: skip


def records(lines: list[dict], run: str, part: str, mtime: float, fold: bool = True) -> list[dict]:
    """LINES (a spool's) as rows of the table, in order."""
    lines = [r for r in lines if isinstance(r, dict) and r.get("label")]
    ends, folds = _ends(lines, mtime), _folds(lines) if fold else {}
    inside = {i for found in folds.values() for i in found}
    out = []
    for i, r in enumerate(lines):
        if i in inside and i not in folds:
            continue
        rec = _record(r, i, ends[i]) | {"run": run, "part": part}
        if i in folds:
            each = [_record(lines[j], j, ends[j]) for j in folds[i]]
            cpu = [e["cpu_ms"] for e in each if e["cpu_ms"] is not None]
            rec |= {"label": FOLDED, "at": max(e["at"] for e in each), "calls": len(each),
                    "ms": round(sum(e["ms"] for e in each), 1), "pid": None, "wait_s": None,
                    "cpu_ms": round(sum(cpu), 1) if cpu else None}  # fmt: skip
        out.append(rec)
    return out


def _first(con: sqlite3.Connection, run: str, part: str) -> tuple | None:
    return con.execute("SELECT seq, at, label, ms FROM steps WHERE run = ? AND part = ? "
                       "ORDER BY seq LIMIT 1", (run, part)).fetchone()  # fmt: skip


def put(con: sqlite3.Connection, run: str, part: str, lines: list[dict], mtime: float) -> None:
    """LINES as (RUN, PART)'s rows. The same spool again replaces them; lines of another (a child
    that outlived its run wrote to the name again) come after the ones there."""
    recs = records(lines, run, part, mtime)
    was = _first(con, run, part)
    if recs and was is not None and was != tuple(recs[0][c] for c in ("seq", "at", "label", "ms")):
        top = con.execute("SELECT max(seq) FROM steps WHERE run = ? AND part = ?",
                          (run, part)).fetchone()[0]  # fmt: skip
        recs = records(lines, run, part, mtime, fold=False)
        recs = [{**r, "seq": top + 1 + r["seq"]} for r in recs]
    elif recs:
        con.execute("DELETE FROM steps WHERE run = ? AND part = ?", (run, part))
    con.executemany(INSERT, recs)


def forget(spool: Path | str) -> None:
    """SPOOL's rows gone from the table: its step is about to be profiled afresh."""
    if goh_cache_db.path().is_file():
        run, part = named(spool)
        with goh_cache_db.missable("goh-cache.db steps"):
            with goh_cache_db.ro() as con:
                was = _first(con, run, part)
            if was is not None:
                with goh_cache_db.transaction() as con:
                    con.execute("DELETE FROM steps WHERE run = ? AND part = ?", (run, part))


def take(con: sqlite3.Connection, run: str, part: str) -> list[dict]:
    """(RUN, PART)'s steps as lib/step_timings.py's rows, in the order they were written."""
    out = []
    cols = [c for c in COLUMNS if c not in ("run", "part", "seq")]
    for rec in con.execute(f"SELECT {', '.join(cols)} FROM steps WHERE run = ? AND part = ? "
                           "ORDER BY seq", (run, part)):  # fmt: skip
        r = dict(zip(cols, rec, strict=True))
        r |= {"t": r.pop("at"), "cwd": state_paths.decode(r.pop("dir") or ""),
              "parent": r["parent"] or ""}  # fmt: skip
        r["control"] = bool(r["control"]) or None
        r["calls"] = r["calls"] if r["calls"] > 1 else None
        out.append({k: v for k, v in r.items() if v is not None})
    return out


def rows(spool: Path | str) -> list[dict] | None:
    lines: list[dict] | None
    try:
        lines = step_timings.load(str(spool))
    except OSError:  # none, or one gone into the table just now: the table has it
        lines = None
    if not goh_cache_db.path().is_file():
        return lines
    run, part = named(spool)
    kept: list[dict] = []
    with goh_cache_db.missable("goh-cache.db steps"), goh_cache_db.ro() as con:
        kept = take(con, run, part)
        first = records(lines, run, part, 0.0)[:1] if lines else []
        if first and _first(con, run, part) == tuple(
                first[0][c] for c in ("seq", "at", "label", "ms")):  # fmt: skip
            kept = []  # committed, its spool not yet removed
    return [*kept, *(lines or ())] if kept or lines is not None else None


@contextlib.contextmanager
def _unwritten(spool: Path) -> Iterator[None]:
    """This process's own lock waits (lib/db_waits.py) are no rows of the SPOOL it is taking in."""
    was = os.environ.get(step_timings.ENV)
    if was == str(spool):
        del os.environ[step_timings.ENV]
    try:
        yield
    finally:
        if was is not None:
            os.environ[step_timings.ENV] = was


def ingest(spool: Path | str) -> bool:
    """SPOOL's rows in the table and SPOOL gone; False when it stays (no file to write to, or a
    write refused). A spool that grew meanwhile stays too, and its next ingest replaces them."""
    spool, done = Path(spool), False
    if not goh_cache_db.path().is_file():
        return False
    with goh_cache_db.missable(f"goh-cache.db steps of {spool.name}"), _unwritten(spool):
        was = spool.stat()
        if lines := step_timings.load(str(spool)):
            with goh_cache_db.transaction() as con:
                put(con, *named(spool), lines, was.st_mtime)
        if done := spool.stat().st_size == was.st_size:
            spool.unlink()
    return done


def ingest_all(spools: list[str]) -> int:
    """Each of SPOOLS that is there; how many went into the table. Never raises: the instrument is
    never the reason a land or a ship fails."""
    done = 0
    for spool in spools:
        try:
            done += os.path.isfile(spool) and ingest(spool)
        except FAULTS as e:
            print(f"steps_store: {spool}: {e}", file=sys.stderr)
    return done


def idle(now: float) -> list[str]:
    """The landing queue's and the ships' spools that nobody writes any more."""
    import ship_records  # only a sweep asks where the ships are

    out = []
    for where, quiet in (((state_paths.landq_dir() / "logs").glob(f"*{SUFFIX}"), LAND_IDLE_S),
                         (ship_records.ship_dir().glob(f"*/*{SUFFIX}"), SHIP_IDLE_S)):  # fmt: skip
        for p in sorted(where):
            try:
                if now - p.stat().st_mtime >= quiet:
                    out.append(str(p))
            except OSError:
                continue
    return out


def sweep(now: float | None = None) -> int:
    if not goh_cache_db.path().is_file():
        return 0
    return ingest_all(idle(time.time() if now is None else now))


def main(argv: list[str]) -> int:
    if argv[:1] == ["ingest"] and len(argv) > 1:
        return 0 if ingest_all(argv[1:]) == len(argv) - 1 else 1
    if argv[:1] == ["sweep"]:
        print(sweep())
        return 0
    if argv[:1] == ["report"] and len(argv) in (2, 3):
        with goh_cache_db.ro() as con:
            print(step_timings.report(take(con, argv[1], argv[2] if len(argv) > 2 else "")))
        return 0
    print("usage: steps_store.py ingest SPOOL... | sweep | report RUN [PART]",
          file=sys.stderr)  # fmt: skip
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
