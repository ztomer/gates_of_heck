"""The gate slots admit by class, then age, with a starvation bound (GOH.SLOTPRIORITY).

2026-11-21: a land's gate waited 161 s for a slot held by the coordinator's `.loop` push gate and a
consumer lane's `tools/ci_hook.sh` (10 min), and earlier lands 293 s behind the pin sweep's consumer
pushes: every push gate was `ship`, ranked with a land, and lands are the critical path. Classes
now are land > ship > lane > work (lib/gate_lanes.py CLASSES), declared by the caller, and a waiter
past `GOH_GATE_SLOT_STARVE_MIN` goes ahead of every class. Pinned here:

  * three waiters on a fake clock are ordered by class, then age;
  * the starvation bound promotes a lower class once it has waited that long, and not before;
  * a land never waits behind a sweep or another repo's gate, before or after it (real processes);
  * the land profile shows the slot wait per class (the log -> the land record -> the stages), and
    the `land attempt` ledger row carries it.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
import timing_bounds
from conftest import REPO_ROOT
from test_gate_slot_fair import Waiter, _cleanup

sys.path.insert(0, str(REPO_ROOT / "lib"))
sys.path.insert(0, str(REPO_ROOT / "tools"))
import gate_lanes
import land_attempts
import land_profile
import landq_report

MIN = 60 * 10**9  # a minute of the fake clock, in ns


class Clock:
    """A fake monotonic clock in ns: what the tickets read for arrival and for their bound."""

    def __init__(self) -> None:
        self.now = 10**12

    def __call__(self) -> int:
        return self.now


def _ticket(d: Path, klass: str, clock: Clock, bound_min: float = 15) -> gate_lanes.Ticket:
    lane, rank = gate_lanes.lane_of(klass)
    t = gate_lanes.Ticket(str(d), lane, rank, clock=clock, bound_ns=int(bound_min * MIN))
    clock.now += 1  # the next arrival is later
    return t


def _names(*tickets: gate_lanes.Ticket) -> list[str]:
    return [t.name for t in tickets]


def test_three_waiters_are_ordered_by_class_then_age(tmp_path: Path) -> None:
    """A sweep push (work) first, a ship second, a land last: the land is first, the sweep last."""
    clock = Clock()
    work = _ticket(tmp_path, "work", clock)
    ship = _ticket(tmp_path, "ship", clock)
    land = _ticket(tmp_path, "land", clock)
    try:
        assert land.ahead() == []
        assert ship.ahead() == _names(land)
        assert work.ahead() == _names(land, ship)
        lane = _ticket(tmp_path, "lane", clock)
        assert lane.ahead() == _names(land, ship) and work.ahead() == _names(land, ship, lane)
        lane.leave()
        later_land = _ticket(tmp_path, "land", clock)  # same class: the older goes first
        assert later_land.ahead() == _names(land)
        later_land.leave()
    finally:
        for t in (work, ship, land):
            t.leave()


def test_the_starvation_bound_promotes_a_lower_class_once_it_waited_that_long(tmp_path) -> None:
    clock = Clock()
    work = _ticket(tmp_path, "work", clock, bound_min=10)
    clock.now += 5 * MIN
    land = _ticket(tmp_path, "land", clock, bound_min=10)
    try:
        assert work.ahead() == _names(land)  # 5 min in: the land goes first
        clock.now = work.seq + 10 * MIN - 1
        assert work.ahead() == _names(land), "promoted before its bound"
        clock.now = work.seq + 10 * MIN
        # GOH.IDLESUITERETIRE: starved, a background gate still never goes ahead of a land
        assert work.ahead() == _names(land) and land.ahead() == []
    finally:
        work.leave()
        land.leave()


def test_a_starved_background_gate_goes_ahead_of_background_never_of_a_land_or_ship(
    tmp_path: Path,
) -> None:
    """GOH.IDLESUITERETIRE: a ship's release commit waited 388 s on v0.149.2 behind background
    work; past its bound a lane or work ticket overtakes every lane and work ticket, and a land or a
    ship still takes the next free slot. A starved ship still goes ahead of a land."""
    clock = Clock()
    work = _ticket(tmp_path, "work", clock, bound_min=10)
    lane = _ticket(tmp_path, "lane", clock, bound_min=60)
    clock.now = work.seq + 10 * MIN  # the work gate is starved, the lane gate not
    land = _ticket(tmp_path, "land", clock, bound_min=60)
    ship = _ticket(tmp_path, "ship", clock, bound_min=10)
    try:
        assert work.ahead() == _names(land, ship), "a starved work gate overtook the release"
        assert lane.ahead() == _names(land, ship, work)  # ahead of every other background
        clock.now = ship.seq + 10 * MIN
        assert ship.ahead() == [] and land.ahead() == _names(ship)  # a starved ship: first
    finally:
        for t in (work, lane, land, ship):
            t.leave()


def test_the_bound_is_named_and_a_bad_one_refused(tmp_path: Path) -> None:
    assert gate_lanes.starve_ns({}, str(tmp_path)) == int(gate_lanes.STARVE_MIN * MIN)
    assert gate_lanes.starve_ns({gate_lanes.STARVE_ENV: "2"}) == 2 * MIN
    (tmp_path / ".gatesrc").write_text(f"{gate_lanes.STARVE_ENV}=3\n")
    assert gate_lanes.starve_ns({}, str(tmp_path)) == 3 * MIN
    assert gate_lanes.starve_ns({}, str(REPO_ROOT)) == 15 * MIN  # goh's own .gatesrc names it
    for bad in ("0", "-1", "soon"):
        with pytest.raises(ValueError, match=gate_lanes.STARVE_ENV):
            gate_lanes.starve_ns({gate_lanes.STARVE_ENV: bad})


def test_a_ticket_written_before_the_bound_still_reads(tmp_path: Path) -> None:
    assert gate_lanes.parse_ticket("long-1-00000000000000000005-42") == ("long", 1, 5, 42, None)
    assert gate_lanes.parse_ticket("long-3-5-42-99") == ("long", 3, 5, 42, 99)
    assert gate_lanes.parse_ticket(".draft-long-3-5-42-99") is None


@pytest.mark.load_sensitive
def test_a_land_never_waits_behind_a_sweep_or_another_repos_gate(tmp_path: Path) -> None:
    """Both orders: a sweep push and another repo's gate queued BEFORE the land, and one queued
    after it -- the freed slot goes to the land either way."""
    holder = Waiter(tmp_path, "holder", 1, 0.05, klass="work")
    sweep = other = land = late = None
    try:
        holder.wait_for("TAKEN")
        sweep = Waiter(tmp_path, "sweep-push", 1, 0.02, klass="work")
        sweep.queued()
        other = Waiter(tmp_path, "consumer-ci-hook", 1, 0.02)  # no class declared: the lowest
        other.queued()
        land = Waiter(tmp_path, "land", 1, 0.5, klass="land")
        land.queued()
        late = Waiter(tmp_path, "late-sweep", 1, 0.02, klass="work")
        late.queued()
        holder.finish()
        land.wait_for("TAKEN", 20)
        time.sleep(timing_bounds.patience(0.3))  # time to take it, had it been allowed
        assert not any(w.taken() for w in (sweep, other, late)), [sweep.out(), other.out()]
        land.finish()
        sweep.wait_for("TAKEN", 20)  # then age within the class
        assert not other.taken() and not late.taken()
    finally:
        _cleanup(late, land, other, sweep, holder)


LOG = (
    "info pre-land: tests\n"
    "load: gate slot 1/2 taken after 40s (GOH_GATE_SLOTS, class short); workers 8 (GOH.POOLSHARE)\n"
    "load: gate slot 2/2 taken after 161s (GOH_GATE_SLOTS, class land); workers 8 (GOH.POOLSHARE)\n"
    "✓ gate slot 1/2 taken after 5s waiting (class lane) -- the step limits count from here\n"
    "→ gate slot 1/1 taken (GOH_GATE_SLOTS, class work)\n"
)


def test_the_slot_wait_is_read_per_class_from_a_lands_log(tmp_path: Path) -> None:
    assert landq_report.slot_waits_by_class(LOG) == {
        "short": 40.0, "land": 161.0, "lane": 5.0, "work": 0.0}  # fmt: skip
    assert landq_report.slot_wait_s(LOG) == 206.0  # the older reader agrees on the total
    log = tmp_path / "x.log"
    log.write_text(LOG)
    got = landq_report.slot_waits({"log": str(log)})
    assert got == {"slot_wait_s": 206.0, "slot_wait_by_class": {
        "short": 40.0, "land": 161.0, "lane": 5.0, "work": 0.0}}  # fmt: skip
    assert landq_report.slot_waits({"log": str(tmp_path / "gone")}) == {}


def test_the_land_profile_and_the_ledger_row_show_each_class(tmp_path: Path) -> None:
    """The record's per-class waits are stages under `slot wait`, in rank order."""
    q = tmp_path / "q"
    for d in ("done", "logs"):
        (q / d).mkdir(parents=True)
    rec = {"repo": "/p/gates_of_heck", "mode": "land", "state": "landed", "item": "GOH.X",
           "started": "2026-11-21T10:05:00+00:00", "finished": "2026-11-21T10:20:00+00:00",
           "log": str(q / "logs" / "1-GOH.X.log"), "slot_wait_s": 201.0,
           "slot_wait_by_class": {"land": 161.0, "short": 40.0}}  # fmt: skip
    (q / "done" / "01795255200000000000-GOH.X.json").write_text(json.dumps(rec))
    (q / "logs" / "1-GOH.X.log").write_text(LOG)
    rows = land_profile.lands(q, [])
    assert len(rows) == 1, rows
    st = rows[0]["stages"]
    assert st["    slot wait"][0] == 201.0
    assert st["      slot wait land"][0] == 161.0 and st["      slot wait short"][0] == 40.0
    order = [r["stage"] for r in land_profile.table(rows)]
    at = order.index("    slot wait")
    assert order[at + 1 : at + 3] == ["      slot wait short", "      slot wait land"], order
    row = land_attempts.row_of(rec, "1-GOH.X")
    assert row is not None and row["slot_wait_by_class"] == {"land": 161.0, "short": 40.0}, row
    assert row["slot_wait_s"] == 201.0


def test_the_callers_declare_their_class() -> None:
    """The land's gate says `land`, goh ship says `ship`; a push gate names none (the lowest
    unless its caller declares one) -- never read off a command line."""
    assert "GOH_GATE_CLASS=land " in (REPO_ROOT / "lib/land_gate.sh").read_text()
    assert 'os.environ["GOH_GATE_CLASS"] = "ship"' in (REPO_ROOT / "tools/ship.py").read_text()
    push = (REPO_ROOT / "gates/push_gate.sh").read_text()
    assert 'gate_slot_acquire "$(basename "$root")";' in push
    assert gate_lanes.DEFAULT_CLASS == "work"
    ranks = {c: gate_lanes.lane_of(c) for c in ("land", "ship", "lane", "work")}
    assert ranks["land"] < ranks["ship"] < ranks["lane"] < ranks["work"]
