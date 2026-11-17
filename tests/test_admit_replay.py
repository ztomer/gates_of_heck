"""lib/admit_replay.py (GOH.CPUADMIT): a day's gate admissions replayed under two rules.

Every expected figure below is computed BY HAND in the comment beside it, so a wrong fluid share,
a wrong admission or a wrong sum fails here. The two rules differ in exactly the cases pinned:
light gates a count of two holds back, and heavy gates a count of two lets saturate the box.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import admit_replay as ar

T0 = 1_794_900_000.0


def job(name: str, arrive: float, work: float, demand: float, weight: float | None = None):
    return ar.Job(name, arrive, work, demand, demand if weight is None else weight)


def test_light_gates_wait_under_a_count_and_not_under_a_cpu_budget():
    # Three gates of 1 core and 100 cpu-s each, all at t=0, on 16 cores.
    # count:2 -> two run 0..100, the third waits 100 s and runs 100..200:
    #   wait 100, gate 300, lane 400.   cpu:16 -> all three at once: wait 0, gate 300, lane 300.
    jobs = [job(f"j{i}", 0, 100, 1) for i in range(3)]
    count = ar.replay(jobs, ar.Rule("count", 2), cores=16)
    cpu = ar.replay(jobs, ar.Rule("cpu", 16), cores=16)
    assert (count.wait_s, count.gate_s, count.lane_s) == (100, 300, 400)
    assert (cpu.wait_s, cpu.gate_s, cpu.lane_s) == (0, 300, 300)


def test_heavy_gates_share_the_box_under_a_count_and_queue_under_a_cpu_budget():
    # Two gates that would each fill 16 cores, 1600 cpu-s each, at t=0.
    # count:2 -> both run, 8 cores each, both end at 200: wait 0, gate 400, lane 400.
    # cpu:16 -> the first runs alone 0..100, the second waits 100 and runs 100..200:
    #   wait 100, gate 200, lane 300.
    jobs = [job("a", 0, 1600, 16), job("b", 0, 1600, 16)]
    count = ar.replay(jobs, ar.Rule("count", 2), cores=16)
    cpu = ar.replay(jobs, ar.Rule("cpu", 16), cores=16)
    assert (count.wait_s, count.gate_s, count.lane_s) == (0, 400, 400)
    assert (cpu.wait_s, cpu.gate_s, cpu.lane_s) == (100, 200, 300)


def test_a_gate_heavier_than_the_budget_still_runs_alone():
    # Weight 20 over a budget of 16: admitted when nothing runs (never starved), 2000/16 = 125 s.
    out = ar.replay([job("big", 0, 2000, 20)], ar.Rule("cpu", 16), cores=16)
    assert (out.wait_s, out.gate_s) == (0, 125)


def test_the_queue_is_first_come_first_served():
    # A heavy head blocks a light gate behind it (no overtaking: the slots' tickets are FIFO).
    # cpu:16, running "a" (12). Head "b" (12) does not fit, so "c" (1) waits too, although it fits.
    # a: 0..100 (1200/12). b: 100..200. c waits 100 and runs beside b: 100 cpu-s at 1 core.
    jobs = [job("a", 0, 1200, 12), job("b", 0, 1200, 12), job("c", 0, 100, 1)]
    out = ar.replay(jobs, ar.Rule("cpu", 16), cores=16)
    assert out.wait_s == 200
    assert out.gate_s == 300


def test_background_load_shares_the_cores():
    # One 16-core gate of 1600 cpu-s beside 16 cores of other work: it gets 8, so 200 s.
    out = ar.replay([job("a", 0, 1600, 16)], ar.Rule("count", 2), cores=16, background=16)
    assert out.gate_s == 200


def test_the_rule_weighs_by_the_weight_not_the_demand():
    # The rule's weight is the record's median (here 1); the run's real demand is 16 each.
    # cpu:16 admits both on their weight, so they share the box exactly as count:2 does.
    jobs = [job("a", 0, 1600, 16, weight=1), job("b", 0, 1600, 16, weight=1)]
    out = ar.replay(jobs, ar.Rule("cpu", 16), cores=16)
    assert (out.wait_s, out.gate_s) == (0, 400)


def test_jobs_from_the_ledger_start_where_the_span_started_and_drop_the_slot_wait():
    rows = [
        # a push: its span starts after the slot, so the arrival is epoch - wall
        {"kind": "push", "repo": "x", "epoch": T0 + 100, "wall_s": 100.0, "cpu_s": 200.0},
        # a land: its span includes the slot wait (40 s, from the queue's record of that sha)
        {
            "kind": "land",
            "repo": "goh",
            "epoch": T0 + 500,
            "wall_s": 240.0,
            "cpu_s": 1000.0,
            "sha": "abcdef012345",
        },
        # no cpu reading, a ship, and a 0 s run carry nothing to replay
        {"kind": "push", "repo": "y", "epoch": T0 + 9, "wall_s": 9.0},
        {"kind": "ship", "repo": "goh", "epoch": T0 + 9, "wall_s": 9.0, "cpu_s": 1.0},
        {"kind": "land", "repo": "goh", "epoch": T0 + 9, "wall_s": 0.0, "cpu_s": 0.0},
    ]
    jobs = ar.jobs_from(rows, slot_waits={"abcdef012345": 40.0})
    assert [(j.name, j.arrive, j.work, j.demand) for j in jobs] == [
        ("x push", T0, 200.0, 2.0),
        ("goh land", T0 + 260, 1000.0, 5.0),  # arrives at the span's start; runs 200 s at 5 cores
    ]


def test_a_push_that_recorded_its_slot_wait_arrives_when_it_asked():
    # GOH.LOADSOURCES: the push row carries slot_wait_s; the job arrived 30 s before its span.
    rows = [
        {
            "kind": "push",
            "repo": "x",
            "epoch": T0 + 100,
            "wall_s": 100.0,
            "cpu_s": 200.0,
            "slot_wait_s": 30.0,
        }
    ]
    (j,) = ar.jobs_from(rows)
    assert (j.arrive, j.work, j.demand) == (T0 - 30, 200.0, 2.0)


def test_the_weight_is_the_median_of_the_runs_before_it_never_a_later_one():
    rows = [
        {"kind": "push", "repo": "x", "epoch": T0 + 10, "wall_s": 10.0, "cpu_s": 10.0},  # 1
        {"kind": "push", "repo": "x", "epoch": T0 + 110, "wall_s": 10.0, "cpu_s": 30.0},  # 3
        {"kind": "push", "repo": "x", "epoch": T0 + 210, "wall_s": 10.0, "cpu_s": 50.0},  # 5
        {"kind": "push", "repo": "z", "epoch": T0 + 310, "wall_s": 10.0, "cpu_s": 90.0},  # 9
    ]
    jobs = ar.jobs_from(rows, unknown_weight=8.0)
    # x#1: no run before it anywhere -> the unknown weight (cores / slots, what a count assumes)
    # x#2: x's own runs before it: [1] -> 1.   x#3: median of [1, 3] -> 2.
    # z#1: no z run before it -> every run before it: median [1, 3, 5] -> 3.
    assert [j.weight for j in jobs] == [8.0, 1.0, 2.0, 3.0]


def test_the_cli_prints_both_rules_for_a_day(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    day_rows = [
        {
            "at": "2026-11-17T10:00:00+00:00",
            "kind": "push",
            "repo": "x",
            "epoch": T0 + 100,
            "wall_s": 100.0,
            "cpu_s": 100.0,
        },
        {
            "at": "2026-11-16T10:00:00+00:00",
            "kind": "push",
            "repo": "x",
            "epoch": T0 - 86400,
            "wall_s": 100.0,
            "cpu_s": 100.0,
        },
    ]
    rows = [{"v": 1, "subject": f"{r['repo']} {r['kind']}", **r} for r in day_rows]
    ledger.write_text("".join(json.dumps(r) + "\n" for r in rows))
    out = subprocess.run(
        [sys.executable, str(REPO_ROOT / "lib/admit_replay.py"), "--day", "2026-11-17",
         "--ledger", str(ledger), "--landq", str(tmp_path / "none"), "--json",
         "--rule", "count:2", "--rule", "cpu:16"],
        capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL,
    )  # fmt: skip
    got = json.loads(out.stdout)
    assert got["jobs"] == 1  # the day before is not replayed
    assert [r["rule"] for r in got["results"]] == ["count:2", "cpu:16"]
    assert got["results"][0]["gate_s"] == 100


@pytest.mark.parametrize("bad", ["count", "cpu:x", "share:2", "count:0"])
def test_a_rule_that_does_not_parse_is_refused(bad):
    with pytest.raises(ValueError):
        ar.Rule.parse(bad)
