"""`goh_db.py landq-export` (lib/landq_export.py): GOH.LANDQDB's revert writes the queue's rows back
as the files a lander before it read, each as that lander wrote it, and never over a file there."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import _goh_db_fixture
import goh_db
from conftest import REPO_ROOT
from timing_bounds import patience

GOH_DB_PY = REPO_ROOT / "lib" / "goh_db.py"


def test_the_export_writes_each_row_as_its_file_and_each_owner_beside_its_slot(
    tmp_path, monkeypatch
):
    db = _goh_db_fixture.use(monkeypatch, tmp_path)
    q = tmp_path / "landq"
    pend = {"item": "T.A", "sha": "a" * 40, "mode": "land", "slot": "tok"}
    key = os.path.realpath(q)
    base = Path(key) / "slots" / "repo-0123456789"
    # rows put by SQL alone: the revert this serves no longer has the code that writes them
    with goh_db.transaction() as con:
        for queue, run, state, body in (
            (key, "01795000000000000001-T.A", "pending", pend),
            (key, "01795000000000000000-T.R", "running", {"item": "T.R"}),
            (key, "4242-T.E", "enqueuing", {"pid": 4242, "item": "T.E", "until": 1.0}),
            (os.path.realpath(tmp_path / "other"), "01-T.X", "pending", {"item": "T.X"}),
        ):
            con.execute(
                "INSERT INTO requests (queue, run, state, filed_at, body) VALUES (?, ?, ?, 0, ?)",
                (queue, run, state, json.dumps(body)),
            )
        con.execute("INSERT INTO slot_owners (repo_key, n, token, pid, pid_stamp, at) "
                    "VALUES (?, 0, 'tok', 1, NULL, 0)", (str(base),))  # fmt: skip
    (q / "pending").mkdir(parents=True)
    (q / "pending" / "01795000000000000001-T.A.json").write_text('{"older": 1}\n')
    out = tmp_path / "out"
    env = {"GOH_DB": str(db), "GOH_LANDQ_DIR": str(q), "PATH": "/usr/bin:/bin"}
    done = subprocess.run([sys.executable, str(GOH_DB_PY), "landq-export", "--to", str(out)],
                          env=env, capture_output=True, text=True, check=False,
                          timeout=patience(60), stdin=subprocess.DEVNULL)  # fmt: skip
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == {"enqueuing": 1, "pending": 1, "running": 1, "slots": 1}
    files = {str(p.relative_to(out)): json.loads(p.read_text()) for p in out.rglob("*.json")}
    assert files == {
        "pending/01795000000000000001-T.A.json": pend,
        "running/01795000000000000000-T.R.json": {"item": "T.R"},
        "enqueuing/4242-T.E.json": {"pid": 4242, "item": "T.E", "until": 1.0},
    }
    (owner,) = out.rglob("*.owner")
    assert owner.relative_to(out) == (base / "0.owner").relative_to(key)
    assert json.loads(owner.read_text())["token"] == "tok"
    # into the queue itself: a file already there is the older truth, never overwritten
    again = subprocess.run([sys.executable, str(GOH_DB_PY), "landq-export"], env=env,
                           capture_output=True, text=True, check=False, timeout=patience(60),
                           stdin=subprocess.DEVNULL)  # fmt: skip
    assert json.loads(again.stdout)["pending"] == 0
    assert json.loads((q / "pending" / "01795000000000000001-T.A.json").read_text()) == {"older": 1}
