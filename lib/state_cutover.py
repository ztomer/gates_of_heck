"""lib/state_cutover.py -- `goh state cutover`: every document out of goh.db and every path a token,
one command, run once (GOH.ABSDATA, the data plan's S7). Killed anywhere, run again: it goes on.

    run(say)      the steps, each one's done-ness read from the file, never counted; its exit code
    lift()        `goh state cutover --abort`: the fence of a dead cutover down
    plan(con)     the steps the file still needs ([]: it is done, and a run does nothing)

WHAT IT IS RUN ON. A file of a goh from before GOH.OLDFORMS: an uncut one, a backup taken before its
cutover, or one a kill left between two steps. A new file is born cut (lib/db_core.py `migrate`), so
such a file is opened by nothing else: every door refuses it, naming this command (lib/goh_db.py
`Uncut`), a hook's row waits in the spill, and lands and ships wait (lib/state_shape.py `paused`).

THE STEPS. fence: `meta.fence` = this pid and its start; an older goh's lands and ships wait for it.
busy: refused while a ship's worker lives (a request moves where it waits, and a dead worker's ship
is settled by the next one). fill: every row's columns from its document (lib/state_backfill.py) and
every red cause in its land. strip: a document goes where its columns give it back
(lib/state_strip.py); one that stays refuses the cutover, named. bury: a value of the wrong type out
of `rest` into the dead-letter file, which `goh state check` names. promote: a spawn's old `item` as
its last item row. kv: the kv documents into their rows (lib/kv_fill.py). contracts: 0017 to 0021 in
order, each with its copy of the file (lib/db_contract.py). rekey: the paths (lib/state_rekey.py).
The fence goes down, and VACUUM gives the space back when the file is idle. A file that lacks 0021
alone is cut already: its kv documents go, and no fence is put up.

A REFUSAL LIFTS THE FENCE and changes nothing more, so the next run continues. A KILL leaves it up
with a dead pid: `goh state cutover` takes it over, `--abort` lifts it. A second cutover while one
runs is refused with its pid.

BEFORE THE FIRST RUN no process, worktree or installed goh older than this one may be left (`goh
state olds --since`): from contract 0017 on, older code opens the file read-only.
Pinned by tests/test_state_cutover.py.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from collections.abc import Callable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db_contract  # lib/, on the path from the line above
import db_schema
import goh_db
import kv_fill
import lock_order
import pid_owner
import state_backfill
import state_columns
import state_paths
import state_rekey
import state_shape
import state_strip

PROMOTE = state_columns.SQL / "promote_items.sql"
VACUUM_MS = 60_000
DOCUMENTS = (17, 18, 19, 20)  # the contracts that drop a `body`; 0021 drops the kv documents
KV_ALONE = ({"kv", "0021"}, {"kv", "0021", "vacuum"}, {"vacuum"})  # nothing a fence is for
DONE, REFUSED, RUNS = 0, 1, 2  # exit codes
Say = Callable[[str], None]


class Refused(sqlite3.OperationalError):
    """The cutover stops here: named, the fence lifted, the file as the steps before left it."""


class Running(Refused):
    """Another cutover holds the fence."""


def passed(step: str) -> None:
    """STEP is done (a test's kill point)."""


def plan(con: sqlite3.Connection) -> list[str]:
    have = set(db_schema.version(con)["applied"])
    out = [f"{n:04d}" for n in state_rekey.CONTRACTS if n not in have]
    out = ["documents"] * any(n not in have for n in DOCUMENTS) + ["kv"] * bool(out) + out
    out += ["rekey"] * (not state_paths.tokens(con))
    out += ["fence"] * (state_shape.fence(con) is not None)
    return out + ["vacuum"] * _loose(con)


def _loose(con: sqlite3.Connection) -> bool:
    """A tenth of the file is free pages: the space the documents left."""
    free = con.execute("PRAGMA freelist_count").fetchone()[0]
    return free * 10 > con.execute("PRAGMA page_count").fetchone()[0]


# -- the fence -----------------------------------------------------------------------------------


def _raise(me: str) -> None:
    with goh_db.transaction() as con:
        held = state_shape.fence(con)
        if held is not None and held != me and pid_owner.alive_line(held):
            raise Running(f"another cutover runs: pid {held}")
        con.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET "
                    "value = excluded.value", (state_shape.FENCE, me))  # fmt: skip


def _lower(me: str) -> None:
    with goh_db.transaction() as con:
        con.execute("DELETE FROM meta WHERE key = ? AND value = ?", (state_shape.FENCE, me))


@goh_db.uncut()
def lift() -> str:
    """The fence lifted; what it was. Refused while its cutover lives, or with none up."""
    with goh_db.transaction() as con:
        held = state_shape.fence(con)
        if held is None:
            raise Refused("no cutover's fence is up")
        if pid_owner.alive_line(held):
            raise Refused(f"its cutover runs: pid {held}; stop it first")
        con.execute("DELETE FROM meta WHERE key = ?", (state_shape.FENCE,))
    return held


# -- the steps -----------------------------------------------------------------------------------


def _busy() -> None:
    with goh_db.known() as con:
        if why := state_rekey.why_not(con):
            raise Refused(f"in flight: {why}: run it again once they end")


def _fill() -> int:
    """Every row filled, every red cause in its land (a row with no document too)."""
    n = state_backfill.run()["folded"]
    with goh_db.transaction() as con:
        if not state_shape.has(con, "red_causes"):
            return n
        left = con.execute(
            "SELECT r.queue, r.run, r.body, l.at FROM red_causes r JOIN lands l ON l.queue = "
            "r.queue AND l.run = r.run WHERE l.red_class IS NULL AND l.body = '{}' AND "
            "json_valid(r.body) AND json_type(r.body, '$.cause') = 'text'").fetchall()  # fmt: skip
        for queue, run, body, at in left:
            _fold(con, (queue, run), json.loads(body), at)
    return n + len(left)


def _fold(con: sqlite3.Connection, key: tuple[str, str], cause: dict, at: float) -> None:
    """CAUSE as the columns and rows of a land that has no document left to take it."""
    rest = dict(cause)
    rule, tests = rest.get("rule"), rest.get("tests")
    rest.pop("cause")
    if isinstance(rule, str):
        rest.pop("rule")
    if isinstance(tests, list) and all(isinstance(t, str) for t in tests):
        con.executemany("INSERT INTO land_lists(queue, run, kind, ord, value, at) VALUES "
                        "(?, ?, 'red_test', ?, ?, ?)",
                        [(*key, i, t, at) for i, t in enumerate(rest.pop("tests"))])  # fmt: skip
    con.execute("UPDATE lands SET red_class = ?, red_rule = ?, rest = json_patch(rest, ?) WHERE "
                "queue = ? AND run = ?",
                (cause["cause"], rule if isinstance(rule, str) else None,
                 json.dumps({"red_cause": rest} if rest else {}), *key))  # fmt: skip


def _bury() -> int:
    """Each promoted key out of `rest`, its value a dead letter: the letter first, so a kill between
    the two leaves it twice, never lost."""
    n = 0
    with goh_db.transaction() as con:
        for table in state_columns.TABLES:
            rows = con.execute(f"SELECT rowid, rest FROM {table} WHERE rest <> '{{}}'").fetchall()
            for rowid, text in rows:
                rest, odd = state_shape.sift(table, json.loads(text))
                if not odd:
                    continue
                key = dict(zip(names := state_columns.key(con, table), con.execute(
                    f"SELECT {', '.join(names)} FROM {table} WHERE rowid = ?", (rowid,)
                ).fetchone(), strict=True))  # fmt: skip
                state_shape.bury(table, {**key, **odd}, "a value of another type, at the cutover")
                con.execute(f"UPDATE {table} SET rest = ? WHERE rowid = ?",
                            (json.dumps(rest), rowid))  # fmt: skip
                n += 1
    return n


def _promote() -> int:
    (sql,) = db_schema.statements(PROMOTE.read_text(encoding="utf-8"))
    with goh_db.transaction() as con:
        here = "item" in state_shape.columns(con, "agent_spawns")
        return con.execute(sql).rowcount if here else 0


def _documents(say: Say) -> None:
    say(f"fill: {_fill()} red causes into their lands")
    passed("fill")
    n, whole = state_strip.run(state_paths.repo_key)
    if whole:
        raise Refused(f"{len(whole)} documents stay: " + "; ".join(whole[:5]))
    say(f"strip: {n} documents dropped, 0 left")
    passed("strip")
    say(f"bury: {_bury()} values into the dead-letter file")
    passed("bury")
    say(f"promote: {_promote()} spawns' items")
    passed("promote")


def _kv(say: Say) -> None:
    kv_fill.run()
    say("kv: the documents are in their rows")
    passed("kv")


def _contracts(say: Say) -> None:
    for n in state_rekey.CONTRACTS:
        with goh_db.known() as con:
            done = n in db_schema.version(con)["applied"]
        if not done:
            try:
                copy = db_contract.contract(goh_db.SEAM, n, lambda: lock_order.check("goh_db"))
            except db_contract.ContractRefused as e:
                raise Refused(str(e)) from e
            say(f"contract {n:04d}: applied; the file before it: {copy.name}")
        passed(str(n))


def vacuum() -> str:
    """VACUUM when the file is idle; what it did."""
    with goh_db.connect() as con:
        if not _loose(con):
            return "nothing to give back"
        pages = con.execute("PRAGMA page_count").fetchone()[0]
        con.execute(f"PRAGMA busy_timeout={VACUUM_MS}")
        con.execute("VACUUM")
        return f"{pages} pages to {con.execute('PRAGMA page_count').fetchone()[0]}"


@goh_db.uncut()
def run(say: Say) -> int:
    state_columns.writable()
    with goh_db.known() as con:
        todo = plan(con)
    if not todo:
        say("done already: nothing to do")
        return DONE
    if set(todo) in KV_ALONE:
        if todo != ["vacuum"]:
            _kv(say)
            _contracts(say)
    else:
        me, t0 = str(pid_owner.stamp_line(os.getpid())), time.monotonic()
        _raise(me)
        passed("fence")
        try:
            _busy()
            if todo[0] == "documents":
                _documents(say)
            if "kv" in todo:
                _kv(say)
            _contracts(say)
            moved = state_rekey.run()
            say(f"rekey: {sum(moved.values())} cells in {len(moved)} columns")
            passed("rekey")
        finally:
            _lower(me)
        say(f"the window was {time.monotonic() - t0:.1f} s")
    try:
        say(f"vacuum: {vacuum()}")
    except sqlite3.OperationalError as e:
        say(f"vacuum: not now ({e}): the next `goh state cutover` does it")
    return DONE


def main(abort: bool = False) -> int:
    from tui.lib import err, info, ok

    try:
        if abort:
            ok(f"state cutover: the fence of pid {lift()} is down; lands and ships go on")
            return DONE
        code = run(lambda s: info(f"state cutover: {s}"))
    except (OSError, sqlite3.Error) as e:
        err(f"state cutover: stopped, the file as the steps before left it: {e}")
        return RUNS if isinstance(e, Running) else REFUSED
    ok("state cutover: done")
    return code
