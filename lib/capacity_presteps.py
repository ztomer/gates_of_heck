"""The land pre-steps in `goh capacity`'s reading (GOH.CAPPRESTEP), and their cap (GOH.LOADCAP).

2026-11-23 22:1x: capacity said "gates in flight weigh 0 cores" at load 23 on 16 cores while an
enqueue's pre-step held the pre lane (pytest -n 12 and a cargo build in landq's checkout slot 1),
and slot 5's owner, pid 27042 from 16:12, was dead. `reading` names every held pre-lane slot
(lib/gate_lanes.py's `pre` lane) weighed by its repo's recorded pre-steps (`prestep_weight`: the
median CPU-s per run-s of a land record's `pre_run`), every enqueue still in its pre-step
(lib/land_slot.py `presteps`), every dead owner it reaped (`reap`), and the cap on pre-steps in
flight (lib/load_cap.py). lib/capacity.py adds the weights to the CPU weight in flight and refuses
another write lane above the cap. Pinned by tests/test_capacity_presteps.py.
"""

from __future__ import annotations

import os
import sqlite3
import statistics
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_lanes
import land_records
import land_slot
import load_cap
import state_paths
import timing_ledger

WEIGHT_RUNS = 10  # a repo's newest recorded pre-steps its weight is the median of


def landq_dir() -> Path:
    return state_paths.landq_dir()


def prestep_weight(repo: str, records: list[dict]) -> tuple[float | None, str]:
    """(cores one pre-step of `repo` burns, where that came from): the median CPU-s per run-s of
    the newest WEIGHT_RUNS recorded pre-steps (a land record's `pre_run`) of the repo, else of
    every repo's, else (None, "no pre-steps")."""

    def ratios(rs: list[dict]) -> list[float]:
        out = []
        for r in rs:
            run = r.get("pre_run") if isinstance(r.get("pre_run"), dict) else {}
            cpu, wall = run.get("cpu_s"), run.get("run_s")
            if isinstance(cpu, (int, float)) and isinstance(wall, (int, float)) and wall > 0 < cpu:
                out.append(cpu / wall)
        return out[-WEIGHT_RUNS:]

    own = ratios([r for r in records if timing_ledger.repo_name(str(r.get("repo", ""))) == repo])
    if own:
        return round(statistics.median(own), 2), repo
    every = ratios(records)
    if every:
        return round(statistics.median(every), 2), "all repos"
    return None, "no pre-steps"


def _slot_repo(repo_key: str) -> str:
    """The repo a checkout slot root `<q>/slots/<name>-<hash>` is of (lib/land_slot.py root)."""
    return timing_ledger.repo_name(os.path.basename(repo_key).rsplit("-", 1)[0])


def reading(slots: dict[str, Any], holders_of: Callable[[str, int, str], list[dict]],
            unread: list[str]) -> dict[str, Any]:  # fmt: skip
    """The land pre-steps (GOH.CAPPRESTEP): the pre lane's held slots, each weighed; the enqueues
    in their pre-step (a checkout slot whose claimer lives, its request not yet filed), whose
    count is judged against the cap (lib/load_cap.py); and the dead owners reaped."""
    q = landq_dir()
    keys = (*load_cap.KEYS, "repo", "pre_run")
    records = [r for _, r in land_records.records(q, keys=keys)]
    width = gate_lanes.lane_slots("pre", slots["total"]) if slots["total"] else 0
    directory = slots["dir"]
    holders = holders_of(directory, width, "pre") if width and os.path.isdir(directory) else []
    for h in holders:
        h["weight"], h["weight_from"] = prestep_weight(h["repo"], records)
    try:
        live = [{"repo": _slot_repo(o["repo_key"]), "n": o["n"], "pid": o["pid"], "since": o["at"]}
                for o in land_slot.presteps(q)]  # fmt: skip
        reaped = [{"repo": _slot_repo(o["repo_key"]), "n": o["n"], "pid": o["pid"]}
                  for o in land_slot.reap(q)]  # fmt: skip
    except (OSError, sqlite3.Error) as e:
        unread.append(f"pre-step slots: {e}")
        live, reaped = [], []
    return {"total": width, "held": len(holders), "holders": holders, "live": live,
            "in_flight": len(live), "reaped": reaped, "cap": load_cap.cap(records)}  # fmt: skip
