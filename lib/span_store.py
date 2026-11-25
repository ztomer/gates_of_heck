"""lib/span_store.py -- the timing ledger's rows as goh.db's `spans` and `span_steps` (goh.db 0014,
GOH.STEPTIMES), the one writer and the one reader of both.

    put(con, row)          ROW as its span (and its costliest steps); False when that span is there
    rows(con, since, ..)   the spans ended since SINCE (epoch), oldest first, as ledger rows
    key(name) name(key)    a subject's repo name <-> the repo column's key (`group/name`)
    COLUMNS, OWNED         a row's fields that are columns; those another table's columns hold

ONE TIMESTAMP. `at` is the run's end, epoch seconds; a row's ISO text and its tenth are rendered
from it. Two runs of one subject in one tenth of a second are two spans (`at` moves by a
millisecond); the same row again (a spool drained twice) is the span already there.

ONE HOME. `attempt`, `mode`, `why` and the slot waits of a land attempt are columns of `lands`, a
ship's `failed_step` and `land_wait_s` of `ships`, an e2e's `tries` and `from` are counted from
`lands`: OWNED, not kept twice. Any other field without a column is said, never dropped in silence;
tests/test_span_store.py holds every field a writer names to COLUMNS or OWNED.
"""

from __future__ import annotations

import datetime as dt
import math
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db_core  # its warning line
import state_paths

SCHEMA = 1
GOH_NAME = "goh"  # the repo name the gates checkout's own subjects use ('goh ship', 'goh land')
CHECKOUT_NAME = "gates_of_heck"
REAL = ("wall_s", "cpu_s", "load1", "load_end", "control_drift", "wait_lock_s", "pre_s",
        "slot_wait_s", "slot_asked_at", "pre_run_s", "pre_cpu_s", "before_s")  # fmt: skip
INTEGER = ("exit", "pre_workers", "fs_events", "fs_dirs", "fs_tests", "fs_whole", "shadow_f",
           "shadow_ran", "shadow_red_out")  # fmt: skip
BOOL = ("ok", "at_day", "pre_fast")
TEXT = ("item", "sha", "version", "slot_class", "pre_reused", "pre_why", "suite_reused",
        "suite_why", "backfill", "shadow_red")  # fmt: skip
COLUMNS = (*REAL, *INTEGER, *BOOL, *TEXT)
KEYED = ("v", "at", "epoch", "subject", "repo", "kind", "ref", "top_steps")
OWNED = {
    "ship": ("failed_step", "land_wait_s"),
    "land attempt": ("attempt", "mode", "why", "slot_wait_by_class"),
    "land e2e plain": ("tries", "from"),
    "land e2e instrumented": ("tries", "from"),
}
STEP_MS = 0.001  # two spans of one subject, one tenth of a second
TRIES = 1000


def repo_name(name: str) -> str:
    """A repository's subject name: its directory name, the gates checkout as `goh`."""
    name = Path(name.rstrip("/")).name or name
    return GOH_NAME if name == CHECKOUT_NAME else name


def key(name: str) -> str:
    """The repo column's value for a subject's repo NAME: its key under the repos root (`group/name`
    for a grouped one), NAME itself when no repository there is called that."""
    root = state_paths.repos_root()
    where = CHECKOUT_NAME if name == GOH_NAME else name
    if not where or (root / where).is_dir():
        return where
    found = sorted(root.glob(f"[!.]*/{where}/.git")) if "/" not in where else []
    return str(state_paths.repo_key(str(found[0].parent))) if found else where


def instant(row: dict) -> float:
    """The epoch second ROW's run ended: the one that renders back as its `at` and its `epoch`."""
    epoch = float(row["epoch"])
    try:
        second = dt.datetime.fromisoformat(str(row["at"])).timestamp()
    except (KeyError, ValueError):
        return epoch
    if math.floor(epoch) == second + 1 and epoch == second + 1:
        return round(epoch - 0.04, 3)  # 59.96 was written as tenth 60.0 of second 59
    return epoch


def _fit(col: str, v: Any) -> Any:
    """V as COL holds it; None when it is of no such type."""
    if v is None:
        return None
    if col in TEXT:
        return str(v) if isinstance(v, (str, int, float)) else None
    if not isinstance(v, (bool, int, float)) or not math.isfinite(v):
        return None
    if col in REAL:
        return float(v)
    if col in BOOL:
        return int(bool(v))
    return int(v) if float(v).is_integer() else None


def _values(row: dict) -> dict[str, Any]:
    out = {c: _fit(c, row[c]) for c in COLUMNS if row.get(c) is not None}
    kind = str(row["kind"])
    lost = [k for k, v in row.items() if k not in KEYED and k not in OWNED.get(kind, ())
            and v is not None and out.get(k) is None]  # fmt: skip
    if lost:
        db_core.warn(f"spans: a {kind} row's {', '.join(sorted(lost))} has no column: not kept")
    return out


def steps_of(row: dict) -> list[tuple[str, float]]:
    found = []
    for pair in row.get("top_steps") or ():
        if (
            isinstance(pair, (list, tuple))
            and len(pair) == 2
            and _fit("wall_s", pair[1]) is not None
        ):
            found.append((str(pair[0]), float(pair[1])))
    return found


def put(con: sqlite3.Connection, row: dict) -> bool:
    """ROW's span, inside the caller's transaction; False when it is there already, or is no span
    at all (no kind, no wall time of 0 or more)."""
    try:
        kind, repo, run = (
            str(row["kind"]),
            key(str(row.get("repo") or "")),
            str(row.get("ref") or ""),
        )
        at, vals = instant(row), _values(row)
    except (KeyError, TypeError, ValueError):
        return False
    if vals.get("wall_s") is None or vals["wall_s"] < 0 or not kind or not math.isfinite(at):
        return False
    vals.setdefault("ok", 1)
    cols = ("kind", "repo", "at", "run", *vals)
    sql = (f"INSERT OR IGNORE INTO spans({', '.join(cols)}) "
           f"VALUES ({', '.join('?' * len(cols))})")  # fmt: skip
    steps = steps_of(row)
    for _ in range(TRIES):
        if con.execute(sql, (kind, repo, at, run, *vals.values())).rowcount:
            con.executemany("INSERT INTO span_steps(kind, repo, at, run, ord, label, seconds) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            [(kind, repo, at, run, i, *s)
                             for i, s in enumerate(steps)])  # fmt: skip
            return True
        was = con.execute(f"SELECT {', '.join(COLUMNS)} FROM spans "
                          "WHERE (kind, repo, at, run) = (?, ?, ?, ?)",
                          (kind, repo, at, run)).fetchone()  # fmt: skip
        if was == tuple(vals.get(c) for c in COLUMNS):
            return False
        at = round(at + STEP_MS, 3)
    return False


def _row(rec: sqlite3.Row | tuple, steps: list[list] | None) -> dict:
    kind, repo, at, run, *vals = rec
    name = repo_name(repo)
    when = dt.datetime.fromtimestamp(at, dt.UTC)
    by = dict(zip(COLUMNS, vals, strict=True))
    out: dict[str, Any] = {
        "v": SCHEMA, "at": when.isoformat(timespec="seconds"), "epoch": round(at, 1),
        "subject": f"{name} {kind}" if name else kind, "repo": name, "kind": kind,
        "wall_s": by.pop("wall_s"), "ok": bool(by.pop("ok"))}  # fmt: skip
    if run:
        out["ref"] = run
    if steps:
        out["top_steps"] = steps
    return out | {c: bool(v) if c == "at_day" else v for c, v in by.items() if v is not None}


def rows(
    con: sqlite3.Connection, since: float, kinds: tuple[str, ...] = (), repo: str | None = None
) -> list[dict]:
    """The spans ended since SINCE, oldest first, as ledger rows: KINDS alone and REPO's (a
    subject's name) alone when given."""
    where, args = "at >= ?", [since]
    if kinds:
        where += f" AND kind IN ({', '.join('?' * len(kinds))})"
        args += kinds
    if repo is not None:
        where += " AND repo = ?"
        args.append(key(repo))
    steps: dict[tuple, list[list]] = {}
    for *k, label, secs in con.execute(
            f"SELECT kind, repo, at, run, label, seconds FROM span_steps WHERE {where} "
            "ORDER BY kind, repo, at, run, ord", args):  # fmt: skip
        steps.setdefault(tuple(k), []).append([label, secs])
    return [_row(rec, steps.get(tuple(rec[:4]))) for rec in con.execute(
        f"SELECT kind, repo, at, run, {', '.join(COLUMNS)} FROM spans WHERE {where} "
        "ORDER BY at, rowid", args)]  # fmt: skip
