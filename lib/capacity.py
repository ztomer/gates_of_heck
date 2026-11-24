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
when its repo has none (a holder's repo is its record's repository key among the registered repos:
a lane worktree's label says `wt`). `gate_weight` is that weight; GOH.CPUADMIT replayed admitting
by it and kept the count (docs/decisions/D-0009).

THE RULE (`room`): another write lane has room while at most GOH_LANE_MAX_WAITING long-lane gates
wait for a slot, the MEASURED CPU idle is at or over GOH_LANE_MIN_IDLE_PCT, free disk is at or
over GOH_LANE_MIN_FREE_GIB, and memory is not low by mem_admit's own rule. The CPU idle is the
host's own tick counters (HOST_CPU_LOAD_INFO on macOS, /proc/stat on Linux) sampled twice,
GOH_CPU_IDLE_WINDOW_S apart (LOOP.FILLIDLE, owner 2026-11-21 "we're at 60% idle cpu"): the load
average on macOS counts I/O-blocked threads, so it refused at load 63.6 while the CPU was 72% idle.
Two samples 2 s apart were a coin flip under bursty load (GOH.CAPWINDOW, 2026-11-22: room at 59.6%
idle, the spawn hook a second later refused at 0.7%), so every reading records its sample in ONE
state file (GOH_CPU_IDLE_STATE), one self-exiting sampler keeps it warm, and the idle is the mean
since the newest sample GOH_CPU_IDLE_ROLLING_S (60 s) old; the 2 s sample is the cold fallback.
THE PRE-STEPS (GOH.CAPPRESTEP, 2026-11-23: "0 gates in flight" at load 23/16 while a pre-step ran
pytest -n 12 and a cargo build): the pre lane's held slots, each weighed by its repo's recorded
pre-steps (`prestep_weight`, a land record's `pre_run`) into the CPU weight in flight; the enqueues
in their pre-step (lib/land_slot.py `presteps`); and every checkout slot owner whose pid is dead
and whose token no queued request names, reaped (`reap`). THE CAP (GOH.LOADCAP): another write
lane has no room while more pre-steps are in flight than lib/load_cap.py's cap, the peak of the
measured curve of landed items per hour against pre-steps in flight (owner 2026-11-23: "choose
the value that will give us the most overall throughput"; 1 on 2026-11-24).
The load average is now the SECONDARY signal: it is read and shown, and it judges (the 5-min per
core over GOH_LANE_LOAD_PER_CORE) only when the idle could not be measured.
A part that could not be read is listed in `unread`, and a reading with any unread part is judged
ROOM: a broken instrument never refuses work (probe-first). hooks/claude/lane_room.py applies it to
every session's write spawns.

SEAMS: GOH_LOADAVG_READING="1m 5m 15m" in place of os.getloadavg(); GOH_CPU_IDLE_READING="<pct>"
in place of sampling the CPU ticks (`cpu_ticks` is the sampler a test fakes); GOH_MEM_READING
(mem_admit's); GOH_TIMING_DIR (timing_ledger's); GOH_GATE_SLOT_DIR / GOH_GATE_SLOTS (the
slots'). The reading try-locks each slot file for an instant (gate_slot.held): a gate polling at
that instant retries at its next poll. Pinned by tests/test_capacity.py.
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
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import admission
import capacity_presteps
import cpu_window
import estate_registry
import gate_lanes
import gate_slot
import lock_order
import mem_admit
import timing_ledger

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from tui.lib import counted, err, info, ok, warn

GIB = 1024**3
WEIGHT_RUNS = 10  # a repo's newest runs its weight is the median of
# name: (default, parse). Calibrated from the lane ledger joined with each lane's land
# (GOH.ADAPTLANES2; the join and the numbers are in docs/config.md); read at every reading.
SETTINGS: dict[str, tuple[float, type]] = {
    "GOH_LANE_MAX_WAITING": (1, int),
    "GOH_LANE_LOAD_PER_CORE": (2.5, float),
    "GOH_LANE_MIN_FREE_GIB": (30.0, float),
    "GOH_LANE_MIN_IDLE_PCT": (20.0, float),
}
IDLE_WINDOW_S = 2.0  # GOH_CPU_IDLE_WINDOW_S: seconds between the two tick samples


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


def repos_by_key() -> dict[str, str]:
    """{repository key: repo name} of the registered repos and this checkout: the key a slot
    record carries (lib/lock_order.py `repo_key`, a hash of the git common dir) is the SAME in
    every worktree of a repository, where the record's label is the worktree's directory."""
    paths = [*estate_registry.existing(), Path(__file__).resolve().parent.parent]
    return {k: timing_ledger.repo_name(str(p)) for p in paths if (k := lock_order.repo_key(p))}


def holder_repo(label: str, key: str, known: dict[str, str]) -> str:
    """The repository a slot holder's gate is for: by its record's key among `known`, else the
    label's first field (a lane worktree's label says `wt`, GOH.CPUADMIT)."""
    return known.get(key) or label.split(":")[0].strip()


def _holders(directory: str, count: int, lane: str) -> list[dict[str, Any]]:
    """The held slots of LANE (its first COUNT slot files): slot, pid, repo, since."""
    holders = []
    known: dict[str, str] | None = None  # read once, and only when a holder has a key
    for i in range(count):
        path = gate_slot.slot_path(directory, i, lane)
        if not os.path.exists(path) or not gate_slot.held(path):
            continue
        rec = gate_slot.record_scoped(path)
        pid, label, since, key = rec if rec else (None, "", None, "")
        if key and known is None:
            known = repos_by_key()
        holders.append({"slot": os.path.basename(path), "pid": pid,
                        "repo": holder_repo(label, key, known or {}),
                        "since": since})  # fmt: skip
    return holders


def _slots(unread: list[str]) -> dict[str, Any]:
    try:
        directory, total = admission.configured()
    except ValueError as e:
        unread.append(f"gate slots: {e}")
        return {"dir": None, "total": None, "held": None, "free": None, "holders": []}
    holders = _holders(directory, total, "long") if total and os.path.isdir(directory) else []
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
        got = gate_lanes.parse_ticket(name)  # the tickets' one parser
        if got and gate_lanes._live_ticket(os.path.join(where, name)):  # its one reader's rule
            found[got[0]] += 1
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


# The clock and the pause the CPU idle reads: seams a test replaces on this module alone, never on
# the time module every thread of a worker shares.
clock = time.time
pause = time.sleep


def cpu_ticks() -> tuple[int, int]:
    """(idle ticks, total ticks) since boot, summed over every core: macOS's HOST_CPU_LOAD_INFO
    (user, system, idle, nice) or Linux's /proc/stat `cpu` line (idle + iowait as idle)."""
    if sys.platform == "darwin":
        import ctypes
        import ctypes.util

        lib = ctypes.CDLL(ctypes.util.find_library("System"))
        lib.mach_host_self.restype = ctypes.c_uint
        arr, count = (ctypes.c_uint32 * 4)(), ctypes.c_uint32(4)
        kr = lib.host_statistics(lib.mach_host_self(), 3, ctypes.byref(arr), ctypes.byref(count))
        if kr != 0:
            raise OSError(f"host_statistics(HOST_CPU_LOAD_INFO) returned {kr}")
        return arr[2], sum(arr)
    with open("/proc/stat", encoding="ascii") as f:
        fields = [int(x) for x in f.readline().split()[1:]]
    return fields[3] + (fields[4] if len(fields) > 4 else 0), sum(fields[:8])


def _cpu_idle() -> dict[str, Any]:
    """{"idle_pct", "window_s", "source", "samples"}: the share of CPU time idle over the newest
    window at least GOH_CPU_IDLE_ROLLING_S long the shared state holds (`rolling`), else over a
    GOH_CPU_IDLE_WINDOW_S sample taken now (`ticks`, the fallback while the state is cold)."""
    seam = os.environ.get("GOH_CPU_IDLE_READING", "").strip()
    if seam:
        try:
            pct = float(seam)
        except ValueError:
            raise ValueError(f"GOH_CPU_IDLE_READING={seam!r} is not a percentage") from None
        return {"idle_pct": pct, "window_s": None, "source": "GOH_CPU_IDLE_READING"}
    now, (idle1, total1) = clock(), cpu_ticks()
    kept, st = cpu_window.record(now, (idle1, total1), reader=True)
    cpu_window.start_sampler(st, now, __file__)
    full = [x for x in kept if now - x[0] >= cpu_window.rolling_s()]
    part = [x for x in kept if now - x[0] >= cpu_window.MIN_PARTIAL_S]
    base = full[-1] if full else (part[0] if part else None)
    if base is not None and total1 > base[2]:
        pct = round(100.0 * (idle1 - base[1]) / (total1 - base[2]), 1)
        n = sum(1 for x in kept if x[0] >= base[0]) + 1
        return {"idle_pct": pct, "window_s": round(now - base[0], 1), "source": "rolling",
                "samples": n}  # fmt: skip
    raw = os.environ.get("GOH_CPU_IDLE_WINDOW_S", "").strip()
    try:
        window = float(raw) if raw else IDLE_WINDOW_S
    except ValueError:
        window = IDLE_WINDOW_S
    pause(window)
    idle2, total2 = cpu_ticks()
    if total2 <= total1:
        raise OSError("the CPU tick counters did not advance")
    cpu_window.record(clock(), (idle2, total2), reader=True)
    pct = round(100.0 * (idle2 - idle1) / (total2 - total1), 1)
    return {"idle_pct": pct, "window_s": window, "source": "ticks", "samples": 2}


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
    try:
        out["cpu"] = _cpu_idle()
    except (OSError, ValueError, IndexError, AttributeError) as e:
        # not `unread` while the load average stands in for it: the rule falls back to the load
        out["cpu"] = {"idle_pct": None, "window_s": None, "source": f"unmeasured: {e}"}
        if out["load"] is None:
            unread.append(f"cpu idle: {e}")
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
    out["presteps"] = capacity_presteps.reading(out["slots"], _holders, unread)
    held = [*out["slots"]["holders"], *out["presteps"]["holders"]]
    weights = [h["weight"] for h in held if h["weight"] is not None]
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
    idle = (r.get("cpu") or {}).get("idle_pct")
    if idle is not None:  # the measured idle judges; the load average is only shown beside it
        if idle < s["GOH_LANE_MIN_IDLE_PCT"]:
            beside = f" (5-min load {load['avg'][1]:g} on {load['cores']} cores)" if load else ""
            why.append(f"{cpu_window.idle_text(r['cpu'])}, under GOH_LANE_MIN_IDLE_PCT="
                       f"{s['GOH_LANE_MIN_IDLE_PCT']:g}{beside}")  # fmt: skip
    elif load and load["per_core"] > s["GOH_LANE_LOAD_PER_CORE"]:
        why.append(f"5-min load {load['avg'][1]:g} on {load['cores']} cores, "
                   f"{load['per_core']:.2f} per core, over "
                   f"GOH_LANE_LOAD_PER_CORE={s['GOH_LANE_LOAD_PER_CORE']:g} "
                   f"(CPU idle unmeasured)")  # fmt: skip
    pre = r.get("presteps") or {}
    cap = (pre.get("cap") or {}).get("value")
    if cap and pre.get("in_flight", 0) > cap:
        why.append(f"{counted(pre['in_flight'], 'pre-step')} in flight, over the cap of {cap} "
                   f"({pre['cap']['from']}, lib/load_cap.py)")  # fmt: skip
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
    if (r.get("cpu") or {}).get("idle_pct") is not None:
        parts.append(cpu_window.idle_text(r["cpu"]))
    if load:
        parts.append(f"load {load['avg'][1]:g}/{load['cores']} cores")
    pre = r.get("presteps")
    if pre:
        cap = (pre.get("cap") or {}).get("value")
        parts.append(f"pre-steps: {pre['held']}/{pre['total']} pre slots held, "
                     f"{pre['in_flight']} in flight (cap {cap})")  # fmt: skip
    parts.append(f"gates and pre-steps in flight weigh {r['cpu_weight']['in_flight']:g} cores")
    if r.get("disk"):
        parts.append(f"{r['disk']['free_gib']:g} GiB free")
    return "; ".join(parts)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true", help="the reading as one JSON object")
    ap.add_argument("--sampler", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.sampler:
        return cpu_window.sampler(cpu_ticks)
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
