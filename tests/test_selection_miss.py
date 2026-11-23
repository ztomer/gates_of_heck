"""lib/selection_miss.py: the selection-miss ledger, its replay of 2026-11-17 and the report
(GOH.SELECTAUDIT).

tests/fixtures/selection_misses/ is that day's every red land attempt that failed a named test:
the `FAILED` lines of its landq log, verbatim, and in attempts.json the diff, the selection's size
and the selection's entries for the failed files, as each tip's own selector chose them
(`python3 lib/selection_miss.py --replay`, run on the real queue).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from _fast_git import fast_init
from conftest import REPO_ROOT, git

sys.path.insert(0, str(REPO_ROOT / "lib"))
import landq_report
import selection_miss as sm

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "selection_misses"
DAY = "2026-11-17"


def _replayed() -> list[dict]:
    rows: list[dict] = []
    for a in json.loads((FIXTURE / "attempts.json").read_text()):
        failed = sm.failed_tests((FIXTURE / f"{a['stem']}.log").read_text())
        chosen = a["chosen"] + [f"padding/{n}" for n in range(a["selected"] - len(a["chosen"]))]
        rows += sm.judge("land", a["item"], a["sha"], DAY, a["changed"], chosen, failed)
    return rows


def test_the_days_reds_replayed_from_the_landq_logs_find_two_misses():
    rows = _replayed()
    misses: dict[str, list[str]] = {}
    for r in rows:
        if r["kind"] == "miss":
            misses.setdefault(f"{r['item']}@{r['sha'][:8]}", []).append(r["test"])
    # GOH.BUILDINCLUDES: lib/gate_identity.py reached tests/test_rust_gate.py only through
    # gates/_rust_proven.sh's "$ROOT/lib/..." (closed by GOH.PREPARITY); GOH.GRACESHAPES: a Rust
    # change before the binary's runners were chosen (GOH.AFFECTEDBINARY).
    assert sorted(misses) == ["GOH.BUILDINCLUDES@73ba690b", "GOH.GRACESHAPES@355f2e74"]
    assert len(misses["GOH.BUILDINCLUDES@73ba690b"]) == 10
    assert all(
        t.startswith("tests/test_rust_gate.py::") for t in misses["GOH.BUILDINCLUDES@73ba690b"]
    )
    grace = "test_a_marker_on_an_image_that_moves_from_the_grace_end_is_green_before_and_after"
    assert misses["GOH.GRACESHAPES@355f2e74"] == [f"tests/test_check_moving_tags.py::{grace}"]
    # ADAPTLANES (x2), CAPHEADROOM, PREPARITY: every red test was in the selection -- red in the
    # pre-step that chose it, or only on the rebased tip -- so none of them is a selection miss.
    judged = {f"{r['item']}@{r['sha'][:8]}": r for r in rows if r["kind"] == "judged"}
    for name in ("GOH.ADAPTLANES@262916c7", "GOH.ADAPTLANES@32c24b29", "GOH.PREPARITY@3709f571",
                 "GOH.CAPHEADROOM@42b91c6c"):  # fmt: skip
        assert judged[name]["failed"] and not judged[name]["missed"], name
    assert sm.summary(rows) == {
        DAY: {"judged": 19, "missed": 2, "tests": 11, "steps": 0, "full": 0, "full_missed": 0}
    }


def test_a_failed_test_is_read_from_each_summary_form_and_never_from_a_nested_assertion():
    log = (
        "FAILED tests/test_a.py::test_x - AssertionError: boom\n"
        "    FAILED tests/test_b.py::test_y@self-walk\n"
        "41:FAILED tests/test_c.py::TestK::test_z[True]\n"
        "    E   FAILED tests/test_fixture_copy.py::test_inner\n"
        "✗ [1/9] FAILED (exit status 1): ./gates/rust_gate.sh\n"
    )
    assert sm.failed_tests(log) == [
        "tests/test_a.py::test_x",
        "tests/test_b.py::test_y",
        "tests/test_c.py::TestK::test_z[True]",
    ]


def test_a_test_is_covered_by_its_file_its_node_or_its_class():
    chosen = ["tests/test_a.py", "tests/test_b.py::test_y", "tests/test_c.py::TestK"]
    assert sm.covered("tests/test_a.py::anything[1]", chosen)
    assert sm.covered("tests/test_b.py::test_y[param]", chosen)
    assert sm.covered("tests/test_c.py::TestK::test_z", chosen)
    assert not sm.covered("tests/test_b.py::test_y2", chosen)
    assert not sm.covered("tests/test_c.py::TestKK::test_z", chosen)


def _repo(tmp_path: Path) -> tuple[Path, str, str]:
    """A repo whose selector chooses tests/test_a.py for any change; (repo, base, tip)."""
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    (repo / "tools" / "affected_tests.py").write_text("print('tests/test_a.py')\n")
    fast_init(repo, "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD").strip()
    (repo / "a.py").write_text("x = 1\n")
    git(repo, "checkout", "-qb", "lane")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "lane")
    tip = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "checkout", "-q", "main")
    return repo, base, tip


LOG = "FAILED tests/test_a.py::test_x\nFAILED tests/test_b.py::test_y - boom\n"


def test_a_red_land_writes_one_miss_row_per_unchosen_test_once(tmp_path):
    repo, _, tip = _repo(tmp_path)
    q = tmp_path / "q"
    q.mkdir()
    log = tmp_path / "land.log"
    log.write_text(LOG)
    outcome = {"state": "red", "mode": "land", "repo": str(repo), "sha": tip, "log": str(log)}
    for _ in range(2):  # a second judging of the same attempt adds nothing
        assert sm.record_land(q, outcome, "GOH.X") == ""
    rows = sm.read(q)
    assert [(r["kind"], r.get("test")) for r in rows] == [
        ("judged", None),
        ("miss", "tests/test_b.py::test_y"),
    ]
    assert rows[0]["changed"] == ["a.py"] and rows[0]["selected"] == 1 and rows[0]["missed"] == 1
    assert sm.record_land(q, {**outcome, "state": "landed"}, "GOH.X") == ""
    assert "could not judge" in sm.record_land(q, {**outcome, "sha": "0" * 40}, "GOH.Y")
    assert len(sm.read(q)) == 2


def test_the_full_runs_shadow_judges_the_diff_since_the_last_run(tmp_path):
    repo, base, tip = _repo(tmp_path)
    q = tmp_path / "q"
    q.mkdir()
    log = tmp_path / "full.log"
    log.write_text(LOG)
    assert sm.shadow(q, repo, base, tip, log) == ""
    assert sm.shadow(q, repo, "", tip, log) == ""  # no earlier run: nothing to compare
    judged = [r for r in sm.read(q) if r["kind"] == "judged"]
    assert [(r["source"], r["item"], r["missed"]) for r in judged] == [
        ("full", f"main@{tip[:8]}", 1)
    ]
    assert sm.summary(sm.read(q))[judged[0]["day"]]["full_missed"] == 1


def test_goh_land_report_shows_the_selection_misses_per_day(tmp_path, capsys):
    (tmp_path / "done").mkdir()
    rows = _replayed()
    # a ledger file from before GOH.STATEDB: read beside goh.db's rows until wave 3 deletes it
    (tmp_path / "selection_misses.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert landq_report.run(tmp_path, "", False) == 0
    assert (
        f"selection misses {DAY}: 2 of 19 red lands judged (11%), 11 tests not chosen; "
        "full-run shadow: 0 of 0"
    ) in capsys.readouterr().out
    assert landq_report.run(tmp_path, "", True) == 0
    assert json.loads(capsys.readouterr().out)["selection"][DAY]["missed"] == 2


def test_a_batch_red_judges_the_stack_runs_its_item_was_in_not_only_its_own_log(tmp_path):
    """GOH.LANDLOGPRUNE (2026-11-19): its batch's gate 1 failed tests/test_check_lock_order.py,
    the bisect's lone gate 3 failed only a gate step, and the item's own log is gate 3's copy -- so
    the ledger read no failed test and recorded no miss. The record names its red runs."""
    repo, _, tip = _repo(tmp_path)
    q = tmp_path / "q"
    q.mkdir()
    own, stack = tmp_path / "own.log", tmp_path / "batch.gate1.log"
    own.write_text("✗ [8/9] FAILED (exit status 1): ./checks/check_x.py .\n")
    stack.write_text(LOG)
    outcome = {"state": "red", "mode": "land", "repo": str(repo), "sha": tip, "log": str(own),
               "red_logs": [str(stack), str(own)]}  # fmt: skip
    assert sm.attempt_logs(outcome) == [own, stack]
    assert sm.record_land(q, outcome, "GOH.X") == ""
    assert [(r["kind"], r.get("test") or r.get("step")) for r in sm.read(q)] == [
        ("judged", None),
        ("miss", "tests/test_b.py::test_y"),
        ("step", "./checks/check_x.py ."),  # gate 3's step, which no pre-step runs
    ]


PYTESTPIN = FIXTURE / "step_red-GOH.PYTESTPIN.log"
CANARY = '"$GOH_PY" ./scripts/release_canary.py'


def test_a_land_red_on_a_gate_step_the_pre_step_skipped_is_a_judged_miss(tmp_path):
    """GOH.PYTESTPIN (d0aa17a3, 2026-11-19): the pre-step ran its tests green and no canary, the
    land gate went red on step 9/9, scripts/release_canary.py, and no test failed -- so the ledger
    wrote nothing (GOH.LANDMISS1119). Its own landq log, verbatim, now writes the step's row."""
    repo, _, tip = _repo(tmp_path)
    q = tmp_path / "q"
    q.mkdir()
    outcome = {"state": "red", "mode": "land", "repo": str(repo), "sha": tip, "log": str(PYTESTPIN)}
    assert sm.red_steps(PYTESTPIN.read_text()) == [CANARY]
    assert sm.record_land(q, outcome, "GOH.PYTESTPIN") == ""
    rows = sm.read(q)
    assert [(r["kind"], r.get("step")) for r in rows] == [("judged", None), ("step", CANARY)]
    head, step = rows
    assert (head["failed"], head["missed"], head["steps"], head["steps_missed"]) == (0, 0, 1, 1)
    assert step["script"] == "scripts/release_canary.py" and step["pre_ran"] is False
    assert step["miss"] is True and step["changed"] == ["a.py"]
    assert sm.summary(rows)[head["day"]] == {"judged": 1, "missed": 1, "tests": 0, "steps": 1,
                                             "full": 0, "full_missed": 0}  # fmt: skip


def test_a_step_the_pre_step_ran_and_still_went_red_is_recorded_but_no_miss(tmp_path):
    """The pre-step ran the canary (affected_tests.canary's line) and the gate's canary still went
    red: the selection worked, the rebase or the estate: a step row, pre_ran, no miss."""
    repo, _, tip = _repo(tmp_path)
    q = tmp_path / "q"
    q.mkdir()
    ran = "→ pre-land: 1 consumer name lib/x.py -- the release canary, as the land gate runs it\n"
    log = tmp_path / "land.log"
    log.write_text(ran + PYTESTPIN.read_text())
    outcome = {"state": "red", "mode": "land", "repo": str(repo), "sha": tip, "log": str(log)}
    assert sm.record_land(q, outcome, "GOH.X") == ""
    head, step = sm.read(q)
    assert step["pre_ran"] is True and step["miss"] is False
    assert (head["steps"], head["steps_missed"]) == (1, 0)
    assert sm.summary([head, step])[head["day"]]["missed"] == 0
