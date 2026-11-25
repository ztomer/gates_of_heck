"""lib/land_judge_rows.py -- what main's code said of a goh land, in goh.db 0023 (GOH.ONEGATE): the
one writer and the one reader of land_judgings and land_judge_steps.

    owe(key, rc, changed, red, now)  a land its gate decided: `waiting`, `same` (no gate code moved)
    expire(repo, before, why)        each land waiting since BEFORE (its own reason kept, else WHY),
                                     or every one (None, by WHY), `unjudged`
    claim(repo)                      the oldest waiting land: (key, its gate's exit, at); None
    wait(key, why) / begin(key, now) it waits for room (WHY) / a step of it runs
    owed_steps(key)                  the steps a red land's own gate had red: {step: its exit}
    settle(repo)                     every `running` row as `unavailable`: its judge is gone
    end(key, steps, why, seconds, now)  its judging: a step's row each, `done`, or `unavailable`
    unjudge(key, why)                it is not run
    judged(repo) / read(repo, now)   the lands judged since the sample was last read / it is read
    rule(tip, step, said, why)       a would-be red ruled `false` (the lane was right) or `true`
    lands(repo, last, since)         judgings since SINCE, the LAST judged's span, newest first
    unrowed(repo, since)             the lands SINCE that landed with no row at all

A KEY is (repo, tip, base): the repository's name in `spans`, the tip its gate judged, the main it
was based on. An waiting row of the same key again (a land tried twice) is written whole. No goh.db,
or one from before 0023, is Unknown to a reader and an error to a writer, never no lands.
Pinned by tests/test_land_judge_rows.py.

GOH.ONEGATE: the land judge's rows (goh.db 0023): a land that its gate decided, where its judging
is, and per step of main's list main's exit, the tip's, the outcome and main's seconds; `rule` says
whether a would-be red was right. Invoked by `land_judge.py`.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from collections.abc import Iterable
from dataclasses import asdict, dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import goh_db  # lib/, beside this file: the one door to goh.db

Key = tuple[str, str, str]
WHERE = "repo = ? AND tip = ? AND base = ?"
STEP = ("step", "outcome", "reason", "main_rc", "tip_rc", "seconds")
NAMED = ("would_red", "broken", "dropped", "deleted")  # a step the judge names


@dataclass
class Step:
    step: str
    outcome: str
    reason: str | None = None
    main_rc: int | None = None
    tip_rc: int | None = None
    seconds: float | None = None


def _steps(con: sqlite3.Connection, key: Key, steps: Iterable[Step]) -> None:
    con.execute(f"DELETE FROM land_judge_steps WHERE {WHERE}", key)
    for i, s in enumerate(steps):
        row = asdict(s)
        con.execute(f"INSERT INTO land_judge_steps(repo, tip, base, ord, {', '.join(STEP)}) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (*key, i, *(row[c] for c in STEP)))  # fmt: skip


def owe(key: Key, land_rc: int, changed: bool, red: dict[str, int], now: float) -> None:
    with goh_db.transaction() as con:
        con.execute("INSERT OR REPLACE INTO land_judgings(repo, tip, base, land_rc, status, at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (*key, land_rc, "waiting" if changed else "same", now))  # fmt: skip
        _steps(con, key, (Step(s, "owed", tip_rc=rc) for s, rc in red.items()))


def expire(repo: str, before: float | None, why: str) -> int:
    with goh_db.transaction() as con:
        return con.execute("UPDATE land_judgings SET status = 'unjudged', why = CASE WHEN ?1 IS "
                           "NULL THEN ?2 ELSE coalesce(why, ?2) END WHERE repo = ?3 AND status = "
                           "'waiting' AND (?1 IS NULL OR at <= ?1)",
                           (before, why, repo)).rowcount  # fmt: skip


def claim(repo: str) -> tuple[Key, int, float] | None:
    with goh_db.known() as con:
        row = con.execute("SELECT tip, base, land_rc, at FROM land_judgings WHERE repo = ? AND "
                          "status = 'waiting' ORDER BY at, tip LIMIT 1",
                          (repo,)).fetchone()  # fmt: skip
    return None if row is None else ((repo, row[0], row[1]), row[2], row[3])


def wait(key: Key, why: str) -> None:
    with goh_db.transaction() as con:
        con.execute(f"UPDATE land_judgings SET status = 'waiting', why = ? WHERE {WHERE}",
                    (why, *key))  # fmt: skip


def begin(key: Key, now: float) -> None:
    with goh_db.transaction() as con:
        con.execute("UPDATE land_judgings SET status = 'running', why = NULL, "
                    f"started = coalesce(started, ?) WHERE {WHERE}", (now, *key))  # fmt: skip


def unjudge(key: Key, why: str) -> None:
    with goh_db.transaction() as con:
        con.execute(f"UPDATE land_judgings SET status = 'unjudged', why = ? WHERE {WHERE}",
                    (why, *key))  # fmt: skip


def judged(repo: str) -> int:
    with goh_db.known() as con:
        return con.execute("SELECT count(*) FROM land_judgings j WHERE repo = ? AND "
                           "status = 'done' AND ended > coalesce((SELECT at FROM land_judge_reads "
                           "r WHERE r.repo = j.repo), 0) AND EXISTS (SELECT 1 FROM "
                           "land_judge_steps s WHERE s.repo = j.repo AND s.tip = j.tip AND "
                           "s.base = j.base AND s.main_rc IS NOT NULL)",
                           (repo,)).fetchone()[0]  # fmt: skip


def read(repo: str, now: float) -> None:
    with goh_db.transaction() as con:
        con.execute("INSERT OR REPLACE INTO land_judge_reads(repo, at) "
                    "VALUES (?, ?)", (repo, now))  # fmt: skip


def owed_steps(key: Key) -> dict[str, int]:
    with goh_db.known() as con:
        return dict(con.execute("SELECT step, tip_rc FROM land_judge_steps WHERE "
                                f"{WHERE} AND outcome = 'owed' ORDER BY ord", key))  # fmt: skip


def settle(repo: str) -> int:
    with goh_db.transaction() as con:
        return con.execute("UPDATE land_judgings SET status = 'unavailable', why = "
                           "'its judge ended before its verdict' WHERE repo = ? AND "
                           "status = 'running'", (repo,)).rowcount  # fmt: skip


def end(key: Key, steps: Iterable[Step], why: str, seconds: float, now: float) -> None:
    with goh_db.transaction() as con:
        con.execute("UPDATE land_judgings SET status = ?, why = ?, seconds = ?, ended = ? "
                    f"WHERE {WHERE}",
                    ("unavailable" if why else "done", why or None, seconds, now,
                     *key))  # fmt: skip
        _steps(con, key, steps)


def rule(tip: str, step: str, said: str, why: str) -> int:
    """How many would-be reds of TIP (a prefix) in STEP (a part of it) are now ruled SAID."""
    with goh_db.transaction() as con:
        return con.execute("UPDATE land_judge_steps SET ruled = ?, ruled_why = ? WHERE "
                           "outcome = 'would_red' AND substr(tip, 1, ?) = ? AND "
                           "instr(step, ?) > 0",
                           (said, why, len(tip), tip, step)).rowcount  # fmt: skip


def lands(repo: str, last: int, since: float) -> list[dict]:
    with goh_db.known() as con:
        cur = con.execute("SELECT * FROM land_judgings WHERE repo = ? AND at >= max(?, coalesce("
                          "(SELECT at FROM land_judgings WHERE repo = ? AND status = 'done' "
                          "ORDER BY at DESC LIMIT 1 OFFSET ?), 0)) ORDER BY at DESC, tip",
                          (repo, since, repo, last - 1))  # fmt: skip
        names = [d[0] for d in cur.description]
        out = [dict(zip(names, row, strict=True)) for row in cur]
        for land in out:
            cur = con.execute("SELECT step, outcome, reason, main_rc, tip_rc, seconds, ruled "
                              f"FROM land_judge_steps WHERE {WHERE} ORDER BY ord",
                              (repo, land["tip"], land["base"]))  # fmt: skip
            names = [d[0] for d in cur.description]
            land["steps"] = [dict(zip(names, row, strict=True)) for row in cur]
    return out


def unrowed(repo: str, since: float) -> list[str]:
    """The landed tips (12 characters, the spans') since SINCE that have no judging."""
    with goh_db.known() as con:
        return [r[0] for r in con.execute(
            "SELECT sha FROM spans s WHERE kind = 'land' AND repo = ? AND ok = 1 AND at >= ? AND "
            "sha IS NOT NULL AND NOT EXISTS (SELECT 1 FROM land_judgings j WHERE j.repo = s.repo "
            "AND substr(j.tip, 1, length(s.sha)) = s.sha) ORDER BY at", (repo, since))]  # fmt: skip
