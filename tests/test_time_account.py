"""lib/time_account.py (GOH.TIMEACCOUNT): the account closes, thrown-away seconds sum by cause, and
each gate step's yield names a step never red alone -- on planted rows, in memory."""

from __future__ import annotations

import pytest
import run_ends
import time_account as ta
from _kv_fixture import Held


@pytest.fixture
def held(monkeypatch) -> Held:
    return Held().lay(monkeypatch)


def test_a_gap_after_the_last_step_is_unaccounted_and_parallel_steps_are_overlap() -> None:
    st = {"e2e": [100.0, None], "enqueue pre-step": [20.0, None], "queue wait": [5.0, None],
          "retries": [0.0, None], "lander run": [70.0, None], "  pre-land": [10.0, None],
          "  gate": [50.0, None], "    slot wait": [4.0, None], "    step a": [30.0, 1.0],
          "      > nested": [99.0, None], "    step b": [10.0, None],
          "  lander other": [10.0, None]}  # fmt: skip
    got = ta.land_account(st)
    # 5 s of the e2e, 10 s of the run (after the gate) and 6 s of the gate belong to no phase
    assert got == {"wall": 100.0, "named": 79.0, "unaccounted": 21.0, "overlap": 0.0}
    st["    step b"] = [30.0, None]  # beside step a: the gate's steps sum past it
    assert ta.land_account(st)["overlap"] == 14.0
    assert ta.land_account({"x": [1.0, None]}) is None
    t = ta.totals([got, {"wall": 100.0, "named": 100.0, "unaccounted": 0.0, "overlap": 0.0}], 5.0)
    assert t["unaccounted_pct"] == 10.5 and t["fault"] and t["median_unaccounted_s"] == 10.5
    assert not ta.totals([], 5.0)["fault"]


def test_thrown_away_seconds_sum_by_cause_and_a_recorded_check_counts_once(held) -> None:
    con = held.goh
    con.execute("INSERT INTO lands(queue, run, item, repo, mode, state, at, cause, started, "
                "finished) VALUES ('q', 'r1', 'I', 'g', 'land', 'red', 10, 'conflict', "
                "'2026-11-25T00:00:00Z', '2026-11-25T00:01:40Z')")  # fmt: skip
    con.execute("INSERT INTO lands(queue, run, item, repo, mode, state, at, why) VALUES "
                "('q', 'r2', 'I', 'g', 'land', 'red', 11, 'check: refused')")  # fmt: skip
    for run, exit_ in (("1-1.000", 137), ("2-1.000", 1)):
        con.execute("INSERT INTO spans(kind, repo, at, run, wall_s, ok, exit) VALUES "
                    "('land check:enqueue', 'g', 12, ?, 30, 0, ?)", (run, exit_))  # fmt: skip
    run_ends.begin("check", "1-1.000", pid=1, now=12.0)
    run_ends.end("check", "1-1.000", "stopped", "exit 137", now=42.0)
    got = ta.thrown(con, 0.0, run_ends.rows())
    assert got == {"land red: conflict": [1, 100.0, 0], "check stopped: exit 137": [1, 30.0, 0],
                   "land check:enqueue refused": [1, 30.0, 0],
                   "land red: check": [1, 0.0, 1]}  # fmt: skip


def test_a_step_red_only_beside_another_is_never_the_only_red(held) -> None:
    rows = [("01-A", "rust", 9000, 1), ("01-A", "double", 5000, 1), ("02-B", "rust", 8000, 1),
            ("02-B", "double", 4000, 0), ("03-C", "rust", 1000, 0), ("03-C", "double", 1000, 0),
            ("ship-x", "rust", 1, 1)]  # fmt: skip
    held.cache.executemany(
        "INSERT INTO steps(run, part, seq, at, label, tier, ms, rc) "
        "VALUES (?, '', ?, 100, ?, 'step', ?, ?)",
        [(r, i, lab, ms, rc) for i, (r, lab, ms, rc) in enumerate(rows)],
    )
    got = {y["step"]: y for y in ta.yields(0.0)}
    assert got["rust"] | {} == {"step": "rust", "runs": 3, "seconds": 18.0, "red": 2,
                                 "only_red": 1}  # fmt: skip
    assert (got["double"]["red"], got["double"]["only_red"]) == (1, 0)
    assert "never the only red" in "\n".join(
        ta.render(
            {
                "lands": ta.totals([], 5),
                "ships": ta.totals([], 5),
                "thrown": {},
                "yield": list(got.values()),
                "readings": {
                    "pre-step phases": {
                        "n": 0,
                        "pre-step": None,
                        "wait": None,
                        "run": None,
                        "remote worker": None,
                        "remote mac": None,
                    },
                    "enqueue check": {
                        "n": 0,
                        "median_s": None,
                        "green": 0,
                        "commit gates recorded": 0,
                    },
                    "tests": {
                        "pre-steps": 0,
                        "reused": 0,
                        "refused": 0,
                        "by": {},
                        "chosen (remote)": None,
                        "recorded": 0,
                    },
                    "cpu by owner": {
                        "windows": 0,
                        "wall_s": 0,
                        "cpu_s": {},
                        "sampler_pct": None,
                        "max_pct": 0.1,
                    },
                    "lane time": {"lanes": 0, "wall_s": 0, "median_wall_s": None, "by kind": {}},
                },
            }
        )
    )
