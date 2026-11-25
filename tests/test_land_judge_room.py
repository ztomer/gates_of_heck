"""GOH.ONEGATE: when the land judge may run a step (lib/land_judge_room.py)."""

from __future__ import annotations

import capacity
import land_judge_room as room
import pytest


def box(**kw) -> dict:
    said = {"unread": [], "room": {"ok": True, "why": []}, "slots": {"dir": None},
            "presteps": {"held": 0, "in_flight": 0}}  # fmt: skip
    return {**said, **kw}


@pytest.fixture
def quiet(monkeypatch) -> dict:
    """The land path idle; a test sets one part busy."""
    state = {"live": [], "queue": False, "held": ["work", "lane", "short"]}
    monkeypatch.setattr(room.landq_inflight, "live", lambda _q: state["live"])
    monkeypatch.setattr(room.landq_state, "any_in", lambda _q, *s: state["queue"] and
                        s == ("pending", "running"))  # fmt: skip
    monkeypatch.setattr(room.land_share, "held_classes", lambda _d: state["held"])
    return state


def test_a_box_with_room_and_no_land_on_its_way_may_run(quiet, tmp_path) -> None:
    assert room.none(lambda: box(slots={"dir": str(tmp_path)})) == ""
    assert room.none(lambda: box(slots={"dir": str(tmp_path / "none")})) == ""


@pytest.mark.parametrize(
    ("part", "why"),
    [({"room": {"ok": False, "why": ["CPU 1% idle", "low memory"]}}, "CPU 1% idle; low memory"),
     ({"unread": ["free disk"], "room": {"ok": True, "why": []}}, "unread: free disk"),
     ({"presteps": {"held": 1, "in_flight": 0}}, "a pre-step is in flight"),
     ({"presteps": {"held": 0, "in_flight": 1}}, "a pre-step is in flight")],
)  # fmt: skip
def test_what_the_reading_refuses(quiet, part, why) -> None:
    assert room.none(lambda: box(**part)) == why


def test_an_enqueue_a_queued_land_and_a_land_paths_gate_each_refuse(quiet, tmp_path) -> None:
    look = lambda: room.none(lambda: box(slots={"dir": str(tmp_path)}))  # noqa: E731
    quiet["live"] = [{"item": "x"}]
    assert look() == "an enqueue is in flight"
    quiet.update(live=[], queue=True)
    assert look() == "a land is in flight"
    quiet["queue"] = False
    for held in ("pre", "land", "ship"):
        quiet["held"] = ["work", held]
        assert look() == f"a {held} gate holds a slot"


def test_a_reading_that_fails_is_no_room(quiet) -> None:
    def fails() -> dict:
        raise OSError("no reading")

    assert room.none(fails) == "unread: OSError: no reading"


def test_the_reading_and_the_threshold_are_capacitys(quiet, monkeypatch) -> None:
    """The same rule, the same setting: a box at 10% idle has no room at 20 and has at 5."""
    real = capacity.room
    assert room.none.__defaults__ == (capacity.reading,)

    def reading() -> dict:
        r = box(cpu={"idle_pct": 10.0}, load=None)
        return {**r, "room": real(r)}

    monkeypatch.delenv("GOH_LANE_MIN_IDLE_PCT", raising=False)
    assert "under GOH_LANE_MIN_IDLE_PCT=20" in room.none(reading)
    monkeypatch.setenv("GOH_LANE_MIN_IDLE_PCT", "5")
    assert room.none(reading) == ""


def test_a_wait_is_told_once_and_ends_when_there_is_room() -> None:
    said, left, slept = [], ["CPU 1% idle", "CPU 2% idle", ""], []
    room.Wait(50.0, said.append, lambda: left.pop(0), slept.append, lambda: 0.0)()
    assert said == ["CPU 1% idle", ""] and slept == [room.POLL_S] * 2


def test_room_past_the_bound_still_runs_and_none_is_no_room() -> None:
    said = []
    room.Wait(5.0, said.append, lambda: "", pytest.fail, lambda: 9.0)()
    assert said == [""]
    with pytest.raises(room.NoRoom, match="CPU 1% idle"):
        room.Wait(5.0, said.append, lambda: "CPU 1% idle", pytest.fail, lambda: 5.0)()
    assert said == [""]
