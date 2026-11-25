"""lib/run_ends.py (GOH.TIMEACCOUNT, goh.db 0024): every start has an end row, in memory. The
planted runs: one killed (a real child, SIGKILLed: its open row is swept `stopped`), one refused
(a check's exit 1), one red (a pre-step's Red, by cause), one carried (a deadline)."""

from __future__ import annotations

import signal
import subprocess
import sys

import check_jobs
import pytest
import run_ends
from _kv_fixture import Held
from timing_bounds import patience


@pytest.fixture
def held(monkeypatch) -> Held:
    return Held().lay(monkeypatch)


def ended(held: Held) -> dict[str, tuple]:
    return {run: (outcome, cause) for run, outcome, cause in held.goh.execute(
        "SELECT run, outcome, cause FROM run_ends")}  # fmt: skip


class Red(Exception):
    pass


@pytest.mark.load_sensitive
def test_a_killed_start_is_swept_stopped_and_a_live_one_stays_open(held) -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], stdin=subprocess.DEVNULL
    )
    try:
        assert run_ends.begin("prestep", "killed", pid=child.pid)
        assert run_ends.begin("land", "alive")  # this process: alive
        assert run_ends.sweep() == 0  # the child lives: nothing is closed
        child.send_signal(signal.SIGKILL)
        child.wait(timeout=patience(10))
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=patience(10))
    assert run_ends.sweep() == 1
    assert ended(held) == {"killed": ("stopped", "died"), "alive": (None, None)}


def test_a_refused_check_and_a_killed_one_each_end_their_start(held) -> None:
    for pid, rc in ((11, 1), (12, 137), (13, 0), (14, 2)):
        record = {"door": "enqueue", "repo": "r", "sha": "s" * 40, "epoch": 5.0, "pid": pid}
        run_ends.begin("check", check_jobs.run_of(record), "r", pid=pid)
        check_jobs.note(record, rc)
    assert ended(held) == {"11-5.000": ("refused", "exit 1"), "12-5.000": ("stopped", "exit 137"),
                           "13-5.000": ("finished", None),
        "14-5.000": ("red", "exit 2"),}  # fmt: skip


def test_a_red_pre_step_is_red_by_cause_with_its_phases_and_tests(held) -> None:
    ran, did = {"wait_s": 3.0, "run_s": 40.0, "cpu_s": 90.0}, {"ran": "differs in tree"}
    with (
        pytest.raises(Red),
        run_ends.prestep("p1", {"repo": "/x/r", "item": "I"}, ran, {}, did, Red),
    ):
        raise Red("the slot's rebase: conflicts with main")
    assert ended(held) == {"p1": ("red", "conflict")}
    assert held.goh.execute(
        "SELECT phase, seconds, cpu_s FROM run_phases ORDER BY ord"
    ).fetchall() == [("wait", 3.0, None), ("run", 40.0, 90.0)]
    assert held.goh.execute(
        "SELECT repo, item, tests_reused, refused_by, wall_s >= 0 FROM run_ends"
    ).fetchone() == ("r", "I", 0, "differs in tree", 1)


def test_a_deadline_is_carried_an_exit_stopped_and_green_finished(held) -> None:
    with run_ends.prestep("p2", {"repo": "r"}, {}, {"why": "no verdict after 9 s"}, {}, Red):
        pass
    with pytest.raises(SystemExit), run_ends.account("land", "l1"):
        raise SystemExit(3)  # a superseded enqueue: a stop, never a verdict
    with run_ends.prestep("p3", {"repo": "r"}, {}, {}, {"took": "abc", "tests": 7}, Red):
        pass
    assert ended(held) == {"p2": ("carried", "no verdict after 9 s"),
                           "l1": ("stopped", "exit 3"), "p3": ("finished", None)}  # fmt: skip
    assert held.goh.execute("SELECT tests_reused FROM run_ends WHERE run = 'p3'").fetchone() == (7,)


def test_the_first_end_stays_and_a_bad_word_is_refused(held) -> None:
    run_ends.begin("idle", "i")
    run_ends.end("idle", "i", "red", "first")
    run_ends.end("idle", "i", "finished")
    assert ended(held) == {"i": ("red", "first")}
    with pytest.raises(ValueError, match="none of"):
        run_ends.end("idle", "i", "lost")
    with pytest.raises(ValueError, match="none of"):
        run_ends.begin("nope", "i")
    with pytest.raises(ValueError, match="no such column"):
        run_ends.end("idle", "i", "red", nope=1)
    assert [r["run"] for r in run_ends.rows()] == ["i"]


def test_no_database_is_one_line_never_a_raise(monkeypatch, capsys) -> None:
    Held(23).lay(monkeypatch)  # a goh.db from before 0024: no table
    assert not run_ends.begin("land", "x")
    assert not run_ends.end("land", "x", "red")
    assert not run_ends.phase("land", "x", "run", 1.0)
    assert run_ends.sweep() == 0 and run_ends.rows() == [] and run_ends.phases() == []
    assert "not recorded" in capsys.readouterr().err
