"""An enqueue in its pre-step is visible to the lander and to `--wait` (lib/landq_inflight.py).

GOH.IDLEYIELDPRE: the idle full suite yielded only to a FILED request, and `goh land --enqueue
--land` files nothing until its 8-10 min pre-step is green: on 2026-11-17 at 22:59 the idle suite
kept running beside three lanes' pre-steps at load 49-57. GOH.WAITUNFILED: `--wait ID` started
beside its own enqueue exited 2, "no request", while that enqueue was still in its pre-step.
Each pre-step here blocks on a file the test writes: an event, not a clock.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import _goh_db_fixture
import pytest
from conftest import REPO_ROOT, commit_all, hermetic_env, write
from orphan_kit import wait_for_file, wait_for_file_py
from test_landq import FAKE_LOOPCTL, LANDQ, _land_wt, landq, recorded
from timing_bounds import GiveUp, assert_sooner, patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import idle_suite
import land_records
import landq_inflight
import landq_state


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    """tests/test_landq.py's queue: its own dir, a fake loopctl recording each outcome."""
    fake = tmp_path / "loopctl.py"
    fake.write_text(FAKE_LOOPCTL)
    return hermetic_env(drop_git=True, GOH_LANDQ_DIR=str(tmp_path / "landq"),
                        GOH_LOOPCTL=str(fake), FAKE_LOOPCTL_LOG=str(tmp_path / "loopctl.log"),
                        GOH_LAND_TIMEOUT="60")  # fmt: skip


def _blocking_pre(tmp_path: Path) -> tuple[str, Path, Path]:
    """A pre-step that says it started, then waits for GO."""
    started, go = tmp_path / "pre-started", tmp_path / "pre-go"
    return f"touch {started}; {wait_for_file(go)}", started, go


def _until(cond, what: str, seconds: float = 60) -> None:
    give_up = GiveUp(seconds)
    while not cond():
        assert not give_up.passed(), f"never: {what}"
        time.sleep(0.05)


def _enqueue(env: dict[str, str], wt: Path, item: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, str(LANDQ), "--enqueue", "--foreground", "--land", str(wt), "--item", item,
         "--no-start"],
        env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True,
    )  # fmt: skip


@pytest.mark.load_sensitive  # processes, a thread, waits bounded by patience
def test_an_enqueue_pre_step_stops_the_idle_suite_within_its_poll(repo, tmp_path, env, monkeypatch):
    for k in (*idle_suite.IN_SUITE, idle_suite.OFF_ENV):
        monkeypatch.delenv(k, raising=False)
    write(repo, ".gatesrc", "GOH_SHIP_FULL_SUITE=on\n")
    commit_all(repo, "declares a full suite")
    wt, _ = _land_wt(repo, tmp_path)
    pre, started, go = _blocking_pre(tmp_path)
    env = {**env, "GOH_LAND_GATE": "true", "GOH_LAND_PRE": pre}
    q = Path(env["GOH_LANDQ_DIR"])
    q.mkdir(parents=True)
    suite_up, release = tmp_path / "suite-up", tmp_path / "suite-release"
    slow = [sys.executable, "-c",
            (f"open({str(suite_up)!r}, 'w').close()\n" + wait_for_file_py(release))]  # fmt: skip
    over, procs = threading.Event(), []
    stamps: dict[str, float] = {}

    def arrive() -> None:
        try:
            _until(lambda: suite_up.exists() or over.is_set(), "the idle suite started")
            procs.append(_enqueue(env, wt, "T.Y"))
            _until(lambda: started.exists() or over.is_set(), "the pre-step started")
            stamps["pre"] = time.monotonic()
            give_up = GiveUp(60)  # today's code: the suite never stops; end it, red
            while not over.is_set() and not give_up.passed():
                time.sleep(0.05)
        finally:
            release.touch()  # the stand-in suite ends however this thread does

    t = threading.Thread(target=arrive)
    t.start()
    try:
        outcome = idle_suite.run(q, repo, argv=slow, poll=0.05)
        stopped = time.monotonic()
    finally:
        over.set()
        t.join()
        go.touch()
        for p in procs:
            p.communicate(timeout=patience(120))
    assert outcome == "deferred", outcome  # stopped for the pre-step, not judged
    assert_sooner(stopped - stamps["pre"], 5, 600, "an enqueue's pre-step stops the idle suite")
    assert procs[0].returncode == 0
    assert len(landq_state.listing(q, "pending")) == 1
    assert landq_inflight.live(q) == []  # the enqueue exited: its marker went with it


@pytest.mark.load_sensitive  # processes, waits bounded by patience
def test_wait_started_during_the_pre_step_returns_the_land_outcome(repo, tmp_path, env):
    wt, tip = _land_wt(repo, tmp_path)
    pre, started, go = _blocking_pre(tmp_path)
    env = {**env, "GOH_LAND_GATE": "true", "GOH_LAND_PRE": pre}
    enq = _enqueue(env, wt, "T.U")
    waiter = None
    try:
        _until(started.exists, "the pre-step started")
        waiter = subprocess.Popen(
            [sys.executable, str(LANDQ), "--wait", "T.U", "--timeout", "300"], env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )  # fmt: skip
        first = ""
        for line in waiter.stdout:  # type: ignore[union-attr]  # until it says it waits, or ends
            first += line
            if "pre-step" in line:
                break
        assert "pre-step" in first and "T.U" in first, first  # it waits; it did not exit 2
    finally:
        go.touch()
        enq.communicate(timeout=patience(120))
    out, _ = waiter.communicate(timeout=patience(300))
    assert waiter.returncode == 0, first + out
    assert "landed" in out and tip[:8] in out, out
    assert recorded(env)["T.U"]["state"] == "landed"
    assert landq(env, "--wait", "T.NOBODY").returncode == 2  # nothing running nor filed: 2


def test_a_dead_enqueues_marker_counts_as_gone(tmp_path):
    q = tmp_path / "landq"
    dead = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                          capture_output=True, text=True, check=True,
                          stdin=subprocess.DEVNULL)  # fmt: skip
    path = landq_inflight.mark(q, "T.D", pid=int(dead.stdout))
    mine = landq_inflight.mark(q, "T/E")
    assert [m["item"] for m in landq_inflight.live(q)] == ["T/E"]
    assert landq_state.get(path) is None  # a reader removes it
    assert landq_inflight.live(q, "T/E") and not landq_inflight.live(q, "T.D")
    landq_inflight.clear(mine)
    assert landq_inflight.live(q) == []


def test_a_dead_enqueue_leaves_a_red_record_not_nothing(tmp_path):
    """GOH.ENQUEUEVANISH: an enqueuer killed in its pre-step (SIGKILL runs no cleanup) used to leave
    no marker, no request and no done record: its land vanished. The reader that finds it dead
    files a red record naming the death, so `--wait`, the watch and reconcile all see it."""
    q = tmp_path / "landq"
    dead = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                          capture_output=True, text=True, check=True,
                          stdin=subprocess.DEVNULL)  # fmt: skip
    landq_inflight.mark(q, "T.V", pid=int(dead.stdout), repo="/r")
    assert landq_inflight.live(q) == []
    records = sorted((q / "done").glob("*-T.V.json"))
    assert len(records) == 1, records
    rec = landq_state.read(records[0])
    assert (rec["item"], rec["repo"], rec["state"]) == ("T.V", "/r", "red"), rec
    assert "died in its pre-step" in rec["why"] and dead.stdout.strip() in rec["why"], rec
    assert landq_inflight.live(q) == [] and len(list((q / "done").glob("*.json"))) == 1  # once


@pytest.mark.load_sensitive
def test_a_dead_enqueues_record_goes_through_the_land_records_door(tmp_path, monkeypatch):
    """GOH.STATEDB S1: the vanished enqueue's red record is a goh.db row like every land record,
    and a record of the item filed after the marker counts as settled when only its row is left
    (a done/ file pruned or never kept): the death is filed once, never over a later record."""
    _goh_db_fixture.use(monkeypatch, tmp_path)
    q = tmp_path / "landq"
    dead = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                          capture_output=True, text=True, check=True,
                          stdin=subprocess.DEVNULL)  # fmt: skip
    landq_inflight.mark(q, "T.V", pid=int(dead.stdout), repo="/r")
    landq_inflight.mark(q, "T.W", pid=int(dead.stdout), repo="/r")
    settled = f"{time.time_ns():020d}-T.W"
    _goh_db_fixture.put(q, settled, {"item": "T.W", "state": "landed"})  # stamped after the mark
    land_records.file(q, settled).unlink()  # the row alone holds it
    assert landq_inflight.live(q) == []
    filed = sorted(f.name.rpartition("-")[2] for f in land_records.file(q).glob("*.json"))
    assert filed == ["T.V.json"], filed  # T.W settled by its row: no death filed over it
    for f in land_records.file(q).glob("*.json"):
        f.unlink()  # the rows alone: every record below is read from goh.db
    got = land_records.records(q)
    assert [r.rpartition("-")[2] for r, _ in got] == ["T.W", "T.V"], got
    rec = got[1][1]
    assert (rec["item"], rec["repo"], rec["state"]) == ("T.V", "/r", "red"), rec
    assert "died in its pre-step" in rec["why"], rec


def test_a_superseded_enqueue_leaves_no_record(tmp_path):
    """A re-enqueue or --cancel stops the older enqueue on purpose: that is no death to report."""
    q = tmp_path / "landq"
    dead = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                          capture_output=True, text=True, check=True,
                          stdin=subprocess.DEVNULL)  # fmt: skip
    path = landq_inflight.mark(q, "T.S", pid=int(dead.stdout))
    assert landq_inflight.supersede_mark(path)
    assert landq_inflight.live(q) == []
    assert not list((q / "done").glob("*.json"))
