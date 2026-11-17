"""`goh budget` backfills what predates its writers: the CIP lanes' push gates
(lib/cip_history.py) and the land queue's attempts (lib/land_attempts.py), once each
(GOH.GATEBUDGET2).

Owner 2026-11-16: "seed one row per done CIP.* item from its recorded after-time and load", so
each repo a CIP lane brought to 10 min or less is ARMED from day one. The lanes name private
repositories, so their figures live in the owner's private seed store; the seed here is a fixture.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import budget
import cip_history
import timing_ledger

SEED = [
    {"item": "CIP.ALPHA", "repo": "alpha", "wall_s": 54, "load1": 31, "load_end": 26,
     "sha": "cb73a26", "at": "2026-11-16T12:00:00", "before_s": 196},
    {"item": "CIP.BETA", "repo": "beta", "wall_s": 551, "load1": 26, "load_end": 31,
     "sha": "84b9ed6", "at": "2026-11-16T04:09:03"},
    {"item": "CIP.GAMMA", "repo": "gamma", "wall_s": 1513},  # no load, no sha: not a row
]  # fmt: skip


def seeded(tmp_path: Path, monkeypatch) -> None:
    seeds = tmp_path / "seeds"
    seeds.mkdir()
    (seeds / cip_history.FILE).write_text(json.dumps(SEED))
    monkeypatch.setenv("GOH_SEED_DIR", str(seeds))
    monkeypatch.setenv("GOH_TIMING_DIR", str(tmp_path / "t"))


def test_every_cip_lane_with_a_push_time_is_a_push_row_and_arms_its_repo(
    tmp_path, monkeypatch, capsys
) -> None:
    seeded(tmp_path, monkeypatch)
    monkeypatch.setenv("GOH_SHIP_DIR", str(tmp_path / "ships"))
    monkeypatch.setenv("GOH_LANDQ_DIR", str(tmp_path / "landq"))
    assert budget.main(["--json"]) == 0
    rows = [r for r in timing_ledger.read() if r.get("backfill") == "CIP"]
    assert [(r["subject"], r["ref"], r["wall_s"]) for r in rows] == [
        ("alpha push", "CIP.ALPHA", 54.0),
        ("beta push", "CIP.BETA", 551.0),
    ]
    by = {s["subject"]: s for s in budget.snapshot()["subjects"]}
    assert by["alpha push"]["state"] == budget.ARMED and by["beta push"]["load1"] == 26
    capsys.readouterr()
    assert budget.main(["--json"]) == 0  # a second read adds nothing
    assert len([r for r in timing_ledger.read() if r.get("backfill") == "CIP"]) == 2


def test_no_seed_backfills_nothing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GOH_SEED_DIR", str(tmp_path / "none"))
    monkeypatch.setenv("GOH_TIMING_DIR", str(tmp_path / "t"))
    assert cip_history.backfill() == 0


def test_a_backfilled_row_older_than_a_live_one_is_judged_in_time_order(
    tmp_path, monkeypatch
) -> None:
    """The CIP rows are appended after live rows already in the ledger: a subject's runs are its
    rows by time, not by line, or the old figure would read as the newest run."""
    seeded(tmp_path, monkeypatch)
    timing_ledger.record("push", "alpha", 999.0, read_load=False)  # today, slower than 10 min
    cip_history.backfill()
    judged = {j["subject"]: j for j in budget.snapshot()["subjects"]}
    assert judged["alpha push"]["last_s"] == 999.0
