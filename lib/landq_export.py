"""lib/landq_export.py -- the land queue's rows back to the files a lander before GOH.LANDQDB read.

    goh_db.py landq-export [--queue Q] [--to DIR]

GOH.LANDQDB's way back (its revert, handover plan (b) L2): every request row of queue Q (goh.db's
`requests`, 0007) becomes `<DIR>/<state>/<run>.json` and every slot owner row under it
`<DIR>/slots/<repo>/<n>.owner`, each written as that lander wrote it (one rename, so no reader
sees half a file). Q defaults to GOH_LANDQ_DIR, else ~/.local/state/goh/landq; DIR defaults to Q.
A file already there is left as it is (the files are the older truth). The rows stay: a goh that
reads them again finds them where they were. This module reads goh.db's tables by SQL only, so a
revert of the code that writes them keeps it working; it needs migration 0007 in the schema dir,
which the revert keeps too (a goh.db holding a migration its code does not ship is read-only).
Pinned by tests/test_landq_export.py.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import goh_db  # lib/, on the path from the line above

STATES = ("enqueuing", "pending", "running")


def qdir() -> Path:
    return Path(os.environ.get("GOH_LANDQ_DIR") or Path.home() / ".local/state/goh/landq")


def _put(path: Path, data: dict, indent: int | None) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, indent=indent) + "\n")
    tmp.replace(path)
    return True


def export(q: Path, to: Path) -> dict[str, int]:
    """Queue Q's rows as files under TO; {state or "slots": files written}."""
    key, got = os.path.realpath(q), {}
    try:
        with goh_db.ro() as con:
            reqs = con.execute("SELECT state, run, body FROM requests WHERE queue = ? ORDER BY run",
                               (key,)).fetchall()  # fmt: skip
            under = os.path.join(key, "slots") + os.sep + "%"
            slots = con.execute("SELECT repo_key, n, token, pid, pid_stamp FROM slot_owners "
                                "WHERE repo_key LIKE ?", (under,)).fetchall()  # fmt: skip
    except sqlite3.OperationalError as e:  # no 0007 tables: nothing was ever queued as rows
        if "no such table" not in str(e):
            raise
        return got
    for state, run, body in reqs:
        if state in STATES and isinstance(rec := json.loads(body), dict):
            indent = None if state == "enqueuing" else 1
            got[state] = got.get(state, 0) + _put(to / state / f"{run}.json", rec, indent)
    for repo_key, n, token, pid, stamp in slots:
        rel = Path(repo_key).relative_to(Path(key))
        owner = {"pid": pid, "pid_stamp": stamp, "token": token}
        got["slots"] = got.get("slots", 0) + _put(to / rel / f"{n}.owner", owner, None)
    return got


def main(argv: list[str]) -> int:
    q, to = qdir(), None
    while argv:
        flag, value, argv = argv[0], argv[1] if len(argv) > 1 else "", argv[2:]
        if flag not in ("--queue", "--to") or not value:
            print("usage: goh_db.py landq-export [--queue Q] [--to DIR]", file=sys.stderr)
            return 2
        if flag == "--queue":
            q = Path(value)
        else:
            to = Path(value)
    print(json.dumps(export(q, to or q)))
    return 0
