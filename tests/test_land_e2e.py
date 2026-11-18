"""A land END TO END is one budget figure per item (lib/land_e2e.py, GOH.LANDTARGET).

Owner target 2026-11-18: a goh land end to end -- enqueue request, pre-step, queue wait, land gate,
retries, main fast-forwarded -- in 5 min plain and 10 min instrumented (the land ran the coverage
step). Each item's e2e is its own request -> landed time, its retries in it; a batch of N shares
one gate and is N runs. The two subjects judge the median of their last five runs.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import budget
import land_e2e
import timing_ledger

MIN = 60.0
T0 = dt.datetime(2026, 11, 18, 10, 0, tzinfo=dt.UTC)
COV_LINE = "  724.64 s 3396.78 s cpu  rc=0   wt4                  coverage (floor 95%)\n"
PLAIN_LINE = "   27.41 s   63.14 s cpu  rc=0   wt   cargo test (plain: the coverage stands)\n"


def iso(minutes: float) -> str:
    return (T0 + dt.timedelta(minutes=minutes)).isoformat()


def ns(minutes: float) -> str:
    return f"{int((T0 + dt.timedelta(minutes=minutes)).timestamp() * 1e9):020d}"


def put(q: Path, filed: float, item: str, state: str, started: float, finished: float,
        log: str = PLAIN_LINE, **extra: object) -> str:  # fmt: skip
    """A done record and its log, filed at minute FILED, run STARTED..FINISHED (minutes)."""
    for d in ("done", "logs"):
        (q / d).mkdir(parents=True, exist_ok=True)
    stem = f"{ns(filed)}-{item}"
    rec = {"repo": "/p/gates_of_heck", "mode": "land", "state": state, "item": item,
           "started": iso(started), "finished": iso(finished),
           "log": str(q / "logs" / f"{stem}.log"), **extra}  # fmt: skip
    (q / "done" / f"{stem}.json").write_text(json.dumps(rec))
    (q / "logs" / f"{stem}.log").write_text(log)
    return stem


@pytest.fixture
def ledger(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("GOH_TIMING_DIR", str(tmp_path / "t"))
    monkeypatch.setenv("GOH_SHIP_DIR", str(tmp_path / "ships"))
    return tmp_path / "q"


def test_the_owners_targets_are_named_constants_with_the_ships_hysteresis() -> None:
    plain, inst = budget.KINDS[land_e2e.PLAIN], budget.KINDS[land_e2e.INSTRUMENTED]
    assert (plain.arm_s, plain.breach_s) == (5 * MIN, 7.5 * MIN)
    assert (inst.arm_s, inst.breach_s) == (10 * MIN, 15 * MIN)
    assert (budget.LAND_E2E_PLAIN_ARM_S, budget.LAND_E2E_PLAIN_BREACH_S) == (300, 450)
    assert (budget.LAND_E2E_INSTRUMENTED_ARM_S, budget.LAND_E2E_INSTRUMENTED_BREACH_S) == (600, 900)
    assert plain.agg == inst.agg == "median5"
    assert plain.writer and inst.writer  # fed: never listed "no writer yet"


@pytest.mark.parametrize("name", [land_e2e.PLAIN, land_e2e.INSTRUMENTED])
def test_one_slow_land_among_four_fast_ones_does_not_breach(name: str) -> None:
    k = budget.KINDS[name]
    fast = [{"wall_s": k.arm_s, "ok": True} for _ in range(4)]
    slow = {"wall_s": k.breach_s * 3, "ok": True}
    assert budget.state_of([*fast, slow], k) == budget.ARMED
    assert budget.state_of([*fast, slow, slow, slow], k) == budget.BREACHED  # the median moved


def test_a_plain_land_spans_its_request_to_landed(ledger: Path) -> None:
    put(ledger, 1.0, "GOH.A", "landed", 2.0, 4.5, requested=iso(0.0), pre_s=55.0)
    (row,) = land_e2e.rows(ledger)
    assert (row["kind"], row["subject"], row["wall_s"]) == (
        land_e2e.PLAIN,
        "goh land e2e plain",
        270.0,
    )
    assert (row["from"], row["pre_s"], row["item"], row["tries"]) == (
        "requested",
        55.0,
        "GOH.A",
        1,
    )


def test_a_record_without_its_request_time_starts_at_its_filing(ledger: Path) -> None:
    """Records before GOH.LANDTARGET carry no request time: the filing (the name) is the start,
    and the row says so -- the enqueue's pre-step is not in it."""
    put(ledger, 1.0, "GOH.A", "landed", 2.0, 4.0)
    (row,) = land_e2e.rows(ledger)
    assert (row["wall_s"], row["from"]) == (180.0, "filed")


def test_the_coverage_step_makes_a_land_instrumented(ledger: Path) -> None:
    put(ledger, 0.0, "GOH.A", "landed", 1.0, 9.0, log=PLAIN_LINE + COV_LINE)
    (row,) = land_e2e.rows(ledger)
    assert row["kind"] == land_e2e.INSTRUMENTED and row["subject"] == "goh land e2e instrumented"


def test_a_batch_gate_log_counts_for_its_items(ledger: Path) -> None:
    stem = put(ledger, 0.0, "GOH.A", "landed", 1.0, 9.0)
    (ledger / "logs" / f"{stem}.gate1.log").write_text(COV_LINE)
    assert [r["kind"] for r in land_e2e.rows(ledger)] == [land_e2e.INSTRUMENTED]


def test_retries_are_in_the_items_e2e_from_its_first_request(ledger: Path) -> None:
    put(ledger, 0.0, "GOH.A", "red", 1.0, 3.0)
    put(ledger, 1.0, "GOH.B", "landed", 3.0, 5.0)
    put(ledger, 6.0, "GOH.A", "landed", 6.0, 8.0)
    by = {r["item"]: r for r in land_e2e.rows(ledger)}
    assert (by["GOH.A"]["wall_s"], by["GOH.A"]["tries"]) == (480.0, 2)
    assert (by["GOH.B"]["wall_s"], by["GOH.B"]["tries"]) == (240.0, 1)


def test_a_batch_of_n_is_n_runs_each_its_own_filed_to_landed(ledger: Path) -> None:
    for filed, item in ((0.0, "GOH.A"), (2.0, "GOH.B"), (3.0, "GOH.C")):
        put(ledger, filed, item, "landed", 4.0, 9.0)  # one shared gate, 4..9
    assert sorted(r["wall_s"] for r in land_e2e.rows(ledger)) == [360.0, 420.0, 540.0]


def test_a_red_chain_a_push_and_a_record_without_times_are_no_e2e(ledger: Path) -> None:
    put(ledger, 0.0, "GOH.A", "red", 1.0, 3.0)
    put(ledger, 0.0, "GOH.P", "landed", 1.0, 3.0, mode="push")
    stem = put(ledger, 0.0, "GOH.N", "landed", 1.0, 3.0)
    rec = json.loads((ledger / "done" / f"{stem}.json").read_text())
    del rec["finished"]
    (ledger / "done" / f"{stem}.json").write_text(json.dumps(rec))
    assert land_e2e.rows(ledger) == []


def test_a_land_whose_log_is_gone_is_not_guessed_plain(ledger: Path) -> None:
    stem = put(ledger, 0.0, "GOH.A", "landed", 1.0, 3.0)
    (ledger / "logs" / f"{stem}.log").unlink()
    assert land_e2e.rows(ledger) == []


def test_the_backfill_writes_each_e2e_once_and_the_budget_judges_it(ledger: Path) -> None:
    for i in range(5):
        put(ledger, 10.0 * i, f"GOH.P{i}", "landed", 10.0 * i + 1, 10.0 * i + 4)
    assert land_e2e.backfill(ledger) == 5
    assert land_e2e.backfill(ledger) == 0
    assert {r["kind"] for r in timing_ledger.read()} == {land_e2e.PLAIN}
    j = next(s for s in budget.snapshot()["subjects"] if s["subject"] == "goh land e2e plain")
    assert (j["state"], j["median_s"], j["runs"]) == (budget.ARMED, 240.0, 5)


def test_a_breached_e2e_names_its_self_improvement_item_in_the_json(ledger: Path) -> None:
    put(ledger, 0.0, "GOH.F", "landed", 1.0, 4.0)
    for i in range(1, 4):
        put(ledger, 10.0 * i, f"GOH.S{i}", "landed", 10.0 * i + 1, 10.0 * i + 9)
    land_e2e.backfill(ledger)
    snap = budget.snapshot()
    (b,) = [b for b in snap["breaches"] if b["subject"] == "goh land e2e plain"]
    assert b["kind"] == land_e2e.PLAIN and b["figure_s"] == 540.0 and b["breach_s"] == 450
    assert "self-improvement" in b["action"] and "goh land e2e plain" in b["action"]
    assert "goh land e2e plain BREACHED" in budget.render(snap)


def test_a_subject_not_breached_is_no_breach(ledger: Path) -> None:
    put(ledger, 0.0, "GOH.F", "landed", 1.0, 4.0)
    land_e2e.backfill(ledger)
    assert budget.snapshot()["breaches"] == []


def test_both_e2e_subjects_are_listed_when_nothing_landed(ledger: Path) -> None:
    names = {u["subject"]: u["why"] for u in budget.snapshot()["unmeasured"]}
    assert names[land_e2e.PLAIN] == names[land_e2e.INSTRUMENTED] == "no landed run recorded"


def test_the_e2e_carries_its_gates_cpu_for_the_relative_guard(ledger: Path) -> None:
    put(ledger, 0.0, "GOH.A", "landed", 1.0, 4.0)
    inside = (T0 + dt.timedelta(minutes=4)).timestamp()
    gates = [{"kind": "land", "repo": "goh", "epoch": inside, "cpu_s": 900.0},
             {"kind": "land", "repo": "goh", "epoch": inside + 600, "cpu_s": 5.0},
             {"kind": "land", "repo": "x", "epoch": inside, "cpu_s": 7.0}]  # fmt: skip
    (row,) = land_e2e.rows(ledger, gates)
    assert row["cpu_s"] == 900.0


def test_the_e2e_says_when_its_land_reused_the_enqueues_pre_step_verdict(ledger: Path) -> None:
    """GOH.PRELANDTWICE: a land that ran no second pre-step is a shorter wall for a reason."""
    put(ledger, 0.0, "GOH.A", "landed", 1.0, 4.0)
    inside = (T0 + dt.timedelta(minutes=4)).timestamp()
    gates = [{"kind": "land", "repo": "goh", "epoch": inside, "cpu_s": 9.0,
              "pre_reused": "0123456789ab whole"}]  # fmt: skip
    (row,) = land_e2e.rows(ledger, gates)
    assert row["pre_reused"] == "0123456789ab whole"
    (plain,) = land_e2e.rows(ledger, [{**gates[0], "pre_reused": None}])
    assert plain.get("pre_reused") is None


def test_an_e2e_without_cpu_seconds_is_never_judged_on_the_wall() -> None:
    k = budget.KINDS[land_e2e.PLAIN]
    runs = [{"wall_s": w, "ok": True} for w in (100, 100, 100, 100, 400)]
    assert budget.trend(runs, k)["verdict"] == "no cpu-s"
    runs = [{"wall_s": 100, "ok": True, "cpu_s": c} for c in (100, 100, 100, 100, 400)]
    assert budget.trend(runs, k)["verdict"] == "regressed"
