"""lib/push_sweep_rows.py -- a pin push sweep's record in goh.db (GOH.PINSWEEP, goh.db 0022): the
one writer and the one reader of push_sweeps, push_sweep_repos and push_sweep_steps.

    put(path, rec)             a sweep about to start, its repos pending; a row of that name goes
    read(path)                 its record, {} for none
    update(path, **cols)       the sweep's own columns; outcome(path, where, **cols) one repo's
    begin(path, run, now)      ("go" | "busy" | "superseded" | "gone", the other sweep): the sweep
                               taken from pending to running, or why not, in one transaction
    untaken(path)              its repos no newer sweep lists
    fail(path, run, why, now)  a sweep whose processes are gone, settled: its repos left unpushed
    live()                     every sweep pending or running
    anchor(path)               when the last sweep's pushes started; with none, the oldest sweep
    verdict(repo)              what a gate last said of REPO: pushed or refused, its tip and steps

A SWEEP IS NAMED by the path its file had (`<result>.sweep.json`), which no code opens: ship_pins,
the ledger and `goh ship --status` already say it. ONE SWEEP AT A TIME and "the newer one takes it"
are judged inside `begin`'s transaction, so two supervisors never both start. No goh.db reads as no
sweep; a record that cannot be written is raised. Paths are tokens, repos keys
(lib/state_paths.py). Pinned by tests/test_push_sweep_rows.py.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import goh_db  # lib/, beside this file: the one door to goh.db
import state_paths

PATHS = ("sweep", "ship", "log")
SWEEP = ("run", "status", "created", "quiet", "every", "due", "started", "finished", "budget",
         "ship", "ship_worker", "supervisor", "runner", "exit", "why", "log")  # fmt: skip
REPO = ("repo", "status", "said", "log", "started", "seconds", "slot_wait_s", "tip", "at")
LIVE = ("pending", "running")
OPEN = ("pending", "pushing")  # a repo its sweep has not said an outcome of
VERDICTS = ("pushed", "refused")  # its gate judged it
# A newer sweep that lists the repo and was not lost takes it: its push carries every older pin.
TAKEN = """SELECT n.sweep FROM push_sweeps n JOIN push_sweep_repos m ON m.sweep = n.sweep
  JOIN push_sweeps o ON o.sweep = r.sweep
  WHERE m.repo = r.repo AND n.status <> 'failed' AND (n.created, n.sweep) > (o.created, o.sweep)
  ORDER BY n.created DESC, n.sweep DESC LIMIT 1"""


def key(path: Path | str) -> str:
    return state_paths.encode(str(path))


def _cell(col: str, value: Any) -> Any:
    if value is None:
        return None
    if col in PATHS:
        return state_paths.encode(str(value))
    return state_paths.repo_key(str(value)) if col == "repo" else value


def _said(col: str, value: Any) -> Any:
    if value is None:
        return None
    if col in PATHS:
        return state_paths.decode(value)
    return state_paths.repo(value) if col == "repo" else value


@contextlib.contextmanager
def _read() -> Iterator[sqlite3.Connection | None]:
    """goh.db read-only; None when there is none or it is from before 0022."""
    with contextlib.ExitStack() as stack:
        try:
            con = stack.enter_context(goh_db.ro())
            con.execute("SELECT 1 FROM push_sweeps LIMIT 0")
        except (OSError, sqlite3.OperationalError):
            con = None
        yield con


def insert(con: sqlite3.Connection, name: str, rec: dict, at: float) -> None:
    """REC as the sweep NAME's rows (its key), the caller's transaction; the ones there go first."""
    for table in ("push_sweep_steps", "push_sweep_repos", "push_sweeps"):
        con.execute(f"DELETE FROM {table} WHERE sweep = ?", (name,))
    con.execute(f"INSERT INTO push_sweeps(sweep, {', '.join(SWEEP)}, at) "
                f"VALUES ({', '.join('?' * (len(SWEEP) + 2))})",
                (name, *(_cell(c, rec.get(c)) for c in SWEEP), at))  # fmt: skip
    for where, r in rec["repos"].items():
        con.execute(f"INSERT INTO push_sweep_repos(sweep, place, {', '.join(REPO)}) "
                    f"VALUES ({', '.join('?' * (len(REPO) + 2))})",
                    (name, where, *(_cell(c, r.get(c)) for c in REPO)))  # fmt: skip
        _steps(con, name, where, r.get("steps") or {})


def _steps(con: sqlite3.Connection, name: str, where: str, steps: dict[str, str | None]) -> None:
    con.execute("DELETE FROM push_sweep_steps WHERE sweep = ? AND place = ?", (name, where))
    con.executemany("INSERT INTO push_sweep_steps(sweep, place, ord, step, closure) VALUES "
                    "(?, ?, ?, ?, ?)",
                    [(name, where, i, s, c) for i, (s, c) in enumerate(steps.items())])  # fmt: skip


def put(path: Path | str, rec: dict) -> None:
    with goh_db.transaction() as con:
        insert(con, key(path), rec, float(rec["created"]))


def document(con: sqlite3.Connection, name: str) -> dict:
    row = con.execute(f"SELECT {', '.join(SWEEP)} FROM push_sweeps WHERE sweep = ?",
                      (name,)).fetchone()  # fmt: skip
    if row is None:
        return {}
    rec = {c: _said(c, v) for c, v in zip(SWEEP, row, strict=True) if v is not None}
    repos: dict[str, dict] = {}
    for where, *cells in con.execute(f"SELECT place, {', '.join(REPO)} FROM push_sweep_repos "
                                     "WHERE sweep = ? ORDER BY place", (name,)):  # fmt: skip
        repos[where] = {c: _said(c, v) for c, v in zip(REPO, cells, strict=True) if v is not None}
    for where, step, closure in con.execute("SELECT place, step, closure FROM push_sweep_steps "
                                            "WHERE sweep = ? ORDER BY place, ord",
                                            (name,)):  # fmt: skip
        repos[where].setdefault("steps", {})[step] = closure
    return {"path": state_paths.decode(name), **rec, "repos": repos}


def read(path: Path | str) -> dict:
    with _read() as con:
        return document(con, key(path)) if con else {}


def _set(cols: dict[str, Any], known: tuple[str, ...]) -> tuple[str, list]:
    if odd := set(cols) - set(known):
        raise ValueError(f"no such column: {', '.join(sorted(odd))}")
    return ", ".join(f"{c} = ?" for c in cols), [_cell(c, v) for c, v in cols.items()]


def update(path: Path | str, **cols: Any) -> None:
    sets, args = _set(cols, SWEEP)
    with goh_db.transaction() as con:
        con.execute(f"UPDATE push_sweeps SET {sets} WHERE sweep = ?", (*args, key(path)))


def outcome(path: Path | str, where: str, steps: dict[str, str | None] | None = None,
            **cols: Any) -> None:  # fmt: skip
    """WHERE's columns, and the STEPS its gate refused at (step -> goh's closure for it)."""
    sets, args = _set(cols, REPO)
    with goh_db.transaction() as con:
        con.execute(f"UPDATE push_sweep_repos SET {sets} WHERE sweep = ? AND place = ?",
                    (*args, key(path), where))  # fmt: skip
        if steps is not None:
            _steps(con, key(path), where, steps)


def _untaken(con: sqlite3.Connection, name: str) -> list[str]:
    return [w for w, by in con.execute(
        f"SELECT r.place, ({TAKEN}) FROM push_sweep_repos r WHERE r.sweep = ? AND r.status = "
        "'pending' ORDER BY r.place", (name,)) if by is None]  # fmt: skip


def untaken(path: Path | str) -> list[str]:
    with _read() as con:
        return _untaken(con, key(path)) if con else []


def begin(path: Path | str, run: str, now: float) -> tuple[str, str]:
    name = key(path)
    with goh_db.transaction() as con:
        mine = con.execute("SELECT 1 FROM push_sweeps WHERE sweep = ? AND run = ? AND status = "
                           "'pending'", (name, run)).fetchone()  # fmt: skip
        if mine is None:
            return "gone", ""
        left = _untaken(con, name)
        if left:
            other = con.execute("SELECT sweep FROM push_sweeps WHERE status = 'running'").fetchone()
            if other:
                return "busy", state_paths.decode(other[0])
        by = ""
        for where, took in con.execute(
            f"SELECT r.place, ({TAKEN}) FROM push_sweep_repos r WHERE r.sweep = ? AND "
            "r.status = 'pending'", (name,)).fetchall():  # fmt: skip
            if took is None:
                continue
            by = state_paths.decode(took)
            con.execute("UPDATE push_sweep_repos SET status = 'superseded', at = ?, said = ? WHERE "
                        "sweep = ? AND place = ?",
                        (now, f"left to the newer sweep {by}, whose push carries this pin", name,
                         where))  # fmt: skip
        if left:
            con.execute("UPDATE push_sweeps SET status = 'running', started = ? WHERE sweep = ?",
                        (now, name))  # fmt: skip
            return "go", ""
        con.execute("UPDATE push_sweeps SET status = 'done', finished = ?, at = ? WHERE sweep = ?",
                    (now, now, name))  # fmt: skip
        return "superseded", by


def fail(path: Path | str, run: str, why: str, now: float, status: str = "failed",
         code: int | None = None) -> list[tuple[str, str]]:  # fmt: skip
    """PATH's sweep ended (STATUS, its run's exit CODE); each repo it said no outcome of is left
    unpushed for WHY: the (where, repo) of each. Nothing when the row is another start's, or
    ended."""
    name = key(path)
    with goh_db.transaction() as con:
        if not con.execute("UPDATE push_sweeps SET status = ?, finished = ?, at = ?, exit = ?, "
                           "why = ? WHERE sweep = ? AND run = ? AND status IN (?, ?)",
                           (status, now, now, code, why if status == "failed" else None, name, run,
                            *LIVE)).rowcount:  # fmt: skip
            return []
        left = [(w, state_paths.repo(r)) for w, r in con.execute(
            "SELECT place, repo FROM push_sweep_repos WHERE sweep = ? AND status IN (?, ?) ORDER "
            "BY place", (name, *OPEN)).fetchall()]  # fmt: skip
        con.execute("UPDATE push_sweep_repos SET status = 'committed', said = ?, at = ? WHERE "
                    "sweep = ? AND status IN (?, ?)", (why, now, name, *OPEN))  # fmt: skip
        return left


def live() -> list[dict]:
    with _read() as con:
        names = con.execute("SELECT sweep FROM push_sweeps WHERE status IN (?, ?) ORDER BY "
                            "created, sweep", LIVE).fetchall() if con else []  # fmt: skip
        return [document(con, n) for (n,) in names]


def anchor(path: Path | str) -> float | None:
    """Of every sweep made before PATH's; None with none before it."""
    sql = ("SELECT coalesce(max(s.started), min(s.created)) FROM push_sweeps s JOIN push_sweeps m "
           "ON m.sweep = ? WHERE s.created < m.created")  # fmt: skip
    with _read() as con:
        return con.execute(sql, (key(path),)).fetchone()[0] if con else None


def verdict(repo: Path | str) -> dict | None:
    """REPO's newest row a gate judged, as `read` gives a repo's, its sweep beside it."""
    with _read() as con:
        row = con.execute(
            "SELECT sweep, place FROM push_sweep_repos WHERE repo = ? AND status IN (?, ?) ORDER "
            "BY at DESC LIMIT 1", (state_paths.repo_key(str(repo)), *VERDICTS)
        ).fetchone() if con else None  # fmt: skip
        if row is None:
            return None
        return {"sweep": state_paths.decode(row[0]), **document(con, row[0])["repos"][row[1]]}
