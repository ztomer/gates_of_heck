"""lib/gate_times.py -- `goh budget gates`: every estate repo's last measured push gate, per check.

    goh budget gates [--json]

The first step of the CIP method (`goh map gate_profile`; CLS.CIPROFILE): which
repo's push gate is slow, and on which check. Read only, from what this machine already recorded:

* the ESTATE is the registry `install.sh` writes (lib/estate_registry.py); a registered repo with
  no `push` row is named under "no recorded push gate", never silently absent;
* each repo's NEWEST `push` row in the timing ledger (lib/timing_ledger.py) gives the wall, the
  load at its start and whether it passed; a land's or a ship's row is not a push gate;
* the checks are the `top_steps` of the newest push row that carries them (a gate run with
  GOH_TIMINGS: tools/gate_profile.sh, a land, or any push since the push gate records them),
  dated when they come from an older run than the wall.

TARGET is the CIP one: 10 min or less at normal load (ARM, lib/budget.py), aim for 5. Nothing is
gated and nothing is backfilled (`goh budget` itself backfills). Pinned by tests/test_gate_times.py.

`goh budget gates [--json]` (CLS.CIPROFILE): every estate repo (the registry, `estate_registry.py`)
by its NEWEST `push` ledger row, slowest first: wall, load at start, passed or not, against the CIP
target (10 min, aim 5); under each, the `top_steps` of its newest push row that carries them (dated
when older than the wall), else `not recorded`; then every registered repo with no push row. Read
only. The checks are there because `gates/push_gate.sh` times its own steps into a file of its own
when no `GOH_TIMINGS` was handed to it (removed after the row).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import estate_registry
import timing_ledger

TARGET_S = 600  # the CIP target: 10 min at normal load
AIM_S = 300  # the aim: 5 min
MARK = {"over": "✗", "near": "⚠", "aim": "✓"}
SHOWN = {"over": "over 10 min", "near": "under 10, over 5", "aim": "within 5 min"}


def parse_at(at: str) -> dt.datetime:
    when = dt.datetime.fromisoformat(at)
    return when if when.tzinfo else when.replace(tzinfo=dt.UTC)


def target(wall_s: float) -> str:
    return "over" if wall_s > TARGET_S else "near" if wall_s > AIM_S else "aim"


def snapshot() -> dict:
    repos, unreadable = estate_registry.read(estate_registry.registry_path())
    names = sorted({timing_ledger.repo_name(str(p)) for p in repos})
    pushes: dict[str, list[dict]] = {}
    for r in timing_ledger.read():
        if r.get("kind") == "push" and r.get("repo") in names and r.get("wall_s") is not None:
            pushes.setdefault(r["repo"], []).append(r)
    shown = []
    for name, runs in pushes.items():
        runs.sort(key=lambda r: r.get("epoch") or 0)
        last = runs[-1]
        checked = next((r for r in reversed(runs) if r.get("top_steps")), None)
        wall = float(last["wall_s"])
        shown.append(
            {
                "repo": name,
                "wall_s": wall,
                "load1": last.get("load1"),
                "ok": bool(last.get("ok")),
                "at": last.get("at"),
                "sha": last.get("sha"),
                "runs": len(runs),
                "target": target(wall),
                "steps": checked["top_steps"] if checked else [],
                "steps_at": None if checked is None or checked is last else checked.get("at"),
            }
        )
    shown.sort(key=lambda j: -j["wall_s"])
    return {
        "ledger": str(timing_ledger.ledger_path()),
        "registry": str(estate_registry.registry_path()),
        "target_s": TARGET_S,
        "aim_s": AIM_S,
        "repos": shown,
        "no_run": [n for n in names if n not in pushes],
        "unreadable": unreadable,
    }


def render(snap: dict) -> str:
    goal = f"target {snap['target_s'] // 60} min, aim {snap['aim_s'] // 60}"
    count = len(snap["repos"])
    out = [f"goh budget gates: {count} repos with a recorded push gate ({goal}; {snap['ledger']})"]
    width = max([len(j["repo"]) for j in snap["repos"]] + [4])
    out.append(f"  {'repo':<{width}}  {'wall s':>7} {'load':>6}  {'at':<16}  target")
    for j in snap["repos"]:
        load = "-" if j["load1"] is None else f"{j['load1']:.1f}"
        red = " (failed)" if not j["ok"] else ""
        at = str(j["at"] or "-")[:16].replace("T", " ")
        out.append(
            f"{MARK[j['target']]} {j['repo']:<{width}}  {j['wall_s']:>7.0f} {load:>6}  {at:<16}  "
            f"{SHOWN[j['target']]}{red}"
        )
        if j["steps"]:
            when = f" (from the run of {str(j['steps_at'])[:10]})" if j["steps_at"] else ""
            checks = ", ".join(f"{a[:60]} {b:.0f} s" for a, b in j["steps"][:3])
            out.append(f"    per check{when}: {checks}")
        else:
            out.append("    per check: not recorded")
    if snap["no_run"]:
        out.append(f"· no recorded push gate: {', '.join(snap['no_run'])}")
    for line in snap["unreadable"]:
        out.append(f"⚠ estate registry line not read: {line}")
    out.append(
        "→ profile one repo per check: tools/gate_profile.sh <repo> (method: goh map gate_profile)"
    )
    return "\n".join(out)


def main(argv: list[str]) -> int:
    snap = snapshot()
    print(json.dumps(snap, ensure_ascii=False, indent=2) if "--json" in argv else render(snap))
    return 0
