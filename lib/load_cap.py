#!/usr/bin/env python3
"""The cap on concurrent land pre-steps: the PEAK of the measured throughput curve (GOH.LOADCAP).

    python3 lib/load_cap.py [--json]    # the curve from the land records, its peak and the cap

THE QUESTION (owner 2026-11-23: "choose the value that will give us the most overall throughput";
earlier "the machine is 80% idle, shouldn't you be a bit more aggressive?"). Throughput is landed
items per hour over the whole machine. At load 40-62 (once 160) on 16 cores four lanes' pre-steps
ran beside the serial lander; ten minutes later, every lane stopped, the load was 5.

THE INSTRUMENT is the number of land pre-steps in flight, never the load average. On the land
records of 2026-11-19..24 (n per row below) the load band at a gate's start barely moves its wall
time (666 s at load 0-16, n=62; 993 s at 48-64, n=55) and its red rate FALLS with load (29% to
9%): macOS's load counts I/O-blocked threads, and the lane QoS clamp stretches a clamped thread
25-35x where the load sees ~3x (GOH.SUITEDRIFTBUILD). The pre-steps in flight during a gate track
it: the lander's gate wall is 604 s with none (n=183), 932 s beside one (n=67), 1515 s beside two
(n=36), and its red rate 19/20/28%.

THE CURVE (`curve`): busy hours of the record (a pre-step or a gate in flight), each banded by the
mean number of pre-steps in flight (a record's `requested` to `requested + pre_s`, its slot wait
included; an enqueue refused before it filed, to its record's time), against the items landed in
that hour. 2026-11-24 (this code, the newest week): 1 in flight 2.44 landed/h (n=39 h), 2 in
flight 2.09 (n=11); in 120-min bins 2.57 (n=15) and 2.00 (n=5). The per-stage model agrees: one
pre-step in flight feeds 5.9 items/h (481 s each, 73% green) into a lander that clears 3.1/h
beside it; two feed 7.4/h into a lander that clears 1.7/h. The serial lander is the bottleneck
and every extra pre-step slows it: the peak is ONE pre-step in flight (DEFAULT).

THE CAP (`cap`): GOH_PRESTEP_CAP when set; else the curve's peak over the newest WINDOW_S of
records, re-read at every reading, so it follows the peak as lands are recorded (after each ship
too); else DEFAULT, the peak measured above. A band of fewer than MIN_HOURS busy hours is noise
and never the peak. lib/capacity.py admits a write lane while the pre-steps in flight are at or
under the cap. Pinned by tests/test_capacity_presteps.py.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # outside any checkout: nothing to re-run from
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import argparse
import collections
import datetime as dt
import json
import os
import sys
from typing import Any

DEFAULT = 1  # the measured peak, 2026-11-24 (above)
MIN_HOURS = 5  # busy hours a band needs before it can be the peak
WINDOW_S = 7 * 86400  # the newest week of records judges
HOUR = 3600.0
BUSY = 0.05  # mean pre-steps plus gates in flight that make an hour busy


def _ts(raw: object) -> float | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return dt.datetime.fromisoformat(raw).timestamp()
    except ValueError:
        return None


def spans(records: list[dict]) -> tuple[list[tuple[float, float]], list[tuple[float, float]],
                                         list[float]]:  # fmt: skip
    """(pre-step spans, gate spans, landed times) of the land records."""
    pres, gates, landed = [], [], []
    for r in records:
        req, pre_s = _ts(r.get("requested")), r.get("pre_s")
        start, end = _ts(r.get("started")), _ts(r.get("finished"))
        if req and isinstance(pre_s, (int, float)) and pre_s > 0:
            pres.append((req, req + float(pre_s)))
        elif req and not start and r.get("state") == "red" and (at := _ts(r.get("at"))):
            pres.append((req, at))  # refused in its pre-step, before it filed
        if start and end and end > start:
            gates.append((start, end))
        if r.get("state") == "landed" and (t := end or _ts(r.get("at"))):
            landed.append(t)
    return pres, gates, landed


def _mean_in_flight(spans_: list[tuple[float, float]], a: float, b: float) -> float:
    return sum(max(0.0, min(e, b) - max(s, a)) for s, e in spans_) / (b - a)


def curve(records: list[dict], window_s: float = WINDOW_S) -> dict[int, dict[str, float]]:
    """{pre-steps in flight: {"per_h": landed items per busy hour, "hours": busy hours}} over the
    newest WINDOW_S of RECORDS (k >= 1: an hour with none in flight is no band a cap can choose)."""
    pres, gates, landed = spans(records)
    if not landed:
        return {}
    end = max(landed)
    t = end - window_s
    t -= t % HOUR
    bands: dict[int, list[int]] = collections.defaultdict(list)
    while t <= end:
        k = _mean_in_flight(pres, t, t + HOUR)
        if k + _mean_in_flight(gates, t, t + HOUR) > BUSY and round(k) >= 1:
            bands[round(k)].append(sum(1 for x in landed if t <= x < t + HOUR))
        t += HOUR
    return {k: {"per_h": round(sum(v) / len(v), 2), "hours": len(v)}
            for k, v in sorted(bands.items())}  # fmt: skip


def peak(c: dict[int, dict[str, float]]) -> int | None:
    """The band with the most landed items per hour among those with MIN_HOURS hours (the fewer
    pre-steps on a tie); None when no band has enough."""
    judged = [(v["per_h"], -k) for k, v in c.items() if v["hours"] >= MIN_HOURS]
    return -max(judged)[1] if judged else None


def cap(records: list[dict], environ: dict[str, str] | None = None) -> dict[str, Any]:
    """{"value": the cap on pre-steps in flight, "from": GOH_PRESTEP_CAP | curve | default}."""
    env = os.environ if environ is None else environ
    raw = env.get("GOH_PRESTEP_CAP", "").strip()
    if raw.isdigit() and int(raw) >= 1:
        return {"value": int(raw), "from": "GOH_PRESTEP_CAP"}
    got = peak(curve(records)) if records else None
    return {"value": got, "from": "curve"} if got else {"value": DEFAULT, "from": "default"}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import land_records

    q = os.environ.get("GOH_LANDQ_DIR") or os.path.expanduser("~/.local/state/goh/landq")
    from pathlib import Path

    recs = [r for _, r in land_records.records(Path(q))]
    c, got = curve(recs), cap(recs)
    if args.json:
        print(json.dumps({"curve": c, "peak": peak(c), "cap": got}, sort_keys=True))
        return 0
    sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    from tui.lib import info, ok

    for k, v in c.items():
        info(f"{k} pre-steps in flight: {v['per_h']:g} landed/h over {v['hours']} busy hours")
    ok(f"peak {peak(c)}; cap {got['value']} ({got['from']})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
