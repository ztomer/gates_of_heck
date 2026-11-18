"""lib/budget_breach.py -- a BREACHED subject reaches the coordinator, never sits silent in a row
(GOH.LANDTARGET; split from lib/budget.py under the 500-line cap).

OWNER, 2026-11-15: going above BREACH "will trigger a self improvement cycle until we are back
below" ARM. `goh budget --json` carries `breaches`: each BREACHED subject (lib/budget.py judge's
dicts) with the figure its kind judges (the last run, or the median of five) and the
self-improvement item it calls for, which the coordinator files; the table prints the same line.
Pinned by tests/test_land_e2e.py.
"""

from __future__ import annotations

BREACHED = "BREACHED"


def minutes(s: float | None) -> str:
    """Seconds as the budget's table shows them: `95 s`, `12.5 min`, `-` for none."""
    return "-" if s is None else (f"{s:.0f} s" if s < 120 else f"{s / 60:.1f} min")


def breaches(judged: list[dict]) -> list[dict]:
    """Every BREACHED subject in JUDGED, each with its self-improvement item's text."""
    out = []
    for j in judged:
        if j["state"] != BREACHED:
            continue
        median = j.get("agg") == "median5"
        fig = j["median_s"] if median else j["last_s"]
        what = "median5" if median else "last run"
        out.append({
            "subject": j["subject"], "kind": j["kind"], "figure_s": fig, "agg": j.get("agg"),
            "arm_s": j["arm_s"], "breach_s": j["breach_s"],
            "action": f"file a self-improvement item: {j['subject']} went over its "
                      f"{minutes(j['breach_s'])} breach line ({what} now {minutes(fig)}); cycle "
                      f"until it is back under {minutes(j['arm_s'])}",
        })  # fmt: skip
    return out
