"""The land queue's requests are goh.db rows (GOH.LANDQDB, lib/landq_state.py).

A request was a JSON file moved by rename between `<q>/enqueuing|pending|running/` and finished by
a done record plus an unlink: a SIGKILL between two of those steps left one request in two states.
Planted here at the two such points the lander and the enqueue have, in a child process killed by
SIGKILL (no cleanup runs, as in a reboot or the OOM killer):
  * the lander, after the done record is written, before the request leaves running/: the next
    lander ran the land AGAIN (a second push of a landed request);
  * the enqueue, after it filed its request, before its pre-step marker left enqueuing/: the next
    reader found a dead enqueuer's marker and filed a red "enqueue died" record for an item whose
    request was pending.
On the database each step is one transaction, so the same kill leaves exactly one state.
"""

from __future__ import annotations

import signal
import sqlite3
import subprocess
import sys
from pathlib import Path

import _goh_db_fixture
import pytest
from conftest import REPO_ROOT, hermetic_env
from test_landq import FAKE_LOOPCTL, LANDQ, make_repo
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import goh_db
import land_records
import landq_batch
import landq_inflight
import landq_state

STATES = ("enqueuing", "pending", "running")

# The child: tools/landq.py as a module, one push request, and a SIGKILL planted at argv[1]'s point.
CHILD = """\
import importlib.util, os, signal, sys
spec = importlib.util.spec_from_file_location("landq_kill_probe", sys.argv[2])
lq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lq)
q = lq.home()
req = lq.request(sys.argv[3], sys.argv[4], "T.KILL", False, "")
def die(*_a, **_k):
    os.kill(os.getpid(), signal.SIGKILL)
if sys.argv[1] == "finish":
    lq.enqueue(req)
    write = lq.land_records.write
    def written_then_killed(*a, **k):
        write(*a, **k)
        die()
    lq.land_records.write = written_then_killed
    lq.drain_held(q)
elif sys.argv[1] == "enqueue":
    lq.landq_inflight.mark(q, "T.KILL", repo=sys.argv[3])
    lq.enqueue(req)
    die()
"""


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    db = _goh_db_fixture.use(monkeypatch, tmp_path)
    fake = tmp_path / "loopctl.py"
    fake.write_text(FAKE_LOOPCTL)
    return hermetic_env(drop_git=True, GOH_LANDQ_DIR=str(tmp_path / "landq"), GOH_DB=str(db),
                        GOH_LOOPCTL=str(fake), FAKE_LOOPCTL_LOG=str(tmp_path / "loopctl.log"),
                        HOOK_LOG=str(tmp_path / "hook.log"), GOH_LAND_TIMEOUT="60")  # fmt: skip


def held(q: Path, item: str) -> list[str]:
    """Every state ITEM's request is in: a file of a state dir, a row, a done record."""
    slug = landq_inflight.slug(item)
    found = [d for d in STATES if (q / d).is_dir()
             for f in (q / d).glob("*.json") if f.stem.partition("-")[2] == slug]  # fmt: skip
    with_rows = getattr(landq_state, "rows", None)
    for d in STATES if with_rows else ():
        found += [d for _, body in with_rows(q, d) if body.get("item") == item]
    found += ["done" for _, rec in land_records.records(q) if rec.get("item") == item]
    return sorted(found)


def killed(env: dict[str, str], plant: str, repo: Path, sha: str) -> None:
    child = subprocess.run([sys.executable, "-c", CHILD, plant, str(LANDQ), str(repo), sha],
                           env=env, capture_output=True, text=True, check=False,
                           timeout=patience(120), stdin=subprocess.DEVNULL)  # fmt: skip
    assert child.returncode == -signal.SIGKILL, child.stderr[-2000:]


def test_a_kill_after_the_land_record_leaves_the_request_done_only(tmp_path, env):
    repo, _, sha = make_repo(tmp_path, "kfin")
    killed(env, "finish", repo, sha)
    q = Path(env["GOH_LANDQ_DIR"])
    assert held(q, "T.KILL") == ["done"]  # on files: ["done", "running"], so it ran again


def test_a_kill_after_the_enqueue_filed_leaves_the_request_pending_only(tmp_path, env):
    repo, _, sha = make_repo(tmp_path, "kenq")
    killed(env, "enqueue", repo, sha)
    q = Path(env["GOH_LANDQ_DIR"])
    assert landq_inflight.live(q, "T.KILL") == []  # a reader's look: the enqueuer is gone
    # on files: ["done", "pending"], a red "enqueue died" record beside the filed request
    assert held(q, "T.KILL") == ["pending"]


def test_a_lander_reads_no_request_file_an_older_goh_left(tmp_path, monkeypatch):
    """The lander imported them at its start until the delete commit; they are `goh state retire
    landq`'s now (lib/state_retire_landq.py), so the lander's start leaves them as they are."""
    _goh_db_fixture.use(monkeypatch, tmp_path)
    q = tmp_path / "landq"
    left = []
    for state, run in (("pending", "01795000000000000001-T.A"), ("running", "02-T.C"),
                       ("enqueuing", "4242-T.D"), ("early", "03-T.E")):  # fmt: skip
        (q / state).mkdir(parents=True, exist_ok=True)
        landq_state.write(q / state / f"{run}.json", {"item": run.partition("-")[2]})
        left.append(q / state / f"{run}.json")
    landq_batch.resume(q, lambda _s: None)
    assert all(p.is_file() for p in left), "a file is no request: the lander leaves it alone"
    assert not any(landq_state.listing(q, s) for s in STATES), "no file became a row"
    assert not hasattr(landq_state, "import_files"), "the retire command is the one import"


def test_a_move_is_one_state_and_a_second_mover_loses(tmp_path, monkeypatch):
    _goh_db_fixture.use(monkeypatch, tmp_path)
    q = tmp_path / "landq"
    path = landq_state.put(q, "01795000000000000001-T.M", {"item": "T.M"})
    assert path == q / "pending" / "01795000000000000001-T.M.json"
    assert not path.exists()  # a name, never a file
    running = landq_state.move(path, "running")
    with pytest.raises(FileNotFoundError):
        landq_state.move(path, "running")  # the rename's answer: another mover took it
    assert landq_state.get(path) is None
    assert landq_state.get(running) == {"item": "T.M"}
    assert landq_state.state_of(q, "01795000000000000001-T.M") == "running"
    land_records.write(q, running.stem, {"item": "T.M", "state": "landed"}, request=running)
    assert landq_state.state_of(q, running.stem) is None
    assert land_records.get(q, running.stem) == {"item": "T.M", "state": "landed"}


def test_the_landq_v1_view_is_the_queue_other_readers_read(tmp_path, monkeypatch):
    """The interface .loop reads (handover plan (d)): named landq_v1, never queue_v1, which names
    loop.db's own queue beside it once goh.db is attached; an enqueuing row carries what its
    liveness rule needs (pid, pid_stamp, until)."""
    _goh_db_fixture.use(monkeypatch, tmp_path)
    q = tmp_path / "landq"
    landq_state.put(q, "77-T.V", {"item": "T.V", "pid": 77, "pid_stamp": "77 x", "until": 9.5},
                    "enqueuing")  # fmt: skip
    with goh_db.ro() as con:
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute("SELECT * FROM landq_v1")]
        views = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='view'")}
    assert "queue_v1" not in views
    assert [(r["run"], r["state"], r["pid"], r["pid_stamp"], r["until"]) for r in rows] == [
        ("77-T.V", "enqueuing", 77, "77 x", 9.5)]  # fmt: skip
