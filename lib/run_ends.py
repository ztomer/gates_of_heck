"""lib/run_ends.py -- every start has an end row (GOH.TIMEACCOUNT, goh.db 0024): the one writer and
the one reader of `run_ends` and `run_phases`.

    begin(kind, run, ..)        the start's row, open (`outcome` NULL), with this process's pid
    end(kind, run, outcome, ..) its end: finished, red, stopped, refused or carried, and why
    phase(kind, run, name, s)   one named phase of a run: seconds, cpu-s, load at its start
    account(kind, run, ..)      begin, then end by how the block left: finished, else `red` on an
                                exception, `stopped` on a signal or an interrupt; `.outcome` and
                                `.cause` set inside the block win
    sweep(now)                  every open start whose process is gone, closed `stopped` (`died`)
    rows(since) phases(since)   the rows, oldest first

WHY. A median of finished runs cannot see the work thrown away: a pre-step stopped by a re-enqueue,
a check killed by its caller's timeout, a red that wrote no record (close-the-time-account). The
row is written BEFORE the work, so a SIGKILL leaves an open row and `sweep` -- run by every reader
-- closes it; nothing has to remember to write the failure. A write that fails is one stderr line:
the instrument is never why a run fails.

Pinned by tests/test_run_ends.py.
"""

from __future__ import annotations

import contextlib
import os
import resource
import sqlite3
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import goh_db  # lib/: the one door to goh.db
import pid_owner

KINDS = ("check", "prestep", "land", "idle", "ship")
OUTCOMES = ("finished", "red", "stopped", "refused", "carried")
THROWN = ("red", "stopped", "refused")  # its seconds bought no verdict that was kept
COLS = ("repo", "item", "pid", "pid_start", "started", "ended", "outcome", "cause", "wall_s",
        "cpu_s", "load1", "tests_chosen", "tests_reused", "tests_refused",
        "refused_by")  # fmt: skip


def _warn(what: str, e: BaseException) -> None:
    with contextlib.suppress(OSError):
        sys.stderr.write(f"run_ends: {what} not recorded ({e})\n")


def _born(pid: int) -> float | None:
    got = pid_owner.birth(pid)
    return None if got is None else got[0][0] + got[0][1] / 1e6


def _load() -> float | None:
    with contextlib.suppress(OSError):
        return round(os.getloadavg()[0], 2)
    return None


def begin(kind: str, run: str, repo: str | None = None, item: str | None = None,
          pid: int | None = None, now: float | None = None) -> bool:  # fmt: skip
    """KIND RUN's open row; False (one stderr line) when it cannot be written."""
    if kind not in KINDS:
        raise ValueError(f"run_ends: kind {kind!r} is none of {KINDS}")
    pid = os.getpid() if pid is None else pid
    t = time.time() if now is None else now
    try:
        with goh_db.transaction() as con:
            con.execute(
                "INSERT OR REPLACE INTO run_ends(kind, run, repo, item, pid, pid_start, started, "
                "load1, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (kind, run, repo, item, pid, _born(pid), t, _load(), t),
            )
    except (OSError, sqlite3.Error, goh_db.Unknown) as e:
        _warn(f"{kind} {run}'s start", e)
        return False
    return True


def end(kind: str, run: str, outcome: str, cause: str | None = None, now: float | None = None,
        **cols: object) -> bool:  # fmt: skip
    """KIND RUN's end; an end already written stays (the first word is the run's own)."""
    if outcome not in OUTCOMES:
        raise ValueError(f"run_ends: outcome {outcome!r} is none of {OUTCOMES}")
    bad = set(cols) - set(COLS)
    if bad:
        raise ValueError(f"run_ends: no such column: {', '.join(sorted(bad))}")
    t = time.time() if now is None else now
    sets = ", ".join(f"{c} = ?" for c in cols)
    try:
        with goh_db.transaction() as con:
            con.execute(
                "UPDATE run_ends SET ended = ?, outcome = ?, cause = ?, "
                "wall_s = max(0, ? - started), at = ?" + (f", {sets}" if sets else "")
                + " WHERE kind = ? AND run = ? AND outcome IS NULL",
                (t, outcome, cause, t, t, *cols.values(), kind, run),
            )  # fmt: skip
    except (OSError, sqlite3.Error, goh_db.Unknown) as e:
        _warn(f"{kind} {run}'s end", e)
        return False
    return True


def phase(kind: str, run: str, name: str, seconds: float, cpu_s: float | None = None,
          load1: float | None = None) -> bool:  # fmt: skip
    try:
        with goh_db.transaction() as con:
            n = con.execute("SELECT count(*) FROM run_phases WHERE kind = ? AND run = ?",
                            (kind, run)).fetchone()[0]  # fmt: skip
            con.execute("INSERT OR REPLACE INTO run_phases(kind, run, phase, ord, seconds, cpu_s, "
                        "load1) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (kind, run, name, n, max(0.0, float(seconds)), cpu_s, load1))  # fmt: skip
    except (OSError, sqlite3.Error, goh_db.Unknown) as e:
        _warn(f"{kind} {run}'s phase {name}", e)
        return False
    return True


def _cpu() -> float:
    s, c = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
    return s.ru_utime + s.ru_stime + c.ru_utime + c.ru_stime


@dataclass
class Run:
    outcome: str | None = None
    cause: str | None = None


@contextlib.contextmanager
def account(kind: str, run: str, repo: str | None = None, item: str | None = None) -> Iterator[Run]:
    """The block as one start and its end (module doc)."""
    r, c0 = Run(), _cpu()
    begin(kind, run, repo, item)
    try:
        yield r
    except KeyboardInterrupt:
        end(kind, run, "stopped", r.cause or "interrupt", cpu_s=_cpu() - c0)
        raise
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1  # an exit is a stop, never a verdict
        out = r.outcome or ("finished" if code == 0 else "stopped")
        end(kind, run, out, r.cause or (None if code == 0 else f"exit {code}"), cpu_s=_cpu() - c0)
        raise
    except BaseException as e:
        end(kind, run, r.outcome or "red", r.cause or type(e).__name__, cpu_s=_cpu() - c0)
        raise
    end(kind, run, r.outcome or "finished", r.cause, cpu_s=_cpu() - c0)


def red_cause(said: str) -> str:
    """A red pre-step's cause (lib/land_prestep.py's Red words), as thrown-away seconds sum by."""
    if "conflicts with main" in said:
        return "conflict"
    return "diff-cov" if "diff coverage judge" in said else "pre-test"


def tests(kind: str, run: str, did: dict) -> bool:
    """The tests a run reused or refused to reuse, and the read that refused them (reading 3):
    lib/affected_verdict.py `outcome` -- {"took", "tests"} on a take, {"ran": why} on a run."""
    if "ran" in did:
        cols = {"tests_reused": 0, "refused_by": str(did["ran"])[:200]}
    else:
        cols = {"tests_reused": did.get("tests"), "tests_refused": 0}
    try:
        with goh_db.transaction() as con:
            con.execute(f"UPDATE run_ends SET {', '.join(f'{c} = ?' for c in cols)} "
                        "WHERE kind = ? AND run = ?", (*cols.values(), kind, run))  # fmt: skip
    except (OSError, sqlite3.Error, goh_db.Unknown) as e:
        _warn(f"{kind} {run}'s tests", e)
        return False
    return True


@contextlib.contextmanager
def prestep(run: str, req: dict, ran: dict, noted: dict, did: dict,
            red: type[BaseException]) -> Iterator[Run]:  # fmt: skip
    """lib/land_prestep.py `run_kept`'s block as one start and its end: a RED raised is `red` by
    its cause, a deadline (NOTED: no verdict, the lander decides) `carried`; RAN's wait and run are
    its phases, DID its tests, whatever the end."""
    from pathlib import Path

    with account("prestep", run, Path(str(req.get("repo") or "")).name, req.get("item")) as acct:
        try:
            yield acct
        except red as e:
            acct.cause = red_cause(str(e))
            raise
        finally:
            for name in ("wait_s", "run_s"):
                if isinstance(ran.get(name), (int, float)):
                    cpu = ran.get("cpu_s") if name == "run_s" else None
                    phase("prestep", run, name.removesuffix("_s"), ran[name], cpu)
            if did:
                tests("prestep", run, did)
        if noted:
            acct.outcome, acct.cause = "carried", str(noted.get("why") or "no verdict")[:200]


def outcome_of_exit(rc: int) -> str:
    """A process's exit as an end: 0 finished, 1 refused, over 128 stopped by a signal, else red."""
    return "finished" if rc == 0 else "refused" if rc == 1 else "stopped" if rc > 128 else "red"


def _gone(pid: int | None, born: float | None) -> bool:
    if pid is None:
        return True
    now = _born(pid)
    return now is None or (born is not None and abs(now - born) > 1e-3)


def sweep(now: float | None = None) -> int:
    """Every open start whose process is gone (or reborn as another), closed `stopped`, cause
    `died`; how many. Its end time is the sweep's: an upper bound, said by the cause."""
    try:
        with goh_db.ro() as con:
            open_ = con.execute("SELECT kind, run, pid, pid_start FROM run_ends "
                                "WHERE outcome IS NULL").fetchall()  # fmt: skip
    except (OSError, sqlite3.Error, goh_db.Unknown):
        return 0
    n = 0
    for kind, run, pid, born in open_:
        if _gone(pid, born) and end(kind, run, "stopped", "died", now):
            n += 1
    return n


def rows(since: float = 0.0) -> list[dict]:
    try:
        with goh_db.ro() as con:
            cur = con.execute(f"SELECT kind, run, {', '.join(COLS)} FROM run_ends "
                              "WHERE started >= ? ORDER BY started", (since,))  # fmt: skip
            return [dict(zip(("kind", "run", *COLS), r, strict=True)) for r in cur]
    except (OSError, sqlite3.Error, goh_db.Unknown):
        return []


def phases(since: float = 0.0) -> list[dict]:
    try:
        with goh_db.ro() as con:
            cur = con.execute(
                "SELECT p.kind, p.run, p.phase, p.seconds, p.cpu_s, p.load1, e.started "
                "FROM run_phases p JOIN run_ends e USING (kind, run) WHERE e.started >= ? "
                "ORDER BY e.started, p.ord", (since,))  # fmt: skip
            keys = ("kind", "run", "phase", "seconds", "cpu_s", "load1", "started")
            return [dict(zip(keys, r, strict=True)) for r in cur]
    except (OSError, sqlite3.Error, goh_db.Unknown):
        return []
