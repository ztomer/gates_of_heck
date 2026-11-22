"""A ship measures on a quiet host, bounded, and says how quiet it was (GOH.SHIPQUIET).

WHY. v0.142.0, v0.143.0, v0.146.0, v0.146.1 and v0.147.0 took 976-1591 s against the 900 s budget
with no more work than the ships under it: goh ship's own CPU was 3.2-4.3k cpu-s in every one, while
the lanes beside it held 10-13 of the 16 cores (CPU 1-13% idle over the run, load 35-75; the
load_sources ledger). Each was filed as a slow ship and read as a code regression. "A release that
measures needs a quiet host" was skill text (agent-fanout), and the coordinator started the ships
anyway: nothing waited.

THE WAIT (`wait`). Before the land lock -- the queue's time, never the ship's `own_s` -- goh ship
reads the MEASURED CPU idle (lib/capacity.py: the host's tick counters twice, a short window apart;
the load average counts I/O-blocked threads, so it is shown, never judged) and, while it is under
GOH_SHIP_QUIET_IDLE_PCT (default 30), waits GOH_SHIP_QUIET_POLL_S (30) and reads again, up to
GOH_SHIP_QUIET_MAX_S (default 600; 0 turns the wait off). Past the bound it ships anyway, said. An
idle it cannot read never holds a ship (probe-first). The record's `quiet` carries the bound, the
seconds waited, the first and last idle read and the 1-minute load.

THE LOAD PER STEP (`ticks`, `idle_since`). Every step's entry carries `idle_pct`, the host's CPU
idle over that step's own run (tick counters at its start and end, nothing sampled in between), and
`load1_end`: a suite that ran at 3% idle reads as contention, not as the code.

SEAMS: `wait(sample=, sleep=, clock=, say=)` for a test's fakes; capacity's GOH_CPU_IDLE_READING and
GOH_CPU_IDLE_WINDOW_S for the sampler a child ship reads. Pinned by tests/test_ship_quiet.py.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from typing import Any

DEFAULT_IDLE_PCT = 30.0
DEFAULT_MAX_S = 600.0
POLL_S = 30.0
KEYS = ("GOH_SHIP_QUIET_IDLE_PCT", "GOH_SHIP_QUIET_MAX_S")


def _number(cfg: Mapping[str, str], key: str, default: float, top: float | None = None) -> float:
    raw = (cfg.get(key) or "").strip()
    if not raw:
        return default
    try:
        val = float(raw)
    except ValueError:
        raise ValueError(f"{key}={raw!r} is not a number") from None
    if val < 0 or (top is not None and val > top):
        raise ValueError(f"{key}={raw!r} is out of range (0..{top if top is not None else 'inf'})")
    return val


def settings(cfg: Mapping[str, str]) -> tuple[float, float]:
    """(minimum idle %, maximum wait s) from the ship's keys; a bad value raises, naming its key."""
    return (_number(cfg, "GOH_SHIP_QUIET_IDLE_PCT", DEFAULT_IDLE_PCT, 100.0),
            _number(cfg, "GOH_SHIP_QUIET_MAX_S", DEFAULT_MAX_S))  # fmt: skip


def poll_s() -> float:
    try:
        return max(0.05, float(os.environ.get("GOH_SHIP_QUIET_POLL_S") or POLL_S))
    except ValueError:
        return POLL_S


def idle_reading() -> float:
    """The host's CPU idle % now (lib/capacity.py's one reading of it)."""
    import capacity  # lib/, on the path through tools/ship.py

    got = capacity._cpu_idle()["idle_pct"]
    if got is None:
        raise OSError("no CPU idle reading")
    return float(got)


def _load1() -> float | None:
    try:
        return round(os.getloadavg()[0], 2)
    except OSError:
        return None


def wait(min_idle: float, max_s: float, *, poll_s: float = POLL_S,
         sample: Callable[[], float] = idle_reading, sleep: Callable[[float], None] = time.sleep,
         clock: Callable[[], float] = time.monotonic,
         say: Callable[[str], Any] = print) -> dict[str, Any]:  # fmt: skip
    """Wait, at most MAX_S, while the CPU idle is under MIN_IDLE; the record of the wait."""
    if max_s <= 0:
        return {"off": "GOH_SHIP_QUIET_MAX_S=0", "waited_s": 0.0}
    t0, first, n = clock(), None, 0
    rec: dict[str, Any] = {"min_idle_pct": min_idle, "max_s": max_s}
    while True:
        try:
            idle = round(float(sample()), 1)
        except (OSError, ValueError, ImportError, AttributeError) as e:
            rec.update(quiet=None, unmeasured=str(e), waited_s=round(clock() - t0, 1),
                       readings=n, load1=_load1())  # fmt: skip
            return rec
        n += 1
        first = idle if first is None else first
        waited = clock() - t0
        rec.update(idle_pct_first=first, idle_pct=idle, readings=n, waited_s=round(waited, 1))
        if idle >= min_idle:
            rec.update(quiet=True, load1=_load1())
            return rec
        if waited >= max_s:
            say(f"goh ship: the host is still {idle}% idle (under {min_idle:g}%) after "
                f"{waited:.0f} s, the bound: shipping under load")  # fmt: skip
            rec.update(quiet=False, load1=_load1())
            return rec
        say(f"goh ship: the host is {idle}% idle, under {min_idle:g}%: waiting for a quiet host "
            f"({waited:.0f} of {max_s:.0f} s)")  # fmt: skip
        sleep(min(poll_s, max_s - waited))


def ticks() -> tuple[int, int] | None:
    """(idle, total) CPU ticks now, or None when the counters cannot be read."""
    try:
        import capacity  # lib/, on the path through tools/ship.py

        return capacity.cpu_ticks()
    except (OSError, ValueError, ImportError, AttributeError, IndexError):
        return None


def idle_since(start: tuple[int, int] | None, now: tuple[int, int] | None = None) -> float | None:
    """The CPU idle % between START's ticks and NOW's (read when not given); None if unknown."""
    now = ticks() if now is None else now
    if start is None or now is None or now[1] <= start[1]:
        return None
    return round(100.0 * (now[0] - start[0]) / (now[1] - start[1]), 1)
