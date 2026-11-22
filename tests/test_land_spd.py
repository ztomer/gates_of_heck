"""`goh land-profile` counts a land the one way the SPD exit is judged (GOH.LANDPROFILEFIX).

On 2026-11-21 three hand counts of one day's lands gave 15.2, 31.0 and 41.6 min and `loopctl
phase-goal SPD` printed 45.3. The causes pinned here: a batch's one pre-land and gate run were
counted on every member; the e2e started at the request, so a red enqueue pre-step and the time
before it were outside it; and no figure was commit to landed, the share of lands that ran the
whole pre-land again, or the pre-step's wait and run seconds.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT, git, hermetic_env
from test_land_profile import REPO, at, put

sys.path.insert(0, str(REPO_ROOT / "lib"))
import land_e2e
import land_prestep
import land_profile
import land_spd

COVERAGE = "  100.00 s 300.00 s cpu  rc=0   wt   coverage (floor 93.1%)\n"
PRE = "✓ pre-land green in {} s: waited {} s, ran {} s of 888 s at {} workers\n"


def ns(minutes: float) -> str:
    return f"{int(at(minutes).timestamp() * 1e9):020d}"


def iso(minutes: float) -> str:
    return at(minutes).isoformat()


def test_a_batchs_shared_runs_count_once_its_own_stages_per_item(tmp_path: Path) -> None:
    """B1 and B2 landed as one batch (one lander run 5..20, one pre-land of 120 s, one gate)."""
    q = tmp_path / "q"
    shared = COVERAGE + PRE.format(120, 0, 120, 8)
    stem = f"{ns(0)}-GOH.B1"
    batch = {"id": stem, "items": 2, "gates": 1}
    put(q, 0, "GOH.B1", "landed", 5, 20, shared, requested=iso(-1), batch=batch, slot_wait_s=30.0)
    put(q, 2, "GOH.B2", "landed", 5, 20, shared, requested=iso(1), batch=batch, slot_wait_s=30.0)
    gates = [
        {
            "kind": "land",
            "repo": "goh",
            "epoch": at(19).timestamp(),
            "wall_s": 800.0,
            "cpu_s": 900.0,
        }
    ]
    rows = land_profile.lands(q, gates)
    t = {r["stage"]: r for r in land_profile.table(rows)}
    assert t["e2e"]["n"] == 2 and t["e2e"]["max_s"] == 21 * 60  # each item waited its own wait
    assert t["queue wait"]["n"] == 2 and t["retries"]["n"] == 2
    for shared_stage in ("lander run", "  pre-land", "  gate", "    slot wait"):
        assert t[shared_stage]["n"] == 1, shared_stage
    assert t["  pre-land"]["median_s"] == 120.0 and t["  gate"]["median_s"] == 800.0


def test_the_e2e_starts_at_the_first_enqueue_attempt_red_ones_included(tmp_path: Path) -> None:
    """E1: an enqueue at -5 that filed no record (only its log), a red enqueue pre-step requested
    at -2, the landing requested at 3 (filed 4), run 5..20. E2: the red one and the landing only."""
    q = tmp_path / "q"
    (q / "logs").mkdir(parents=True)
    (q / "logs" / f"enqueue-{ns(-5)}-GOH.E1.log").write_text("✗ the pre-step was killed\n")
    for item in ("GOH.E1", "GOH.E2"):
        red = {"repo": REPO, "mode": "land", "state": "red", "item": item, "requested": iso(-2),
               "why": "enqueue pre-step: the pre-land tests failed"}  # fmt: skip
        (q / "done").mkdir(exist_ok=True)
        (q / "done" / f"{ns(-1)}-{item}.json").write_text(json.dumps(red))
        put(q, 4, item, "landed", 5, 20, COVERAGE, requested=iso(3))
    e1, e2 = land_profile.lands(q, [])
    assert e1["stages"]["e2e"][0] == 25 * 60  # from the enqueue log, never from the request at 3
    assert e1["stages"]["earlier attempts"][0] == 8 * 60
    assert e1["stages"]["enqueue pre-step"][0] == 60.0  # the landing's own request, not the red's
    assert e1["start"] == at(-5)
    assert e2["stages"]["e2e"][0] == 22 * 60  # from the red enqueue's request at -2
    assert e2["stages"]["queue wait"][0] == 60.0 and e2["stages"]["retries"][0] == 0.0


def test_the_pre_steps_wait_run_and_workers_are_read_from_its_output() -> None:
    lines = ["noise\n", PRE.format(30, 2, 28, 16), "✓ enqueued X\n"]
    assert land_spd.pre_run(lines) == {"wait_s": 2, "run_s": 28, "limit_s": 888, "workers": 16}
    assert land_spd.pre_run([PRE.format(5, 0, 5, "?").replace("888", "?")]) == {
        "wait_s": 0, "run_s": 5, "limit_s": None, "workers": None}  # fmt: skip
    assert land_spd.pre_run(["nothing"]) is None


def test_the_enqueue_pre_step_keeps_its_wait_and_run_in_the_land_record(tmp_path: Path) -> None:
    script = tmp_path / "pre.sh"
    script.write_text(f"printf '{PRE.format(30, 2, 28, 16)}'\n")
    took: dict = {}
    land_prestep.run(tmp_path, "a" * 40, script, (), took=took)
    assert took == {"wait_s": 2, "run_s": 28, "limit_s": 888, "workers": 16}
    req = {"enqueued_at": "t", "pre_run": took}
    assert land_e2e.asked(req) == {"requested": "t", "pre_run": took}


@pytest.fixture
def spd(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    """A repo of three commits authored at 0, 10 and 20 min, and three lands of them: X landed A
    at 12 (the lander reused the verdict; the enqueue took a kept one; its log has the pre-step's
    line), Y landed B at 40 and Z landed C at 50 (both ran the whole pre-land again; their
    records carry the pre-step's figures)."""
    repo = tmp_path / "repo"
    fast_init(repo)
    shas = {}
    for name, minutes in (("A", 0), ("B", 10), ("C", 20)):
        (repo / name).write_text(name)
        git(repo, "add", name)
        stamp = {"GIT_AUTHOR_DATE": iso(minutes), "GIT_COMMITTER_DATE": iso(minutes)}
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                        "commit", "-q", "-m", name], check=True, env=hermetic_env(**stamp),
                       stdin=subprocess.DEVNULL)  # fmt: skip
        shas[name] = git(repo, "rev-parse", "HEAD").strip()
    q = tmp_path / "q"
    again = (
        "→ pre-land: the verdict the enqueue judged at 1 is not 2's (lib/x.py moved) -- run again\n"
    )
    x = put(q, 1, "GOH.X", "landed", 2, 12, "✓ pre-land: the verdict d judged at a holds for b -- "
            "not run again: no moved path is read\n" + COVERAGE, requested=iso(0), repo=str(repo),
            landed=shas["A"],
            pre_affected={"took": "t", "by": "l", "lane_s": 1.0, "tests": 3})  # fmt: skip
    put(q, 11, "GOH.Y", "landed", 12, 40, again + PRE.format(100, 0, 100, 8) + COVERAGE,
        requested=iso(10), repo=str(repo), landed=shas["B"], pre_affected={"ran": "differs"},
        pre_run={"wait_s": 30, "run_s": 300, "workers": 8})  # fmt: skip
    put(q, 12, "GOH.Z", "landed", 40, 50, PRE.format(99, 1, 98, 4) + COVERAGE, requested=iso(11),
        repo=str(repo), landed=shas["C"], pre_affected={"ran": "differs"},
        pre_run={"wait_s": 10, "run_s": 100, "workers": 4})  # fmt: skip
    (q / "logs" / f"enqueue-{ns(0)}-GOH.X.log").write_text(
        PRE.format(61, 1, 60, 16) + f"✓ enqueued GOH.X @ abc (land): {q}/pending/{x}.json\n"
    )
    return q, repo, shas


def test_commit_to_landed_the_full_rerun_share_and_the_pre_steps_wait_run_workers(spd) -> None:
    q, _, _ = spd
    s = land_spd.summary(q, land_profile.lands(q, []))
    assert (s["lands"], s["commit_to_landed_n"]) == (3, 3)
    assert s["commit_to_landed_s"] == 30 * 60.0  # 12, 30 and 30 min: author date to `finished`
    assert (s["whole_rerun"], s["whole_rerun_of"]) == (2, 3)  # X's lander reused the verdict
    assert (s["prestep_ran"], s["prestep_took"]) == (2, 1)
    assert (s["prestep_wait_s"], s["prestep_run_s"], s["prestep_workers"]) == (10.0, 100.0, 8.0)
    assert s["prestep_without_record"] == 0  # X's figures came from its enqueue log


def test_the_command_prints_the_last_n_and_the_lands_since_a_commit(spd, tmp_path, monkeypatch,
                                                                    capsys) -> None:  # fmt: skip
    q, _, shas = spd
    (tmp_path / "t").mkdir()
    monkeypatch.setenv("GOH_TIMING_DIR", str(tmp_path / "t"))
    monkeypatch.setenv("GOH_LANDQ_DIR", str(q))
    assert land_profile.main(["--last", "2", "--since", shas["A"][:10]]) == 0
    out = capsys.readouterr().out
    assert "== SPD last 2: 2 lands" in out and f"== SPD since {shas['A'][:10]}: 2 lands" in out
    assert "commit to landed: median 30.0 min" in out
    assert "lander re-ran the whole pre-land: 100% (2/2)" in out
    assert "waited 20 s, ran 200 s at 6 workers (median)" in out
    assert land_profile.main(["--since", shas["B"], "--json", "--last", "5"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["spd"][f"since {shas['B']}"]["lands"] == 1  # strictly past B: Z only
    assert got["spd"]["last 5"]["lands"] == 3
    assert dt.timedelta(minutes=30).total_seconds() == got["spd"]["last 5"]["commit_to_landed_s"]
