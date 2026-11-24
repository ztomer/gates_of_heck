"""A ship measures on a quiet host, and its record says how quiet it was (GOH.SHIPQUIET).

Five ships (v0.142.0 .. v0.147.0, 976-1591 s) went over the 900 s budget with the same work as the
ships under it: the ship's own CPU was 3.2-4.3k cpu-s in every one, while the lanes beside it took
10-13 of 16 cores (CPU 1-13% idle; goh's load_sources ledger). The rule "a release that measures
needs a quiet host" was skill text only. Now goh ship waits, bounded, while the MEASURED CPU idle is
under GOH_SHIP_QUIET_IDLE_PCT, records the wait and the idle it found, and every step records the
idle over its own run, so a slow ship under load is not read as a code regression.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import _goh_db_fixture as gdb
import pytest
from _shipped_kit import shipped_clone
from conftest import REPO_ROOT
from test_ship import SHIP, _env
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import ship_quiet


class Fake:
    """A sampler that reads the given idle percentages in turn, and a clock its sleep advances."""

    def __init__(self, readings: list[float | Exception]) -> None:
        self.readings, self.now, self.slept, self.said = list(readings), 0.0, [], []

    def sample(self) -> float:
        got = self.readings.pop(0) if len(self.readings) > 1 else self.readings[0]
        if isinstance(got, Exception):
            raise got
        return got

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.now += s

    def quiet(self, min_idle: float, max_s: float, poll_s: float = 30.0) -> dict:
        return ship_quiet.wait(min_idle, max_s, poll_s=poll_s, sample=self.sample,
                               sleep=self.sleep, clock=lambda: self.now,
                               say=self.said.append)  # fmt: skip


def test_a_quiet_host_ships_at_once_and_says_so():
    f = Fake([72.5])
    rec = f.quiet(30, 600)
    assert f.slept == [] and f.said == []
    assert rec["quiet"] is True and rec["waited_s"] == 0 and rec["idle_pct"] == 72.5
    assert rec["min_idle_pct"] == 30 and rec["max_s"] == 600


def test_a_busy_host_is_waited_on_until_it_is_quiet():
    f = Fake([8.0, 21.0, 44.0])
    rec = f.quiet(30, 600)
    assert f.slept == [30.0, 30.0]
    assert rec["quiet"] is True and rec["waited_s"] == 60 and rec["readings"] == 3
    assert rec["idle_pct_first"] == 8.0 and rec["idle_pct"] == 44.0
    assert len(f.said) == 2 and "8.0% idle" in f.said[0]


def test_the_wait_is_bounded_and_the_ship_goes_on_naming_the_load():
    f = Fake([5.0])
    rec = f.quiet(30, 90)
    assert sum(f.slept) == 90 and rec["waited_s"] == 90
    assert rec["quiet"] is False and rec["idle_pct"] == 5.0
    assert "still 5.0% idle" in f.said[-1]


def test_the_last_sleep_never_overruns_the_bound():
    f = Fake([5.0])
    f.quiet(30, 45)
    assert f.slept == [30.0, 15.0]


def test_an_unmeasured_idle_never_holds_a_ship():
    f = Fake([OSError("host_statistics returned 5")])
    rec = f.quiet(30, 600)
    assert f.slept == [] and rec["quiet"] is None
    assert "host_statistics returned 5" in rec["unmeasured"]


def test_a_bound_of_zero_turns_the_wait_off_without_a_reading():
    f = Fake([RuntimeError("must not be read")])
    rec = f.quiet(30, 0)
    assert rec == {"off": "GOH_SHIP_QUIET_MAX_S=0", "waited_s": 0.0}


@pytest.mark.parametrize(
    ("cfg", "want"),
    [
        ({}, (ship_quiet.DEFAULT_IDLE_PCT, ship_quiet.DEFAULT_MAX_S)),
        ({"GOH_SHIP_QUIET_IDLE_PCT": "45", "GOH_SHIP_QUIET_MAX_S": "120"}, (45.0, 120.0)),
        ({"GOH_SHIP_QUIET_MAX_S": "0"}, (ship_quiet.DEFAULT_IDLE_PCT, 0.0)),
    ],
)
def test_settings_read_the_two_keys(cfg, want):
    assert ship_quiet.settings(cfg) == want


BAD = [{"GOH_SHIP_QUIET_MAX_S": "soon"}, {"GOH_SHIP_QUIET_IDLE_PCT": "140"},
       {"GOH_SHIP_QUIET_MAX_S": "-1"}]  # fmt: skip


@pytest.mark.parametrize("cfg", BAD)
def test_a_bad_setting_is_named(cfg):
    with pytest.raises(ValueError, match="GOH_SHIP_QUIET_"):
        ship_quiet.settings(cfg)


def test_the_idle_over_a_span_is_read_from_the_tick_counters():
    assert ship_quiet.idle_since((100, 1000), now=(400, 2000)) == 30.0
    assert ship_quiet.idle_since(None, now=(400, 2000)) is None
    assert ship_quiet.idle_since((400, 2000), now=(400, 2000)) is None  # no ticks passed


def _ship(repo: Path, tmp_path: Path, **env: str) -> dict:
    argv = [sys.executable, str(SHIP), "--repo", str(repo), "--out", str(tmp_path / "ship.json"),
            "--wait"]  # fmt: skip
    r = subprocess.run(argv, env=_env(tmp_path, **env), capture_output=True, text=True,
                       timeout=patience(120), stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    return gdb.shipped(tmp_path / "ship.json")


def test_a_ship_on_a_busy_host_waits_its_bound_and_records_the_wait_and_load(
    tmp_path, tmp_path_factory
):
    repo = shipped_clone(tmp_path, tmp_path_factory)
    res = _ship(repo, tmp_path, GOH_CPU_IDLE_READING="4", GOH_SHIP_QUIET_MAX_S="1",
                GOH_SHIP_QUIET_POLL_S="0.2")  # fmt: skip
    q = res["quiet"]
    assert q["quiet"] is False and q["idle_pct"] == 4.0 and q["waited_s"] >= 1.0, q
    assert isinstance(q["load1"], float)
    ran = [s for s in res["steps"] if s["status"] == "ok"]
    # macOS advances the host tick counters in bursts, so a fixture step (well under a second) may
    # see none pass and read None: unknown, never a made-up 0 (the arithmetic: idle_since's test)
    assert ran and all("idle_pct" in s and "load1_end" in s for s in ran), ran
    assert all(s["idle_pct"] is None or 0.0 <= s["idle_pct"] <= 100.0 for s in ran), ran


def test_a_ship_on_a_quiet_host_starts_at_once(tmp_path, tmp_path_factory):
    repo = shipped_clone(tmp_path, tmp_path_factory)
    res = _ship(repo, tmp_path, GOH_CPU_IDLE_READING="88", GOH_SHIP_QUIET_MAX_S="600")
    assert res["quiet"]["quiet"] is True and res["quiet"]["waited_s"] < 5, res["quiet"]
