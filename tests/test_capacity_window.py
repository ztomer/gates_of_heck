"""CPU idle judged on ONE rolling window from one sampler (GOH.CAPWINDOW, 2026-11-22).

`goh capacity`, hooks/claude/lane_room.py and the loop watch all judge room on lib/capacity.py's
`reading()`. Its CPU idle was two tick samples 2 s apart: under bursty gate load a coin flip --
`goh capacity` printed room at 59.6% idle while the spawn hook, a second later, refused at 0.7%.
Now every reading records its tick sample in one state file, and the idle is the mean between now
and the newest sample at least GOH_CPU_IDLE_ROLLING_S (60 s) old; the 2 s sample is only the
fallback when the state holds nothing usable. A fake clock and a fake tick source drive it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import capacity

TICKS_PER_S = 1600  # 16 cores at 100 Hz


class Machine:
    """A fake clock and the cumulative tick counters of a machine whose idle share is `idle(t)`."""

    def __init__(self, idle) -> None:
        self.t, self.idle, self.ticks, self.share = 1_000_000.0, 0.0, 0.0, idle

    def advance(self, s: float) -> None:
        step = 0.25
        while s > 1e-9:
            d = min(step, s)
            self.idle += self.share(self.t) * TICKS_PER_S * d
            self.ticks += TICKS_PER_S * d
            self.t += d
            s -= d

    def counters(self) -> tuple[int, int]:
        return int(self.idle), int(self.ticks)


@pytest.fixture
def machine(monkeypatch, tmp_path):
    def install(idle):
        m = Machine(idle)
        monkeypatch.setattr(capacity, "cpu_ticks", m.counters)
        monkeypatch.setattr(capacity.time, "sleep", m.advance)
        monkeypatch.setattr(capacity.time, "time", lambda: m.t)
        monkeypatch.setenv("GOH_CPU_IDLE_STATE", str(tmp_path / "cpu_idle.json"))
        monkeypatch.setenv("GOH_CPU_SAMPLER", "off")
        monkeypatch.delenv("GOH_CPU_IDLE_READING", raising=False)
        monkeypatch.delenv("GOH_CPU_IDLE_WINDOW_S", raising=False)
        return m

    return install


def _admits(cpu: dict) -> bool:
    base = {"waiting": {"long": 0}, "unread": [], "load": None, "cpu": cpu}
    return capacity.room(base, {})["ok"]


def flapping(t: float) -> float:
    """30% idle for 2 s, 2% idle for 2 s: a mean of 16%, under the 20% floor."""
    return 0.30 if int(t // 2) % 2 == 0 else 0.02


def test_a_flapping_machine_never_admits(machine) -> None:
    m = machine(flapping)
    verdicts = []
    for _ in range(120):  # a reader every 3 s for 6 min, landing on both phases
        m.advance(3.0)
        verdicts.append(_admits(capacity._cpu_idle()))
    assert not any(verdicts[30:]), "once the window is warm, a flapping series never admits"


def test_sustained_idle_admits_within_one_window(machine) -> None:
    m = machine(lambda t: 0.02 if t < 1_000_300 else 0.60)
    while m.t < 1_000_290:
        m.advance(3.0)
        capacity._cpu_idle()
    assert not _admits(capacity._cpu_idle()), "busy: no room"
    first = None
    while m.t < 1_000_300 + 120:
        m.advance(3.0)
        cpu = capacity._cpu_idle()
        if first is None and _admits(cpu):
            first = m.t - 1_000_300
    assert first is not None and first <= 66, f"idle from t=300 admitted after {first} s"


def test_the_figure_names_its_window_and_samples(machine) -> None:
    m = machine(lambda t: 0.5)
    for _ in range(25):
        m.advance(3.0)
        cpu = capacity._cpu_idle()
    assert cpu["source"] == "rolling" and 60 <= cpu["window_s"] <= 63, cpu
    assert cpu["samples"] >= 20 and cpu["idle_pct"] == pytest.approx(50.0, abs=0.5), cpu
    assert "60 s" in capacity.cpu_window.idle_text(cpu) or "61 s" in capacity.cpu_window.idle_text(
        cpu
    ), cpu


def test_a_cold_state_falls_back_to_the_short_sample(machine) -> None:
    machine(lambda t: 0.4)
    cpu = capacity._cpu_idle()
    assert cpu["source"] == "ticks" and cpu["window_s"] == capacity.IDLE_WINDOW_S, cpu
