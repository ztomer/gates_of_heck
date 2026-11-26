"""The land gate's goal: the median of the newest quiet lands' lock-held gate walls (the land
queue's done records) against 5 minutes, which `goh ship` will not pass over, and which says when
it cannot judge (lib/gate_goal.py)."""

from __future__ import annotations

import datetime as dt

import gate_goal
import ship_item
import test_work
from _kv_fixture import Held

CONF = {"gate_goal": {"seconds": 300, "quiet_lands": 3}}
CORES = 16


def _land(n: int, wall: float, load1: float = 4.0, state: str = "landed",
          repo: str = "/r/gates_of_heck") -> dict:  # fmt: skip
    """A done record as lib/land_records.py reads it: the lock held from `started` to `finished`."""
    start = dt.datetime(2026, 11, 26, tzinfo=dt.UTC) + dt.timedelta(hours=n)
    end = start + dt.timedelta(seconds=wall)
    return {"repo": repo, "state": state, "started": start.isoformat(),
            "finished": end.isoformat(), "load_start": load1}  # fmt: skip


def test_the_median_of_the_newest_quiet_lands_is_judged_against_the_goal() -> None:
    rows = [_land(1, 900), _land(2, 200), _land(3, 400), _land(4, 350), _land(5, 9999, load1=64),
            _land(6, 9999, state="red"), _land(7, 9999, repo="/r/other")]  # fmt: skip
    got = gate_goal.reading(
        rows, CONF, CORES, "gates_of_heck"
    )  # newest 3 quiet landed: 350, 400, 200
    assert got is not None and got.median_s == 350 and got.over, got
    assert (
        "median land gate of the newest 3 quiet lands is 350 s, OVER the goal 300 s" in got.line()
    )
    under = gate_goal.reading([*rows, _land(8, 100), _land(9, 250)], CONF, CORES, "gates_of_heck")
    assert under is not None and under.median_s == 250 and not under.over, under


def test_too_few_quiet_lands_is_not_judged_and_says_so() -> None:
    rows = [_land(1, 900), _land(2, 900, load1=40)]
    got = gate_goal.reading(rows, CONF, CORES)
    assert got is not None and got.median_s is None and not got.over
    assert got.line().startswith("gate goal: not judged: 1 of 3 quiet lands"), got.line()
    assert gate_goal.reading(rows, {}, CORES) is None  # no goal configured: no reading


def test_a_ship_is_refused_over_the_goal_and_told_when_it_is_not_judged(
    tmp_path, monkeypatch, capsys
) -> None:
    Held().lay(monkeypatch)
    monkeypatch.setattr(test_work, "ship_refusal", lambda conf: None)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_work_ceiling.json").write_text(
        '{"gate_goal": {"seconds": 300, "quiet_lands": 3}}'
    )
    monkeypatch.setattr(gate_goal.worker_share, "cores", lambda: CORES)
    lands = [_land(1, 400, repo=str(tmp_path)), _land(2, 500, repo=str(tmp_path))]
    queue = tmp_path / "landq"
    monkeypatch.setattr(gate_goal.state_paths, "landq_dir", lambda: queue)

    def done(q, keys=None) -> list[tuple[str, dict]]:
        return [(str(i), r) for i, r in enumerate(lands)]

    monkeypatch.setattr(gate_goal.land_records, "records", done)
    assert ship_item.work_refusal(tmp_path) is None
    assert "not judged: 2 of 3 quiet lands" in capsys.readouterr().err
    lands.append(_land(3, 450, repo=str(tmp_path)))
    why = ship_item.work_refusal(tmp_path)
    assert why and "450 s, OVER the goal 300 s" in why and "no ship" in why, why
    lands.extend([_land(4, 100, repo=str(tmp_path)), _land(5, 120, repo=str(tmp_path))])
    assert ship_item.work_refusal(tmp_path) is None  # 100, 120, 450: the median 120
