"""lib/landq_size.py (GOH.ADAPTBATCH): the lander sizes each batch from its repo's gated red rate.

GOH_LANDQ_BATCH=3 paid at a low red rate and backfired at a high one (goh land --report: 11-20,
4 of 47 gated red, 1.05 gates per landed item; 11-23, 14 of 34, 1.90), because a batch of k is
green only with (1-p)^k and every red bisects. What must hold: the model counts the gates the
lander's own bisect spends (the counts tests/test_landq_batch.py and test_landq_bisect_par.py pin
on real lands); a planted red history gives k=1 and a clean one k>3; reds that never reached a
gate (an enqueue pre-step's) do not shrink the batch; GOH_LANDQ_BATCH still wins; the lander says
k and why, each record carries it, and --report shows it per batch.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT
from test_landq_batch import enqueue_all, env, gate_runs, landq, lane, recorded  # noqa: F401

sys.path.insert(0, str(REPO_ROOT / "lib"))
import landq_batch
import landq_report
import landq_size
import landq_state

REPO = "/x/alpha"


def plant(q: Path, n: int, state: str, repo: str = REPO, why: str = "exit 1", at: int = 1) -> None:
    """N done records of REPO in Q, each `state` (a red one with WHY; a landed one picked at 3)."""
    (q / "done").mkdir(parents=True, exist_ok=True)
    for i in range(n):
        rec = {"repo": repo, "mode": "land", "state": state, "log": "",
               "at": "2026-11-23T10:00:00Z"}  # fmt: skip
        rec.update({"k": 3} if state == "landed" else {"why": why})
        stem = f"{at * 10**15 + i:020d}-T.P{at}N{i}"
        landq_state.write(q / "done" / f"{stem}.json", rec)


@pytest.fixture(autouse=True)
def no_env_size(monkeypatch):
    monkeypatch.delenv("GOH_LANDQ_BATCH", raising=False)
    monkeypatch.setenv("GOH_GATE_SLOTS", "2")  # the halves gate at once, as on the lander's Mac


def test_the_model_spends_the_gates_the_real_bisect_spends():
    # three stacked, the middle red: serial 4 runs, the halves at once <= 5 (test_landq_batch)
    assert landq_size.bisect((False, True, False), parallel=False) == (4, 2)
    assert landq_size.bisect((False, True, False)) == (5, 2)
    # two, the first red: the stack, X, Y on main beside it -- 3, no 4th (test_landq_bisect_par)
    assert landq_size.bisect((True, False)) == (3, 1)
    assert landq_size.bisect((True, False), parallel=False) == (3, 1)
    # two, the second red: Y on X is the stack's tree, red with no 4th run
    assert landq_size.bisect((False, True)) == (3, 1)
    assert landq_size.bisect((False,) * 5) == (1, 5)  # green: one gate for all
    assert landq_size.bisect((True,)) == (1, 0)


def test_the_expected_cost_is_exact_where_it_can_be_done_by_hand():
    gates, landed = landq_size.expected(0.5, 2, parallel=False)
    # GG: 1 gate, 2 landed. RG: the stack, X, Y -- 3 gates, 1 landed. GR: the stack, X lands,
    # and Y on X is the stack's tree, known red -- 2 gates, 1 landed. RR: 3 gates, 0 landed.
    assert (gates, landed) == (0.25 * (1 + 3 + 2 + 3), 0.25 * 2 + 0.5 * 1)
    assert landq_size.per_landed(0.0, 4) == 0.25 and landq_size.per_landed(1.0, 3) == float("inf")


def test_the_best_size_falls_as_the_red_rate_rises():
    ks = [landq_size.best(p / 20) for p in range(21)]
    assert ks == sorted(ks, reverse=True), ks
    assert ks[0] == landq_size.MOST and ks[-1] == 1 and landq_size.best(0.41) == 1


def test_a_planted_red_history_gives_one_land_per_gate(tmp_path):
    plant(tmp_path, 8, "landed")
    plant(tmp_path, 12, "red", at=2)  # newer: 12 of the last 20 red
    got = landq_size.size(tmp_path, REPO)
    assert got.k == 1, got
    assert "12 of the last 20 gated lands of alpha red (p 0.60)" in got.why, got.why
    assert "at k=1" in got.why and "at 3" in got.why, got.why  # what the old size would cost


def test_a_clean_history_gives_a_batch_bigger_than_three(tmp_path):
    plant(tmp_path, 20, "landed")
    got = landq_size.size(tmp_path, REPO)
    assert got.k > 3 and got.k == landq_size.MOST, got
    assert "0 of the last 20" in got.why, got.why


def test_reds_that_never_reached_a_gate_do_not_shrink_the_batch(tmp_path):
    plant(tmp_path, 20, "landed")
    plant(tmp_path, 30, "red", why="enqueue pre-step: the pre-land tests failed", at=2)
    plant(tmp_path, 5, "red", why="conflict: did not stack on 1234abcd", at=3)
    plant(tmp_path, 5, "red", why="check: tests/test_x.py::test_y refused", at=4)
    plant(tmp_path, 5, "red", why="exit 75: no verdict: the gate never ran", at=5)
    plant(tmp_path, 20, "red", repo="/x/beta", at=6)  # another repo's reds are its own
    assert landq_size.size(tmp_path, REPO).k == landq_size.MOST


def test_too_little_history_keeps_the_default_and_says_so(tmp_path):
    plant(tmp_path, landq_size.LEAST - 1, "red")
    got = landq_size.size(tmp_path, REPO)
    assert got.k == landq_size.DEFAULT and "fewer than" in got.why, got


def test_an_explicit_batch_size_still_wins(tmp_path, monkeypatch):
    plant(tmp_path, 20, "red")
    monkeypatch.setenv("GOH_LANDQ_BATCH", "2")
    assert landq_size.size(tmp_path, REPO) == landq_size.Size(2, "GOH_LANDQ_BATCH=2 is set")
    monkeypatch.setenv("GOH_LANDQ_BATCH", "x")
    assert landq_size.size(tmp_path, REPO).k == landq_size.DEFAULT


def pend(q: Path, n: int) -> list[Path]:
    (q / "pending").mkdir(parents=True, exist_ok=True)
    out = []
    for i in range(n):
        path = q / "pending" / f"{9 * 10**18 + i:020d}-T.L{i}.json"
        landq_state.write(path, {"mode": "land", "repo": REPO, "worktree": f"/wt/{i}"})
        out.append(path)
    return out


def test_the_pick_takes_k_lands_and_says_k_and_why(tmp_path):
    said: list[str] = []
    plant(tmp_path, 20, "red")
    picked = landq_batch.pick(pend(tmp_path, 3), said.append)
    assert len(picked.paths) == 1 and picked.size and picked.size.k == 1, picked
    assert len(said) == 1 and "batch size k=1: 20 of the last 20" in said[0], said
    plant(tmp_path, 20, "landed", at=7)
    assert len(landq_batch.pick(pend(tmp_path, 5), said.append).paths) == 5
    assert "batch size k=6" in said[-1], said


def test_a_lone_land_records_the_size_it_was_picked_at(tmp_path):
    path = pend(tmp_path, 1)[0]
    size = landq_size.Size(1, "why")
    got = landq_batch.run(tmp_path, [path], None, lambda _q, _p: {"log": "", "state": "landed"},
                          size)  # fmt: skip
    assert (got[0]["k"], got[0]["k_why"]) == (1, "why"), got


def test_the_report_counts_no_gate_for_an_enqueue_red_and_shows_k_per_batch(tmp_path):
    plant(tmp_path, 2, "landed")  # lone lands, k 3 each
    plant(tmp_path, 3, "red", why="enqueue pre-step: the pre-land tests failed", at=2)
    rec = {"repo": REPO, "mode": "land", "state": "landed", "log": "", "k": 6,
           "at": "2026-11-23T10:00:00Z", "batch": {"id": "B", "items": 2, "gates": 1}}  # fmt: skip
    for i in (0, 1):
        landq_state.write(tmp_path / "done" / f"{3 * 10**15 + i:020d}-T.B{i}.json", rec)
    (row,) = landq_report.build(landq_report.read_attempts(tmp_path))["rows"]
    assert (row["landed"], row["red"], row["gates"]) == (4, 3, 3), row  # 2 lone + 1 batch gate
    assert row["k"] == {3: 2, 6: 1}, row  # two lone lands at 3, one batch of two at 6
    text = landq_report.render(landq_report.build(landq_report.read_attempts(tmp_path)))
    assert "batch size k: 3 x2, 6 x1" in text, text


def test_the_lander_gates_each_land_alone_after_a_red_history(repo, tmp_path, env):  # noqa: F811
    wts = [(lane(repo, tmp_path, n, {f"{n}.txt": "1\n"}), f"T.{n.upper()}") for n in ("one", "two")]
    enqueue_all(env, wts)
    q = Path(env["GOH_LANDQ_DIR"])
    head = json.loads(min((q / "pending").glob("*.json")).read_text())
    plant(q, 20, "red", repo=head["repo"])  # the repo's newest 20 gated lands: all red
    done = landq(env, "--drain")
    assert done.returncode == 0, done.stdout + done.stderr
    assert gate_runs(env) == ["one", "two"], gate_runs(env)  # one gate each, no stack of two
    assert "batch size k=1: 20 of the last 20 gated lands" in done.stdout, done.stdout
    got = recorded(env)
    assert all(got[i]["state"] == "landed" and got[i]["k"] == 1 for _, i in wts), got
