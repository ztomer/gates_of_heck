#!/usr/bin/env python3
"""The machine's room for more work: ONE reading, ONE rule (GOH.ADAPTLANES). `goh capacity`.

    goh capacity [--json]      # lib/capacity.py: exit 0 room (or the reading failed), 1 no room

THE GAP (owner 2026-11-17: "are the number of lanes across the sessions adaptive to the available
resources?"). How many agent lanes ran was a hand-set constant (the loop queue's `rules.lanes`), and
every Claude session decided alone: one session pushed its own repo while three goh lanes
landed. Nothing read the machine, so lanes started into a box whose gate slots were held and whose
load ran 40-130 on 16 cores, then idled waiting for a slot (one land 11+ min).

THE READING is the gate slots' OWN state -- which of `GOH_GATE_SLOTS` slot files are flock'd, and
the live tickets in their `waiting/` dir (lib/gate_slot.py, lib/gate_lanes.py) -- never a second
registry (GOH.ADMITREGISTRY); the load average over the cores; memory (lib/mem_admit.py's reading);
free disk on the build volume (the volume of `~/.cache`, where the shared build dirs and sccache
live; GOH_CAPACITY_DISK moves it); and the CPU WEIGHT of the gates in flight: each holder's median
cpu-s per wall-s over its runs in the one timing record (lib/timing_ledger.py), the machine's median
when its repo has none. `gate_weight` is that weight, the one GOH.CPUADMIT's admission is to reuse.

THE RULE (`room`): another write lane has room while no long-lane gate waits for a slot
(GOH_LANE_MAX_WAITING), the 5-minute load per core is at or under GOH_LANE_LOAD_PER_CORE (the 5-min,
not the 1-min: a test burst swings the 1-min, and a lane starts its gates minutes after it starts),
free disk is at or over GOH_LANE_MIN_FREE_GIB, and memory is not low by mem_admit's own rule.
A part that could not be read is listed in `unread`, and a reading with any unread part is judged
ROOM: a broken instrument never refuses work (probe-first). hooks/claude/lane_room.py applies it to
every session's write spawns.

SEAMS: GOH_LOADAVG_READING="1m 5m 15m" in place of os.getloadavg(); GOH_MEM_READING (mem_admit's);
GOH_TIMING_DIR (timing_ledger's); GOH_GATE_SLOT_DIR / GOH_GATE_SLOTS (the slots'). The reading
try-locks each slot file for an instant (gate_slot.held): a gate polling at that instant retries at
its next poll. Pinned by tests/test_capacity.py.
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
import datetime as dt
import json
import os
import shutil
import statistics
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import admission
import gate_lanes
import gate_slot
import mem_admit
import timing_ledger

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from tui.lib import counted, err, info, ok, warn

GIB = 1024**3
WEIGHT_RUNS = 10  # a repo's newest runs its weight is the median of
# name: (default, parse). Documented in docs/config.md; read at every reading.
SETTINGS: dict[str, tuple[float, type]] = {
    "GOH_LANE_MAX_WAITING": (0, int),
    "GOH_LANE_LOAD_PER_CORE": (2.0, float),
    "GOH_LANE_MIN_FREE_GIB": (30.0, float),
}


def settings(environ: dict[str, str] | None = None) -> dict[str, float]:
    """The rule's ceilings; a value that does not parse is the default (named in `unread`)."""
    env = os.environ if environ is None else environ
    out: dict[str, float] = {}
    for name, (default, kind) in SETTINGS.items():
        raw = env.get(name, "").strip()
        try:
            out[name] = kind(raw) if raw else default
        except ValueError:
            out[name] = default
    return out


def gate_weight(repo: str, rows: list[dict]) -> tuple[float | None, str]:
    """(cores one gate of `repo` burns, where that came from): the median cpu-s per wall-s of its
    newest WEIGHT_RUNS runs in the timing record, else of every run's, else (None, "no runs")."""

    def ratios(rs: list[dict]) -> list[float]:
        out = []
        for r in rs:
            cpu, wall = r.get("cpu_s"), r.get("wall_s")
            if isinstance(cpu, (int, float)) and isinstance(wall, (int, float)) and wall > 0:
                out.append(cpu / wall)
        return out[-WEIGHT_RUNS:]

    own = ratios([r for r in rows if r.get("repo") == timing_ledger.repo_name(repo)])
    if own:
        return round(statistics.median(own), 2), repo
    every = ratios(rows)
    if every:
        return round(statistics.median(every), 2), "all runs"
    return None, "no runs"


def _slots(unread: list[str]) -> dict[str, Any]:
    try:
        directory, total = admission.configured()
    except ValueError as e:
        unread.append(f"gate slots: {e}")
        return {"dir": None, "total": None, "held": None, "free": None, "holders": []}
    holders = []
    if total and os.path.isdir(directory):
        for i in range(total):
            path = gate_slot.slot_path(directory, i)
            if not os.path.exists(path) or not gate_slot.held(path):
                continue
            rec = gate_slot.record(path)
            pid, repo, since = rec if rec else (None, "", None)
            holders.append({"slot": os.path.basename(path), "pid": pid,
                            "repo": repo.split(":")[0].strip(), "since": since})  # fmt: skip
    free = None if not total else total - len(holders)
    return {"dir": directory, "total": total, "held": len(holders), "free": free,
            "holders": holders}  # fmt: skip


def _waiting(directory: str | None) -> dict[str, int]:
    """Live tickets per lane in the slots' waiting/ dir; a dead waiter's is reaped, not counted."""
    found = dict.fromkeys(gate_lanes.LANES, 0)
    where = os.path.join(directory, gate_lanes.WAITING) if directory else ""
    try:
        names = os.listdir(where) if where else []
    except FileNotFoundError:
        names = []
    for name in names:
        parts = name.split("-")
        live = len(parts) == 4 and parts[0] in found
        if live and gate_lanes._live_ticket(os.path.join(where, name)):  # its one reader's rule
            found[parts[0]] += 1
    return found


def _loadavg() -> list[float]:
    seam = os.environ.get("GOH_LOADAVG_READING", "")
    if not seam.strip():  # unset or empty: the OS's, as every GOH_* reads an empty value
        return [round(x, 2) for x in os.getloadavg()]
    try:
        avg = [float(x) for x in seam.split()]
    except ValueError:
        avg = []
    if len(avg) != 3:
        raise ValueError(f"GOH_LOADAVG_READING={seam!r} is not three numbers")
    return avg


def reading() -> dict[str, Any]:
    """The machine now. Every part is read on its own: one that fails is named in `unread`."""
    unread: list[str] = []
    out: dict[str, Any] = {"v": 1, "at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds")}
    out["slots"] = _slots(unread)
    try:
        out["waiting"] = _waiting(out["slots"]["dir"])
    except OSError as e:
        unread.append(f"waiting tickets: {e}")
        out["waiting"] = None
    cores = os.cpu_count() or 1
    try:
        avg = _loadavg()
        out["load"] = {"avg": avg, "cores": cores, "per_core": round(avg[1] / cores, 2)}
    except (OSError, ValueError) as e:
        unread.append(f"load average: {e}")
        out["load"] = None
    mem = mem_admit.reading()
    mb = mem_admit.MB
    out["memory"] = {"avail_pct": mem.avail_pct, "source": mem.source,
                     "swap_used_mb": None if mem.swap_used is None else mem.swap_used // mb,
                     "total_mb": None if mem.mem_total is None else mem.mem_total // mb,
                     "low": mem_admit.low(mem)}  # fmt: skip
    disk = os.environ.get("GOH_CAPACITY_DISK") or os.path.expanduser("~/.cache")
    try:
        out["disk"] = {"path": disk, "free_gib": round(shutil.disk_usage(disk).free / GIB, 1)}
    except OSError as e:
        unread.append(f"free disk at {disk}: {e}")
        out["disk"] = None
    rows = timing_ledger.read() if out["slots"]["holders"] else []
    for h in out["slots"]["holders"]:
        h["weight"], h["weight_from"] = gate_weight(h["repo"], rows)
    weights = [h["weight"] for h in out["slots"]["holders"] if h["weight"] is not None]
    out["cpu_weight"] = {"in_flight": round(sum(weights), 2),
                         "per_core": round(sum(weights) / cores, 2)}  # fmt: skip
    out["unread"] = unread
    out["room"] = room(out)
    return out


def room(r: dict[str, Any], environ: dict[str, str] | None = None) -> dict[str, Any]:
    """{"ok": room for another write lane, "why": each reason there is none, "settings": the
    ceilings}; a reading with an unread part is room (`unread: true`): it never refuses."""
    s = settings(environ)
    why: list[str] = []
    waiting = (r.get("waiting") or {}).get("long")
    if waiting is not None and waiting > s["GOH_LANE_MAX_WAITING"]:
        why.append(f"{counted(waiting, 'gate')} waiting for a slot, over "
                   f"GOH_LANE_MAX_WAITING={s['GOH_LANE_MAX_WAITING']:g}")  # fmt: skip
    load = r.get("load")
    if load and load["per_core"] > s["GOH_LANE_LOAD_PER_CORE"]:
        why.append(f"5-min load {load['avg'][1]:g} on {load['cores']} cores, "
                   f"{load['per_core']:.2f} per core, over "
                   f"GOH_LANE_LOAD_PER_CORE={s['GOH_LANE_LOAD_PER_CORE']:g}")  # fmt: skip
    disk = r.get("disk")
    if disk and disk["free_gib"] < s["GOH_LANE_MIN_FREE_GIB"]:
        why.append(f"{disk['free_gib']:g} GiB free at {disk['path']}, under "
                   f"GOH_LANE_MIN_FREE_GIB={s['GOH_LANE_MIN_FREE_GIB']:g}")  # fmt: skip
    if (r.get("memory") or {}).get("low"):
        why.append(r["memory"]["low"])
    out: dict[str, Any] = {"ok": not why, "why": why, "settings": s}
    if r.get("unread"):
        out.update(ok=True, unread=True)
    return out


def summary(r: dict[str, Any]) -> str:
    """One line of the reading, for a refusal or a warning."""
    s, load, w = r["slots"], r.get("load"), r.get("waiting") or {}
    parts = [f"gate slots {s['held']}/{s['total']} held, {w.get('long', '?')} waiting"]
    if load:
        parts.append(f"load {load['avg'][1]:g}/{load['cores']} cores")
    parts.append(f"gates in flight weigh {r['cpu_weight']['in_flight']:g} cores")
    if r.get("disk"):
        parts.append(f"{r['disk']['free_gib']:g} GiB free")
    return "; ".join(parts)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true", help="the reading as one JSON object")
    args = ap.parse_args(argv)
    r = reading()
    verdict = r["room"]
    if args.json:
        print(json.dumps(r, sort_keys=True))
    else:
        info(summary(r))
        for part in r["unread"]:
            warn(f"not read: {part} (judged as room)")
        if verdict["ok"]:
            ok("room for another write lane")
        else:
            err("no room for another write lane: " + "; ".join(verdict["why"]))
    return 0 if verdict["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
