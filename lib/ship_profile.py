"""Where a `goh ship` spent its time: each SUB-step, with the box's load when it started
(GOH.SHIPPROFILE).

Owner 2026-11-15: "go ship taking > 1hrs is not very productive". The result said `release 1192 s`
and `install 2037 s`; which gate check, which consumer's push, and how loaded the box was (load
90-180 on 16 cores those days) were three files and a reflog away. So each step runs with
GOH_TIMINGS (lib/step_timings.py) at a file beside the result: every bounded step under it -- each
gate check of the release, each phase release_goh.sh wraps, each consumer's commit and push of the
install -- appends one row with its `load1`, and `goh ship --status RESULT` prints the rows as one
table, costliest first. Rows nest (a check inside the land gate inside the release): a row's
parent is named beside it, and the table is read as "where does it go", never summed.
"""

from __future__ import annotations

import os
from pathlib import Path

import step_timings  # lib/: its one importer (lib/ship_status.py) has lib/ on sys.path

TOP = 20


def profile(stem: Path, name: str) -> str:
    """Point GOH_TIMINGS at step NAME's fresh timings file beside the result STEM; its path."""
    path = Path(f"{stem}.{name}.timings.jsonl")
    path.write_text("", encoding="utf-8")  # a resumed step is profiled afresh, never appended to
    os.environ[step_timings.ENV] = str(path)
    os.environ.pop(step_timings.PARENT_ENV, None)
    return str(path)


def rows(res: dict) -> list[tuple[str, dict]]:
    """(step name, row) for every timings row the result's steps recorded."""
    out = []
    for s in res.get("steps", []):
        if not s.get("timings"):
            continue
        try:
            out += [(s["name"], r) for r in step_timings.load(s["timings"])]
        except OSError:
            continue
    return out


def table(res: dict, top: int = TOP) -> list[str]:
    """The costliest TOP sub-steps, one line each: seconds, load at its start, step, label."""
    found = sorted(rows(res), key=lambda sr: -sr[1].get("ms", 0.0))[:top]
    if not found:
        return []
    head = "costliest sub-steps (s = wall seconds, load = 1-min load average at its start):"
    out = [f"the {len(found)} {head}"]
    for name, r in found:
        load = f"{r['load1']:6.1f}" if isinstance(r.get("load1"), (int, float)) else "     -"
        under = f"  (in {r['parent']})" if r.get("parent") else ""
        out.append(f"  {r['ms'] / 1000:8.1f} s  load {load}  {name:<8} {r['label']}{under}")
    return out
