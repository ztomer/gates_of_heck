"""lib/cpu_owners.py (GOH.TIMEACCOUNT reading 4): CPU by owner, from two process tables, in memory;
the rollup's cost on a table of 2000 processes against its bound."""

from __future__ import annotations

import time

import cpu_owners as co
import pytest
from _kv_fixture import Held
from proc_table import Proc, Snapshot

RULES = co.rules(co.DEFAULT_RULES)


def proc(pid: int, ppid: int, cpu: float, cwd: str | None = None, child: float = 0.0,
         start: float = 1.0) -> Proc:  # fmt: skip
    return Proc(pid, ppid, "x", "/bin/x", cpu, child, 1, cwd, None, start)


def snap(t: float, busy: float, *ps: Proc) -> Snapshot:
    return Snapshot(t, busy, 1.0, 4, {p.pid: p for p in ps})


def test_an_owner_is_the_first_marker_in_its_own_or_an_ancestors_cwd() -> None:
    procs = {p.pid: p for p in (
        proc(1, 0, 0, "/"), proc(2, 1, 0, "/s/landq/idle-suite/wt"), proc(3, 2, 0, None),
        proc(4, 1, 0, "/s/landq/pre"), proc(5, 1, 0, "/l/lanes/goh/1/wt"), proc(6, 5, 0, "/tmp"),
        proc(7, 99, 0, None))}  # fmt: skip
    assert co.owners(procs, RULES) == {1: "other", 2: "idle suite", 3: "idle suite", 4: "gate",
                                       5: "lane", 6: "lane", 7: "other"}  # fmt: skip


def test_a_window_books_new_cpu_once_and_the_rest_is_other() -> None:
    a = snap(0.0, 100.0, proc(1, 0, 5.0, "/"), proc(2, 1, 10.0, "/a/lanes/x"),
             proc(3, 2, 4.0, "/a/lanes/x"), proc(9, 1, 50.0, "/q/landq/g", start=2.0))  # fmt: skip
    # 3 was reaped into 2 (its 4 s seen, 1 s new); 9 was reborn as another process
    b = snap(60.0, 160.0, proc(1, 0, 5.0, "/"), proc(2, 1, 12.0, "/a/lanes/x", child=5.0),
             proc(9, 1, 7.0, "/q/landq/g", start=3.0))  # fmt: skip
    assert co.window(a, b, RULES) == pytest.approx({"lane": 3.0, "gate": 7.0, "other": 50.0})


def test_a_tick_writes_the_window_and_the_samplers_cost(monkeypatch) -> None:
    held = Held().lay(monkeypatch)
    a, b = (
        snap(0.0, 0.0, proc(1, 0, 0.0, "/l/lanes/x")),
        snap(60.0, 9.0, proc(1, 0, 6.0, "/l/lanes/x")),
    )
    assert co.tick(a, b) and not co.tick(b, b)
    got = {o: s for _, _, o, s in co.rows()}
    assert got["lane"] == 6.0 and got["other"] == 3.0 and got["sampler"] >= 0
    s = co.summary(co.rows())
    assert s["windows"] == 1 and s["wall_s"] == 60.0 and s["max_pct"] == 0.1
    assert held.goh.execute("SELECT count(*) FROM cpu_owners").fetchone() == (3,)


def test_the_rollup_costs_under_its_bound_on_a_big_table(monkeypatch) -> None:
    """The bound (0.1% of one core at the default period) on 2000 processes; CALIBRATED: a period
    of 1 s puts the same rollup over the bound, so this assertion can fail."""
    ps = [proc(i, max(0, i - 1) // 4, float(i), f"/w/{'lanes' if i % 3 else 'landq'}/{i}")
          for i in range(1, 2001)]  # fmt: skip
    a, b = (
        snap(0.0, 0.0, *ps),
        snap(120.0, 1e6, *(proc(p.pid, p.ppid, p.cpu_s + 1, p.cwd) for p in ps)),
    )
    t = time.process_time()
    for _ in range(5):
        co.window(a, b, RULES)
    cost = (time.process_time() - t) / 5
    assert 100 * cost / co.period_s() < co.DEFAULT_MAX_PCT
    assert 100 * cost / 0.001 > co.DEFAULT_MAX_PCT  # the calibration: a 1 ms period would not pass


def test_bad_rules_and_numbers(monkeypatch) -> None:
    with pytest.raises(ValueError, match="no owner=marker"):
        co.rules("lane")
    monkeypatch.setenv(co.PERIOD_ENV, "x")
    assert co.period_s() == co.DEFAULT_PERIOD_S
    monkeypatch.setenv(co.ENV, "a=/b/")
    assert co.rules() == [("a", "/b/")]


def test_every_books_one_window_a_period(monkeypatch) -> None:
    Held().lay(monkeypatch)
    monkeypatch.setattr(co, "_BASE", [])
    tables = [snap(t, t, proc(1, 0, t, "/l/lanes/x")) for t in (0.0, 60.0, 130.0, 200.0, 260.0)]
    assert [co.every(s) for s in tables] == [False, False, True, False, True]
    assert sorted({w for _, w, _, _ in co.rows()}) == [130.0]
