#!/usr/bin/env python3
"""daemon_cpu.py -- what the system daemons our work drives cost, and which processes feed them.

fseventsd, mds/mds_stores/mdworker, mediaanalysisd, ReportCrash and WindowServer do work on behalf
of whoever writes files, crashes or draws, and their CPU is billed to themselves: `top` names the
daemon, never the writer. EST.FSEVENTSCHURN (2026-11-14): fseventsd sat at 100% and an fs_usage
capture (sudo, owner-run) traced it to goh's coverage gates rebuilding instrumented builds cold.
This probe is the no-sudo half of that attribution, repeatable by anyone: it samples every
process's cumulative CPU (`ps -A`, which reads root daemons' times without privilege) through a
window and reports

* each daemon's CPU over the window, in seconds and as % of one core;
* the non-daemon processes that burned the most CPU in the same window, grouped by command name,
  with the share that ran inside the measured command's own process tree (`run` mode) -- the
  writers the daemons were serving;
* the size change of each `--path` (a build cache, say): bytes written that stayed.

    tools/daemon_cpu.py idle 60                       # the machine at rest, 60 s
    tools/daemon_cpu.py run --path ~/.cache/goh/llvm-cov -- bash gates/coverage_gate.sh ...

Sampling sees a process only while it lives: one born and dead between two samples (`--interval`,
0.5 s) is missed, and one that dies loses its last interval. `run` therefore prints the command
tree's exact CPU (its wait4 rusage) beside what the samples attributed to the tree, so the
gap is a number, not a guess. Per-FILE attribution (which paths fseventsd lstat()s, who issued
each write) needs `sudo fs_usage -w -f filesys`, which this probe never runs. Pinned by
tests/test_daemon_cpu.py.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

_tui = importlib.import_module("tui.lib")  # after the path: goh's own TUI, from this checkout
info, ok, section, warn = _tui.info, _tui.ok, _tui.section, _tui.warn

DAEMONS = (
    "fseventsd",
    "mds",
    "mds_stores",
    "mdworker",
    "mdworker_shared",
    "mediaanalysisd",
    "photoanalysisd",
    "ReportCrash",
    "WindowServer",
)


def parse_cputime(text: str) -> float:
    """`ps -o time` is [[dd-]hh:]mm:ss[.cc]; seconds as a float."""
    days = 0
    if "-" in text:
        d, text = text.split("-", 1)
        days = int(d)
    secs = 0.0
    for part in text.split(":"):
        secs = secs * 60 + float(part)
    return days * 86400 + secs


def snapshot(ps=None) -> dict[int, tuple[int, float, str]]:
    """pid -> (ppid, cumulative CPU seconds, command basename), for every process."""
    out = (ps or _ps)()
    procs: dict[int, tuple[int, float, str]] = {}
    for line in out.splitlines():
        fields = line.split(None, 3)
        if len(fields) < 4 or not fields[0].isdigit():
            continue
        pid, ppid, cpu, comm = fields
        procs[int(pid)] = (
            int(ppid),
            parse_cputime(cpu),
            os.path.basename(comm.strip().strip("()")),
        )
    return procs


def _ps() -> str:
    return subprocess.run(
        ["ps", "-A", "-o", "pid=,ppid=,time=,comm="],
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
    ).stdout


class Window:
    """Every process's CPU through a window of samples: a pid's delta is its last CPU less its CPU
    at the first sample (0 when it was born inside the window). A pid is keyed with its command
    name, so a reused pid is another process."""

    def __init__(self, first: dict[int, tuple[int, float, str]]) -> None:
        self.base = {(pid, comm): cpu for pid, (_ppid, cpu, comm) in first.items()}
        self.last: dict[tuple[int, str], float] = dict(self.base)
        self.parent: dict[int, int] = {pid: ppid for pid, (ppid, _c, _n) in first.items()}

    def add(self, snap: dict[int, tuple[int, float, str]]) -> None:
        for pid, (ppid, cpu, comm) in snap.items():
            key = (pid, comm)
            self.last[key] = max(cpu, self.last.get(key, 0.0))
            self.parent.setdefault(pid, ppid)

    def deltas(self) -> dict[tuple[int, str], float]:
        return {k: v - self.base.get(k, 0.0) for k, v in self.last.items()}

    def in_tree(self, pid: int, root: int) -> bool:
        seen = set()
        while pid not in seen and pid > 1:
            if pid == root:
                return True
            seen.add(pid)
            pid = self.parent.get(pid, 0)
        return False


def report(window: Window, wall: float, root: int | None, top: int) -> dict:
    daemons: dict[str, float] = {}
    writers: dict[str, list[float]] = {}  # comm -> [cpu, cpu inside the command's tree]
    for (pid, comm), cpu in window.deltas().items():
        if cpu <= 0:
            continue
        if comm in DAEMONS:
            daemons[comm] = daemons.get(comm, 0.0) + cpu
            continue
        row = writers.setdefault(comm, [0.0, 0.0])
        row[0] += cpu
        if root is not None and window.in_tree(pid, root):
            row[1] += cpu
    ranked = sorted(writers.items(), key=lambda kv: -kv[1][0])[:top]
    return {
        "wall_s": round(wall, 2),
        "daemons": {
            d: {
                "cpu_s": round(daemons.get(d, 0.0), 2),
                "pct_core": round(100 * daemons.get(d, 0.0) / wall, 1) if wall else 0.0,
            }
            for d in DAEMONS
        },
        "writers": [
            {"comm": c, "cpu_s": round(v[0], 2), "in_command_s": round(v[1], 2)} for c, v in ranked
        ],
        "tree_attributed_s": round(sum(v[1] for v in writers.values()), 2),
    }


def tree_bytes(path: Path) -> int:
    total = 0
    for dirpath, _d, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_blocks * 512
            except OSError:
                continue
    return total


def measure(cmd: list[str] | None, seconds: float, interval: float, top: int, paths: list[Path]):
    before = {str(p): tree_bytes(p) for p in paths}
    window = Window(snapshot())
    t0 = time.monotonic()
    rc = 0
    root = None
    cpu = 0.0
    if cmd:
        proc = subprocess.Popen(
            cmd, stdin=None
        )  # the command inherits the probe's stdin on purpose
        root = proc.pid
        while True:
            # wait4 on the command itself: its tree's exact CPU, never the probe's own `ps` runs
            pid, status, usage = os.wait4(root, os.WNOHANG)
            if pid:
                rc = os.waitstatus_to_exitcode(status)
                cpu = usage.ru_utime + usage.ru_stime
                proc.returncode = rc
                break
            window.add(snapshot())
            time.sleep(interval)
    else:
        while time.monotonic() - t0 < seconds:
            time.sleep(interval)
            window.add(snapshot())
    window.add(snapshot())
    wall = time.monotonic() - t0
    out = report(window, wall, root, top)
    if cmd:
        out["command"] = {"argv": cmd, "rc": rc, "cpu_s": round(cpu, 2)}
    out["paths"] = {
        p: {"before_bytes": b, "after_bytes": tree_bytes(Path(p))} for p, b in before.items()
    }
    return out, rc


def show(out: dict) -> None:
    section(f"daemon CPU over {out['wall_s']} s")
    for name, d in out["daemons"].items():
        if d["cpu_s"]:
            info(f"{name:16} {d['cpu_s']:9.2f} s  {d['pct_core']:6.1f}% of a core")
    section("top CPU outside the daemons (the writers they serve)")
    for w in out["writers"]:
        info(f"{w['comm'][:24]:24} {w['cpu_s']:9.2f} s  ({w['in_command_s']:.2f} s in the command)")
    if "command" in out:
        c = out["command"]
        note = f"command rc={c['rc']}: {c['cpu_s']} s CPU, {out['tree_attributed_s']} s sampled"
        (ok if c["rc"] == 0 else warn)(note)
    for p, s in out["paths"].items():
        delta = (s["after_bytes"] - s["before_bytes"]) / 2**20
        info(
            f"{p}: {s['before_bytes'] / 2**30:.2f} -> {s['after_bytes'] / 2**30:.2f} GiB "
            f"({delta:+.0f} MiB)"
        )


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("mode", choices=["idle", "run"])
    ap.add_argument("seconds", nargs="?", type=float, default=30.0, help="idle window (idle mode)")
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--path", action="append", type=Path, default=[])
    ap.add_argument("--json", type=Path, help="also write the result as JSON here")
    cut = argv.index("--") if "--" in argv else len(argv)
    args, cmd = ap.parse_args(argv[:cut]), argv[cut + 1 :]
    if args.mode == "run" and not cmd:
        ap.error("run needs -- COMMAND")
    out, rc = measure(
        cmd if args.mode == "run" else None,
        args.seconds,
        args.interval,
        args.top,
        [p.expanduser() for p in args.path],
    )
    show(out)
    if args.json:
        args.json.write_text(json.dumps(out, indent=1) + "\n")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
