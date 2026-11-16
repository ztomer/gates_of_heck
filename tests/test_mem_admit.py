"""A new gate waits, bounded, while the machine is short of memory, and says why (GOH.MEMADMIT).

2026-11-16: gates kept starting into a machine already swapping (a 46.7 GiB compressor, one
coalition at 78.6 GiB of 64) until it was restarted by hand; every admission goh had counted CPU
work. lib/mem_admit.py is asked by the gate slot's holder before it takes a slot. These tests drive
the REAL shell library and holder with the reading faked through GOH_MEM_READING, a file read at
every poll, and lift the reading to watch the gate start.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from test_gate_slot import Gate, _env

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))
import mem_admit  # noqa: E402 -- lib/ is on the path from the line above

LOW_SWAP = "avail_pct=60 swap_used_mb=40000 mem_total_mb=65536"
LOW_MEMORY = "avail_pct=5 swap_used_mb=0 mem_total_mb=65536"
PLENTY = "avail_pct=80 swap_used_mb=0 mem_total_mb=65536"


def _mem_env(tmp_path: Path, seam: Path, **extra: str) -> dict[str, str]:
    return _env(
        tmp_path, 2, GOH_MEM_ADMIT="1", GOH_MEM_READING=str(seam), GOH_MEM_POLL="0.05", **extra
    )


@pytest.mark.load_sensitive
def test_a_gate_under_a_low_swap_reading_waits_says_why_and_starts_once_it_lifts(tmp_path):
    seam = tmp_path / "reading"
    seam.write_text(LOW_SWAP)
    gate = Gate(tmp_path, "alpha", _mem_env(tmp_path, seam))
    try:
        text = gate.wait_for("waits before it starts")
        assert "40000 MB of swap in use" in text and "GOH_SWAP_MAX_PCT=50%" in text, text
        assert "TAKEN" not in gate.out(), "the gate took its slot under a low-swap reading"
        seam.write_text(PLENTY)
        text = gate.wait_for("TAKEN")
        assert "starts after" in text, text
        assert gate.finish() == 0
    finally:
        gate.kill()


@pytest.mark.load_sensitive
def test_calibration_the_same_gate_under_a_plenty_reading_starts_without_a_wait(tmp_path):
    seam = tmp_path / "reading"
    seam.write_text(PLENTY)
    gate = Gate(tmp_path, "alpha", _mem_env(tmp_path, seam))
    try:
        text = gate.wait_for("TAKEN")
        assert "memory is low" not in text, text
        assert gate.finish() == 0
    finally:
        gate.kill()


@pytest.mark.load_sensitive
def test_the_wait_is_bounded_and_says_the_gate_starts_anyway(tmp_path):
    seam = tmp_path / "reading"
    seam.write_text(LOW_MEMORY)
    gate = Gate(tmp_path, "alpha", _mem_env(tmp_path, seam, GOH_MEM_WAIT_MAX="1"))
    try:
        text = gate.wait_for("TAKEN")
        assert "5% of memory available, under GOH_MEM_MIN_PCT=20%" in text, text
        assert "starts anyway" in text, text
        assert gate.finish() == 0
    finally:
        gate.kill()


def test_off_admits_whatever_the_reading(monkeypatch):
    monkeypatch.setenv("GOH_MEM_ADMIT", "0")
    low = mem_admit.Reading(avail_pct=1, source="test")
    assert mem_admit.wait("x", read=lambda: low) == 0.0


def test_no_reading_is_not_low_memory():
    assert mem_admit.low(mem_admit.Reading()) is None


def test_the_os_readings_parse():
    assert mem_admit.parse_swapusage(
        "total = 4096.00M  used = 1.50G  free = 2560.00M  (encrypted)"
    ) == int(1.5 * (1 << 30))
    assert mem_admit.parse_swapusage("total = 0.00M  used = 0.00M  free = 0.00M") == 0
    r = mem_admit.parse_meminfo(
        "MemTotal:       16000000 kB\nMemAvailable:    1600000 kB\n"
        "SwapTotal:       2000000 kB\nSwapFree:         500000 kB\n"
    )
    assert r.avail_pct == 10 and r.swap_used == 1500000 * 1024 and r.mem_total == 16000000 * 1024
