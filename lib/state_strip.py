"""lib/state_strip.py -- `goh state strip`: the document of a row dropped ('{}') once its columns
give it back (GOH.LANDCOLS; the data plan's S7 runs it, inside its cutover).

    run(key)  every row whose document its columns render equal: (rows stripped, [why not, ...]);
              KEY gives a checkout's path as a REPO column holds it
    main(key) `goh state strip`: exit 0 when no document is left; without KEY nothing is stripped

REFUSED UNTIL THE READERS HAVE MOVED: this goh must ship schema file 0017, the contract that drops
the column. Before it, a stripped row reads as an empty record (lands_v1 and ships_v2 alone render
one). Refused too while a ship runs: its worker writes its document again.

ONE ROW AT A TIME IS DECIDED, 200 to a transaction: read, rendered and compared under the write
lock (lib/state_columns.py `differs`), then `body = '{}'` where the body is the text read. A row
that differs, was never filled, has a red cause not folded in, or is a ship that runs again by then,
stays whole and is named. Done is read from the file (a row with
no document), so a kill anywhere costs at most its 200 and a second run goes on. A ship's
`repo` takes its checkout's key in the same transaction, the one field only its document had.
Pinned by tests/test_state_strip.py.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections.abc import Callable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db_schema  # lib/, on the path from the line above
import goh_db
import state_columns

BATCH = 200
CONTRACT = 17  # 0017 drops lands.body: the code that ships it reads the columns


class StripRefused(sqlite3.OperationalError):
    """Nothing was stripped."""


def _may() -> None:
    if CONTRACT not in db_schema.migrations(goh_db.SEAM):
        raise StripRefused("this goh still reads the documents: it ships no contract "
                           f"{CONTRACT:04d}")  # fmt: skip
    state_columns.writable()
    with goh_db.known() as con:
        n = con.execute(f"SELECT count(*) FROM ships WHERE {state_columns.IN_FLIGHT}").fetchone()[0]
    if n:
        raise StripRefused(f"ships still running: {n}")


def _batch(
    table: str, after: int, repo_key: Callable[[str], str]
) -> tuple[int | None, int, list[str]]:
    """The next BATCH rows past rowid AFTER: (the last one seen or None, stripped, why not)."""
    done, kept = 0, []
    with goh_db.transaction() as con:
        busy = f"SELECT rowid FROM ships WHERE {state_columns.IN_FLIGHT}"
        wait = {"lands": ("its red cause is not folded in", state_columns.UNFOLDED),
                "ships": ("it runs", busy)}.get(table)  # fmt: skip
        held = {r for (r,) in con.execute(wait[1])} if wait else set()
        rows = list(state_columns.rendered(
            con, table, f"x.rowid IN (SELECT rowid FROM {table} WHERE rowid > ? AND body <> '{{}}' "
            f"AND json_valid(body) ORDER BY rowid LIMIT {BATCH})", (after,)))  # fmt: skip
        for rowid, key, doc, again, rest in rows:
            why = wait[0] if wait and rowid in held else "not filled" if rest is None else ""
            if not why and (keys := state_columns.differs(table, doc, again)):
                why = f"its columns do not give back {', '.join(keys)}"
            if why:
                kept.append(f"{table}: {key}: {why}")
                continue
            con.execute(f"UPDATE {table} SET body = '{{}}' WHERE rowid = ? AND body = ?",
                        (rowid, doc))  # fmt: skip
            if table == "ships" and isinstance(repo := json.loads(doc).get("repo"), str):
                con.execute("UPDATE ships SET repo = ? WHERE rowid = ?", (repo_key(repo), rowid))
            done += 1
    return (rows[-1][0] if rows else None), done, kept


def run(repo_key: Callable[[str], str]) -> tuple[int, list[str]]:
    """Every row of every table: (rows stripped, one line per row that stays whole)."""
    _may()
    total, whole = 0, []
    for t in state_columns.TABLES:
        after: int | None = -(1 << 62)
        while after is not None:
            after, done, kept = _batch(t, after, repo_key)
            total += done
            whole += kept
        with goh_db.known() as con:
            whole += [f"{t}: {k}: its document is not JSON"
                      for k in state_columns.unfilled(con, t)[1]]  # fmt: skip
    return total, whole


def main(repo_key: Callable[[str], str] | None = None) -> int:
    from tui.lib import err, ok

    try:
        _may()
        if repo_key is None:
            raise StripRefused("the cutover runs it, and hands it the codec of a repo")
        n, whole = run(repo_key)
    except (OSError, sqlite3.Error) as e:
        err(f"state strip: stopped, and may be run again: {e}")
        return 1
    ok(f"state strip: {n} documents dropped, {len(whole)} left")
    for line in whole:
        err(f"state strip: {line}")
    return 1 if whole else 0
