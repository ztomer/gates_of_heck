"""lib/pin_schedule.py (GOH.PINSWEEP): a release's pin pushes wait for a quiet time or a day, and
the two bounds are configuration."""

from __future__ import annotations

import pin_schedule
import pytest

H = 3600.0
DAY = pin_schedule.DEFAULTS[pin_schedule.EVERY]
QUIET = pin_schedule.DEFAULTS[pin_schedule.QUIET]


@pytest.mark.parametrize(("anchor", "want"), [
    (None, QUIET),  # nothing before it
    (-1 * H, QUIET),  # a sweep an hour ago: the quiet time
    (-20 * H, 4 * H),  # a day since the last comes before the quiet time does
    (-60 * H, 0.0),  # long past: now, never before it was made
])  # fmt: skip
def test_the_due_time_is_the_earlier_of_the_two(anchor, want) -> None:
    created = 1_795_000_000.0
    at = None if anchor is None else created + anchor
    assert pin_schedule.due(created, QUIET, DAY, at) == created + want


def test_close_releases_never_put_a_sweep_off_past_a_day() -> None:
    made = [i * 3 * H for i in range(20)]  # a release every 3 h: none is ever quiet for 6
    fired = next(i for i, c in enumerate(made)
                 if pin_schedule.due(c, QUIET, DAY, made[0]) <= c + 3 * H)  # fmt: skip
    assert made[fired] + 3 * H >= DAY > made[fired - 1]


def test_the_settings_are_the_environments_then_the_gatesrcs_then_the_defaults(
    tmp_path, monkeypatch
) -> None:
    for name in pin_schedule.DEFAULTS:
        monkeypatch.delenv(name, raising=False)
    assert pin_schedule.settings(tmp_path) == (6 * H, 24 * H)
    (tmp_path / ".gatesrc").write_text("GOH_PIN_SWEEP_QUIET=60\nGOH_PIN_SWEEP_EVERY=600\n")
    assert pin_schedule.settings(tmp_path) == (60.0, 600.0)
    monkeypatch.setenv("GOH_PIN_SWEEP_QUIET", "0")
    assert pin_schedule.settings(tmp_path) == (0.0, 600.0)


@pytest.mark.parametrize("bad", ["6h", "-1", "inf", "nan"])
def test_a_value_that_is_no_number_of_seconds_is_refused_by_name(
    tmp_path, monkeypatch, bad
) -> None:
    monkeypatch.setenv("GOH_PIN_SWEEP_EVERY", bad)
    with pytest.raises(ValueError, match="GOH_PIN_SWEEP_EVERY"):
        pin_schedule.settings(tmp_path)
