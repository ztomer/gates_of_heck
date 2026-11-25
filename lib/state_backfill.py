"""lib/state_backfill.py -- `goh state backfill`: every document written before its table had
columns, put into them (GOH.LANDCOLS, lib/state_sql/backfill.sql).

    run()     the statements in the file's order, each run until it changes no row; {step: rows}
    main()    `goh state backfill`: exit 0 with no row left, 1 with one left or a refusal

EACH RUN OF A STATEMENT IS ONE TRANSACTION of at most 500 rows (the two repairs: their few rows at
once), so a writer waits for one of them, never for the whole. The row's own fill trigger does the
work (REPLACE runs it), the same one a writer of older goh fires: a filled row is one it has
filled. So it may be killed anywhere and run again, and any number of times: a statement picks
only rows that still need it (`rest` NULL; a land with no red class of its own).

THE KV DOCUMENTS are lib/kv_fill.py's (GOH.KVBLOBS).

A row whose document is not JSON is never picked: it stays, named by `goh state check`.
Pinned by tests/test_state_backfill.py.
"""

from __future__ import annotations

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db_schema  # lib/, on the path from the line above
import goh_db
import kv_fill
import state_columns
import state_shape

SQL = state_columns.SQL / "backfill.sql"
STEPS = ("repaired", "folded", *state_columns.TABLES)


def steps() -> list[tuple[str, str]]:
    """(its name, its statement), in the file's order."""
    return list(zip(STEPS, db_schema.statements(SQL.read_text(encoding="utf-8")), strict=True))


@goh_db.uncut()
def run() -> dict[str, int]:
    """Every step to its end; {step: the rows it changed}."""
    state_columns.writable()
    done = {}
    for name, sql in steps():
        done[name] = 0
        with goh_db.known() as con:
            table = "lands" if name in ("repaired", "folded") else name
            there = state_columns.documents(con, table)
            there = there and (name != "folded" or state_shape.has(con, "red_causes"))
        if not there:  # its contract has run: no document is left to put anywhere
            continue
        before = None
        while True:
            with goh_db.transaction() as con:
                n = con.execute(sql).rowcount
                left = con.execute(f"SELECT count(*) FROM {name} WHERE rest IS NULL AND "
                                   "json_valid(body)").fetchone()[0] if (
                                       name in state_columns.TABLES) else 0  # fmt: skip
            done[name] += n
            if not left:
                break
            if before is not None and left >= before:
                raise sqlite3.OperationalError(f"{name}: {left} rows stay unfilled")
            before = left
    return done


@goh_db.uncut()
def main() -> int:
    from tui.lib import err, ok

    try:
        done = {**run(), **kv_fill.run()}
        with goh_db.known() as con:
            bad = {t: state_columns.unfilled(con, t) for t in state_columns.TABLES}
    except (OSError, sqlite3.Error) as e:
        err(f"state backfill: stopped, and may be run again: {e}")
        return 1
    ok("state backfill: " + ", ".join(f"{n} {k}" for k, n in done.items()))
    left = [f"{t}: {n} not filled" for t, (n, _) in bad.items() if n]
    left += [f"{t}: {k}: its document is not JSON" for t, (_, ks) in bad.items() for k in ks]
    left += kv_fill.left()
    for line in left:
        err(f"state backfill: {line}")
    return 1 if left else 0
