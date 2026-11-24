"""Every land attempt is a ledger row, and an item's retries are one budget figure
(lib/land_attempts.py, GOH.GATEBUDGET2).

Owner 2026-11-15: "apply the budget to the land queue retries too". GOH.REUSEAUDIT went red after
537 s and landed on its second attempt; the ledger held only the land that took the lock, so the
red attempt was nobody's cost. A red then a green land of one item is two `land attempt` rows,
numbered 1 and 2, and `goh budget` shows the item's chain as one `land retries` run.
"""

from __future__ import annotations

import sys
from pathlib import Path

from conftest import REPO_ROOT, git
from test_landq import env, landq, make_repo  # noqa: F401 -- `env` is the queue's fixture

sys.path.insert(0, str(REPO_ROOT / "lib"))
import budget
import land_attempts
import land_records
import timing_ledger

FAIL = "#!/bin/sh\nsleep 0.2\nexit 1\n"
PASS = "#!/bin/sh\nsleep 0.2\nexit 0\n"


def attempt(rid: str, item: str, ok: bool, wall: float, epoch: float, repo: str = "goh") -> dict:
    return {"kind": land_attempts.KIND, "repo": repo, "item": item, "ok": ok, "wall_s": wall,
            "epoch": epoch, "at": f"t{epoch}", "ref": rid}  # fmt: skip


def test_a_red_then_green_chain_is_one_retries_run_of_its_summed_wall() -> None:
    rows = [
        attempt("1", "GOH.A", False, 537.0, 1),
        attempt("2", "GOH.B", True, 100.0, 2),
        attempt("3", "GOH.A", True, 300.0, 3),
    ]
    runs = land_attempts.retry_runs(rows)
    assert [(r["item"], r["wall_s"], len(r["attempts"]), r["ok"]) for r in runs] == [
        ("GOH.B", 100.0, 1, True),
        ("GOH.A", 837.0, 2, True),
    ]
    assert runs[1]["subject"] == "goh land retries"
    assert land_attempts.chain_line(runs[1]) == "GOH.A: 2 attempts: red 537 s, landed 300 s = 837 s"


def test_a_chain_still_red_is_an_open_failed_run() -> None:
    runs = land_attempts.retry_runs([attempt("1", "GOH.A", False, 400.0, 1),
                                     attempt("2", "GOH.A", False, 700.0, 2)])  # fmt: skip
    assert [(r["wall_s"], r["ok"]) for r in runs] == [(1100.0, False)]
    k = budget.KINDS[land_attempts.RETRIES]
    assert budget.counts(runs[0], k)  # past BREACH: a red chain proves its cost


def test_the_attempt_number_counts_reds_since_the_items_last_landing(tmp_path: Path) -> None:
    lq = tmp_path
    for i, (item, state) in enumerate(
        [("GOH.A", "red"), ("GOH.A", "landed"), ("GOH.A", "red"), ("GOH.B", "red")]
    ):
        land_records.write(lq, f"{i:020d}-{item}", {"state": state})
    assert land_attempts.attempt_of(lq, "GOH.A") == 2
    assert land_attempts.attempt_of(lq, "GOH.B") == 2
    assert land_attempts.attempt_of(lq, "GOH.C") == 1


def test_a_red_then_green_land_through_the_queue_is_two_rows_and_one_budget_chain(
    tmp_path: Path,
    env: dict[str, str],  # noqa: F811 -- the fixture imported above
    monkeypatch,
) -> None:
    ledger = tmp_path / "timing"
    env["GOH_TIMING_DIR"] = str(ledger)
    repo, remote, sha = make_repo(tmp_path, "delta")
    hook = repo / ".git" / "hooks" / "pre-push"
    for gate in (FAIL, PASS):
        hook.write_text(gate)
        done = landq(env, "--enqueue", str(repo), sha, "--item", "T.RETRY", "--no-start")
        assert done.returncode == 0, done.stdout + done.stderr
        done = landq(env, "--drain")
        assert done.returncode == 0, done.stdout + done.stderr
    assert git(remote, "rev-parse", "main").strip() == sha
    records = [r for _, r in land_records.records(tmp_path / "landq")]
    assert [(r["item"], r["attempt"], r["state"]) for r in records] == [
        ("T.RETRY", 1, "red"),
        ("T.RETRY", 2, "landed"),
    ]
    monkeypatch.setenv("GOH_TIMING_DIR", str(ledger))
    monkeypatch.setenv("GOH_LANDQ_DIR", str(tmp_path / "landq"))
    rows = [r for r in timing_ledger.read() if r["kind"] == land_attempts.KIND]
    assert [(r["subject"], r["attempt"], r["ok"]) for r in rows] == [
        ("delta land attempt", 1, False),
        ("delta land attempt", 2, True),
    ]
    assert all(r["wall_s"] > 0 for r in rows), rows
    assert land_attempts.backfill(tmp_path / "landq") == 0  # never doubled
    snap = budget.snapshot()
    chain = next(s for s in snap["subjects"] if s["subject"] == "delta land retries")
    assert [a[:2] for a in chain["attempts"]] == [[1, "red"], [2, "landed"]], chain
    shown = budget.render(snap)
    assert "T.RETRY: 2 attempts: red" in shown, shown


def test_done_records_with_their_times_are_backfilled_once(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GOH_TIMING_DIR", str(tmp_path / "t"))
    lq = tmp_path
    times = {"started": "2026-11-17T10:00:00+00:00", "finished": "2026-11-17T10:08:57+00:00"}
    second = {"started": "2026-11-17T10:10:00+00:00", "finished": "2026-11-17T10:15:00+00:00"}
    goh = {"repo": "/p/gates_of_heck"}
    land_records.write(lq, "01-GOH.X", {**goh, "state": "red", **times})
    land_records.write(lq, "02-GOH.X", {**goh, "state": "landed", **second})
    land_records.write(lq, "03-GOH.Y", {"repo": "/p/x", "state": "landed"})  # no times
    assert land_attempts.backfill(lq) == 2
    assert land_attempts.backfill(lq) == 0
    rows = timing_ledger.read()
    assert [(r["subject"], r["item"], r["attempt"], r["wall_s"]) for r in rows] == [
        ("goh land attempt", "GOH.X", 1, 537.0),
        ("goh land attempt", "GOH.X", 2, 300.0),
    ]
