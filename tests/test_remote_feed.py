"""The pre-step's worker shares (GOH.REMOTECARRY, lib/remote_feed.py, tests/_worker_feed.py): half
of what the worker should take, then more as it frees, split of what will really run; and a test is
in one share or in both, never in neither, whatever dies between."""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest
from conftest import REPO_ROOT
from timing_bounds import GiveUp, patience

sys.path.insert(0, str(REPO_ROOT))
from lib import affected_verdict as av, remote_feed as rf, remote_lane as rl, remote_saved as rs
from lib.remote_rates import Rates

SAFE = [f"tests/test_s{i}.py" for i in range(8)]
REST = [*SAFE, "tests/test_m.py"]
JUDGES = ["tests/test_j.py", "tests/test_s9.py"]


def junit(nodes: list[str], bad: str = "", collected: int | None = None) -> str:
    """A shard's junit: each of NODES one test green, BAD a failure."""
    cases = ""
    for n in nodes:
        path, _, name = n.partition("::")
        cases += (f'<testcase classname="{path[:-3].replace("/", ".")}" name="{name or "test_x"}" '
                  f'time="1.0">{"<failure/>" * (n == bad)}</testcase>')  # fmt: skip
    many = len(nodes) if collected is None else collected
    return (f'<testsuites><testsuite time="10"><properties><property name="goh_collected" '
            f'value="{many}"/></properties>{cases}</testsuite></testsuites>')  # fmt: skip


class Proc:
    """A shard's process: it ends when the test says, or at once when killed."""

    def __init__(self) -> None:
        self.ended, self.rc = threading.Event(), 0

    def end(self, rc: int = 0) -> None:
        self.rc = rc
        self.ended.set()

    def wait(self) -> int:
        assert self.ended.wait(patience(30)), "a shard nobody ended"
        return self.rc

    def poll(self) -> int | None:
        return self.rc if self.ended.is_set() else None

    def kill(self) -> None:
        self.end(self.rc or -9)


class Rig:
    """A feed whose shards are the test's: SENT as launched, each ended by `done`."""

    def __init__(self, root: Path, record: Path, monkeypatch) -> None:
        self.root, self.record, self.sent, self.procs = root, record, [], []
        self.mac: list[list[str]] = []
        self.refuse: BaseException | None = None
        monkeypatch.setattr(rl, "launch", self.launch)
        ready = Proc()
        ready.end()
        monkeypatch.setattr(rf, "prepare", lambda _root: ready)

    def launch(self, _root, nodes, mac_s, env=None, later=False) -> rl.Share:
        if self.refuse:
            raise self.refuse
        n = len(self.sent)
        self.sent.append(list(nodes))
        self.procs.append(Proc())
        log = self.root / f"{n}.log"
        log.write_text("said\n")
        (self.root / f"{n}.xml").write_text(junit(nodes))
        return rl.Share(list(nodes), self.procs[-1], self.root / f"{n}.xml", log, 1.0, later=later)

    def begin(self, judges=JUDGES, rest=REST, skip=(), mine=(), rust=False) -> rf.Feed:
        self.feed = rf.begin(self.root, list(judges), list(mine), list(rest), str(self.record),
                             rust=rust)  # fmt: skip
        if self.feed.dir:  # an idle feed has none
            rf._put(Path(self.feed.dir) / "plan.json", {"skip": list(skip)})
        return self.feed

    def until(self, what, why: str) -> None:
        give_up = GiveUp(30)
        while not what():
            assert not give_up.passed(), why
            time.sleep(0.01)  # polled under GiveUp's load-stretched deadline

    def shares(self, n: int) -> None:
        self.until(lambda: len(self.sent) >= n and len(self.feed.shares) >= n,
                   f"{len(self.sent)} of {n} shares sent")  # fmt: skip

    def asks(self, n: int) -> dict:
        at = Path(self.feed.dir) / "ask.json"
        self.until(lambda: (rf._read(at) or {}).get("n", 0) >= n, f"no ask {n}")
        return rf._read(at) or {}

    def reply(self, n: int, want_s: float, nodes=()) -> None:
        rf._put(Path(self.feed.dir) / f"reply-{n}.json", {"want_s": want_s, "nodes": list(nodes)})

    def run_local(self, nodes: list[str]) -> int:
        self.mac.append(list(nodes))
        passed = {f"{n}::test_x" if n.endswith(".py") else n: False for n in nodes}
        self.record.write_text(json.dumps({"identity": "t", "sha": "s", "passed": passed}))
        return 0

    def ends(self) -> int:
        return self.feed.end(av.merge_records, self.run_local)

    def verdicts(self) -> set[str]:
        rec = json.loads(self.record.read_text())
        return {n.split("::")[0] for n in {*rec["passed"], *rec.get("remote", ())}}


@pytest.fixture
def rig(tmp_path: Path, monkeypatch):
    for k, v in {"GOH_REMOTE_HOST": "fakehost", "GOH_TEST_DURATIONS": str(tmp_path / "goh.db"),
                 "TMPDIR": str(tmp_path)}.items():  # fmt: skip
        monkeypatch.setenv(k, v)
    for k in ("GOH_REMOTE_SHARE", "GOH_LAND_REMOTE", "GOH_LAND_PRE_TOOK"):
        monkeypatch.delenv(k, raising=False)
    table = {f"{n}::test_x": 20.0 for n in [*REST, *JUDGES]}
    monkeypatch.setattr(rf.fast_tests, "load", lambda: dict(table))
    monkeypatch.setattr(rf, "load_rates", lambda: Rates(mac_width=1.0, worker_width=1.0,
                                                        overhead_s=0.0))  # fmt: skip
    monkeypatch.setattr(rl, "load_rates", rf.load_rates)
    monkeypatch.setattr(rl, "measure", lambda *_a: None)
    (tmp_path / "tests").mkdir()
    for n in [*REST, *JUDGES]:
        (tmp_path / n).write_text("def test_x(): pass\n")
    safe = "\n".join(n[6:-3] for n in [*SAFE, "tests/test_s9.py"])
    (tmp_path / rl.LINUX_SAFE).write_text(safe + "\n")
    r = Rig(tmp_path, tmp_path / "record.json", monkeypatch)
    yield r
    if getattr(r, "feed", None) is not None:
        r.feed.cancel()


def ran(rig: Rig, nodes: list[str]) -> None:
    """The Mac's runs are done: their record."""
    passed = {f"{n}::test_x": False for n in nodes}
    rig.record.write_text(json.dumps({"identity": "t", "sha": "s", "passed": passed}))


def test_the_first_share_is_half_of_the_balanced_split(rig: Rig):
    rig.begin()
    rig.shares(1)  # 11 nodes of 20 s at equal paces: five balance, so half is three
    assert len(rig.sent[0]) == 3 and set(rig.sent[0]) <= set(SAFE)
    rest = rig.feed.rest()
    assert sorted([*rest, *rig.sent[0]]) == sorted(REST)


def test_a_test_main_s_record_answers_is_neither_sent_nor_counted(rig: Rig):
    """The old split was blind to the carry: the worker ran what the Mac deselected."""
    skip = [f"{n}::test_x" for n in SAFE[:6]]
    feed = rig.begin(skip=skip)
    rig.shares(1)
    assert set(rig.sent[0]) <= set(SAFE[6:]), rig.sent
    assert set(SAFE[:6]) <= set(feed.rest())  # on the Mac, whose own carry deselects them
    assert feed.secs(SAFE) == {n: 0.0 if n in SAFE[:6] else 20.0 for n in SAFE}


def test_the_worker_is_handed_more_as_it_frees(rig: Rig):
    feed = rig.begin()
    rig.shares(1)
    rig.procs[0].end()
    ask = rig.asks(1)
    assert ask["pool_s"] == 100.0 and ask["after_s"] == 120.0 and ask["part"] == 1.0
    rig.reply(1, 40.0)  # two nodes' worth, of the pool: the Mac has not begun the rest
    rig.shares(2)
    assert len(rig.sent[1]) == 2 and not set(rig.sent[0]) & set(rig.sent[1])
    mac = feed.rest()
    assert sorted([*mac, *rig.sent[0], *rig.sent[1]]) == sorted(REST)
    rig.procs[1].end()
    assert rig.asks(2)["pool_s"] == 0.0  # the pool is closed: its running queue is all there is
    rig.reply(2, 20.0, ["tests/test_m.py::test_x"])
    rig.shares(3)
    rig.procs[2].end()
    rig.asks(3)
    rig.reply(3, 0.0)
    ran(rig, [*JUDGES, *mac])
    assert rig.ends() == 0 and rig.mac == []
    assert rig.verdicts() == {*REST, *JUDGES}


def test_an_ask_the_judges_run_never_answered_is_the_rest_run_s(rig: Rig):
    rig.begin()
    rig.shares(1)
    rig.procs[0].end()
    assert rig.asks(1)["after_s"] > 0
    rig.feed.rest()
    ask = rf._read(Path(rig.feed.dir) / "ask.json") or {}
    assert (ask["n"], ask["after_s"], ask["pool_s"]) == (1, 0.0, 0)


def test_a_reply_that_met_a_closing_pool_is_asked_again(rig: Rig):
    feed = rig.begin()
    rig.shares(1)
    rig.procs[0].end()
    rig.asks(1)
    with feed.lock:  # the Mac's rest began between the answer and its reading
        feed.open = False
    rig.reply(1, 40.0)
    assert rig.asks(2)["pool_s"] == 0.0 and len(rig.sent) == 1


@pytest.mark.parametrize("rc", [1, 3])
def test_a_planted_dead_worker_loses_no_verdict(rig: Rig, rc: int):
    """Its workers crashed (exit 1, cases missing) or it stalled, skipped differently, fell back
    (exit 3): the share reruns whole on the Mac, and no other share is sent."""
    rig.begin()
    rig.shares(1)
    sent = rig.sent[0]
    if rc == 1:
        rig.feed.shares[0].junit.write_text(junit(sent[:1], collected=len(sent)))
    mac = rig.feed.rest()
    rig.procs[0].end(rc)
    ran(rig, [*JUDGES, *mac])
    assert rig.ends() == 0
    assert rig.mac == [sent] and len(rig.sent) == 1
    assert rig.verdicts() == {*REST, *JUDGES}


def test_a_later_share_s_dead_worker_reruns_that_share_alone(rig: Rig):
    rig.begin()
    rig.shares(1)
    rig.procs[0].end()
    rig.asks(1)
    rig.reply(1, 40.0)
    rig.shares(2)
    mac = rig.feed.rest()
    rig.procs[1].end(3)
    ran(rig, [*JUDGES, *mac])
    assert rig.ends() == 0 and rig.mac == [rig.sent[1]]
    assert rig.verdicts() == {*REST, *JUDGES}


def test_a_share_that_cannot_start_runs_on_the_mac(rig: Rig, capsys):
    rig.refuse = OSError("no process")
    feed = rig.begin()
    rig.until(lambda: feed.fault is not None, "the launch was not refused")
    mac = feed.rest()
    ran(rig, [*JUDGES, *mac])
    assert rig.ends() == 0
    assert rig.mac == [sorted(feed.given)] and len(feed.given) == 3
    assert rig.verdicts() == {*REST, *JUDGES}
    assert "no process" in capsys.readouterr().out


def test_units_handed_over_as_the_mac_ended_run_on_the_mac(rig: Rig):
    """The controller's answer is on disk and its queue no longer holds them: nobody launched."""
    rig.begin()
    rig.shares(1)
    mac = rig.feed.rest()
    rig.procs[0].end()
    rig.asks(1)
    rig.feed.over.set()
    rig.until(lambda: not rig.feed.thread.is_alive(), "the feed outlived the Mac")
    rig.reply(1, 20.0, ["tests/test_j.py::test_x"])
    ran(rig, ["tests/test_s9.py", *mac])
    assert rig.ends() == 0 and rig.mac == [["tests/test_j.py::test_x"]]
    assert rig.verdicts() == {*REST, *JUDGES}


def test_a_red_found_at_the_judges_end_is_rerun_before_the_rest(rig: Rig, monkeypatch):
    feed = rig.begin()
    rig.shares(1)
    bad = rig.sent[0][0]
    feed.shares[0].junit.write_text(junit(rig.sent[0], bad=bad))
    ran(rig, JUDGES)
    assert feed.early(av.merge_records) == 0  # it runs yet: nothing to say
    rig.procs[0].end(1)
    reruns: list[list[str]] = []
    monkeypatch.setattr(rl, "mac_run", lambda _root: lambda ns: reruns.append(ns) or 1)
    assert feed.early(av.merge_records) == 1
    assert reruns == [[f"{bad}::test_x"]]


def test_a_mac_red_stops_every_share_and_starts_no_other(rig: Rig):
    feed = rig.begin()
    rig.shares(1)
    feed.cancel()
    assert rig.procs[0].rc == -9 and len(rig.sent) == 1
    assert not feed.thread.is_alive() and not Path(feed.dir).exists()


def test_the_judges_go_too_when_there_is_no_pool(rig: Rig):
    """Half of a pre-step's Mac wall was judges no share could take; a set of judges alone, or a
    rest with no judges before it, is asked of the Mac's own run."""
    rig.begin(judges=JUDGES, rest=["tests/test_m.py"])
    ask = rig.asks(1)
    assert ask["part"] == rf.PART and "test_s9" in ask["safe"] and "test_m" not in ask["safe"]
    rig.reply(1, 20.0, ["tests/test_s9.py::test_x"])
    rig.shares(1)
    assert rig.sent == [["tests/test_s9.py::test_x"]]


def test_the_mac_s_pace_is_read_of_what_it_ran(rig: Rig, monkeypatch):
    """A carried test's seconds counted as run made the Mac look faster than it was, and the next
    split starved the worker."""
    read: list[tuple[float, bool]] = []
    monkeypatch.setattr(
        rl, "measure", lambda _r, share, _j, end: read.append((share.mac_s, share.later))
    )
    feed = rig.begin(skip=[f"{n}::test_x" for n in SAFE[:4]])
    rig.shares(1)
    feed.shares[0].started = feed.began
    rig.procs[0].end()
    rig.asks(1)
    rig.reply(1, 20.0)
    rig.shares(2)
    rig.procs[1].end()
    ran(rig, JUDGES)  # 40 s of table; the four carried (80 s) are no part of it
    assert rig.ends() == 0
    assert read == [(pytest.approx(40.0), False), (0.0, True)]


def test_a_later_share_folds_no_width_and_no_overhead(tmp_path: Path, monkeypatch):
    kept: list[Rates] = []
    monkeypatch.setattr(rl.remote_rates, "put", lambda _t, fold: kept.append(fold(Rates())))
    monkeypatch.setattr(rl.fast_tests, "load", dict)
    (tmp_path / "tests").mkdir()
    (tmp_path / "j.xml").write_text("")
    for later in (False, True):
        share = rl.Share([], Proc(), tmp_path / "j.xml", tmp_path / "l", 1.0, later=later)
        rl.measure(tmp_path, share, junit(SAFE), 100.0)
    assert kept[0].worker_width != Rates().worker_width
    assert kept[1].worker_width == Rates().worker_width
    assert kept[1].overhead_s == Rates().overhead_s


def test_a_node_s_cost_is_its_tests_sum_whatever_it_names():
    table = {"t.py::a[1]": 2.0, "t.py::a[2]": 3.0, "t.py::C::b": 4.0}
    nodes = ["t.py", "t.py::a", "t.py::a[2]", "t.py::C", "t.py::new"]
    assert rl.cost(nodes, table) == dict(zip(nodes, [9.0, 5.0, 3.0, 4.0, 1.0], strict=True))


def test_the_record_s_one_figure_sums_the_shares():
    a = {"tests": 3, "outcome": "remote", "cause": None, "fresh": False, "t0": 100.0, "mac_s": 60.0,
         "worker_s": 30.0, "mac_est_s": 20.0, "rerun_s": 0.0, "saved_s": 20.0}  # fmt: skip
    b = {**a, "tests": 2, "outcome": "fallback", "cause": "busy", "t0": 131.0, "saved_s": -5.0}
    got = rs.whole([a, b])
    assert (got["tests"], got["outcome"], got["cause"]) == (5, "fallback", "busy")
    assert (got["t0"], got["worker_s"], got["saved_s"]) == (100.0, 61.0, 15.0)
    assert rs.whole([a]) == a and rs.whole([]) == {}


def test_a_rust_change_shares_its_far_tests_alone_and_no_mac_pace(rig: Rig):
    """GOH.RUSTPRECARRY: the measured run (the binary's tests) stays whole on the Mac, no judge
    goes, the worker is asked for no unit of a run, and an instrumented wall is no Mac pace. With
    no judges the first share is decided before the measured run begins, beside it."""
    feed = rig.begin(judges=[], mine=["tests/test_m.py"], rest=SAFE, rust=True)
    assert feed.rest()[0] == "tests/test_m.py" and len(rig.feed.given) == 2  # half of four
    rig.shares(1)
    assert set(rig.sent[0]) <= set(SAFE)
    rig.procs[0].end()
    assert rig.asks(1)["safe"] == []
    rig.reply(1, 0.0)
    ran(rig, [*SAFE, "tests/test_m.py"])
    assert feed._executed() == 0.0 and rig.ends() == 0


def test_a_rust_retry_sends_nothing(rig: Rig):
    feed = rig.begin(judges=JUDGES, mine=REST, rest=[], rust=True)  # kin: far is empty
    assert feed.thread is None and feed.rest() == REST
