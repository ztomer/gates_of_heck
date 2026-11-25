"""lib/state_rekey.py -- every stored path of goh.db as a token (`$STATE/..`, `$REPOS/..`,
`$HOMEDIR/..`) and every repo as its key, in one transaction with `meta.paths` (GOH.ABSDATA).

    run(undo, queued)   {column: cells moved}; {} when the file holds that form already
    left(con)         {column: cells that hold a path of this host where a token belongs}
    prose(con)        {column: cells whose TEXT names such a path: said as it was, never moved}
    main(undo)        `goh state rekey [--undo]`; portable() is `goh state check --portable`'s part

ONE CODEC: the writers' own (lib/state_paths.py encode, repo_key), called from SQL, so a moved cell
is what a writer of the moved file writes (tests/test_state_rekey.py compares the two files whole).
From this transaction on every writer asks `meta.paths` inside its own and writes tokens; readers
take both forms in every file.

REFUSED until 0017 to 0020 are applied (a document would still hold the paths), while a request or
an early run is in the queue or a ship runs (their owners hold what they read), and when a row
exists under both forms (named; two rows are never made one here). The way back is --undo; a repo
a ship's grace names is not turned back (that table held both spellings before).
"""

from __future__ import annotations

import functools
import json
import os
import sqlite3
import sys
from collections.abc import Callable
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db_schema  # lib/, on the path from the line above
import goh_db
import state_columns
import state_paths

CONTRACTS = (17, 18, 19, 20)
SHIP = ("ships", "ship_steps", "ship_lists", "ship_bisect", "ship_diffcov_lines",
        "ship_diffcov_faults", "ship_grace", "ship_pins", "ship_pin_repos")  # fmt: skip
LAND = ("lands", "land_lists", "land_flakes", "land_remote_shares", "land_cov_files",
        "land_cov_carry", "land_cap_items", "land_waived", "land_waived_items")  # fmt: skip
RUNS = ("requests", "early")
# (table, column, which of its rows hold a path)
PATHS: tuple[tuple[str, str, str], ...] = (
    *((t, "result", "1") for t in SHIP),
    *((t, "queue", "1") for t in LAND),
    *((t, "queue", "1") for t in (
        *RUNS, "request_nodes", "request_cov_files", "request_cov_carry", "selection_misses")),
    *((t, c, "1") for t in RUNS for c in ("worktree", "lane", "pre_keep", "slot_worktree")),
    ("lands", "log", "1"),
    ("land_lists", "value", "kind = 'red_log'"),
    *(("ships", c, "1") for c in ("before_clock_log", "loopctl", "verdicts")),
    *(("ship_steps", c, "1") for c in ("log", "timings")),
    ("ship_pins", "sweep", "1"),
    ("wf_pins", "sweep", "1"),
    *(("pin_pushes", c, "1") for c in ("sweep", "log")),
    ("pin_sweep_repos", "sweep", "1"),
    ("imports", "source", "1"),
    ("spawn_lists", "value", "kind LIKE 'app%'"),
    ("spawn_app_exits", "app", "1"),
    ("slot_owners", "repo_key", "1"),
    ("reuse_shadow", "repo", "1"),
    ("reuse_evidence", "repo", "1"),
    *(("lane_starts", c, "1") for c in ("disk_path", "slots_dir")),
    *(("flakes", c, "1") for c in ("log_first", "log_second")),
)  # fmt: skip
REPOS: tuple[tuple[str, str], ...] = tuple(
    (t, "repo") for t in ("lands", "ships", *RUNS, "flakes", "main_readings", "pin_pushes",
                          "pin_sweep_repos", "ship_grace"))  # fmt: skip
FORWARD = ("ships", "ship_grace")  # these held a repo's key or name before too
KV = tuple({*state_paths.PATHS, *state_paths.REPOS, *state_paths.NAMES})


class RekeyRefused(sqlite3.OperationalError):
    """Nothing was moved."""


def _cells(con: sqlite3.Connection) -> list[tuple[str, str, str, str]]:
    """(table, column, the rows, the codec's name) of every one this file has."""
    have = {t: set(state_columns.columns(con, t)) for (t,) in con.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}  # fmt: skip
    all_of = [*((*p, "path") for p in PATHS), *((t, c, "1", "repo") for t, c in REPOS)]
    return [x for x in all_of if x[1] in have.get(x[0], ())]


def _codecs(con: sqlite3.Connection, undo: bool) -> None:
    path, repo = (state_paths.decode, state_paths.repo) if undo else (
        state_paths.encode, state_paths.repo_key)  # fmt: skip
    for name, fn in (("path", path), ("repo", repo)):
        con.create_function(f"goh_{name}", 1, functools.lru_cache(maxsize=None)(fn),
                            deterministic=True)  # fmt: skip


def why_not(con: sqlite3.Connection, queued: bool = False) -> str | None:
    """What keeps goh.db from a move now, or None. QUEUED: a request may wait (the cutover's fence
    holds it where it is, and its owner reads both forms)."""
    held = [f"{t}: {n}" for t in (() if queued else RUNS)
            if (n := con.execute(f"SELECT count(*) FROM {t}").fetchone()[0])]  # fmt: skip
    n = con.execute(f"SELECT count(*) FROM ships WHERE {state_columns.IN_FLIGHT}").fetchone()[0]
    held += [f"ships still running: {n}"] * bool(n)
    return ", ".join(held) or None


def run(undo: bool = False, queued: bool = False) -> dict[str, int]:
    to = "absolute" if undo else state_paths.TOKEN
    moved: dict[str, int] = {}
    with goh_db.transaction() as con:
        if not state_paths.tokens(con) if undo else state_paths.tokens(con):
            return {}
        if missing := sorted(set(CONTRACTS) - set(db_schema.version(con)["applied"])):
            raise RekeyRefused(f"contracts {missing} are not applied: a document holds the paths")
        if why := why_not(con, queued):
            raise RekeyRefused(why)
        _codecs(con, undo)
        for table, col, rows, kind in _cells(con):
            if undo and kind == "repo" and table in FORWARD:
                continue
            fn = f'goh_{kind}("{col}")'
            try:
                n = con.execute(f'UPDATE "{table}" SET "{col}" = {fn} WHERE ({rows}) AND '
                                f'"{col}" IS NOT {fn}').rowcount  # fmt: skip
            except sqlite3.IntegrityError as e:
                raise RekeyRefused(f"{table}.{col}: a row is there under both forms: {e}") from e
            if n:
                moved[f"{table}.{col}"] = n
        con.execute("INSERT INTO meta(key, value) VALUES ('paths', ?) ON CONFLICT(key) DO UPDATE "
                    "SET value = excluded.value", (to,))  # fmt: skip
        if n := _kv(con, undo):
            moved["kv.body"] = n
    return moved


def _kv(con: sqlite3.Connection, undo: bool) -> int:
    """The paths in the kv documents lib/state_paths.py names, whatever else they hold."""
    codec: Callable[[str, Any], Any] = (
        state_paths.got if undo else lambda name, doc: state_paths.put(con, name, doc)
    )
    n = 0
    for name, body in con.execute("SELECT name, body FROM kv").fetchall():
        if name not in KV:
            continue
        doc = json.loads(body)
        if (to := codec(name, doc)) != doc:
            con.execute("UPDATE kv SET body = ? WHERE name = ?",
                        (json.dumps(to, sort_keys=True), name))  # fmt: skip
            n += 1
    return n


def _texts(con: sqlite3.Connection) -> list[tuple[str, str]]:
    return [(t, r[1]) for (t,) in con.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        " ORDER BY name").fetchall() for r in con.execute(f'PRAGMA table_info("{t}")')]  # fmt: skip


def left(con: sqlite3.Connection) -> dict[str, int]:
    _codecs(con, False)
    out: dict[str, int] = {}
    for table, col, rows, kind in _cells(con):
        n = con.execute(f'SELECT count(*) FROM "{table}" WHERE ({rows}) AND "{col}" IS NOT '
                        f'goh_{kind}("{col}")').fetchone()[0]  # fmt: skip
        if n:
            out[f"{table}.{col}"] = n
    return out


def prose(con: sqlite3.Connection) -> dict[str, int]:
    """Cells that name a path under the home directory in their text, a path column's aside."""
    home = os.path.expanduser("~")
    named: dict[tuple[str, str], str] = {}
    for table, col, rows, _ in _cells(con):
        named[table, col] = rows
    out: dict[str, int] = {}
    for table, col in _texts(con):
        others = f"NOT ({named[table, col]})" if (table, col) in named else "1"
        n = con.execute(f'SELECT count(*) FROM "{table}" WHERE {others} AND typeof("{col}") = '
                        f"'text' AND instr(\"{col}\", ?)", (home + "/",)).fetchone()[0]  # fmt: skip
        if n:
            out[f"{table}.{col}"] = n
    return out


def portable(con: sqlite3.Connection) -> tuple[list[str], list[str]]:
    """(what is not portable, what is only said)."""
    bad = [f"{c}: {n} cells hold a path of this host" for c, n in left(con).items()]
    return bad, [f"{c}: {n} cells name one in their text" for c, n in prose(con).items()]


def main(undo: bool = False) -> int:
    from tui.lib import err, ok

    try:
        state_columns.writable()
        moved = run(undo)
    except (OSError, sqlite3.Error) as e:
        err(f"state rekey: nothing moved: {e}")
        return 1
    said = f"{sum(moved.values())} cells in {len(moved)} columns"
    ok(f"state rekey: {said}" if moved else "state rekey: the file holds that form already")
    return 0
