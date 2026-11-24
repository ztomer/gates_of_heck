"""`goh_db.py landq-export` (lib/landq_export.py): GOH.LANDQDB's revert writes the queue's rows back
as the files a lander before it read, each as that lander wrote it, and never over a file there."""

from __future__ import annotations

import json
import subprocess
import sys

import _goh_db_fixture
import land_slot
import landq_state
from conftest import REPO_ROOT
from timing_bounds import patience

GOH_DB_PY = REPO_ROOT / "lib" / "goh_db.py"


def test_the_export_writes_each_row_as_its_file_and_each_owner_beside_its_slot(
    tmp_path, monkeypatch
):
    db = _goh_db_fixture.use(monkeypatch, tmp_path)
    q = tmp_path / "landq"
    pend = {"item": "T.A", "sha": "a" * 40, "mode": "land", "slot": "tok"}
    landq_state.put(q, "01795000000000000001-T.A", pend)
    landq_state.put(q, "01795000000000000000-T.R", {"item": "T.R"}, "running")
    landq_state.put(q, "4242-T.E", {"pid": 4242, "item": "T.E", "until": 1.0}, "enqueuing")
    landq_state.put(tmp_path / "other", "01-T.X", {"item": "T.X"})  # another queue: not exported
    base = land_slot.root(q, tmp_path)
    base.mkdir(parents=True)
    land_slot._take(q, base / "0.owner", "tok")
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
    assert owner.relative_to(out) == (base / "0.owner").relative_to(q)
    assert json.loads(owner.read_text())["token"] == "tok"
    # into the queue itself: a file already there is the older truth, never overwritten
    again = subprocess.run([sys.executable, str(GOH_DB_PY), "landq-export"], env=env,
                           capture_output=True, text=True, check=False, timeout=patience(60),
                           stdin=subprocess.DEVNULL)  # fmt: skip
    assert json.loads(again.stdout)["pending"] == 0
    assert json.loads((q / "pending" / "01795000000000000001-T.A.json").read_text()) == {"older": 1}
