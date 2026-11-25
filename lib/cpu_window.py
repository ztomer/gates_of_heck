"""The CPU idle's rolling window: one state file of tick samples, one sampler (GOH.CAPWINDOW).

lib/capacity.py's CPU idle was two tick samples 2 s apart, a coin flip under bursty gate load
(2026-11-22: `goh capacity` printed room at 59.6% idle while the spawn hook, a second later,
refused at 0.7%). Every capacity reading -- `goh capacity`, hooks/claude/lane_room.py and the loop
watch all take it -- now records its sample here, one self-exiting sampler keeps the state warm,
and capacity judges the mean since the newest sample GOH_CPU_IDLE_ROLLING_S old. Pinned by
tests/test_capacity_window.py.

THE STATE is goh-cache.db's `kv` row `cpu_idle` (GOH.STATEDBS3: 300 s of samples, rewritten every
5 s, disposable): one read-change-write per sample in one transaction, so two readers never lose
each other's sample. GOH_CPU_IDLE_STATE (a test's) names the state, and the goh-cache.db beside it
holds it; else goh-cache.db's own path. A cache error is a cold window: the 2 s fallback.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import goh_cache_db  # the lib directory, on the path from the line above

NAME = "cpu_idle"  # goh-cache.db's kv row

ROLLING_S = 60.0  # GOH_CPU_IDLE_ROLLING_S: the window the idle is judged over (GOH.CAPWINDOW)
MIN_PARTIAL_S = 15.0  # a shorter warm window still beats the 2 s sample; under this it does not
STATE_MAX_AGE_S = 300.0  # samples older than this are dropped; none usable: the 2 s fallback
SAMPLE_EVERY_S = 5.0  # the one sampler's period
SAMPLER_IDLE_EXIT_S = 900.0  # the sampler exits once no reader has asked for this long


def db() -> Path:
    """The goh-cache.db the window lives in: beside GOH_CPU_IDLE_STATE when a caller names one
    (GOH_CACHE_DB still wins), else goh-cache.db's own."""
    named = os.environ.get("GOH_CPU_IDLE_STATE", "").strip()
    if named and not os.environ.get(goh_cache_db.SEAM.env):
        return Path(named).with_name(goh_cache_db.SEAM.file)
    return goh_cache_db.path()


def _body(con: sqlite3.Connection) -> dict[str, Any]:
    row = con.execute("SELECT body FROM kv WHERE name=?", (NAME,)).fetchone()
    try:
        v = json.loads(row[0]) if row else {}
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


def load_state() -> dict[str, Any]:
    """The window as the last sample left it; {} when there is none (cold)."""
    with goh_cache_db.missable("cpu idle window"):
        try:
            with goh_cache_db.ro(db()) as con:
                return _body(con)
        except FileNotFoundError:
            pass
    return {}


def record(now: float, ticks: tuple[int, int], *, reader: bool, sampler: int | None = None):
    """Append one tick sample to the state (pruned) and return the samples before it: [t, idle,
    total], oldest first, every one consistent with `ticks` (a reboot resets the counters)."""
    idle, total = ticks
    kept: list = []
    st: dict[str, Any] = {}
    with goh_cache_db.missable("cpu idle window"), goh_cache_db.transaction(db()) as con:
        st = _body(con)
        raw = st.get("samples") if isinstance(st.get("samples"), list) else []
        kept = [x for x in raw
                if isinstance(x, list) and len(x) == 3 and now - STATE_MAX_AGE_S <= x[0] < now
                and x[1] <= idle and x[2] <= total]  # fmt: skip
        st["samples"] = [*kept, [now, idle, total]][-400:]
        if reader:
            st["read_at"] = now
        if sampler is not None:
            st.update(sampler_pid=sampler, sampler_at=now)
        con.execute("INSERT INTO kv(name, body, at) VALUES (?, ?, ?) "
                    "ON CONFLICT(name) DO UPDATE SET "
                    "body=excluded.body, at=excluded.at", (NAME, json.dumps(st), now))  # fmt: skip
    return kept, st  # an unwritable state only costs the next reader its window


def start_sampler(st: dict[str, Any], now: float, script: str) -> None:
    """Start the ONE sampler when none has written lately; it records every SAMPLE_EVERY_S and
    exits by itself SAMPLER_IDLE_EXIT_S after the last reader. GOH_CPU_SAMPLER=off: never."""
    if os.environ.get("GOH_CPU_SAMPLER", "").strip() == "off":
        return
    if now - float(st.get("sampler_at") or 0) < 3 * SAMPLE_EVERY_S:
        return
    import contextlib
    import subprocess

    with contextlib.suppress(OSError):  # no sampler: every reader still records its own sample
        subprocess.Popen(
            [sys.executable, os.path.abspath(script), "--sampler"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, close_fds=True)  # fmt: skip


def sampler(cpu_ticks: Callable[[], tuple[int, int]]) -> int:
    """The one sampler loop: a sample every SAMPLE_EVERY_S until no reader asked for
    SAMPLER_IDLE_EXIT_S, or another live sampler owns the state."""
    me = os.getpid()
    while True:
        _, st = record(time.time(), cpu_ticks(), reader=False, sampler=me)
        if time.time() - float(st.get("read_at") or 0) > SAMPLER_IDLE_EXIT_S:
            return 0
        time.sleep(SAMPLE_EVERY_S)
        st = load_state()
        recent = time.time() - float(st.get("sampler_at") or 0) < 2 * SAMPLE_EVERY_S
        if recent and st.get("sampler_pid") not in (None, me):
            return 0  # another sampler wrote since: it owns the state


def rolling_s() -> float:
    raw = os.environ.get("GOH_CPU_IDLE_ROLLING_S", "").strip()
    try:
        return float(raw) if raw else ROLLING_S
    except ValueError:
        return ROLLING_S


def idle_text(cpu: dict[str, Any]) -> str:
    """The idle with the window it was judged over: `CPU 41% idle over 60 s (13 samples)`."""
    idle = cpu.get("idle_pct")
    if idle is None:
        return "CPU idle unmeasured"
    w = cpu.get("window_s")
    if w is None:
        return f"CPU {idle:g}% idle"
    kind = f"{cpu.get('samples', 2)} samples" if cpu.get("source") == "rolling" else "cold state"
    return f"CPU {idle:g}% idle over {w:g} s ({kind})"
