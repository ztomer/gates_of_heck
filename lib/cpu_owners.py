"""lib/cpu_owners.py -- CPU by owner over time (GOH.TIMEACCOUNT reading 4, goh.db `cpu_owners`).

    owners(procs, rules)        {pid: owner}: the first rule whose marker is in its cwd; with
                                none, its parent's owner (a lane's pytest in /tmp is the lane's)
    window(prev, cur, rules)    {owner: cpu-s} between two process tables; `other` is the machine's
                                busy cpu-s less every named owner's
    tick(prev, cur)             that window as rows, the sampler's own cpu-s as owner `sampler`
    every(cur)                  the load sampler's hook: a window once a period has passed
    rows(since)                 the rows, oldest first

WHY. Throughput waited on CPU (0% idle, pre-steps capped at one) and nothing said whose: the lanes,
the gates, the idle suite or the rest. lib/load_sources.py already reads the process table every
`--interval`; its loop hands each table here once every GOH_CPU_OWNERS_PERIOD_S (default 120), so
this reading adds no table read of its own -- its cost is the rollup and one write, recorded each
window as `sampler` (the bound: under GOH_CPU_OWNERS_MAX_PCT, default 0.1% of one core).

THE RULES are configuration, GOH_CPU_OWNERS: `owner=marker;...`, first match wins, default
`idle suite=/idle-suite/;gate=/landq/;gate=/goh/ship;lane=/lanes/` -- the idle suite's worktree is
under the land queue, so it is named first. Pinned by tests/test_cpu_owners.py.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import goh_db

ENV, PERIOD_ENV, MAX_ENV = "GOH_CPU_OWNERS", "GOH_CPU_OWNERS_PERIOD_S", "GOH_CPU_OWNERS_MAX_PCT"
DEFAULT_RULES = "idle suite=/idle-suite/;gate=/landq/;gate=/goh/ship;lane=/lanes/"
DEFAULT_PERIOD_S, DEFAULT_MAX_PCT = 120.0, 0.1
OTHER, SAMPLER = "other", "sampler"
_WROTE = [0.0]  # the last window's write: cpu-s booked to the next window's `sampler`


def rules(text: str | None = None) -> list[tuple[str, str]]:
    """GOH_CPU_OWNERS as (owner, marker) pairs; a pair with no `=` is refused loudly."""
    raw = os.environ.get(ENV) or DEFAULT_RULES if text is None else text
    out = []
    for part in filter(None, (p.strip() for p in raw.split(";"))):
        owner, eq, marker = part.partition("=")
        if not eq or not owner.strip() or not marker:
            raise ValueError(f"{ENV}: {part!r} is no owner=marker")
        out.append((owner.strip(), marker))
    return out


def number(env: str, default: float) -> float:
    try:
        return float(os.environ.get(env) or default)
    except ValueError:
        return default


def period_s() -> float:
    return number(PERIOD_ENV, DEFAULT_PERIOD_S)


def owners(procs: dict, pairs: list[tuple[str, str]]) -> dict[int, str]:
    """Each pid's owner (module doc): its own cwd, else its parents' up the chain."""
    out: dict[int, str] = {}

    def of(pid: int, seen: int = 0) -> str:
        if pid in out:
            return out[pid]
        p = procs.get(pid)
        if p is None or seen > 64:
            return OTHER
        cwd = getattr(p, "cwd", None) or ""
        got = next((o for o, m in pairs if m in cwd), None)
        if got is None:
            got = of(p.ppid, seen + 1) if p.ppid not in (0, pid) else OTHER
        out[pid] = got
        return got

    for pid in procs:
        of(pid)
    return out


def window(prev, cur, pairs: list[tuple[str, str]]) -> dict[str, float]:
    """{owner: cpu-s} from PREV to CUR (lib/proc_table.Snapshot): each live process's own cpu and
    its reaped children's since PREV, a vanished child's already-seen share taken off its parent
    (lib/load_sources.py `attribute`); `other` is the machine's busy rest."""
    from load_sources import attribute  # lib/: it imports this module at its top

    who = owners(cur.procs, pairs)
    own, reaped = attribute(prev.procs, cur.procs)
    got: dict[str, float] = {}
    for pid in cur.procs:
        used = own.get(pid, 0.0) + reaped.get(pid, 0.0)
        if used > 0:
            got[who[pid]] = got.get(who[pid], 0.0) + used
    named = sum(v for k, v in got.items() if k != OTHER)
    if prev.busy_s is not None and cur.busy_s is not None:
        got[OTHER] = max(0.0, cur.busy_s - prev.busy_s - named)
    return got


def tick(prev, cur) -> bool:
    """The window PREV-CUR as rows, with the sampler's own cpu-s for its rollup and its write."""
    span = cur.t - prev.t
    if span <= 0:
        return False
    c0 = time.process_time()
    got = window(prev, cur, rules())
    try:
        with goh_db.transaction() as con:
            # this rollup, and the last window's write (its cost is known only after it)
            got[SAMPLER] = time.process_time() - c0 + _WROTE[0]
            con.executemany("INSERT OR REPLACE INTO cpu_owners(at, window_s, owner, cpu_s) "
                            "VALUES (?, ?, ?, ?)",
                            [(round(cur.t, 3), span, o, round(s, 6))
                             for o, s in got.items()])  # fmt: skip
    except (OSError, sqlite3.Error, goh_db.Unknown) as e:
        with contextlib.suppress(OSError):
            sys.stderr.write(f"cpu_owners: window not recorded ({e})\n")
        return False
    finally:
        _WROTE[0] = time.process_time() - c0 - got.get(SAMPLER, 0.0) + _WROTE[0]
    return True


_BASE: list = []  # the table the last window ended at


def every(cur) -> bool:
    """CUR, the load sampler's table: a window booked once GOH_CPU_OWNERS_PERIOD_S passed since the
    last (the first table only starts the clock)."""
    if not _BASE or cur.t - _BASE[0].t < period_s():
        _BASE[:] = _BASE or [cur]
        return False
    done = tick(_BASE[0], cur)
    _BASE[:] = [cur]
    return done


def rows(since: float = 0.0) -> list[tuple[float, float, str, float]]:
    try:
        with goh_db.ro() as con:
            return con.execute("SELECT at, window_s, owner, cpu_s FROM cpu_owners WHERE at >= ? "
                               "ORDER BY at, owner", (since,)).fetchall()  # fmt: skip
    except (OSError, sqlite3.Error, goh_db.Unknown):
        return []


def summary(found: list[tuple[float, float, str, float]]) -> dict:
    """Per owner its cpu-s and mean cores; the sampler's share of one core, and the bound."""
    walls = {at: w for at, w, _, _ in found}
    wall = sum(walls.values())
    by: dict[str, float] = {}
    for _, _, owner, s in found:
        by[owner] = by.get(owner, 0.0) + s
    cost = 100.0 * by.get(SAMPLER, 0.0) / wall if wall else None
    return {"windows": len(walls), "wall_s": wall, "cpu_s": by, "sampler_pct": cost,
            "max_pct": number(MAX_ENV, DEFAULT_MAX_PCT)}  # fmt: skip
