#!/usr/bin/env python3
"""Memory admission: a new gate, land or suite waits while the machine is short of memory.

    lib/mem_admit.py reading               # the reading, and why it is low (exit 1) or not (0)
    lib/mem_admit.py wait --what NAME      # wait, bounded, while memory is low; says why

THE INCIDENT (GOH.MEMADMIT, 2026-11-16). The machine thrashed from ~00:45 to a forced restart at
06:16. A JetsamEvent at 02:32 counted low-swap kills, a 46.7 GiB compressor, and one coalition
(the agents' lanes, lands and gates) at 78.6 GiB on a 64 GiB machine. Every admission goh had --
the gate slots (lib/gate_slot.py), the load places (lib/load_slot.py) -- counted CPU work; nothing
asked whether the memory was there, so each new gate started into a machine already swapping.

THE RULE. A new gate (the gate slot's holder calls `wait` before it takes a slot: every full gate,
every land's gate and, since GOH.ADMITSUITE, a suite run by hand), and a suite with the gate slots
off (tools/pytest.sh, GOH_GATE_SLOTS=0) wait while
  * the memory available is under GOH_MEM_MIN_PCT percent (default 20): macOS's
    `kern.memorystatus_level`, Linux's MemAvailable over MemTotal; or
  * swap in use is at or over GOH_SWAP_MAX_PCT percent of physical memory (default 50): macOS's
    `vm.swapusage`, Linux's SwapTotal - SwapFree.
The wait is BOUNDED (GOH_MEM_WAIT_MAX seconds, default 600): on expiry it says so and starts
anyway -- a reading must never wedge every gate on the machine. It says why when it starts and
every 30 s. A reading the OS does not give admits, and says nothing: no reading is not low memory.
GOH_MEM_ADMIT=0 turns it off; the suite turns it off for its fixture gates (tests/host_locks.py),
which run inside a suite already admitted.

THE SEAM. GOH_MEM_READING names a file of `key=value` words (`avail_pct=5 swap_used_mb=40000
mem_total_mb=65536`), read at every poll in place of the OS, so a test can fake a low reading and
then lift it. Pinned by tests/test_mem_admit.py.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # a copy outside any checkout: run as is
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import argparse
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from tui.lib import info, ok, report, warn

REPORT_EVERY_S = 30.0
MB = 1 << 20


@dataclass(frozen=True)
class Reading:
    avail_pct: int | None = None
    swap_used: int | None = None  # bytes
    mem_total: int | None = None  # bytes
    source: str = "none"


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _sysctl(name: str) -> str | None:
    try:
        r = subprocess.run(
            ["sysctl", "-n", name],
            capture_output=True,
            text=True,
            timeout=10,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def parse_swapusage(text: str) -> int | None:
    """Bytes of swap in use from `sysctl -n vm.swapusage` (`total = 2048.00M  used = 1.50G ...`)."""
    m = re.search(r"used\s*=\s*([\d.]+)([KMGT])", text)
    if m is None:
        return None
    scale = {"K": 1 << 10, "M": 1 << 20, "G": 1 << 30, "T": 1 << 40}[m.group(2)]
    return int(float(m.group(1)) * scale)


def parse_meminfo(text: str) -> Reading:
    """A reading from Linux's /proc/meminfo."""
    kb = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].isdigit():
            kb[parts[0].rstrip(":")] = int(parts[1]) * 1024
    total, avail = kb.get("MemTotal"), kb.get("MemAvailable")
    pct = int(avail * 100 / total) if total and avail is not None else None
    swap = None
    if "SwapTotal" in kb and "SwapFree" in kb:
        swap = kb["SwapTotal"] - kb["SwapFree"]
    return Reading(pct, swap, total, "/proc/meminfo")


def parse_seam(text: str) -> Reading:
    """A reading from the GOH_MEM_READING file's `key=value` words."""
    words = dict(w.split("=", 1) for w in text.split() if "=" in w)

    def num(key: str) -> int | None:
        v = words.get(key, "")
        return int(v) if v.isdigit() else None

    swap, total = num("swap_used_mb"), num("mem_total_mb")
    return Reading(
        num("avail_pct"),
        None if swap is None else swap * MB,
        None if total is None else total * MB,
        "GOH_MEM_READING",
    )


def reading() -> Reading:
    """The machine's memory now: the seam's file when GOH_MEM_READING names one, else the OS."""
    seam = os.environ.get("GOH_MEM_READING", "")
    if seam:
        try:
            with open(seam, encoding="utf-8") as f:
                return parse_seam(f.read())
        except OSError:
            return Reading(source="GOH_MEM_READING (unreadable)")
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/meminfo", encoding="utf-8") as f:
                return parse_meminfo(f.read())
        except OSError:
            return Reading()
    level = _sysctl("kern.memorystatus_level")
    swap = _sysctl("vm.swapusage")
    total = _sysctl("hw.memsize")
    return Reading(
        int(level) if level and level.isdigit() else None,
        parse_swapusage(swap) if swap else None,
        int(total) if total and total.isdigit() else None,
        "kern.memorystatus_level, vm.swapusage",
    )


def low(r: Reading) -> str | None:
    """Why `r` is too little memory to start more work on, or None when it is enough."""
    min_pct = _int_env("GOH_MEM_MIN_PCT", 20)
    max_swap_pct = _int_env("GOH_SWAP_MAX_PCT", 50)
    if r.avail_pct is not None and r.avail_pct < min_pct:
        return f"{r.avail_pct}% of memory available, under GOH_MEM_MIN_PCT={min_pct}% ({r.source})"
    if r.swap_used is not None and r.mem_total:
        pct = r.swap_used * 100 // r.mem_total
        if pct >= max_swap_pct:
            return (
                f"{r.swap_used // MB} MB of swap in use, {pct}% of memory, at or over "
                f"GOH_SWAP_MAX_PCT={max_swap_pct}% ({r.source})"
            )
    return None


def _ago(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 60}m {s % 60:02d}s" if s >= 60 else f"{s}s"


def wait(
    what: str,
    *,
    alive: Callable[[], bool] = lambda: True,
    read: Callable[[], Reading] = reading,
) -> float:
    """Wait while memory is low, at most GOH_MEM_WAIT_MAX seconds; the seconds waited.

    Returns early (and the caller stops) when `alive()` turns False: the gate it waits for is gone.
    """
    if os.environ.get("GOH_MEM_ADMIT", "1") in ("0", "off", "no", "false"):
        return 0.0
    why = low(read())
    if why is None:
        return 0.0
    max_s = _int_env("GOH_MEM_WAIT_MAX", 600)
    poll = float(os.environ.get("GOH_MEM_POLL", "") or 2.0)
    start = time.monotonic()
    next_report = 0.0
    while why is not None:
        waited = time.monotonic() - start
        if waited >= max_s:
            warn(
                f"memory admission: waited {_ago(waited)} for memory (GOH_MEM_WAIT_MAX={max_s}s) "
                f"and it is still low -- {what} starts anyway: {why}"
            )
            return waited
        if waited >= next_report:
            head = (
                "memory is low"
                if next_report == 0.0
                else f"waited {_ago(waited)}; memory is still low"
            )
            report(f"{head} -- {what} waits before it starts (lib/mem_admit.py):", why)
            next_report += REPORT_EVERY_S
        if not alive():
            return waited
        time.sleep(poll)
        why = low(read())
    waited = time.monotonic() - start
    ok(f"memory admission: {what} starts after {_ago(waited)} waiting for memory")
    return waited


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("reading")
    w = sub.add_parser("wait")
    w.add_argument("--what", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "reading":
        r = reading()
        why = low(r)
        info(f"memory: {r}")
        info(f"low: {why}" if why else "enough to start more work")
        return 1 if why else 0
    wait(args.what)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
