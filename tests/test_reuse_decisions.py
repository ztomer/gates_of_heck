"""Every reuse decision of a land is recorded with its reason (GOH.SUITEONCEMISS).

The lander's pre-land logged why it ran the set again, but the gate's suite said why it did not take
the enqueue's verdict only in the gate's own temp log, gone with the land: over the last 24 lands of
2026-11-21 the ledger named 3 suite reuses and not one reason for the 21 misses, and 6 pre-land
decisions (a batch with no enqueue verdict) left no line at all. Now tools/pytest.sh appends each
suite decision to `<record>.why`, tools/land.sh logs it (`suite: reused|missed: WHY`, or why the
gate never asked) and writes both reasons on the land's ledger row (`pre_why`, `suite_why`), and
`goh land-profile --last N` counts them by reason (lib/land_spd.py `decisions`).
"""

from __future__ import annotations

import sys
from pathlib import Path

from conftest import REPO_ROOT
from test_landq import _land_wt, env, landq, recorded  # noqa: F401  -- `env` is the queue's fixture
from test_suite_once import _record, _runner

sys.path.insert(0, str(REPO_ROOT / "lib"))
import land_spd
import timing_ledger


def test_the_gates_runner_records_why_it_took_or_missed_the_verdict(tmp_path):
    for sub in ("miss", "take"):
        (tmp_path / sub).mkdir()
    _runner(tmp_path / "miss", 1, _record(tmp_path / "miss"))
    assert (tmp_path / "miss" / "verdicts.why").read_text() == "missed: the stub judged\n"
    _runner(tmp_path / "take", 0, _record(tmp_path / "take"))
    assert (tmp_path / "take" / "verdicts.why").read_text() == "reused: the stub judged\n"


def _land_row(repo: Path, tmp_path: Path, env: dict, gate: str, pre: str) -> tuple[dict, str]:  # noqa: F811
    wt, _ = _land_wt(repo, tmp_path)
    env = {**env, "GOH_LAND_GATE": gate, "GOH_TIMING_DIR": str(tmp_path / "timing"),
           "GOH_LAND_PRE": pre}  # fmt: skip
    done = landq(env, "--enqueue", "--foreground", "--land", str(wt), "--item", "T.T", "--no-start")
    assert done.returncode == 0, done.stdout + done.stderr
    assert landq(env, "--drain").returncode == 0
    got = recorded(env)["T.T"]
    log = Path(got["log"]).read_text()
    assert got["state"] == "landed", log
    rows = [
        r for r in timing_ledger.read(tmp_path / "timing" / "ledger.jsonl") if r["kind"] == "land"
    ]
    assert len(rows) == 1, rows
    return rows[0], log


def test_the_land_records_both_decisions_and_their_reasons(repo, tmp_path, env):  # noqa: F811
    gate = 'echo "missed: the gate judged another tree" >> "$GOH_LAND_VERDICTS_REUSE.why"'
    row, log = _land_row(repo, tmp_path, env, gate, 'echo "{}" > "$GOH_LAND_VERDICTS_RECORD"')
    assert row["pre_why"].startswith("reuse: the tree judged at"), row
    assert row["suite_why"] == "missed: the gate judged another tree", row
    assert "suite: missed: the gate judged another tree" in log, log
    got = land_spd.decisions([log])
    assert got["suite"] == ("missed", "the gate judged another tree"), got
    assert got["pre"] is not None and got["pre"][0] == "reuse", got


def test_a_gate_that_never_asks_the_suite_is_a_recorded_miss(repo, tmp_path, env):  # noqa: F811
    row, _ = _land_row(repo, tmp_path, env, "true", 'echo "{}" > "$GOH_LAND_VERDICTS_RECORD"')
    assert row["suite_why"].startswith("missed: the gate ran no whole suite to ask"), row


def test_no_kept_record_from_the_enqueue_is_a_recorded_miss(repo, tmp_path, env):  # noqa: F811
    row, _ = _land_row(repo, tmp_path, env, "true", "true")  # green, and it records nothing
    assert row["suite_why"] == "missed: no kept record from the enqueue", row


def test_land_profile_counts_the_decisions_by_reason():
    gatesrc = (
        "→ pre-land: the verdict the enqueue judged at 4e7c6f34 is not f0253b0f's (.gatesrc moved:"
        " the pre-step's own code) -- run again\n→ suite: missed: the pre-step chose against"
        " 4e7c6f34, the gate's base is 50e2d2c0"
    )
    adds = (
        "→ pre-land: the verdict the enqueue judged at 5ca0958e is not c96fead0's (lib/pre_reuse.py"
        " moved: the pre-step's own code, and the tip's set adds 2 tests) -- run again"
    )
    whole = (
        "✓ pre-land: the verdict the enqueue judged at 45af9aae holds for 45af9aae -- not run"
        " again: the tree judged at 45af9aae is 45af9aae's\n"
        "→ suite: reused: the pre-step judged this tree"
    )
    none = "→ pre-land: nothing kept from the enqueue for this land -- the set runs"
    logs = [gatesrc, adds, whole, none]
    rows = [{"item": "T.T", "ref": "r", "stages": {"e2e": [1.0, None]},
             "decisions": land_spd.decisions([t])} for t in logs]  # fmt: skip
    s = land_spd.summary(Path("/nonexistent"), rows)
    assert s["pre_why"] == {
        "rerun: <path> moved: the pre-step's own code": 2,
        "reuse: the tree judged at <sha> is <sha>'s": 1,
        "rerun: nothing kept from the enqueue": 1,
    }, s["pre_why"]
    assert s["suite_why"] == {
        "missed: the pre-step chose against <sha>, the gate's base is <sha>": 1,
        "unrecorded": 2,
        "reused: the pre-step judged this tree": 1,
    }, s["suite_why"]
    text = land_spd.render("last 4", s)
    assert "\nlander pre-land: 2 rerun: <path> moved: the pre-step's own code; 1 " in text, text
    assert "\ngate suite: 2 unrecorded; 1 missed: the pre-step chose against <sha>" in text, text
