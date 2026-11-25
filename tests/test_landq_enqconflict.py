"""An enqueue that conflicts with one ahead of it, and `--wait` at an enqueue's edges
(GOH.ENQCONFLICT, lib/landq_early.py, lib/landq_background.py, lib/landq_look.py).

In 7 days to 2026-11-25, 22 lands ran a whole pre-step (23,719 s) and then went red on a conflict;
replayed, 19 of them conflicted at their enqueue with a land already ahead of them: in its
pre-step (5) or queued. And `goh land --wait ITEM` could answer with the PREVIOUS run's record: its
enqueue had not marked itself within the start's 60 s, or was refused before its request existed,
or was cancelled. No git repository here: the replay is a table of which tips conflict.
"""

from __future__ import annotations

import argparse
import itertools
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import REPO_ROOT
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(REPO_ROOT / "lib"))
sys.path.insert(0, str(REPO_ROOT))
import land_check
import land_records
import landq
import landq_background
import landq_inflight
import landq_look
import landq_request
import landq_state

from lib import landq_early  # the module land_check.at_enqueue imports

MAIN, MINE, THEIRS = "0" * 40, "b" * 40, "a" * 40
LATER = 4102444800.0  # 2100-01-01
RUNS = itertools.count(100)  # request names, in the order they are filed


@pytest.fixture(autouse=True)
def _in_process(monkeypatch):
    """No marker of a test is this worker's; its exit clears none; no poll is slept out."""
    monkeypatch.setattr(landq_inflight, "_MINE", [])
    monkeypatch.setattr(landq_inflight.atexit, "register", lambda *_a: None)
    monkeypatch.setattr(landq_look, "poll", 0.01)


def _ahead(q: Path, state: str, item: str = "T.AHEAD", repo: str = "/r") -> Path:
    body = {"item": item, "repo": repo, "sha": THEIRS, "mode": "land"}
    pid = os.getppid()  # another live process: this one's own marker is never ahead of it
    run = f"{pid}-{item}" if state == "enqueuing" else f"0000000001-{item}"
    if state == "enqueuing":
        stamp = landq_inflight.pid_owner.stamp_line(pid)
        body |= {"pid": pid, "pid_stamp": stamp, "at": 1.0, "until": LATER}
    return landq_state.put(q, run, body, state)


def _enqueued(
    q: Path, monkeypatch, no_pre: str | None = None, clash=frozenset((THEIRS, MINE))
) -> dict:
    """One enqueue of MINE up to its pre-step, on a replay where the two of CLASH alone conflict:
    {ran: its guards started, red: the outcome recorded, log: its text}."""
    got: dict = {"ran": False}

    def replay(_repo, base: str, _upstream: str, tip: str):
        assert base, "nothing is replayed onto a land that does not stack"
        return ("", ["shared.py"]) if {base, tip} == clash else (tip, [])

    def git(_repo, *args: str, **_k) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(args, 0, MAIN + "\n", "")

    def guards(*_a, **_k) -> list[str]:
        got["ran"] = True
        return []

    def record(path: Path, outcome: dict, named: str) -> None:
        got.update(red=outcome, item=named, log=Path(outcome["log"]).read_text())
        land_records.write(q, path.stem, {**outcome, "item": named}, request=path)

    monkeypatch.setattr(landq_early, "replay", replay)
    monkeypatch.setattr(landq_early, "_git", git)
    monkeypatch.setattr(land_check, "run", guards)
    monkeypatch.setattr(land_check.main_red, "notice", lambda *_a: None)
    monkeypatch.setattr(land_check.cov_risk, "say", lambda *_a: None)
    req = {"item": "T.MINE", "repo": "/r", "sha": MINE, "mode": "land", "worktree": "/w"}
    run = f"{next(RUNS):020d}-T.MINE"
    got["why"] = land_check.at_enqueue(
        q, req, lambda r, into: landq_state.put(q, run, r, into), record, lambda _s: None, no_pre
    )
    return got


@pytest.mark.parametrize("state", ["enqueuing", "pending", "running"])
def test_a_conflict_with_a_land_ahead_is_told_before_the_guards_and_the_pre_step(
    tmp_path, monkeypatch, state
):
    _ahead(tmp_path, state)
    got = _enqueued(tmp_path, monkeypatch)
    assert not got["ran"] and got["why"], got
    assert got["red"]["why"].startswith("conflict: ") and "T.AHEAD" in got["red"]["why"], got
    assert got["red"]["early"] is True and got["item"] == "T.MINE", got
    assert "shared.py" in got["log"] and "goh land --wait T.AHEAD" in got["log"], got


def test_what_is_not_ahead_of_an_enqueue_is_no_conflict(tmp_path, monkeypatch):
    """Its own item's, another repo's, a superseded and a dead enqueue's marker: none is told."""
    _ahead(tmp_path, "pending", item="T.MINE")
    _ahead(tmp_path, "running", item="T.OTHER", repo="/elsewhere")
    _ahead(tmp_path, "enqueuing", item="T.ABROAD", repo="/elsewhere")
    gone = _ahead(tmp_path, "enqueuing", item="T.GONE")
    assert landq_inflight.supersede_mark(gone)
    assert _enqueued(tmp_path, monkeypatch)["ran"]


def test_the_same_tip_enqueued_again_after_it_was_told_runs(tmp_path, monkeypatch):
    """A land ahead may go red (9 of the 40 replayed): told once, the lane's second enqueue of the
    same tip against the same one ahead is its choice, and runs its pre-step."""
    _ahead(tmp_path, "pending")
    assert not _enqueued(tmp_path, monkeypatch)["ran"]
    assert _enqueued(tmp_path, monkeypatch)["ran"]


def test_a_no_pre_enqueue_has_no_pre_step_to_save(tmp_path, monkeypatch):
    _ahead(tmp_path, "pending")
    assert _enqueued(tmp_path, monkeypatch, no_pre="forced in: a load-judged red")["ran"]


def test_the_marker_names_the_tip_before_the_guards_run(tmp_path, monkeypatch):
    """What a later enqueue reads: the early mark carried no sha until the pre-step began."""
    seen: list[dict] = []
    req = {"item": "T.M", "repo": "/r", "sha": MINE, "mode": "land", "worktree": "/w"}
    monkeypatch.setattr(landq, "qdir", lambda: tmp_path)
    monkeypatch.setattr(landq, "land_request", lambda *_a: req)

    def judged(*_a) -> str:
        seen.extend(landq_inflight.live(tmp_path, "T.M"))
        return "red"

    monkeypatch.setattr(land_check, "at_enqueue", judged)
    assert landq.main(["--enqueue", "--land", "/w", "--item", "T.M", "--foreground"]) == 1
    assert [m.get("sha") for m in seen] == [MINE], seen


def _args(**over) -> argparse.Namespace:
    base = {"enqueue": [], "ship": False, "land": "/w", "item": "T.W", "detached": True}
    return argparse.Namespace(**{**base, **over})


def _old_red(q: Path) -> None:
    land_records.write(q, f"{1:020d}-T.W", {"item": "T.W", "state": "red", "sha": "c" * 40,
                                             "why": "try 1"})  # fmt: skip


def _waited(q: Path, capsys, bound: float = 0.0) -> tuple[int, str]:
    code = landq_look.wait(q, "T.W", bound, lambda: None)
    got = capsys.readouterr()
    return code, got.out + got.err


def _sleeps(monkeypatch, sleep) -> None:
    """The wait's own clock: it ticks 10 ms a look, and SLEEP is its poll."""
    ticks = itertools.count(1.0, 0.01)
    clock = SimpleNamespace(monotonic=lambda: next(ticks), sleep=sleep)
    monkeypatch.setattr(landq_look, "time", clock)


def _start(q: Path, monkeypatch, polled: int | None) -> int:
    """`start` with a child that never marks itself: POLLED is its exit, None still running."""

    class Child:
        pid, returncode = os.getpid(), polled

        def __init__(self, *_a, **_k) -> None:
            pass

        def poll(self) -> int | None:
            return polled

    fake = SimpleNamespace(Popen=Child, DEVNULL=-3, STDOUT=-2)
    monkeypatch.setattr(landq_background, "subprocess", fake)
    ticks = itertools.chain([0.0, 0.0], itertools.repeat(landq_background.MARK_WAIT_S + 1.0))
    clock = SimpleNamespace(monotonic=lambda: next(ticks), time_ns=lambda: 1, sleep=lambda _s: None)
    monkeypatch.setattr(landq_background, "time", clock)  # one look, then past its wait
    req = {"item": "T.W", "repo": "/r", "sha": MINE, "mode": "land", "worktree": "/w"}
    monkeypatch.setattr(landq_request, "land_request", lambda *_a: req)
    return landq_background.start(_args(detached=False), ["--enqueue"], q, Path("landq.py"))


def test_a_start_that_gives_up_on_its_marker_exits_non_zero_with_the_reason(
    tmp_path, monkeypatch, capsys
):
    code = _start(tmp_path, monkeypatch, None)
    out = "".join(capsys.readouterr())
    assert code == 75 and str(os.getpid()) in out and "goh land --wait T.W" in out, (code, out)


def test_a_wait_after_a_slow_start_waits_for_that_enqueue_never_the_last_red(
    tmp_path, monkeypatch, capsys
):
    _old_red(tmp_path)
    _start(tmp_path, monkeypatch, None)
    capsys.readouterr()
    code, out = _waited(tmp_path, capsys)
    assert code == 3 and "pre-step running" in out and "try 1" not in out, (code, out)


def test_a_child_refused_before_its_request_exists_leaves_a_red_record(
    tmp_path, monkeypatch, capsys
):
    _old_red(tmp_path / "landq")

    def refuse(*_a):
        raise landq_request.Refused("no checkout of bbbbbbbb for the land: disk full")

    q = tmp_path / "landq"
    monkeypatch.setattr(landq, "qdir", lambda: q)
    monkeypatch.setattr(landq, "land_request", refuse)
    monkeypatch.setattr(landq, "loopctl", lambda: tmp_path / "none")
    argv = ["--enqueue", "--land", str(tmp_path), "--item", "T.W", "--detached"]
    assert landq.main(argv) == 2
    capsys.readouterr()
    code, out = _waited(q, capsys)
    assert code == 1 and "disk full" in out and "try 1" not in out, (code, out)


def test_a_wait_whose_enqueue_ends_filing_nothing_never_answers_with_the_last_red(
    tmp_path, monkeypatch, capsys
):
    """Cancelled, or superseded with no successor: exit 3 and the reason, not the run before it."""
    _old_red(tmp_path)
    marker = landq_inflight.mark(tmp_path, "T.W")
    _sleeps(monkeypatch, lambda _s: landq_inflight.clear(marker))
    monkeypatch.setattr(landq_look, "GONE_S", 0.0, raising=False)
    code, out = _waited(tmp_path, capsys, bound=patience(60))
    assert code == 3 and "nothing" in out and "try 1" not in out, (code, out)


@pytest.mark.parametrize(
    "last",
    [
        {"sha": "e" * 40, "why": f"conflict: does not stack on T.AHEAD @ {THEIRS[:8]}, queued"},
        {"sha": MINE, "why": "conflict: does not stack on T.AHEAD @ eeeeeeee, queued"},
        {"sha": MINE, "why": f"conflict: did not stack on {MAIN[:8]}"},
    ],
)
def test_told_of_another_tip_either_side_is_not_told_of_this_one(tmp_path, monkeypatch, last):
    _ahead(tmp_path, "pending")
    run = f"{next(RUNS):020d}-T.MINE"
    land_records.write(tmp_path, run, {"item": "T.MINE", "state": "red", **last})
    assert not _enqueued(tmp_path, monkeypatch)["ran"]


def test_a_land_ahead_that_does_not_stack_on_main_is_none(tmp_path, monkeypatch):
    _ahead(tmp_path, "pending")
    assert _enqueued(tmp_path, monkeypatch, clash={MAIN, THEIRS})["ran"]


def test_of_two_enqueues_at_once_only_the_later_has_one_ahead(tmp_path):
    other = _ahead(tmp_path, "enqueuing")
    body = landq_state.request(other)
    landq_inflight.mark_request(tmp_path, {"item": "T.MINE", "repo": "/r", "sha": MINE})
    assert [m["sha"] for m in landq_inflight.ahead(tmp_path, "/r", "T.MINE")] == [THEIRS]
    landq_state.update(other, {**body, "at": LATER})  # marked after this one
    assert landq_inflight.ahead(tmp_path, "/r", "T.MINE") == []


def test_the_starters_mark_never_replaces_the_childs_own(tmp_path):
    child = landq_inflight.mark(tmp_path, "T.W", repo="/r", slot={"sha": MINE})
    assert landq_inflight.mark_child(tmp_path, "T.W", os.getpid(), "/r") == 0.0
    assert landq_state.request(child)["sha"] == MINE


def test_an_enqueue_that_crashes_leaves_its_marker_for_the_reader_that_records_it(
    tmp_path, monkeypatch
):
    """An unhandled exception is no refusal and files nothing: its marker, cleared at exit, left a
    `--wait` the attempt before it. Python says so at exit, in `sys.last_exc`."""
    said = "import atexit,sys;atexit.register(lambda:print(hasattr(sys,'last_exc')));"
    for end, crashed in (("raise ValueError", "True"), ("raise SystemExit(2)", "False")):
        got = subprocess.run([sys.executable, "-c", said + end], capture_output=True, text=True,
                             stdin=subprocess.DEVNULL, check=False)  # fmt: skip
        assert got.stdout.strip() == crashed, got
    path = landq_inflight.mark(tmp_path, "T.W")
    monkeypatch.setattr(sys, "last_exc", ValueError("x"), raising=False)
    landq_inflight.left(path)
    assert landq_state.get(path)
    monkeypatch.delattr(sys, "last_exc")
    landq_inflight.left(path)
    assert landq_state.get(path) is None


def test_a_wait_that_saw_its_enqueue_gone_still_follows_the_one_that_replaced_it(
    tmp_path, monkeypatch, capsys
):
    """A re-enqueue stops the old one, then marks its own: the wait between the two goes on."""
    _old_red(tmp_path)
    first = landq_inflight.mark(tmp_path, "T.W")
    ticks: list[float] = []

    def step(s: float) -> None:
        ticks.append(s)
        if len(ticks) == 1:
            landq_inflight.clear(first)
        elif len(ticks) == 3:
            landq_state.put(tmp_path, f"{2:020d}-T.W",
                            {"item": "T.W", "repo": "/r", "sha": MINE, "mode": "land"})  # fmt: skip
        elif len(ticks) == 4:
            rec = {"item": "T.W", "state": "landed", "sha": MINE}
            land_records.write(
                tmp_path, f"{2:020d}-T.W", rec, tmp_path / "pending" / f"{2:020d}-T.W.json"
            )

    _sleeps(monkeypatch, step)
    code, out = _waited(tmp_path, capsys, bound=patience(60))
    assert code == 0, (code, out)
