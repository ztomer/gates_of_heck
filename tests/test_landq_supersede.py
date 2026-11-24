"""A re-enqueue of an item supersedes the earlier enqueue's pre-step; `--cancel ITEM` on demand
(GOH.REENQUEUECANCEL, lib/landq_supersede.py).

Before it, a re-enqueue left the first enqueue's pre-step running: it held one of the two gate
slots and its checkout slot for up to 25 min, and then filed a second request for the replaced sha
(GOH.BINREACH's c36145ad, deleted by hand); with no cancel, GOH.FSCEILINGRED killed a pid and a
process group by hand. The fixture pre-step records its pid and blocks until the test releases
it, so a pre-step that was not stopped is seen to run on, and to file once released.
"""

from __future__ import annotations

import time
from pathlib import Path

import land_records
import land_slot
import landq_state
import pid_owner
import pytest
from conftest import git, hermetic_env, write
from orphan_kit import wait_for_file
from test_landq import FAKE_LOOPCTL, _land_wt, landq
from timing_bounds import GiveUp


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    """tests/test_landq.py's queue: its own dir, a fake loopctl recording each outcome."""
    fake = tmp_path / "loopctl.py"
    fake.write_text(FAKE_LOOPCTL)
    return hermetic_env(drop_git=True, GOH_LANDQ_DIR=str(tmp_path / "landq"),
                        GOH_LOOPCTL=str(fake), FAKE_LOOPCTL_LOG=str(tmp_path / "loopctl.log"),
                        GOH_LAND_TIMEOUT="60")  # fmt: skip


def _until(what: str, cond, limit: float = 60.0):
    give_up = GiveUp(limit)
    while not give_up.passed():
        if got := cond():
            return got
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {what}")


def _slow(env: dict[str, str], tmp_path: Path) -> dict[str, str]:
    """The pre-step: its shell's pid to PIDS, then it blocks until GO exists (bounded, and it
    ends with the test: never a stray)."""
    pre = f'echo $$ >> "$PIDS"; {wait_for_file(tmp_path / "go")}'
    return {**env, "GOH_LAND_GATE": "true", "GOH_LAND_PRE": pre,
            "PIDS": str(tmp_path / "pre.pids"), "GO": str(tmp_path / "go")}  # fmt: skip


def _pre_pids(env: dict[str, str]) -> list[int]:
    path = Path(env["PIDS"])
    return [int(w) for w in path.read_text().split()] if path.exists() else []


def _markers(env: dict[str, str]) -> list[dict]:
    return [m for _, m in landq_state.rows(Path(env["GOH_LANDQ_DIR"]), "enqueuing")]


def _requests(env: dict[str, str]) -> list[dict]:
    q = Path(env["GOH_LANDQ_DIR"])
    return [
        *(r for d in ("pending", "running") for _, r in landq_state.rows(q, d)),
        *(r for _, r in land_records.records(q)),
    ]


def _first_running(env: dict[str, str], wt: Path, item: str):
    """Enqueue ITEM in the background; once its pre-step runs, (its stamp, its enqueuer's)."""
    done = landq(env, "--enqueue", "--land", str(wt), "--item", item, "--no-start")
    assert done.returncode == 0, done.stdout + done.stderr
    pre = pid_owner.stamp(_until("the first pre-step", lambda: _pre_pids(env))[0])
    enqueuer = pid_owner.stamp(int(_until("its marker", lambda: _markers(env))[0]["pid"]))
    assert pre is not None and enqueuer is not None
    return pre, enqueuer


def _second(wt: Path) -> str:
    write(wt, "g.txt", "second\n")
    git(wt, "add", "g.txt")
    git(wt, "commit", "-q", "--no-verify", "-m", "second")
    return git(wt, "rev-parse", "HEAD").strip()


@pytest.mark.load_sensitive
def test_a_second_enqueue_stops_the_first_enqueues_pre_step(repo, tmp_path, env):
    wt, _ = _land_wt(repo, tmp_path)
    env = _slow(env, tmp_path)
    pre, enqueuer = _first_running(env, wt, "T.RE")
    _second(wt)
    done = landq(env, "--enqueue", "--land", str(wt), "--item", "T.RE", "--no-start")
    out = done.stdout + done.stderr
    try:  # stopped by the second enqueue, never by the release below
        assert done.returncode == 0, out
        _until("the first pre-step stopped", lambda: pid_owner.gone(pre), 15)
        _until("the first enqueuer stopped", lambda: pid_owner.gone(enqueuer), 15)
    finally:
        Path(env["GO"]).touch()
    _until("the second enqueue's end", lambda: not _markers(env))  # no process outlives the test


@pytest.mark.load_sensitive
def test_a_second_enqueue_files_no_request_for_the_replaced_sha(repo, tmp_path, env):
    wt, _ = _land_wt(repo, tmp_path)
    env = _slow(env, tmp_path)
    _, enqueuer = _first_running(env, wt, "T.RS")
    second = _second(wt)
    done = landq(env, "--enqueue", "--land", str(wt), "--item", "T.RS", "--no-start")
    assert done.returncode == 0, done.stdout + done.stderr
    Path(env["GO"]).touch()  # a first pre-step still running would now pass and file
    _until("the second request", lambda: [r for r in _requests(env) if r["sha"] == second])
    _until("the first enqueuer ended", lambda: pid_owner.gone(enqueuer))
    assert [r["sha"] for r in _requests(env)] == [second]


@pytest.mark.load_sensitive
def test_cancel_stops_the_pre_step_frees_its_slot_and_files_nothing(repo, tmp_path, env):
    wt, _ = _land_wt(repo, tmp_path)
    env = _slow(env, tmp_path)
    pre, enqueuer = _first_running(env, wt, "T.CX")
    owners = land_slot.owners(Path(env["GOH_LANDQ_DIR"]))  # goh.db rows (GOH.LANDQDB)
    assert len(owners) == 1, owners
    done = landq(env, "--cancel", "T.CX")
    out = done.stdout + done.stderr
    try:
        assert done.returncode == 0, out
        assert "T.CX" in out and "cancel" in out, out
        assert pid_owner.gone(pre) and pid_owner.gone(enqueuer)
        assert not land_slot.owners(Path(env["GOH_LANDQ_DIR"]))  # its checkout slot free again
    finally:
        Path(env["GO"]).touch()
    assert _requests(env) == []
    assert _markers(env) == []


def test_cancel_of_an_item_with_no_enqueue_says_so(env):
    done = landq(env, "--cancel", "T.NONE")
    out = done.stdout + done.stderr
    assert done.returncode == 3 and "T.NONE" in out and "nothing" in out, out
