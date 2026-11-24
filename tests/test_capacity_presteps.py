"""`goh capacity` counts the land pre-steps it used to miss, and caps them (GOH.CAPPRESTEP,
GOH.LOADCAP).

CAPPRESTEP (2026-11-23 22:1x): capacity said "gates in flight weigh 0 cores" at load 23 on 16
cores while an enqueue's pre-step held the pre lane (pytest -n 12 and a cargo build in landq's
checkout slot 1), and slot 5's owner, pid 27042 from 16:12, was dead. Now the reading names every
held pre-lane slot with its weight (the median CPU-s per wall-s of the repo's recorded pre-steps),
names every enqueue in its pre-step (a checkout slot whose owner lives), and reaps an owner whose
pid is gone and whose token no queued request names -- the claim's own staleness rule.

LOADCAP (owner 2026-11-23: "choose the value that will give us the most overall throughput"): the
cap on concurrent pre-steps is the peak of the measured curve of landed items per hour against the
pre-steps in flight (lib/load_cap.py, from the land records), and capacity admits up to it.
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
import time
from pathlib import Path

import _goh_db_fixture
import pytest
from conftest import REPO_ROOT
from test_capacity import Planted, _cli

sys.path.insert(0, str(REPO_ROOT / "lib"))
import goh_db
import land_slot
import landq_state
import load_cap

H = 3600.0
T0 = 1_795_000_000.0 - 1_795_000_000.0 % H  # an hour boundary


@pytest.fixture(autouse=True)
def _own_goh_db(monkeypatch, tmp_path):
    _goh_db_fixture.use(monkeypatch, tmp_path)


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _record(q: Path, run: str, start: float, pre_s: float, state: str, *, finished: float = 0.0,
            cpu: float = 0.0, repo: str = "/r/gates_of_heck") -> None:  # fmt: skip
    rec = {"repo": repo, "item": run, "mode": "land", "state": state, "requested": _iso(start),
           "pre_s": pre_s, "pre_run": {"run_s": pre_s, "cpu_s": cpu, "wait_s": 0}}  # fmt: skip
    if finished:
        rec.update(started=_iso(finished - 60), finished=_iso(finished), at=_iso(finished))
    _goh_db_fixture.land(q, f"{int(start * 1e9):020d}-{run}", rec)


DEAD = f"{os.getpid()} Mon Nov 23 16:12:06 2026"  # this pid, stamped with a start it never had:
# the owner a reused pid leaves (lib/pid_owner.py alive_line judges it gone)


def _owner(q: Path, n: int, pid: int, stamp: str, token: str) -> None:
    key = str(land_slot.root(q, Path("/r/gates_of_heck")))
    row = (key, n, token, pid, stamp, T0)
    with goh_db.transaction() as con:
        con.execute("INSERT OR REPLACE INTO slot_owners (repo_key, n, token, pid, pid_stamp, "
                    "at) VALUES (?, ?, ?, ?, ?, ?)", row)  # fmt: skip


def _env(p: Planted, q: Path, **extra: str) -> dict[str, str]:
    return {**p.env, "GOH_DB": os.environ["GOH_DB"], "GOH_LANDQ_DIR": str(q), **extra}


def test_a_held_pre_lane_slot_is_named_and_weighed_by_its_recorded_pre_steps(tmp_path) -> None:
    q = tmp_path / "landq"
    for i, cpu in enumerate((3000.0, 5000.0, 4000.0)):  # 6, 10 and 8 cores over 500 s
        _record(q, f"T.W{i}", T0 + i * H, 500.0, "landed", finished=T0 + i * H + 900, cpu=cpu)
    p = Planted(tmp_path, waiting=0, idle=80)
    fd = os.open(p.dir / "pre-0", os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.write(fd, f"{os.getpid()}\tgoh\t{T0:.0f}\tkey\tpre\n".encode())
    try:
        r = _cli(_env(p, q), "--json")
        human = _cli(_env(p, q))
    finally:
        os.close(fd)
        p.close()
    got = json.loads(r.stdout)
    pre = got["presteps"]
    assert (pre["held"], pre["total"]) == (1, 2), pre
    (holder,) = pre["holders"]
    assert (holder["slot"], holder["repo"], holder["pid"]) == ("pre-0", "goh", os.getpid())
    assert (holder["weight"], holder["weight_from"]) == (8.0, "goh"), holder
    assert got["cpu_weight"]["in_flight"] == 15.0, got["cpu_weight"]  # the gate's 7 and its 8
    assert "pre-steps: 1/2 pre slots held" in human.stdout + human.stderr, human.stdout


def test_a_live_enqueue_is_counted_and_a_dead_owner_no_request_names_is_reaped(tmp_path):
    q = tmp_path / "landq"
    dead = os.getpid()
    _owner(q, 0, os.getpid(), land_slot.pid_owner.stamp_line(os.getpid()) or "", "live")
    _owner(q, 5, dead, DEAD, "stale")
    _owner(q, 2, dead, DEAD, "queued")
    req = {"repo": "/r/gates_of_heck", "item": "T.Q", "slot": "queued", "sha": "0" * 40}
    landq_state.put(q, "00000000000000000001-T.Q", req, "pending")
    p = Planted(tmp_path, waiting=0, idle=80)
    try:
        got = json.loads(_cli(_env(p, q), "--json").stdout)
    finally:
        p.close()
    pre = got["presteps"]
    assert [(x["n"], x["pid"]) for x in pre["live"]] == [(0, os.getpid())], pre
    assert pre["live"][0]["repo"] == "goh", pre["live"]
    assert [(x["n"], x["pid"]) for x in pre["reaped"]] == [(5, dead)], pre
    left = {o["n"] for o in land_slot.owners(q)}
    assert left == {0, 2}, left  # the queued request's slot is its request's, never reaped


def _curve_records(q: Path, peak: int) -> None:
    """Hours with k pre-steps in flight for k = 1..3; the hours at k=PEAK land 4 items, the
    others 1: the curve's peak is at PEAK."""
    hour = 0
    for k in (1, 2, 3):
        for _ in range(load_cap.MIN_HOURS):
            t = T0 + hour * H
            for j in range(k):  # k pre-steps, each the whole hour
                _record(q, f"T.P{hour}-{j}", t, H - 1, "red")
            for j in range(4 if k == peak else 1):
                _record(q, f"T.L{hour}-{j}", t + 1, 0.0, "landed", finished=t + 600 + j)
            hour += 1


@pytest.mark.parametrize("peak", [1, 2])
def test_the_curve_peaks_where_the_records_say(tmp_path, peak) -> None:
    q = tmp_path / "landq"
    _curve_records(q, peak)
    from land_records import records

    curve = load_cap.curve([r for _, r in records(q)])
    assert load_cap.peak(curve) == peak, curve
    assert curve[peak]["per_h"] == 4.0 and curve[peak]["hours"] == load_cap.MIN_HOURS, curve


def test_capacity_admits_up_to_the_curves_peak_and_refuses_above(tmp_path) -> None:
    q = tmp_path / "landq"
    _curve_records(q, 2)
    for n in (0, 1):
        _owner(q, n, os.getpid(), land_slot.pid_owner.stamp_line(os.getpid()) or "", f"t{n}")
    p = Planted(tmp_path, waiting=0, idle=80)
    try:
        at_peak = json.loads(_cli(_env(p, q), "--json").stdout)
        _owner(q, 2, os.getpid(), land_slot.pid_owner.stamp_line(os.getpid()) or "", "t2")
        over = _cli(_env(p, q), "--json")
    finally:
        p.close()
    assert at_peak["presteps"]["cap"] == {"value": 2, "from": "curve"}, at_peak["presteps"]
    assert at_peak["room"]["ok"] is True, at_peak["room"]
    got = json.loads(over.stdout)
    why = " ".join(got["room"]["why"])
    assert over.returncode == 1 and "3 pre-steps in flight, over the cap of 2" in why, why


def test_too_few_hours_fall_back_to_the_measured_default() -> None:
    assert load_cap.peak({1: {"per_h": 9.0, "hours": 1}}) is None
    assert load_cap.cap([], {}) == {"value": load_cap.DEFAULT, "from": "default"}
    assert load_cap.cap([], {"GOH_PRESTEP_CAP": "3"}) == {"value": 3, "from": "GOH_PRESTEP_CAP"}
